"""Positive/negative security contracts; real OPA is a separately labelled integration."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from hypothesis import given, strategies as st, settings, HealthCheck

from actiongate.controls import (ControlError, ControlPlane, Label, OPAClient, PolicyConfig, SignedFeed,
    canonical_json, digest, inspect_plain_json, load_json, load_yaml, sign_document, typed_calculate,
    valid_iban, valid_pesel, validate_artifact, validate_feed, validate_snapshot, verify_document)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def plane():
    return ControlPlane(ROOT / "policy", require_feed=False)


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode(), {"test": base64.b64encode(public).decode()}


def feed_payload(revision=1, rules=None):
    now = datetime.now(timezone.utc)
    return {"schema_version": 1, "revision": revision, "publisher": "approved-threat-feed",
            "issued_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(), "rules": rules or []}


def feed_rule(**updates):
    return {"id": "external.demo", "scope": "text", "selector": "text", "operator": "re2",
            "value": "forbidden-export", "action": "block", "source": "urn:actiongate:test", **updates}


@pytest.mark.parametrize("text", ["The supplier offers a five year warranty.", "Review the security advisory CVE-2025-68664.",
    "Please explain why pickle is unsafe without loading it.", "Legal arithmetic: 25 + 15."])
def test_legal_text_is_allowed(plane, text):
    assert plane.scan(text).decision == "allow"


@pytest.mark.parametrize("text,entity", [("alex@example.com", "EMAIL"), ("44051401458", "PESEL"),
    ("PL61109010140000071219812874", "IBAN"), ("+48 501 234 567", "PHONE"),
    ("ａｌｅｘ＠ｅｘａｍｐｌｅ．ｃｏｍ", "EMAIL"), ("alex\u200b@example.com", "EMAIL")])
def test_pii_redaction_and_strict_pair(plane, text, entity):
    balanced = plane.scan(text)
    assert balanced.decision == "redact"
    assert entity in balanced.entities
    assert text not in balanced.redacted_text
    assert plane.scan(text, profile="strict").decision == "block"


@pytest.mark.parametrize("secret", ["AG_TEST_SECRET_abcdefghijklmnop", "sk-proj-abcdefghijklmnopqrstuv123456",
    "ghp_abcdefghijklmnopqrstuvwxyz12345", "-----BEGIN PRIVATE KEY-----", "Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghij.klmnop"])
def test_secret_rejected_without_content_copy(plane, secret):
    found = plane.scan("prefix " + secret)
    assert found.decision == "block"
    assert found.redacted_text == ""
    assert secret not in json.dumps(found.metadata())
    output, scan = plane.scan_payload({"content": secret}, ["/content"])
    assert output is None and scan.decision == "block"


def test_checksums_prevent_invalid_identifier_false_positive():
    assert valid_pesel("44051401458")
    assert not valid_pesel("44051401459")
    assert not valid_pesel("00000000000")
    assert valid_iban("PL61 1090 1014 0000 0712 1981 2874")
    assert not valid_iban("PL62 1090 1014 0000 0712 1981 2874")


def test_only_approved_text_fields_redacted(plane):
    safe, result = plane.scan_payload({"content": "alex@example.com", "recipient": "internal_demo_sink"}, ["/content"])
    assert result.decision == "redact"
    assert safe["recipient"] == "internal_demo_sink"
    assert safe["content"] == "[REDACTED:EMAIL]"
    assert plane.scan_payload({"recipient": "alex@example.com"}, ["/content"])[0] is None
    assert plane.scan_payload({"nested": [{"content": "alex@example.com"}]}, ["/nested/0/content"])[1].decision == "redact"


def test_input_and_output_share_identical_scanner(plane):
    text = "Supplier email alex@example.com"
    assert plane.scan(text, scope="text").metadata() == plane.scan(text, scope="text").metadata()
    assert plane.scan(text).redacted_text == "Supplier email [REDACTED:EMAIL]"


def test_control_disabling_and_observe_are_explicit(plane):
    config = plane.config.model_dump()
    config["controls"]["pii"]["enabled"] = False
    changed = ControlPlane(ROOT / "policy", config, require_feed=False, scanner=plane.scanner)
    assert changed.scan("alex@example.com").decision == "allow"
    assert plane.scan("alex@example.com", profile="observe", tenant="acme").decision == "block"
    assert plane.scan("alex@example.com", profile="observe", tenant="synthetic_test_tenant").decision == "allow"


@pytest.mark.parametrize("mutation", [lambda c: c.update(unknown=1),
    lambda c: c["controls"]["authentication"].update(enabled=False),
    lambda c: c["flow"].update(label_override_by_agent=True),
    lambda c: c["flow"]["sinks"]["cloud_model"].update(max_label="CONFIDENTIAL"),
    lambda c: c["tools"].update(registry="../escape.json"),
    lambda c: c["semantic"].update(overlap_tokens=2048),
    lambda c: c["budgets"].update(unknown_usage="release"),
    lambda c: c["budgets"].update(run_total_tokens="100"),
    lambda c: c["profiles"]["observe"].update(restrict_to="acme")])
def test_invalid_policy_preserves_platform_invariants(plane, mutation):
    config = plane.config.model_dump()
    mutation(config)
    with pytest.raises(ValueError):
        PolicyConfig.model_validate(config)


def test_yaml_json_duplicate_alias_and_depth_are_rejected():
    for payload in ('{"a":1,"a":2}', '{"x":NaN}', '[' * 30 + '0' + ']' * 30):
        with pytest.raises(ControlError):
            load_json(payload)
    for payload in ("a: 1\na: 2", "a: &anchor [1]\nb: *anchor", "x: !!python/object:os.system {}"):
        with pytest.raises(ControlError):
            load_yaml(payload)


def test_signature_tamper_unknown_key_and_rollback(keys):
    private, public = keys
    envelope = sign_document(feed_payload(), private, "test")
    assert validate_feed(envelope, public).revision == 1
    for mutation in (lambda e: e["payload"].update(revision=2), lambda e: e.update(key_id="attacker"), lambda e: e.update(algorithm="none")):
        changed = copy.deepcopy(envelope)
        mutation(changed)
        with pytest.raises(ControlError):
            validate_feed(changed, public)
    with pytest.raises(ControlError):
        validate_feed(envelope, public, minimum_revision=1)


def test_feed_expiry_future_and_duplicate_rules(keys):
    private, public = keys
    for update in ({"expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
                   {"issued_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
                   {"rules": [feed_rule(), feed_rule()]}):
        payload = feed_payload()
        payload.update(update)
        with pytest.raises(ControlError):
            validate_feed(sign_document(payload, private, "test"), public)


def test_signed_feed_add_remove_changes_decision(plane, keys):
    private, public = keys
    initial = validate_feed(sign_document(feed_payload(), private, "test"), public)
    added = validate_feed(sign_document(feed_payload(2, [feed_rule()]), private, "test"), public, minimum_revision=1)
    removed = validate_feed(sign_document(feed_payload(3), private, "test"), public, minimum_revision=2)
    for feed, expected in [(initial, "allow"), (added, "block"), (removed, "allow")]:
        active = ControlPlane(ROOT / "policy", plane.config, feed=feed, scanner=plane.scanner)
        assert active.scan("Please use forbidden-export now").decision == expected


def test_feed_cannot_execute_code_or_grant_permission(keys):
    private, public = keys
    for update in ({"action": "allow"}, {"operator": "python"}, {"value": "(?<=abc)def"},
                   {"value": "(a)\\1"}, {"extra_code": "import os"}):
        with pytest.raises(ControlError):
            validate_feed(sign_document(feed_payload(rules=[feed_rule(**update)]), private, "test"), public)


def test_feed_required_and_expired_fail_closed(plane):
    protected = ControlPlane(ROOT / "policy", scanner=plane.scanner)
    assert protected.scan("Legal supplier report").rule_ids == ["availability.feed"]
    payload = feed_payload()
    payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    expired = ControlPlane(ROOT / "policy", feed=payload, scanner=plane.scanner)
    assert expired.scan("Legal supplier report").decision == "block"


def test_registry_typed_tool_pair_and_definition_digest(plane):
    valid = plane.validate_tool("calculator.evaluate", {"expression": "(25 + 15) / 2"})
    assert typed_calculate("(25 + 15) / 2") == 20
    modified = valid.model_copy(update={"description": "Changed meaning"})
    assert modified.definition_digest != valid.definition_digest
    for tool, args in [("shell", {"command": "id"}), ("documents.read", {"document_id": "../globex"}),
                       ("reports.publish_demo", {"content": "hello", "recipient": "https://example.com"}),
                       ("calculator.evaluate", {"expression": "__import__('os')"}),
                       ("memory.write", {"key": "x", "content": "ok", "tenant": "globex"})]:
        with pytest.raises(ControlError):
            plane.validate_tool(tool, args)


@pytest.mark.parametrize("expression", ["__import__('os')", "exec('x')", "1**10000", "1/0", "(" * 20 + "1" + ")" * 20, "1e200"])
def test_calculator_rejects_code_and_resource_abuse(expression):
    with pytest.raises(ControlError):
        typed_calculate(expression)


def manifest(path, **updates):
    return {"schema_version": 1, "name": "safe-test", "format": "json", "source": "urn:approved",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size,
        "package": "data", "version": "1.0.0", "trust_remote_code": False, **updates}


def test_historical_json_pair_never_calls_loader(tmp_path):
    safe = tmp_path / "safe.json"
    safe.write_text(json.dumps({"document": "This advisory describes lc secret reconstruction."}))
    assert validate_artifact(safe, manifest(safe), approved_sources={"urn:approved"})["executed_loader"] is False
    evil = tmp_path / "blocked.json"
    evil.write_text(json.dumps({"nested": {"lc": 1, "type": "secret", "id": ["NOT_A_REAL_SECRET"]}}))
    with pytest.raises(ControlError, match="CVE-2025-68664"):
        validate_artifact(evil, manifest(evil), approved_sources={"urn:approved"})
    inspect_plain_json({"document": "import os and eval are unsafe APIs, not instructions here."})


def test_artifact_digest_source_format_and_runtime_pairs(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text('{"safe":"data"}')
    for overrides in ({"sha256": "0" * 64}, {"source": "urn:attacker"}, {"format": "gguf"},
                      {"package": "llama-cpp-python", "version": "0.2.71"}, {"trust_remote_code": True},
                      {"dependencies": {"langchain-core": "1.2.4"}}):
        with pytest.raises(ControlError):
            validate_artifact(path, manifest(path, **overrides), approved_sources={"urn:approved"})
    assert validate_artifact(path, manifest(path, package="llama-cpp-python", version="0.2.72"), approved_sources={"urn:approved"})["admitted"]
    assert validate_artifact(path, manifest(path, dependencies={"langchain-core": "1.2.5"}), approved_sources={"urn:approved"})["admitted"]
    pickle = tmp_path / "weights.pkl"
    pickle.write_bytes(b"INERT TEST DATA")
    with pytest.raises(ControlError):
        validate_artifact(pickle, manifest(pickle), approved_sources={"urn:approved"})


def test_gguf_template_must_be_exactly_approved(tmp_path):
    template = "Safe approved prompt template"
    def string(value):
        raw = value.encode()
        return struct.pack("<Q", len(raw)) + raw
    data = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + string("tokenizer.chat_template") + struct.pack("<I", 8) + string(template)
    path = tmp_path / "model.gguf"
    path.write_bytes(data)
    approved = hashlib.sha256(template.encode()).hexdigest()
    assert validate_artifact(path, manifest(path, format="gguf", approved_template_sha256=approved), approved_sources={"urn:approved"})["admitted"]
    with pytest.raises(ControlError, match="template"):
        validate_artifact(path, manifest(path, format="gguf"), approved_sources={"urn:approved"})


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]),
       st.sampled_from(["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]),
       st.sets(st.sampled_from(["documents", "mcp", "llm", "memory"])))
def test_label_join_is_monotonic_commutative_and_idempotent(first, second, origins):
    a, b = Label(first, frozenset({"finance"}), frozenset(origins)), Label(second)
    assert a.join(b) == b.join(a)
    assert a.join(a) == a
    assert a.join(b).origins.issuperset(a.origins)
    assert a.join(b).compartments.issuperset(a.compartments)
    assert not Label("CONFIDENTIAL").flows_to("PUBLIC")


def test_signed_snapshot_binds_registry_and_generation(plane, keys):
    private, public = keys
    snapshot = plane.snapshot(1)
    signed = sign_document(snapshot, private, "test")
    assert validate_snapshot(signed, public)["generation"] == 1
    tampered = copy.deepcopy(signed)
    tampered["payload"]["tools"][0]["description"] = "changed"
    with pytest.raises(ControlError):
        validate_snapshot(tampered, public)
    with pytest.raises(ControlError):
        validate_snapshot(signed, public, minimum_generation=1)


def test_cloud_enablement_registry_and_price_are_one_contract(plane):
    config = plane.config.model_dump()
    config["models"]["allowed"].append("cloud-business")
    with pytest.raises(ControlError, match="enablement"):
        ControlPlane(ROOT / "policy", config, require_feed=False, scanner=plane.scanner)
    config["models"]["cloud_enabled"] = True
    price = json.loads((ROOT / "policy/prices.json").read_text())
    now = datetime.now(timezone.utc)
    price.update(verified_at=(now-timedelta(minutes=1)).isoformat(),
                 valid_until=(now+timedelta(days=1)).isoformat())
    active = ControlPlane(ROOT / "policy", config, require_feed=False, scanner=plane.scanner, prices=price)
    assert active.snapshot(99)["prices"] == price
    expired = {**price, "valid_until": "2020-01-01T00:00:00Z"}
    with pytest.raises(ControlError):
        ControlPlane(ROOT / "policy", config, require_feed=False, scanner=plane.scanner, prices=expired)
    # A local deployment remains usable after optional cloud pricing expires.
    local = ControlPlane(ROOT / "policy", plane.config, require_feed=False, scanner=plane.scanner, prices=expired)
    assert local.scan("Public supplier fields").decision == "allow"
    with pytest.raises(ControlError, match="registry differs"):
        ControlPlane(ROOT / "policy", config, require_feed=False, scanner=plane.scanner,
                     prices={**price, "revision": "a-different-revision"})


def test_cloud_registry_cannot_disguise_remote_model_as_local(plane):
    from actiongate.controls import ModelDefinition
    model = plane.models["cloud-business"].model_dump()
    for updates in ({"recipient": "local_model"}, {"model": "arbitrary-upstream"},
                    {"purpose": "guard"}, {"pricing_ref": None}, {"context_tokens": 4096}):
        with pytest.raises(ValueError):
            ModelDefinition.model_validate({**model, **updates})


def complete_tool_conversation():
    return [{"role": "user", "content": "Calculate two plus two."},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "calculator.evaluate", "arguments": '{"expression":"2+2"}'}}]},
            {"role": "tool", "tool_call_id": "call-1", "name": "calculator.evaluate", "content": '{"value":4}'}]


def test_complete_tool_call_conversation_survives_chat_api_serialization(plane):
    from actiongate.contracts import ChatRequest
    request = ChatRequest(messages=complete_tool_conversation())
    payload = {"model": request.model, "messages": [message.model_dump(exclude_none=True) for message in request.messages]}
    approved = plane.validate_tool("models.chat", payload)
    assert approved.version == 3
    assert "content" not in payload["messages"][1]
    assert payload["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"expression":"2+2"}'
    # Explicit null content from API/action clients is valid as well.
    plane.validate_tool("models.chat", {"model": "local-business", "messages": complete_tool_conversation()})


@pytest.mark.parametrize("variant", ["orphan", "missing", "duplicate", "bad_json", "wrong_role", "null_without_calls"])
def test_incomplete_or_ambiguous_tool_call_conversations_rejected(plane, variant):
    messages = complete_tool_conversation()
    if variant == "orphan":
        messages[2]["tool_call_id"] = "other-call"
    elif variant == "missing":
        messages.pop()
    elif variant == "duplicate":
        messages.extend(copy.deepcopy(messages[1:]))
    elif variant == "bad_json":
        messages[1]["tool_calls"][0]["function"]["arguments"] = '{"expression":'
    elif variant == "wrong_role":
        messages[1]["role"] = "user"
    else:
        messages[1].pop("tool_calls")
    with pytest.raises(ControlError):
        plane.validate_tool("models.chat", {"model": "local-business", "messages": messages})


@pytest.mark.asyncio
async def test_opa_absence_fail_closed():
    result = await OPAClient("http://127.0.0.1:1", timeout=0.1).evaluate({}, 1)
    assert result["decision"] == "block" and result["available"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_opa_positive_and_hard_denials(plane):
    url = os.getenv("ACTIONGATE_TEST_OPA_URL")
    if not url:
        if os.getenv("ACTIONGATE_SUITE") == "all-local":
            pytest.fail("all-local requires a real OPA endpoint")
        pytest.skip("Real OPA integration requires ACTIONGATE_TEST_OPA_URL")
    opa = OPAClient(url)
    generation = 900_000_000 + __import__("secrets").randbelow(99_000_000)
    await opa.stage(generation, plane.config, ROOT / "policy/control.rego")
    valid = {"identity_ok": True, "tenant_ok": True, "grant_ok": True, "labels_ok": True, "budget_ok": True,
             "registry_ok": True, "tenant": "acme", "tool": "documents.read", "deterministic": {"decision": "allow", "rule_ids": []},
             "semantic": {"verdict": "benign", "risk_level": 0, "complete": True}}
    assert (await opa.evaluate(valid, generation))["decision"] == "allow"
    for key in ("identity_ok", "tenant_ok", "grant_ok", "labels_ok", "budget_ok", "registry_ok"):
        assert (await opa.evaluate({**valid, key: False}, generation))["decision"] == "block"
    malicious = {**valid, "semantic": {"verdict": "suspicious", "risk_level": 3, "complete": True}}
    assert (await opa.evaluate(malicious, generation))["decision"] == "block"
    approval = {**valid, "tool": "reports.publish_demo"}
    assert (await opa.evaluate(approval, generation))["decision"] == "require_approval"
    assert (await opa.evaluate({**approval, "approval_valid": True}, generation))["decision"] == "allow"
    assert (await opa.evaluate({**approval, "approval_valid": True, "labels_ok": False}, generation))["decision"] == "block"
    assert (await opa.evaluate(valid, 2_000_000_001))["decision"] == "block"


@pytest.mark.parametrize("pooled", [False, True])
async def test_opa_reused_trust_store_rejects_an_untrusted_tls_policy_server(tmp_path, pooled):
    """Transport optimization cannot accept an attacker's valid-looking allow."""
    import ipaddress
    import json
    import ssl
    import threading
    from datetime import datetime, timedelta, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from contextlib import AsyncExitStack
    from actiongate.controls.opa import OPAClient, pooled_opa_clients

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    instant = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(instant - timedelta(minutes=1)).not_valid_after(instant + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    certificate_path, key_path = tmp_path / "test-cert.pem", tmp_path / "test-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            payload = json.dumps({"result": {"generation": 7, "decision": "allow"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with AsyncExitStack() as stack:
            if pooled:
                await stack.enter_async_context(pooled_opa_clients())
            result = await OPAClient(f"https://127.0.0.1:{server.server_port}").evaluate({}, 7)
            assert result["decision"] == "block" and result["available"] is False
            assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_opa_connection_reuse_does_not_cache_a_changed_decision_or_stale_generation():
    """One TCP connection must still perform and validate every real decision."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from actiongate.controls.opa import pooled_opa_clients

    requests = []
    connections = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def setup(self):
            super().setup()
            connections.append(self.connection)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            count = len(requests)
            generation = 6 if count == 3 else 7
            payload = json.dumps({"result": {"generation": generation,
                "decision": "block" if count == 2 else "allow"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        async with pooled_opa_clients():
            assert (await OPAClient(url).evaluate({}, 7))["decision"] == "allow"
            assert (await OPAClient(url).evaluate({}, 7))["decision"] == "block"
            stale = await OPAClient(url).evaluate({}, 7)
            assert stale["decision"] == "block" and stale["available"] is False
            assert len(connections) == 1 and len(requests) == 3
        # A fresh application lifetime must own a new transport.
        async with pooled_opa_clients():
            assert (await OPAClient(url).evaluate({}, 7))["decision"] == "allow"
            assert len(connections) == 2 and len(requests) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
