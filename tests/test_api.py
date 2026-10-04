"""HTTP contracts with signed identities and the real PostgreSQL policy/ledger.

Only model inference is controlled in contract cases. Missing platform services
fail the shared platform fixture; these are not semantic quality measurements.
"""
import json
import asyncio
import copy

import httpx
import jwt
import pytest
from sqlalchemy import select, func

from actiongate.db import Principal, ConnectorReceipt, transaction, uid
from actiongate.security import issue_token
from actiongate.settings import settings


@pytest.fixture
def authenticated(platform):
    def create(role="analyst", tenant="synthetic_test_tenant"):
        principal = f"http-contract:{uid()}"
        with transaction() as db:
            db.add(Principal(id=principal, tenant=tenant, role=role))
        return {"Authorization": "Bearer " + issue_token(principal, tenant, role)}
    return create


@pytest.fixture
async def client(platform):
    from actiongate.app import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8080") as client:
        yield client


async def new_run(client, headers, **kwargs):
    response = await client.post("/runs", headers=headers, json={"document_ids": [], **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


async def act(client, headers, run, tool, arguments, key=None):
    response = await client.post("/actions", headers=headers, json={"run_id": run["id"], "tool": tool,
        "arguments": arguments, "idempotency_key": key or uid()})
    return response


async def test_http_validation_never_echoes_secret_keys_values_or_nested_locations():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from pydantic import BaseModel, ConfigDict
    from actiongate.app import validation_error

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        values: dict[str, int]

    isolated = FastAPI()
    isolated.add_exception_handler(RequestValidationError, validation_error)

    @isolated.post("/validate")
    async def validate(payload: Payload):
        return {"accepted": True}

    secret_key = "SYNTHETIC_PRIVATE_KEY_SENTINEL_79fd"
    secret_value = "SYNTHETIC_PRIVATE_VALUE_SENTINEL_9b1c"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated), base_url="http://testserver") as actual:
        rejected = await actual.post("/validate", json={"values": {secret_key: secret_value}, secret_key: secret_value})
        assert rejected.status_code == 422
        assert secret_key not in rejected.text and secret_value not in rejected.text
        assert {error["type"] for error in rejected.json()["detail"]} == {"extra_forbidden", "int_parsing"}
        assert all(error["loc"] == ["body"] for error in rejected.json()["detail"])
        accepted = await actual.post("/validate", json={"values": {"count": 3}})
        assert accepted.status_code == 200 and accepted.json() == {"accepted": True}


@pytest.mark.parametrize("tenant", ["acme", "synthetic_test_tenant"])
async def test_playground_uses_actual_current_tenant_and_exposes_inspected_fields(client, authenticated, controlled_models, tenant):
    from actiongate.db import DataObject
    from actiongate.security import decrypt
    headers = authenticated(tenant=tenant)
    text = "Internal synthetic delivery review."
    response = await client.post("/api/playground", headers=headers, json={"text": text})
    assert response.status_code == 200
    operation = response.json()
    assert operation["tenant"] == tenant and operation["status"] == "completed"
    assert operation["arguments"]["content"] == text and operation["result"]["saved"] is True
    assert operation["metadata"]["semantic"]["model_digest"] == "contract-fixture"
    with transaction() as db:
        saved = db.scalar(select(DataObject).where(DataObject.name == "report-" + operation["id"]))
        assert saved.tenant == tenant and decrypt(saved.encrypted) == text


async def test_interactive_advisory_is_neither_pass_nor_failure_count_and_remains_tenant_scoped(client, authenticated):
    from actiongate.db import TestRun as Job
    headers = authenticated(tenant="synthetic_test_tenant")
    job_id = uid()
    with transaction() as db:
        db.add(Job(id=job_id, tenant="synthetic_test_tenant", suite="contract", status="advisory", results=[
            {"name": "positive-fixture", "passed": True, "assessment": "passed"},
            {"name": "disabled-fixture", "passed": False, "assessment": "advisory", "policy_behavior_matches": True}]))
    response = await client.get("/api/tests", headers=headers)
    row = next(item for item in response.json()["runs"] if item["id"] == job_id)
    assert (row["status"], row["passed"], row["failed"], row["advisory"], row["total"]) == ("advisory", 1, 0, 1, 2)
    foreign = await client.get("/api/tests", headers=authenticated(tenant="acme"))
    assert all(item["id"] != job_id for item in foreign.json()["runs"])


