"""Bounded generative security properties, with explicit positive/negative oracles.

No model, external feed or dynamic object loader is involved. Generated examples
exercise the real parsers, signed-feed verifier, typed registry and DLP scanner.
"""
import base64
import copy
import json
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from hypothesis import given, settings, strategies as st

from actiongate.controls.artifacts import inspect_plain_json
from actiongate.controls.dlp import DLPScanner, normalize_with_spans
from actiongate.controls.registry import ToolDefinition, typed_calculate
from actiongate.controls.schema import PolicyConfig
from actiongate.controls.signed import ControlError, canonical_json, load_json, load_yaml, sign_document, validate_feed

ROOT = Path(__file__).resolve().parents[1]
BOUNDED = settings(max_examples=40, deadline=None, database=None)
TEXT = st.text(st.characters(blacklist_categories=("Cs",)), max_size=24)
SCALAR = st.none() | st.booleans() | st.integers(-10**12, 10**12) | TEXT
JSON_VALUE = st.recursive(SCALAR, lambda child: st.lists(child, max_size=4)
    | st.dictionaries(TEXT, child, max_size=4), max_leaves=20)
ASCII_ID = st.text(string.ascii_letters + string.digits + "_-", min_size=1, max_size=20)


@pytest.fixture(scope="module")
def controls():
    config = PolicyConfig.model_validate(load_yaml((ROOT/"policy/control.yaml").read_text(encoding="utf-8")))
    tools = {item["name"]: ToolDefinition.model_validate(item)
        for item in load_json((ROOT/"policy/tool-registry.json").read_bytes())}
    return config, DLPScanner(), tools


@pytest.fixture(scope="module")
def signing_keys():
    key = Ed25519PrivateKey.generate()
    return (base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode(),
        {"fuzz": base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()})


@BOUNDED
@given(JSON_VALUE)
def test_fuzz_json_round_trip_preserves_exact_data_and_canonical_bytes(value):
    encoded = canonical_json(value)
    decoded = load_json(encoded)
    assert decoded == value
    assert canonical_json(decoded) == encoded


@BOUNDED
@given(key=ASCII_ID, first=SCALAR, second=SCALAR)
def test_fuzz_duplicate_json_and_yaml_keys_never_choose_an_authoritative_value(key, first, second):
    # The second spelling decodes to the same key, including a Unicode escape.
    escaped = '"\\u%04x%s"' % (ord(key[0]), key[1:])
    raw = "{" + json.dumps(key) + ":" + json.dumps(first) + "," + escaped + ":" + json.dumps(second) + "}"
    with pytest.raises(ControlError):
        load_json(raw)
    raw_yaml = yaml.safe_dump({key: first}) + yaml.safe_dump({key: second})
    with pytest.raises(ControlError):
        load_yaml(raw_yaml)


@BOUNDED
@given(value=JSON_VALUE, bad=st.sampled_from([b"\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"]))
def test_fuzz_invalid_utf8_cannot_be_accepted_as_trailing_or_embedded_data(value, bad):
    for raw in (canonical_json(value) + bad, b'{"text":"' + bad + b'"}'):
        with pytest.raises(ControlError):
            load_json(raw)


@BOUNDED
@given(codepoint=st.integers(0xD800, 0xDFFF), as_key=st.booleans())
def test_fuzz_unpaired_escaped_surrogates_are_rejected_before_canonicalization(codepoint, as_key):
    escaped = '"\\u%04x"' % codepoint
    raw = "{" + escaped + ':"value"}' if as_key else '{"value":' + escaped + "}"
    with pytest.raises(ControlError):
        load_json(raw)
    with pytest.raises(ControlError):
        load_json('"' + chr(codepoint) + '"')
    with pytest.raises(ControlError):
        load_yaml('value: "' + chr(codepoint) + '"')
    # A valid surrogate pair must decode to its Unicode scalar, not be lost.
    assert load_json(r'"\ud83d\ude00"') == "\U0001f600"


@BOUNDED
@given(exponent=st.integers(309, 10000), negative=st.booleans())
def test_fuzz_numeric_overflow_cannot_create_non_finite_json_numbers(exponent, negative):
    with pytest.raises(ControlError):
        load_json('{"number":' + ("-" if negative else "") + "1e" + str(exponent) + "}")
    assert load_json("1e100") == 1e100


@BOUNDED
@given(depth=st.integers(25, 45), value=SCALAR)
def test_fuzz_parser_depth_limit_rejects_both_json_and_yaml(depth, value):
    nested = value
    for _ in range(depth):
        nested = [nested]
    with pytest.raises(ControlError):
        load_json(json.dumps({"value": nested}))
    with pytest.raises(ControlError):
        load_yaml("value: " + json.dumps(nested))