async def test_budgets_show_signed_current_catalog_separately_from_recorded_price(client, authenticated, controlled_models):
    from actiongate import policies
    from actiongate.db import Operation
    from actiongate.controls.signed import load_json
    headers = authenticated()
    run = await new_run(client, headers)
    op = (await act(client, headers, run, "reports.save", {"content": "Internal supplier review."})).json()
    assert op["status"] == "completed"
    # Clearly labelled provenance fixture; never a claim of provider billing.
    with transaction() as db:
        row = db.get(Operation, op["id"])
        row.metadata_ = {**row.metadata_, "price_revision": "contract-historical-price"}
    snap = policies.snapshot()
    signed = load_json(snap["signature"])["payload"]
    response = await client.get("/api/budgets", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    price = body["pricing"]
    assert price["signature_verified"] is True and price["status"] == "valid"
    assert price["catalog"] == signed["prices"]
    assert price["generation"] == snap["generation"] and price["policy_digest"] == snap["digest"]
    rows = [r for r in body["reservations"] if r["operation_id"] == op["id"]]
    execution = next(r for r in rows if r["kind"] == "execution")
    assert execution["price_revision"] == "contract-historical-price" != price["catalog"]["revision"]
    assert execution["execution_mode"] == "contract-controlled-models"
    assert all(r["price_revision"] is None for r in rows if r["kind"].startswith("guard"))
    foreign = (await client.get("/api/budgets", headers=authenticated(tenant="globex"))).json()
    assert not any(r["operation_id"] == op["id"] for r in foreign["reservations"])


async def test_overview_operational_latency_and_failures_have_exact_tenant_denominators(client, authenticated):
    from actiongate.db import Operation
    tenant = "overview-contract-" + uid()
    headers = authenticated(tenant=tenant)
    run = await new_run(client, headers)
    with transaction() as db:
        for index, (status, measured) in enumerate((("completed", 10), ("completed", 20), ("completed", None),
                ("failed", 999), ("outcome_unknown", None), ("blocked", None))):
            db.add(Operation(tenant=tenant, actor="contract-observation", run_id=run["id"], root_id=run["id"],
                tool="calculator.evaluate", idempotency_key=uid(), payload_hash="contract", input_hash="contract",
                status=status, metadata_={"execution_mode": "contract-controlled-models", "latency_ms": measured}))
        db.add(Operation(tenant="foreign-observation-"+uid(), actor="contract-observation", run_id=uid(), root_id=uid(),
            tool="calculator.evaluate", idempotency_key=uid(), payload_hash="contract", input_hash="contract",
            status="completed", metadata_={"latency_ms": 99999, "execution_mode": "contract-controlled-models"}))
    response = await client.get("/api/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latency"]["p95_ms"] == 20
    assert (body["latency"]["sample_count"], body["latency"]["considered_count"]) == (2, 3)
    assert body["latency"]["execution_modes"] == {"contract-controlled-models": 2}
    assert "not a benchmark" in body["latency"]["scope"]
    assert body["execution_outcomes"]["total_operations"] == 6
    assert body["execution_outcomes"]["statuses"] == {"completed": 3, "failed": 1, "outcome_unknown": 1, "blocked": 1}
    empty = (await client.get("/api/overview", headers=authenticated(tenant="empty-stats-"+uid()))).json()
    assert empty["latency"]["p95_ms"] is None and empty["latency"]["sample_count"] == 0


async def test_dashboard_history_fields_come_from_recorded_run_generation_and_tenant_audit(client, authenticated, controlled_models):
    from actiongate import policies
    headers = authenticated()
    run = await new_run(client, headers)
    op = (await act(client, headers, run, "calculator.evaluate", {"expression": "2+3"})).json()
    detail = (await client.get("/api/operations/"+op["id"], headers=headers)).json()
    assert detail["purpose"] == "supplier_review"
    assert detail["feed_version"] == policies.snapshot()["feed"]["revision"]
    workflow = (await client.get("/api/runs/"+run["id"], headers=headers)).json()["run"]
    assert workflow["created_at"] and workflow["tenant"] == "synthetic_test_tenant"
    overview = (await client.get("/api/operations", headers=headers)).json()
    assert any(event["operation_id"] == op["id"] for event in overview["events"])
    assert all(event["tenant"] == "synthetic_test_tenant" for event in overview["events"])
    assert [event["id"] for event in overview["events"]] == sorted(event["id"] for event in overview["events"])
    feed = (await client.get("/api/feed", headers=headers)).json()
    assert feed["signature_verified"] is True and feed["signature_status"] == "Verified in signed control generation"
    assert feed["generation"] == op["policy_generation"]


async def test_unverifiable_price_is_never_presented_as_verified_catalog(client, authenticated, monkeypatch):
    from actiongate import policies
    headers = authenticated()
    def invalid(*args, **kwargs):
        raise ValueError("Controlled invalid signature")
    monkeypatch.setattr(policies, "verify_stored_snapshot", invalid)
    response = await client.get("/api/budgets", headers=headers)
    assert response.status_code == 200
    assert response.json()["pricing"] == {"status": "unavailable", "signature_verified": False,
        "catalog": None, "note": "The active signed catalog could not be verified."}


async def test_policy_validation_error_never_echoes_secret_yaml_key_or_value(client, authenticated):
    import yaml
    from actiongate import policies
    before = policies.snapshot()
    key = "sk-proj-" + "k"*60
    value = "sk-proj-" + "v"*60
    body = yaml.safe_dump({**before["configuration"], key: value})
    response = await client.post("/api/policies/validate", headers=authenticated("admin"), json={"yaml": body})
    assert response.status_code == 200 and response.json()["valid"] is False
    assert response.json()["errors"] == [{"type": "ValidationError",
        "message": "Policy validation failed. Check the schema, registered resources and signed dependency status."}]
    assert key not in response.text and value not in response.text
    assert policies.snapshot()["generation"] == before["generation"]


async def test_price_freshness_failure_keeps_signature_and_catalog_distinct(client, authenticated, monkeypatch):
    from actiongate import cloud
    from actiongate.controls.signed import ControlError
    headers = authenticated()
    def expired(*args, **kwargs):
        raise ControlError("Controlled price expiry")
    monkeypatch.setattr(cloud, "load_price", expired)
    result = (await client.get("/api/budgets", headers=headers)).json()["pricing"]
    assert result["signature_verified"] is True and result["status"] == "invalid_or_expired"
    assert result["catalog"]["revision"]


async def test_authentication_rejects_missing_signature_wrong_issuer_audience_expiry_and_role_spoof(client, authenticated):
    headers = authenticated()
    good = headers["Authorization"].split()[1]
    assert (await client.get("/api/session", headers=headers)).status_code == 200
    assert (await client.get("/api/session")).status_code == 401
    claims = jwt.decode(good, settings()["auth_key"], algorithms=["HS256"], audience=settings()["audience"])
    for field, value in [("iss", "outside"), ("aud", "another-service"), ("exp", 1)]:
        token = jwt.encode({**claims, field: value}, settings()["auth_key"], algorithm="HS256")
        assert (await client.get("/api/session", headers={"Authorization": f"Bearer {token}"})).status_code == 401
    invalid = jwt.encode(claims, "untrusted-key-not-the-platform-key", algorithm="HS256")
    assert (await client.get("/api/session", headers={"Authorization": f"Bearer {invalid}"})).status_code == 401
    promoted = jwt.encode({**claims, "role": "admin"}, settings()["auth_key"], algorithm="HS256")
    assert (await client.get("/api/session", headers={"Authorization": f"Bearer {promoted}"})).status_code == 403


async def test_roles_cannot_activate_export_raw_or_create_unscoped_roots(client, authenticated):
    analyst, manager, approver = authenticated(), authenticated("manager"), authenticated("approver")
    for headers in (analyst, manager, approver):
        assert (await client.post("/api/policies/activate", headers=headers, json={"yaml": "invalid"})).status_code == 403
        assert (await client.post("/api/policies/compare-synthetic", headers=headers, json={"yaml": "invalid"})).status_code == 403
        assert (await client.post("/api/kill-switch", headers=headers, json={"enabled": True})).status_code == 403
    assert (await client.get("/api/exports/audit.jsonl", headers=manager)).status_code == 403
    assert (await client.get("/api/exports/management.csv", headers=manager)).status_code == 200
    assert (await client.post("/runs", headers=manager, json={"document_ids": []})).status_code == 403
    assert (await client.post("/runs", headers=approver, json={"document_ids": []})).status_code == 403
    assert (await client.post("/runs", headers=analyst, json={"document_ids": [], "tenant": "globex"})).status_code == 422


async def test_tenant_isolation_and_manager_redaction_match_exports(client, authenticated, controlled_models):
    analyst, manager = authenticated(), authenticated("manager")
    foreign = authenticated("analyst", "globex")
    run = await new_run(client, analyst)
    response = await act(client, analyst, run, "reports.save", {"content": "Supplier review contains an unresolved capacity risk."})
    assert response.status_code == 200, response.text
    operation = response.json()
    assert operation["status"] == "completed", operation
    assert (await client.get(f"/api/runs/{run['id']}", headers=foreign)).status_code == 404
    assert (await client.get(f"/actions/{operation['id']}", headers=foreign)).status_code == 404
    managed = (await client.get(f"/actions/{operation['id']}", headers=manager)).json()
    assert "arguments" not in managed and "result" not in managed
    evidence = await client.get("/api/exports/audit.jsonl", headers=analyst)
    assert evidence.status_code == 200
    events = [json.loads(line) for line in evidence.text.splitlines() if line]
    assert all(row["tenant"] == "synthetic_test_tenant" for row in events)
    assert any(row["operation_id"] == operation["id"] for row in events)
    assert "Supplier review contains an unresolved capacity risk." not in evidence.text
    assert "workload_token" not in evidence.text
    assert "nosniff" == evidence.headers["x-content-type-options"]


async def test_exact_approval_rejects_wrong_hash_role_tenant_and_replay_then_records_one_effect(client, authenticated, controlled_models):
    analyst, approver = authenticated(), authenticated("approver")
    foreign = authenticated("approver", "globex")
    run = await new_run(client, analyst, allow_publish=True)
    key = uid()
    arguments = {"content": "Internal supplier capacity review.", "recipient": "internal_demo_sink"}
    waiting = (await act(client, analyst, run, "reports.publish_demo", arguments, key)).json()
    assert waiting["status"] == "waiting_approval", waiting
    endpoint = f"/api/approvals/{waiting['id']}"
    approval = {"approved": True, "payload_hash": waiting["payload_hash"]}
    assert (await client.post(endpoint, headers=analyst, json=approval)).status_code == 403
    assert (await client.post(endpoint, headers=foreign, json=approval)).status_code == 404
    assert (await client.post(endpoint, headers=approver, json={**approval, "payload_hash": "0"*64})).status_code == 409
    changed = await act(client, analyst, run, "reports.publish_demo", {**arguments, "recipient": "public_demo_sink"}, key)
    assert changed.status_code == 409
    with transaction() as db:
        assert db.get(ConnectorReceipt, waiting["id"]) is None
    accepted = await client.post(endpoint, headers=approver, json=approval)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "completed", accepted.json()
    assert (await client.post(endpoint, headers=approver, json=approval)).status_code == 409
    repeated = await act(client, analyst, run, "reports.publish_demo", arguments, key)
    assert repeated.json()["id"] == waiting["id"]
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ConnectorReceipt).where(ConnectorReceipt.operation_id == waiting["id"])) == 1


async def test_previous_generation_approval_cannot_authorize_current_snapshot(client, authenticated, controlled_models, monkeypatch):
    from actiongate import broker, policies
    from actiongate.db import HumanApproval, ExecutionGrant, AuditEvent, Reservation
    analyst, approver = authenticated(), authenticated("approver")
    run = await new_run(client, analyst, allow_publish=True)
    waiting = (await act(client, analyst, run, "reports.publish_demo", {
        "content": "An internal supplier review.", "recipient": "internal_demo_sink"})).json()
    assert waiting["status"] == "waiting_approval"
    original = broker.execute
    observed = {}
    async def resume_with_archived_approval(*args, **kwargs):
        # Reproduce approval at G followed by a new authentic snapshot at G+1,
        # without modifying the active signed configuration for other requests.
        observed["current"] = policies.snapshot()["generation"]
        with transaction() as db:
            approval = db.get(HumanApproval, waiting["id"])
            approval.generation = observed["current"] - 1
        return await original(*args, **kwargs)
    monkeypatch.setattr(broker, "execute", resume_with_archived_approval)
    result = await client.post(f"/api/approvals/{waiting['id']}", headers=approver,
        json={"approved": True, "payload_hash": waiting["payload_hash"]})
    assert result.status_code == 200, result.text
    operation = result.json()
    assert operation["policy_generation"] == observed["current"]
    assert operation["status"] == "blocked" and "approval.invalid" in operation["rule_ids"], operation
    with transaction() as db:
        assert db.get(ConnectorReceipt, waiting["id"]) is None
        assert db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == waiting["id"])) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.operation_id == waiting["id"], AuditEvent.event == "operation.dispatched")) is None
        reservations = list(db.scalars(select(Reservation).where(Reservation.operation_id == waiting["id"])))
        assert reservations and all(row.kind == "guard.input" and row.status == "settled" for row in reservations)


@pytest.mark.parametrize("changed", ["expiry", "generation", "payload", "decision"])
@pytest.mark.parametrize("tool", ["reports.publish_demo", "reports.save"])
async def test_exact_approval_is_rechecked_after_output_reservation_before_dispatch(client, authenticated, controlled_models, monkeypatch, changed, tool):
    from datetime import timedelta
    from actiongate.db import HumanApproval, ExecutionGrant, AuditEvent, Reservation, BudgetAccount, DataObject
    from actiongate.semantic import SemanticGuard
    analyst, approver = authenticated(), authenticated("approver")
    run = await new_run(client, analyst, allow_publish=True)
    arguments = {"content": "An internal supplier review."}
    if tool == "reports.publish_demo":
        arguments["recipient"] = "internal_demo_sink"
    else:
        original_scan = SemanticGuard.scan
        async def review(self, *args, **kwargs):
            result = await original_scan(self, *args, **kwargs)
            return {**result, "verdict": "suspicious", "risk_level": 1, "category": "contract_semantic_review"}
        monkeypatch.setattr(SemanticGuard, "scan", review)
    waiting = (await act(client, analyst, run, tool, arguments)).json()
    assert waiting["status"] == "waiting_approval"
    tickets_released = []
    async def change_after_admission(self, *args, **kwargs):
        with transaction() as db:
            approval = db.get(HumanApproval, waiting["id"])
            if changed == "expiry":
                approval.expires_at = db.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
            elif changed == "generation":
                approval.generation -= 1
            elif changed == "payload":
                approval.payload_hash = "0" * 64
            else:
                approval.approved = False
        return {"ticket_id": "approval-race-ticket", "windows": 1}
    async def release(self, ticket):
        tickets_released.append(ticket)
    monkeypatch.setattr(SemanticGuard, "reserve_output", change_after_admission)
    monkeypatch.setattr(SemanticGuard, "release_output", release)
    result = await client.post(f"/api/approvals/{waiting['id']}", headers=approver,
        json={"approved": True, "payload_hash": waiting["payload_hash"]})
    assert result.status_code == 200, result.text
    operation = result.json()
    assert operation["status"] == "blocked" and operation["stage"] == "dispatch", operation
    assert "approval.invalid" in operation["rule_ids"] and operation["result"] is None
    assert tickets_released == ["approval-race-ticket"]
    with transaction() as db:
        assert db.get(ConnectorReceipt, waiting["id"]) is None
        assert db.scalar(select(DataObject).where(DataObject.tenant == "synthetic_test_tenant", DataObject.name == "report-" + waiting["id"])) is None
        assert db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == waiting["id"])) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.operation_id == waiting["id"], AuditEvent.event == "operation.dispatched")) is None
        reservations = list(db.scalars(select(Reservation).where(Reservation.operation_id == waiting["id"])))
        assert {row.kind for row in reservations} == {"guard.input", "guard.output", "execution"}
        assert all(row.status == ("settled" if row.kind == "guard.input" else "released") for row in reservations)
        root_accounts = list(db.scalars(select(BudgetAccount).where(BudgetAccount.id.like(f"root:{run['id']}:%"))))
        assert root_accounts and all(account.reserved == 0 for account in root_accounts)
        assert sum(account.spent for account in root_accounts if account.unit == "tokens") == 50