@BOUNDED
@given(wrapper=st.lists(st.booleans(), max_size=12), marker=st.sampled_from([
    {"lc": 1, "type": "constructor", "id": ["os", "system"], "kwargs": {"command": "never-run"}},
    {"lc": 1, "type": "secret", "id": ["ENV_SECRET"]}, {"py/object": "os.system"},
    {"__reduce__": ["never-run"]}, {"py/reduce": ["never-run"]}]))
def test_fuzz_nested_reconstruction_markers_are_rejected_without_deserializing(wrapper, marker):
    value = copy.deepcopy(marker)
    for as_list in wrapper:
        value = [value] if as_list else {"untrusted": value}
    with pytest.raises(ControlError):
        inspect_plain_json(value)
    # A normal description is still data, even when it discusses the marker.
    inspect_plain_json({"description": json.dumps(marker)})


def obfuscate_ascii(value, fullwidth, invisible):
    converted = "".join(chr(ord(char) + 0xFEE0) if fullwidth and "!" <= char <= "~" else char for char in value)
    return invisible.join(converted)


@BOUNDED
@given(local=st.text(string.ascii_lowercase, min_size=3, max_size=16), fullwidth=st.booleans(),
    invisible=st.sampled_from(["", "\u200b", "\u200c", "\u2060", "\ufeff"]))
def test_fuzz_unicode_email_redaction_preserves_only_approved_text_fields(controls, local, fullwidth, invisible):
    config, scanner, tools = controls
    email = local + "@example.com"
    disguised = obfuscate_ascii(email, fullwidth, invisible)
    normalized, spans = normalize_with_spans(disguised)
    assert normalized == email and len(spans) == len(email)
    payload = {"content": "Contact: " + disguised + ". Done.", "recipient": "internal_demo_sink"}
    original = copy.deepcopy(payload)
    tools["reports.publish_demo"].validate(payload)
    safe, scan = scanner.scan_payload(payload, config, redact_fields=["/content"])
    assert scan.decision == "redact" and scan.entities == ["EMAIL"]
    assert safe == {"content": "Contact: [REDACTED:EMAIL]. Done.", "recipient": "internal_demo_sink"}
    assert payload == original
    assert email not in json.dumps(scan.metadata()) and disguised not in json.dumps(scan.metadata(), ensure_ascii=False)
    denied, _ = scanner.scan_payload({"recipient": disguised}, config, redact_fields=["/content"])
    assert denied is None


@BOUNDED
@given(suffix=st.text(string.ascii_letters + string.digits, min_size=8, max_size=32),
    fullwidth=st.booleans(), invisible=st.sampled_from(["", "\u200b", "\u2060"]))
def test_fuzz_unicode_secret_never_returns_a_copy_in_result_or_audit_metadata(controls, suffix, fullwidth, invisible):
    config, scanner, _ = controls
    original = "AG_TEST_SECRET_" + suffix
    disguised = obfuscate_ascii(original, fullwidth, invisible)
    result = scanner.scan("Prefix " + disguised + " suffix", config)
    assert result.decision == "block" and result.redacted_text == "" and "SECRET" in result.entities
    assert original not in json.dumps(result.metadata()) and disguised not in json.dumps(result.metadata(), ensure_ascii=False)


@BOUNDED
@given(content=TEXT.filter(bool), field=st.sampled_from(["tenant", "role", "root_id", "command", "endpoint", "approval", "label"]), value=JSON_VALUE)
def test_fuzz_typed_payload_rejects_extra_authority_fields_without_mutating_data(controls, content, field, value):
    tool = controls[2]["reports.save"]
    legal = {"content": content}
    tool.validate(legal)
    untrusted = {**legal, field: value}
    original = copy.deepcopy(untrusted)
    with pytest.raises(ControlError):
        tool.validate(untrusted)
    assert untrusted == original


@BOUNDED
@given(identifier=ASCII_ID, separator=st.sampled_from(["/", "\\", "%2f", "%5c", "\u2215", "\uff0f", "\x00", "\r\n"]))
def test_fuzz_logical_resource_ids_cannot_become_paths_or_encoded_paths(controls, identifier, separator):
    tool = controls[2]["documents.read"]
    tool.validate({"document_id": identifier})
    for path in (".." + separator + identifier, identifier + separator + "foreign"):
        with pytest.raises(ControlError):
            tool.validate({"document_id": path})