async def test_workload_token_cannot_change_root_or_use_operator_endpoints(client, authenticated, controlled_models):
    analyst = authenticated()
    first, other = await new_run(client, analyst), await new_run(client, analyst)
    workload = {"Authorization": "Bearer " + first["workload_token"]}
    assert (await client.get("/api/operations", headers=workload)).status_code == 403
    assert (await client.post("/runs", headers=workload, json={"document_ids": []})).status_code == 403
    assert (await act(client, workload, other, "reports.save", {"content": "A harmless report."})).status_code == 403


async def test_approver_can_inspect_but_cannot_execute_or_delegate_an_existing_workflow(client, authenticated, controlled_models):
    analyst, approver = authenticated(), authenticated("approver")
    run = await new_run(client, analyst, allow_publish=True)
    waiting = (await act(client, analyst, run, "reports.publish_demo", {
        "content": "Supplier review awaiting the exact authorized approval.", "recipient": "internal_demo_sink"})).json()
    assert waiting["status"] == "waiting_approval"
    inspect = await client.get(f"/actions/{waiting['id']}", headers=approver)
    assert inspect.status_code == 200
    assert inspect.json()["payload_hash"] == waiting["payload_hash"]
    calls = dict(controlled_models)
    assert (await act(client, approver, run, "calculator.evaluate", {"expression": "2*(3+4)"})).status_code == 403
    assert (await client.post("/runs", headers=approver, json={"parent_id": run["id"], "document_ids": []})).status_code == 403
    assert (await client.post("/v1/chat/completions", headers={**approver, "X-ActionGate-Run-Id": run["id"]},
        json={"messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100})).status_code == 403
    assert controlled_models == calls
    with transaction() as db:
        assert db.get(ConnectorReceipt, waiting["id"]) is None


async def test_delegated_workload_cannot_borrow_parent_or_sibling_grants_or_results(client, authenticated, controlled_models):
    analyst = authenticated()
    parent = await new_run(client, analyst)
    parent_token = {"Authorization": "Bearer " + parent["workload_token"]}
    child = await new_run(client, parent_token, parent_id=parent["id"], tools=["calculator.evaluate"])
    sibling = await new_run(client, parent_token, parent_id=parent["id"], tools=["reports.save"])
    child_token = {"Authorization": "Bearer " + child["workload_token"]}
    own = (await act(client, child_token, child, "calculator.evaluate", {"expression": "2*(3+4)"})).json()
    assert own["status"] == "completed", own
    assert (await client.get(f"/actions/{own['id']}", headers=child_token)).status_code == 200
    parent_result = (await act(client, parent_token, parent, "calculator.evaluate", {"expression": "3+4"})).json()
    assert parent_result["status"] == "completed"
    calls = dict(controlled_models)
    for target in (parent, sibling):
        response = await act(client, child_token, target, "reports.save", {"content": "Do not widen the delegated grant."})
        assert response.status_code == 403, response.text
    implicit = await client.post("/v1/chat/completions", headers=child_token,
        json={"messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100})
    assert implicit.status_code == 403
    assert "grant.tool" in implicit.json()["actiongate"]["rule_ids"]
    assert (await client.get(f"/actions/{parent_result['id']}", headers=child_token)).status_code == 404
    assert (await client.post("/runs", headers=child_token, json={"parent_id": parent["id"], "document_ids": []})).status_code == 403
    assert controlled_models == calls
    grandchild = await new_run(client, child_token, parent_id=child["id"], tools=["calculator.evaluate"])
    assert grandchild["root_id"] == parent["root_id"]


async def test_empty_tool_grants_stay_empty_for_roots_and_delegations(client, authenticated, controlled_models):
    headers = authenticated()
    root = await new_run(client, headers, tools=[])
    child = await new_run(client, headers, parent_id=root["id"], tools=[])
    for run in (root, child):
        token = {"Authorization": "Bearer " + run["workload_token"]}
        result = (await act(client, token, run, "calculator.evaluate", {"expression": "1+1"})).json()
        assert result["status"] == "blocked" and "grant.tool" in result["rule_ids"]
        assert (await client.get(f"/api/runs/{run['id']}", headers=headers)).json()["run"]["grant"]["tools"] == []
    assert controlled_models == {"business": 0, "guard": 0}


@pytest.mark.parametrize("revoked", ["parent", "root"])
async def test_revoked_workflow_cannot_recreate_authority_by_delegation(client, authenticated, revoked):
    from actiongate.db import Run
    headers = authenticated()
    root = await new_run(client, headers)
    child = await new_run(client, headers, parent_id=root["id"])
    child_token = {"Authorization": "Bearer " + child["workload_token"]}
    with transaction() as db:
        target = db.get(Run, root["id"] if revoked == "root" else child["id"])
        target.status = "revoked"
        target.grant = {**target.grant, "revoked": True}
    response = await client.post("/runs", headers=child_token, json={"parent_id": child["id"], "document_ids": []})
    assert response.status_code == 403


async def test_delegation_fanout_is_bounded_by_shared_root_configuration(client, authenticated):
    from actiongate import policies
    headers = authenticated()
    root = await new_run(client, headers)
    maximum = policies.snapshot()["configuration"]["budgets"]["max_steps"]
    for _ in range(maximum):
        await new_run(client, headers, parent_id=root["id"], tools=[])
    response = await client.post("/runs", headers=headers, json={"parent_id": root["id"], "tools": [], "document_ids": []})
    assert response.status_code == 403


@pytest.mark.parametrize("variant", ["seeded_document", "report", "acl", "purpose", "expired"])
async def test_memory_write_cannot_overwrite_a_protected_or_unrelated_object(client, authenticated, controlled_models, variant):
    from datetime import timedelta
    from actiongate.db import DataObject, now
    from actiongate.security import encrypt, decrypt
    headers = authenticated()
    run = await new_run(client, headers)
    name = "supplier-synthetic_test_tenant-1" if variant == "seeded_document" else "write-boundary-" + uid()
    with transaction() as db:
        if variant != "seeded_document":
            db.add(DataObject(tenant="synthetic_test_tenant", name=name, kind="report" if variant == "report" else "memory",
                owner="shared-tenant-fixture", purpose="another_purpose" if variant == "purpose" else "supplier_review",
                acl=["admin"] if variant == "acl" else ["admin", "analyst", "agent"], label=2,
                encrypted=encrypt("Original protected content."), expires_at=now()+timedelta(days=-1 if variant == "expired" else 1)))
        db.flush()
        obj = db.scalar(select(DataObject).where(DataObject.tenant == "synthetic_test_tenant", DataObject.name == name))
        before, object_id, version = decrypt(obj.encrypted), obj.id, obj.version
    response = await act(client, headers, run, "memory.write", {"key": name, "content": "Replacement must never be stored."})
    result = response.json()
    assert result["status"] == "blocked", result
    assert "acl.memory_write" in result["rule_ids"]
    assert controlled_models == {"business": 0, "guard": 0}
    with transaction() as db:
        obj = db.get(DataObject, object_id)
        assert decrypt(obj.encrypted) == before and obj.version == version


async def test_shared_memory_write_inherits_existing_restrictions_before_guard(client, authenticated, controlled_models):
    from datetime import timedelta
    from actiongate.db import DataObject, RunContext, now
    from actiongate.security import encrypt
    headers = authenticated()
    run = await new_run(client, headers)
    key = "shared-restricted-" + uid()
    with transaction() as db:
        db.add(DataObject(tenant="synthetic_test_tenant", name=key, kind="memory", owner="another-author",
            purpose="supplier_review", acl=["analyst"], label=3, origins=["prior-memory"], compartments=["procurement"],
            encrypted=encrypt("Protected prior note."), expires_at=now()+timedelta(days=1)))
    outcome = (await act(client, headers, run, "memory.write", {"key": key, "content": "New permitted shared note."})).json()
    assert outcome["status"] == "completed", outcome
    assert outcome["label"] == "RESTRICTED"
    with transaction() as db:
        context = db.get(RunContext, run["id"])
        assert context.label == 3 and "prior-memory" in context.origins and "procurement" in context.compartments


@pytest.mark.parametrize("boundary", ["read_acl", "read_label", "write_acl", "write_label"])
async def test_memory_authority_and_labels_are_rechecked_at_actual_effect(client, authenticated, controlled_models, monkeypatch, boundary):
    from actiongate import broker
    from actiongate.db import DataObject
    from actiongate.security import decrypt
    headers = authenticated()
    writer = await new_run(client, headers)
    key = "memory-race-" + uid()
    saved = (await act(client, headers, writer, "memory.write", {"key": key, "content": "Protected original note."})).json()
    assert saved["status"] == "completed"
    reader = await new_run(client, headers)
    original = broker.dispatch
    async def authority_changed(*args, **kwargs):
        with transaction() as db:
            obj = db.get(DataObject, saved["result"]["id"])
            if boundary.endswith("acl"):
                obj.acl = ["admin"]
            else:
                obj.label = 3
                obj.compartments = ["new-restriction"]
        return await original(*args, **kwargs)
    monkeypatch.setattr(broker, "dispatch", authority_changed)
    arguments = {"key": key}
    if boundary.startswith("write"):
        arguments["content"] = "This write must not land after authority changed."
    result = (await act(client, headers, reader, "memory.write" if boundary.startswith("write") else "memory.read", arguments)).json()
    assert result["status"] == "output_blocked", result
    assert result["result"] is None
    assert any(rule in result["rule_ids"] for rule in ("acl.memory", "acl.memory_write", "fence.memory_changed"))
    with transaction() as db:
        assert decrypt(db.get(DataObject, saved["result"]["id"]).encrypted) == "Protected original note."


@pytest.mark.parametrize("boundary", ["acl", "purpose", "expired", "label", "grant_ttl", "root_deadline"])
async def test_document_connector_rechecks_current_authority_before_decrypting(client, authenticated, controlled_models, monkeypatch, boundary):
    from datetime import timedelta
    from actiongate import broker, policies
    from actiongate.db import DataObject, Run, ExecutionGrant, now
    from actiongate.security import encrypt
    headers = authenticated()
    name = "document-race-" + uid()
    with transaction() as db:
        obj = DataObject(tenant="synthetic_test_tenant", name=name, kind="document", owner="trusted-fixture",
            purpose="supplier_review", acl=["analyst"], label=2, origins=["registry"],
            encrypted=encrypt("DOCUMENT_CONTENT_MUST_REMAIN_WITHHELD"), expires_at=now()+timedelta(days=1))
        db.add(obj)
        db.flush()
        object_id = obj.id
    run = await new_run(client, headers, document_ids=[name])
    original = broker.dispatch
    async def changed(*args, **kwargs):
        with transaction() as db:
            obj = db.get(DataObject, object_id)
            if boundary == "acl":
                obj.acl = ["admin"]
            elif boundary == "purpose":
                obj.purpose = "another_purpose"
            elif boundary == "expired":
                obj.expires_at = now()-timedelta(seconds=1)
            elif boundary == "label":
                obj.label = 3
                obj.compartments = ["new-restriction"]
            elif boundary == "grant_ttl":
                db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == args[3])).expires_at = now()-timedelta(seconds=1)
            else:
                db.get(Run, run["id"]).created_at = now()-timedelta(seconds=policies.snapshot()["configuration"]["budgets"]["run_deadline_seconds"]+1)
        return await original(*args, **kwargs)
    monkeypatch.setattr(broker, "dispatch", changed)
    response = await act(client, headers, run, "documents.read", {"document_id": name})
    outcome = response.json()
    assert outcome["status"] in ("outcome_unknown", "output_blocked"), outcome
    assert outcome["result"] is None and "DOCUMENT_CONTENT_MUST_REMAIN_WITHHELD" not in response.text
    with transaction() as db:
        assert db.get(ConnectorReceipt, outcome["id"]) is None


async def test_replay_is_read_only_and_browser_origin_and_csv_are_safe(client, authenticated, controlled_models):
    from actiongate.app import csv_safe
    admin = authenticated("admin")
    policy = (await client.get("/api/policies", headers=admin)).json()
    with transaction() as db:
        before = db.scalar(select(func.count()).select_from(ConnectorReceipt))
    response = await client.post("/api/policies/compare", headers=admin, json={"yaml": policy["yaml"]})
    assert response.status_code == 200, response.text
    assert response.json()["effects_executed"] == 0
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ConnectorReceipt)) == before
    blocked = await client.post("/runs", headers={**admin, "Origin": "https://untrusted.example"}, json={"document_ids": []})
    assert blocked.status_code == 403
    for value in ("=SUM(A1:A3)", "+1", "-1", "@command", "  =formula"):
        assert csv_safe(value).startswith("'")
    assert csv_safe("ordinary") == "ordinary"


async def test_buffered_stream_releases_only_inspected_complete_content(client, authenticated, controlled_models):
    headers = authenticated()
    run = await new_run(client, headers)
    response = await client.post("/v1/chat/completions", headers={**headers, "X-ActionGate-Run-Id": run["id"]},
        json={"messages": [{"role": "user", "content": "Review the granted supplier."}], "max_tokens": 100, "stream": True})
    assert response.status_code == 200, response.text
    assert response.headers["x-actiongate-buffering"] == "full-inspection"
    assert response.text.endswith("data: [DONE]\n\n")
    chunks = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: {")]
    assert chunks[0]["choices"][0]["delta"]["content"] == "Supplier review completed."
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.parametrize("variant", ["secret_at_end", "incomplete_tool_call", "buffer_overflow"])
async def test_unsafe_complete_model_result_emits_no_stream_prefix(client, authenticated, controlled_models, monkeypatch, variant):
    from actiongate.runtime import BusinessModel
    secret = "sk-proj-" + "b" * 60
    message = {"role": "assistant", "content": "SAFE_PREFIX_UNRELEASED " + secret}
    if variant == "incomplete_tool_call":
        message = {"role": "assistant", "content": "SAFE_PREFIX_UNRELEASED", "tool_calls": [
            {"id": "call-invalid", "type": "function", "function": {"name": "calculator.evaluate", "arguments": '{"expression":'}}]}
    elif variant == "buffer_overflow":
        message = {"role": "assistant", "content": "SAFE_PREFIX_UNRELEASED" + "x" * 70000}
    async def answer(self, *args, **kwargs):
        return {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {"total_tokens": 30}}
    monkeypatch.setattr(BusinessModel, "chat", answer)
    headers = authenticated()
    run = await new_run(client, headers)
    response = await client.post("/v1/chat/completions", headers={**headers, "X-ActionGate-Run-Id": run["id"]},
        json={"messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100, "stream": True})
    assert response.status_code == 403, response.text
    assert response.json()["actiongate"]["status"] == "output_blocked"
    assert secret not in response.text and "SAFE_PREFIX_UNRELEASED" not in response.text
    assert "text/event-stream" not in response.headers.get("content-type", "")
    assert response.json()["actiongate"]["settlement_status"] == "settled"


async def test_client_cancellation_does_not_abandon_output_inspection_or_settlement(client, authenticated, controlled_models, monkeypatch):
    from actiongate.runtime import BusinessModel
    from actiongate.db import Operation, Reservation
    began, release = asyncio.Event(), asyncio.Event()
    original = BusinessModel.chat
    async def paused(self, *args, **kwargs):
        began.set()
        await release.wait()
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(BusinessModel, "chat", paused)
    headers = authenticated()
    run = await new_run(client, headers)
    key = uid()
    request = asyncio.create_task(act(client, headers, run, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100}, key))
    await asyncio.wait_for(began.wait(), 20)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release.set()
    for _ in range(100):
        with transaction() as db:
            op = db.scalar(select(Operation).where(Operation.idempotency_key == key))
            complete = op.status == "completed"
            op_id = op.id
        if complete:
            break
        await asyncio.sleep(.05)
    assert complete
    assert controlled_models["guard"] >= 2
    with transaction() as db:
        assert all(row.status in ("settled", "released") for row in db.scalars(select(Reservation).where(Reservation.operation_id == op_id)))


async def test_stale_generation_is_denied_before_dispatch_without_mutating_live_policy(client, authenticated, controlled_models, monkeypatch):
    from actiongate import broker, policies
    headers = authenticated()
    run = await new_run(client, headers)
    live = policies.snapshot()
    stale = copy.deepcopy(live)
    stale["generation"] -= 1
    plane = policies.plane_for(live)
    verified_archive = policies.verify_stored_snapshot(live)
    original_opa = broker._opa
    async def evaluate(*args, **kwargs):
        return await original_opa(live, *args[1:], **kwargs)
    monkeypatch.setattr(policies, "snapshot", lambda *args, **kwargs: stale)
    monkeypatch.setattr(policies, "plane_for", lambda *args, **kwargs: plane)
    # Isolate the generation admission fence from the separately tested archive
    # signature boundary. The archived artifacts remain a verified real snapshot.
    monkeypatch.setattr(policies, "verify_stored_snapshot", lambda *args, **kwargs: verified_archive)
    monkeypatch.setattr(broker, "_opa", evaluate)
    response = await act(client, headers, run, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Review supplier."}], "max_tokens": 100})
    assert response.json()["status"] == "blocked", response.text
    assert "fence.changed" in response.json()["rule_ids"]
    assert controlled_models["business"] == 0
    assert controlled_models["guard"] == 0


async def test_generation_change_after_execution_quarantines_result(client, authenticated, controlled_models, monkeypatch):
    from actiongate import broker, policies
    headers = authenticated()
    run = await new_run(client, headers)
    live = policies.snapshot()
    original = broker.dispatch
    async def dispatched(*args, **kwargs):
        result = await original(*args, **kwargs)
        changed = {**live, "generation": live["generation"] + 1}
        monkeypatch.setattr(policies, "snapshot", lambda *args, **kwargs: changed)
        return result
    monkeypatch.setattr(broker, "dispatch", dispatched)
    response = await act(client, headers, run, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Review supplier."}], "max_tokens": 100})
    result = response.json()
    assert result["status"] == "output_blocked", result
    assert "fence.output_generation" in result["rule_ids"]
    assert result["result"] is None and result["settlement_status"] == "settled"


async def test_previous_owner_cannot_release_after_root_fence_changes(client, authenticated, controlled_models, monkeypatch):
    from actiongate.runtime import BusinessModel
    from actiongate.db import RunContext
    headers = authenticated()
    run = await new_run(client, headers)
    original = BusinessModel.chat
    async def lost_owner(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        with transaction() as db:
            db.get(RunContext, run["id"]).fence += 1
        return result
    monkeypatch.setattr(BusinessModel, "chat", lost_owner)
    response = await act(client, headers, run, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Review supplier."}], "max_tokens": 100})
    result = response.json()
    assert result["status"] in ("output_blocked", "outcome_unknown"), result
    assert result.get("result") is None


async def test_public_projection_has_an_independent_exactly_once_budget(actor, controlled_models):
    from actiongate import broker, policies, ledger
    from actiongate.app import public_projection
    from actiongate.contracts import RunRequest
    from actiongate.db import BudgetAccount
    from fastapi import HTTPException
    document = "supplier-synthetic_test_tenant-1"
    run = broker.create_run(RunRequest(document_ids=[document], public_document_ids=[document]), actor)
    reservation = ledger.reserve(run["id"], uid(), "execution", policies.snapshot()["configuration"], tokens=25)
    ledger.settle(reservation, {"tokens": 25, "usd_micros": 0, "slot_millis": 0})
    with transaction() as db:
        before = {row.id: (row.spent, row.reserved) for row in db.scalars(select(BudgetAccount).where(
            BudgetAccount.id.like(f"root:{run['id']}:%")))}
        assert before
    result = await public_projection(run["id"], actor)
    assert result["label"] == "PUBLIC" and result["records"][0]["document_id"] == document
    with transaction() as db:
        projection = db.get(BudgetAccount, f"public:{run['id']}:operations")
        assert (projection.scope, projection.unit, projection.limit, projection.spent, projection.reserved) == (
            "public_projection", "operations", 1, 1, 0)
        after = {row.id: (row.spent, row.reserved) for row in db.scalars(select(BudgetAccount).where(
            BudgetAccount.id.like(f"root:{run['id']}:%")))}
        assert after == before
    with pytest.raises(HTTPException) as denied:
        await public_projection(run["id"], actor)
    assert denied.value.status_code == 403
    assert controlled_models == {"guard": 0, "business": 0}


async def test_delegated_action_and_new_child_use_original_root_deadline(client, authenticated, controlled_models):
    from datetime import timedelta
    from actiongate import policies
    from actiongate.db import Run, now
    headers = authenticated()
    root = await new_run(client, headers)
    child = await new_run(client, headers, parent_id=root["id"])
    with transaction() as db:
        db.get(Run, root["id"]).created_at = now() - timedelta(seconds=policies.snapshot()["configuration"]["budgets"]["run_deadline_seconds"] + 1)
    result = (await act(client, headers, child, "calculator.evaluate", {"expression": "2+2"})).json()
    assert result["status"] == "blocked" and "budget.deadline" in result["rule_ids"], result
    assert (await client.post("/runs", headers=headers, json={"document_ids": [], "parent_id": child["id"]})).status_code == 403
    assert controlled_models == {"guard": 0, "business": 0}


async def test_public_projection_cannot_outlive_its_root_deadline(actor):
    from datetime import timedelta
    from actiongate import broker, policies
    from actiongate.app import public_projection
    from actiongate.contracts import RunRequest
    from actiongate.db import Run, BudgetAccount, now
    from fastapi import HTTPException
    run = broker.create_run(RunRequest(document_ids=[], public_document_ids=["supplier-synthetic_test_tenant-1"]), actor)
    with transaction() as db:
        db.get(Run, run["id"]).created_at = now() - timedelta(seconds=policies.snapshot()["configuration"]["budgets"]["run_deadline_seconds"] + 1)
    with pytest.raises(HTTPException) as denied:
        await public_projection(run["id"], actor)
    assert denied.value.status_code == 403
    with transaction() as db:
        assert db.get(BudgetAccount, f"public:{run['id']}:operations").spent == 0


async def test_deadline_expiring_after_inference_withholds_output_but_settles(client, authenticated, controlled_models, monkeypatch):
    from datetime import timedelta
    from actiongate import policies
    from actiongate.runtime import BusinessModel
    from actiongate.db import Run, now
    headers = authenticated()
    root = await new_run(client, headers)
    original = BusinessModel.chat
    async def expire(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        with transaction() as db:
            db.get(Run, root["id"]).created_at = now() - timedelta(seconds=policies.snapshot()["configuration"]["budgets"]["run_deadline_seconds"] + 1)
        return result
    monkeypatch.setattr(BusinessModel, "chat", expire)
    result = (await act(client, headers, root, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Summarize supplier performance."}], "max_tokens": 100})).json()
    assert result["status"] == "output_blocked" and "budget.deadline" in result["rule_ids"], result
    assert result["result"] is None and result["settlement_status"] == "settled"


async def test_root_deadline_is_rechecked_between_guard_and_dispatch(client, authenticated, controlled_models, monkeypatch):
    from datetime import timedelta
    from actiongate import broker, policies
    from actiongate.db import Run, now, ExecutionGrant
    headers = authenticated()
    root = await new_run(client, headers)
    original = broker._opa
    async def expire_after_input(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs.get("stage", "input") == "input":
            with transaction() as db:
                db.get(Run, root["id"]).created_at = now() - timedelta(seconds=policies.snapshot()["configuration"]["budgets"]["run_deadline_seconds"] + 1)
        return result
    monkeypatch.setattr(broker, "_opa", expire_after_input)
    result = (await act(client, headers, root, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Summarize supplier performance."}], "max_tokens": 100})).json()
    assert result["status"] == "blocked" and "budget.deadline" in result["rule_ids"], result
    assert controlled_models["business"] == 0 and result["result"] is None
    with transaction() as db:
        assert db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == result["id"])) is None


async def test_dashboard_tail_snapshot_and_reconnect_preserve_event_cursor(actor):
    import time
    from starlette.requests import Request
    from actiongate.app import events
    from actiongate import broker
    from actiongate.contracts import RunRequest
    from actiongate.db import AuditEvent, Outbox
    async def connected():
        return {"type": "http.request", "body": b"", "more_body": False}
    def request(cursor=None):
        return Request({"type": "http", "method": "GET", "path": "/api/events", "query_string": b"tail=true",
            "headers": [] if cursor is None else [(b"last-event-id", str(cursor).encode())]}, connected)
    claims = {**actor, "exp": time.time()+60}
    before = broker.create_run(RunRequest(document_ids=[]), actor)
    initial = (await events(request(), claims)).body_iterator
    try:
        ready = await anext(initial)
        assert "event: ready" in ready
        cursor = int(ready.splitlines()[0].split(": ")[1])
        assert cursor > 0
        after = broker.create_run(RunRequest(document_ids=[]), actor)
        with transaction() as db:
            outbox = db.scalars(select(Outbox).where(Outbox.tenant == actor["tenant"], Outbox.id > cursor).order_by(Outbox.id)).first()
            minimum_age = (db.scalar(select(func.clock_timestamp()))-outbox.created_at).total_seconds()*1000
            row_id, original_payload = outbox.id, copy.deepcopy(outbox.payload)
            assert original_payload["_outbox_clock"] == "database"
            audit_event = db.get(AuditEvent, outbox.event_id)
            original_audit = (copy.deepcopy(audit_event.evidence), audit_event.hash, audit_event.previous_hash)
            audit_id = audit_event.id
        delivered = await anext(initial)
        assert "event: audit" in delivered and after["id"] in delivered and before["id"] not in delivered
        payload = json.loads(next(line[6:] for line in delivered.splitlines() if line.startswith("data: ")))
        with transaction() as db:
            maximum_age = (db.scalar(select(func.clock_timestamp()))-db.get(Outbox, row_id).created_at).total_seconds()*1000
        assert max(0, minimum_age-.001) <= payload["_delivery"]["server_backlog_age_ms"] <= maximum_age+.001
        assert payload["_delivery"]["clock"] == "database"
        assert {k: v for k, v in payload.items() if k != "_delivery"} == original_payload
    finally:
        await initial.aclose()
    resumed = (await events(request(cursor), claims)).body_iterator
    try:
        replayed = await anext(resumed)
        assert "event: audit" in replayed and "event: ready" not in replayed
        assert after["id"] in replayed and before["id"] not in replayed
        assert int(replayed.splitlines()[0].split(": ")[1]) > cursor
        again = json.loads(next(line[6:] for line in replayed.splitlines() if line.startswith("data: ")))
        assert again["_delivery"]["server_backlog_age_ms"] >= payload["_delivery"]["server_backlog_age_ms"]
        with transaction() as db:
            assert db.get(Outbox, row_id).payload == original_payload
            persisted = db.get(AuditEvent, audit_id)
            assert (persisted.evidence, persisted.hash, persisted.previous_hash) == original_audit
    finally:
        await resumed.aclose()


async def test_dashboard_legacy_replay_never_claims_a_shared_clock(actor, monkeypatch):
    import time
    from starlette.requests import Request
    from actiongate import broker, security
    from actiongate.app import events
    from actiongate.contracts import RunRequest
    from actiongate.db import Outbox, AuditEvent
    def legacy_outbox(**kwargs):
        kwargs["payload"] = {k: v for k, v in kwargs["payload"].items() if k != "_outbox_clock"}
        kwargs.pop("created_at")
        return Outbox(**kwargs)
    # A real newly persisted event with the earlier writer contract, not an
    # UPDATE of immutable outbox/audit rows or an invented SSE response.
    with monkeypatch.context() as scoped:
        scoped.setattr(security, "Outbox", legacy_outbox)
        run = broker.create_run(RunRequest(document_ids=[]), actor)
    with transaction() as db:
        outbox = db.scalar(select(Outbox).join(AuditEvent, AuditEvent.id == Outbox.event_id).where(AuditEvent.run_id == run["id"]))
        row_id, original_payload = outbox.id, copy.deepcopy(outbox.payload)
        assert "_outbox_clock" not in original_payload
    async def connected():
        return {"type": "http.request", "body": b"", "more_body": False}
    request = Request({"type": "http", "method": "GET", "path": "/api/events", "query_string": b"tail=true",
        "headers": [(b"last-event-id", str(row_id-1).encode())]}, connected)
    stream = (await events(request, {**actor, "exp": time.time()+60})).body_iterator
    try:
        delivered = await anext(stream)
        payload = json.loads(next(line[6:] for line in delivered.splitlines() if line.startswith("data: ")))
        assert payload["_delivery"]["server_backlog_age_ms"] is None
        assert payload["_delivery"]["clock"] == "unverified"
        assert {k: v for k, v in payload.items() if k != "_delivery"} == original_payload
        with transaction() as db:
            assert db.get(Outbox, row_id).payload == original_payload
    finally:
        await stream.aclose()


@pytest.mark.parametrize("choice", ["auto", "none"])
async def test_chat_tool_choice_preserves_intent_and_controls_actual_upstream_and_output(client, authenticated, controlled_models, monkeypatch, choice):
    from actiongate import policies
    from actiongate.runtime import BusinessModel
    from actiongate.semantic import SemanticGuard
    headers = authenticated()
    run = await new_run(client, headers)
    registered = policies.plane_for(policies.snapshot()).tools["calculator.evaluate"]
    schemas = [{"type": "function", "function": {"name": registered.name,
        "description": registered.description, "parameters": registered.input_schema}}]
    calls, scans = [], []
    original_scan = SemanticGuard.scan
    async def inspected(self, text, *args, **kwargs):
        scans.append(text)
        return await original_scan(self, text, *args, **kwargs)
    async def answer(self, messages, **kwargs):
        calls.append(kwargs)
        return {"id": "tool-choice-fixture", "model": "local-business", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "TOOL_CHOICE_WITHHELD_PREFIX", "tool_calls": [
                {"id": "call-calculation", "type": "function", "function": {"name": "calculator.evaluate", "arguments": '{"expression":"2+2"}'}}]},
            "finish_reason": "tool_calls"}], "usage": {"total_tokens": 30, "inference_slot_seconds": .001}}
    monkeypatch.setattr(SemanticGuard, "scan", inspected)
    monkeypatch.setattr(BusinessModel, "chat", answer)
    response = await client.post("/v1/chat/completions", headers={**headers, "X-ActionGate-Run-Id": run["id"]},
        json={"messages": [{"role": "user", "content": "Calculate two plus two."}], "tools": schemas,
              "tool_choice": choice, "temperature": 0, "max_tokens": 100, "stream": choice == "none"})
    assert len(calls) == 1 and calls[0]["tools"] == (schemas if choice == "auto" else None)
    inspected_request = json.loads(scans[0])
    assert inspected_request["tool_choice"] == choice and inspected_request["tools"] == schemas
    body = response.json()
    if choice == "none":
        assert response.status_code == 403 and body["actiongate"]["status"] == "output_blocked", body
        assert "schema.tool_choice" in body["actiongate"]["rule_ids"]
        assert body["actiongate"]["result"] is None and body["actiongate"]["settlement_status"] == "settled"
        assert "TOOL_CHOICE_WITHHELD_PREFIX" not in response.text
        operation_id = body["actiongate"]["id"]
    else:
        assert response.status_code == 200, body
        assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "calculator.evaluate"
        operation_id = body["actiongate"]["operation_id"]
    evidence = (await client.get("/actions/"+operation_id, headers=headers)).json()
    assert evidence["arguments"]["tool_choice"] == choice and evidence["arguments"]["tools"] == schemas


@pytest.mark.parametrize("temperature", [.1, 1, -1, False, "0"])
async def test_chat_rejects_unavailable_generation_temperature_before_models(client, authenticated, controlled_models, temperature):
    headers = authenticated()
    run = await new_run(client, headers)
    response = await client.post("/v1/chat/completions", headers={**headers, "X-ActionGate-Run-Id": run["id"]},
        json={"messages": [{"role": "user", "content": "Review supplier performance."}], "temperature": temperature})
    assert response.status_code == 422, response.text
    assert controlled_models == {"guard": 0, "business": 0}