@BOUNDED
@given(identifier=ASCII_ID, prefix=st.sampled_from(["../", "..\\", "/etc/", "C:\\", "D:", "nested/", "nested\\", ".", "\x00"]),
    registry=st.sampled_from(["tools", "models"]))
def test_fuzz_registry_paths_stay_inside_the_approved_policy_directory(controls, identifier, prefix, registry):
    config = controls[0].model_dump()
    config[registry]["registry"] = identifier + ".json"
    PolicyConfig.model_validate(config)
    config[registry]["registry"] = prefix + identifier + ".json"
    with pytest.raises(ValueError):
        PolicyConfig.model_validate(config)


@pytest.mark.parametrize("registry", ["tools", "models"])
@pytest.mark.parametrize("name", ["D:outside.json", "registry.json:alternate_stream", "registry\x00.json"])
def test_windows_drive_relative_alternate_stream_and_nul_registry_names_are_rejected(controls, registry, name):
    from pathlib import PureWindowsPath
    config = controls[0].model_dump()
    if name.startswith("D:"):
        # Reproduce the actual path escape without opening a file or drive.
        combined = PureWindowsPath("C:/project/policy") / name
        assert combined.drive == "D:" and not combined.is_relative_to(PureWindowsPath("C:/project/policy"))
    config[registry]["registry"] = name
    with pytest.raises(ValueError, match="filename inside the policy directory"):
        PolicyConfig.model_validate(config)


@BOUNDED
@given(a=st.integers(-1000, 1000), b=st.integers(-1000, 1000), c=st.integers(-100, 100),
    d=st.integers(1, 100), suffix=st.sampled_from([";import os", ".__class__", "\x00", "[0]", "**2", "\uff0b1"]))
def test_fuzz_calculator_has_arithmetic_semantics_and_no_code_suffix(a, b, c, d, suffix):
    expression = f"(({a})+({b}))*({c})/{d}"
    assert typed_calculate(expression) == pytest.approx((a + b) * c / d)
    with pytest.raises(ControlError):
        typed_calculate(expression + suffix)


def signed_feed(keys, revision, minor):
    timestamp = datetime.now(timezone.utc)
    payload = {"schema_version": 1, "revision": revision, "publisher": "approved-threat-feed",
        "issued_at": timestamp.isoformat(), "expires_at": (timestamp + timedelta(hours=1)).isoformat(),
        "rules": [{"id": "package.fuzz", "scope": "artifact", "selector": "version", "operator": "version_range",
            "value": f">=2.{minor}.0,<2.{minor + 1}.0", "action": "block", "source": "urn:actiongate:fuzz", "package": "fuzz-package"}]}
    return sign_document(payload, keys[0], "fuzz"), timestamp


@BOUNDED
@given(revision=st.integers(1, 100000), minor=st.integers(0, 100), patch=st.integers(0, 100))
def test_fuzz_signed_feed_is_package_scoped_and_signature_revision_bound(signing_keys, revision, minor, patch):
    envelope, timestamp = signed_feed(signing_keys, revision, minor)
    feed = validate_feed(envelope, signing_keys[1], now=timestamp)
    attrs = {"package": "fuzz-package", "version": f"2.{minor}.{patch}"}
    assert feed.match("artifact", attrs) == ["package.fuzz"]
    assert feed.match("artifact", {**attrs, "package": "other-package"}) == []
    assert feed.match("artifact", {**attrs, "version": f"2.{minor + 1}.{patch}"}) == []
    assert feed.match("tool", attrs) == []
    with pytest.raises(ControlError):
        validate_feed(envelope, signing_keys[1], minimum_revision=revision, now=timestamp)
    tampered = copy.deepcopy(envelope)
    tampered["payload"]["rules"][0]["value"] = f">=2.{minor}.0"
    with pytest.raises(ControlError):
        validate_feed(tampered, signing_keys[1], now=timestamp)


@BOUNDED
@given(revision=st.integers(1, 100000), invalid=st.sampled_from(["(?<=secret)token", r"(token)\1", "(" * 8, "x" * 513]))
def test_fuzz_even_validly_signed_feed_cannot_install_unbounded_regex(signing_keys, revision, invalid):
    envelope, timestamp = signed_feed(signing_keys, revision, 0)
    payload = envelope["payload"]
    payload["rules"][0].update(scope="text", selector="text", operator="re2", value=invalid)
    invalid_envelope = sign_document(payload, signing_keys[0], "fuzz")
    with pytest.raises(ControlError):
        validate_feed(invalid_envelope, signing_keys[1], now=timestamp)
