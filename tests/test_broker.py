"""Real PostgreSQL/OPA/connector effects with explicitly controlled model results."""
import asyncio
import base64
import copy
from datetime import timedelta
import json
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from actiongate import broker, policies
from actiongate.contracts import ActionRequest, RunRequest, ApprovalRequest
from actiongate.db import (DataObject, Operation, RunContext, ConnectorReceipt, HumanApproval, State,
                          Principal, Reservation, ExecutionGrant, AuditEvent, transaction, uid, now)
from actiongate.security import decrypt, encrypt

pytestmark = pytest.mark.integration


async def invoke(run, actor, tool, arguments, key=None):
    return await broker.execute(ActionRequest(run_id=run["id"], tool=tool, arguments=arguments,
                                              idempotency_key=key or uid()), actor)


def count_receipt(op_id):
    with transaction() as db:
        return db.scalar(select(func.count()).select_from(ConnectorReceipt).where(ConnectorReceipt.operation_id == op_id))


async def test_legal_read_memory_and_report(workflow_run, actor, controlled_models):
    read = await invoke(workflow_run, actor, "documents.read", {"document_id": "supplier-synthetic_test_tenant-1"})
    assert read["status"] == "completed", read
    assert count_receipt(read["id"]) == 1
    assert read["label"] == "CONFIDENTIAL"
    key = "contract-"+uid()
    saved = await invoke(workflow_run, actor, "memory.write", {"key": key, "content": "Delivery review passed."})
    assert saved["status"] == "completed", saved
    note = await invoke(workflow_run, actor, "memory.read", {"key": key})
    assert note["result"]["content"] == "Delivery review passed."
    assert note["label"] == "CONFIDENTIAL"
    report = await invoke(workflow_run, actor, "reports.save", {"content": "Supplier has stable delivery performance."})
    assert report["result"]["saved"] is True
    assert controlled_models["guard"] == 8


async def test_cross_tenant_does_not_reach_connector_or_guard(workflow_run, actor, controlled_models):
    result = await invoke(workflow_run, actor, "documents.read", {"document_id": "supplier-globex-1"})
    assert result["status"] == "blocked"
    assert count_receipt(result["id"]) == 0
    assert controlled_models["guard"] == 0


async def test_secret_rejection_never_persists_plaintext(workflow_run, actor, controlled_models):
    secret = "sk-proj-"+"a"*60
    result = await invoke(workflow_run, actor, "reports.save", {"content": secret})
    assert result["status"] == "blocked"
    assert result["arguments"] is None
    assert controlled_models["guard"] == 0
    with transaction() as db:
        op = db.get(Operation, result["id"])
        assert op.encrypted_payload is None
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.operation_id == op.id)))
        assert secret not in json.dumps([e.evidence for e in events])


async def test_pii_redacts_actual_stored_report(workflow_run, actor, controlled_models):
    result = await invoke(workflow_run, actor, "reports.save", {"content": "Contact analyst@example.org for the review."})
    assert result["status"] == "completed", result
    assert result["decision"] == "redact"
    with transaction() as db:
        obj = db.get(DataObject, result["result"]["id"])
        assert "analyst@example.org" not in decrypt(obj.encrypted)
        assert "[REDACTED:EMAIL]" in decrypt(obj.encrypted)
        assert obj.label == 2


@pytest.mark.parametrize("tool", ["reports.save", "memory.write"])
async def test_generated_storage_uuid_is_not_redacted_but_uuid_shaped_content_is(workflow_run, actor, controlled_models, monkeypatch, tool):
    from sqlalchemy import event
    from actiongate.semantic import SemanticGuard
    # This real generated reference previously matched Presidio PHONE [0, 8).
    object_id = "02529806-ab2a-4463-9e20-" + uid().replace("-", "")[-12:]
    content = "Call the contact in record 02529806-ab2a-4463-9e20-73e7347b0207."
    seen = []
    original = SemanticGuard.scan
    async def capture(self, text, *args, **kwargs):
        seen.append(text)
        return await original(self, text, *args, **kwargs)
    monkeypatch.setattr(SemanticGuard, "scan", capture)
    def force_reference(mapper, connection, target):
        target.id = object_id
    event.listen(DataObject, "before_insert", force_reference)
    try:
        arguments = {"content": content}
        if tool == "memory.write":
            arguments["key"] = "reference-regression-" + uid().replace("-", "")
        result = await invoke(workflow_run, actor, tool, arguments)
    finally:
        event.remove(DataObject, "before_insert", force_reference)
    assert result["status"] == "completed", result
    assert result["decision"] == "redact"
    assert result["result"]["id"] == object_id
    assert any(object_id in text for text in seen), "The complete receipt must still reach semantic inspection"
    with transaction() as db:
        stored = db.get(DataObject, object_id)
        assert stored and stored.tenant == actor["tenant"]
        assert "02529806" not in decrypt(stored.encrypted)
        assert "[REDACTED:PHONE]" in decrypt(stored.encrypted)
    assert result["settlement_status"] == "settled"


async def test_uuid_shaped_caller_memory_key_remains_content(workflow_run, actor, controlled_models):
    result = await invoke(workflow_run, actor, "memory.write", {
        "key": "02529806-ab2a-4463-9e20-73e7347b0207", "content": "Legal report text."})
    assert result["status"] == "blocked", result
    assert "pii.phone.presidio" in result["rule_ids"]
    assert controlled_models["guard"] == 0
    with transaction() as db:
        assert db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == result["id"])) is None


async def test_plain_model_json_cannot_claim_generated_identifier_provenance(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.runtime import BusinessModel
    async def forged(self, *args, **kwargs):
        return {"id": "02529806-ab2a-4463-9e20-73e7347b0207", "generated_identifiers": {"id": "02529806"},
                "choices": [{"message": {"content": "Supplier review completed."}}], "usage": {"total_tokens": 30}}
    monkeypatch.setattr(BusinessModel, "chat", forged)
    result = await invoke(workflow_run, actor, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Review delivery performance."}], "max_tokens": 100})
    assert result["status"] == "output_blocked", result
    assert "dlp.output_structure" in result["rule_ids"] and result["result"] is None


@pytest.mark.parametrize("change", ["identifier", "extra_content"])
async def test_local_receipt_provenance_does_not_hide_modified_or_extra_content(workflow_run, actor, controlled_models, monkeypatch, change):
    original = broker.dispatch
    secret = "AG_TEST_SECRET_ReceiptBoundaryRegression"
    async def modified(*args, **kwargs):
        receipt, usage = await original(*args, **kwargs)
        receipt["id" if change == "identifier" else "extra"] = secret
        return receipt, usage
    monkeypatch.setattr(broker, "dispatch", modified)
    result = await invoke(workflow_run, actor, "reports.save", {"content": "Synthetic storage boundary review."})
    assert result["status"] == "output_blocked", result
    assert result["result"] is None and secret not in json.dumps(result)
    assert ("schema.local_receipt" if change == "identifier" else "secret.synthetic") in result["rule_ids"]
    assert result["settlement_status"] == "settled"


async def test_exact_approval_one_effect_and_replay_conflict(workflow_run, actor, controlled_models):
    args = {"content": "Supplier delivery review is approved internally.", "recipient": "internal_demo_sink"}
    key = uid()
    waiting = await invoke(workflow_run, actor, "reports.publish_demo", args, key)
    assert waiting["status"] == "waiting_approval", waiting
    assert count_receipt(waiting["id"]) == 0
    with transaction() as db:
        db.add(HumanApproval(operation_id=waiting["id"], tenant=actor["tenant"], approver="test-approver",
            payload_hash=waiting["payload_hash"], approved=True, generation=waiting["policy_generation"], expires_at=now()+timedelta(minutes=5)))
    req = ActionRequest(run_id=workflow_run["id"], tool="reports.publish_demo", arguments=args, idempotency_key=key)
    done = await broker.execute(req, actor, existing_id=waiting["id"])
    assert done["status"] == "completed", done
    replay = await broker.execute(req, actor)
    assert replay["id"] == done["id"] and count_receipt(done["id"]) == 1
    with transaction() as db:
        assert db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == done["id"])).status == "consumed"
    with pytest.raises(HTTPException) as err:
        await invoke(workflow_run, actor, "reports.publish_demo", {**args, "recipient": "public_demo_sink"}, key)
    assert err.value.status_code == 409
    assert count_receipt(done["id"]) == 1


@pytest.mark.parametrize("mismatch", [False, True])
async def test_controlled_publisher_reference_requires_exact_operation_binding(workflow_run, actor, controlled_models, monkeypatch, mismatch):
    operation_id = "02529806-ab2a-4463-9e20-" + uid().replace("-", "")[-12:]
    args = {"content": "Approved delivery report.", "recipient": "internal_demo_sink"}
    key = uid()
    with monkeypatch.context() as creation:
        creation.setattr(broker, "uid", lambda: operation_id)
        waiting = await invoke(workflow_run, actor, "reports.publish_demo", args, key)
    assert waiting["status"] == "waiting_approval", waiting
    with transaction() as db:
        db.add(HumanApproval(operation_id=operation_id, tenant=actor["tenant"], approver="test-approver",
            payload_hash=waiting["payload_hash"], approved=True, generation=waiting["policy_generation"],
            expires_at=now()+timedelta(minutes=5)))
    if mismatch:
        original = broker.dispatch
        async def wrong_reference(*args, **kwargs):
            receipt, usage = await original(*args, **kwargs)
            return {**receipt, "receipt_id": uid()}, usage
        monkeypatch.setattr(broker, "dispatch", wrong_reference)
    result = await broker.execute(ActionRequest(run_id=workflow_run["id"], tool="reports.publish_demo",
        arguments=args, idempotency_key=key), actor, existing_id=operation_id)
    assert count_receipt(operation_id) == 1
    assert result["settlement_status"] == "settled"
    if mismatch:
        assert result["status"] == "output_blocked" and "schema.connector_receipt" in result["rule_ids"], result
        assert result["result"] is None
    else:
        assert result["status"] == "completed", result
        assert result["result"]["receipt_id"] == operation_id
        assert result["result"]["content"] == args["content"]


async def test_encoded_memory_delegation_cannot_export_with_false_benign(workflow_run, actor, controlled_models):
    key = "encoded-"+uid()
    encoded = base64.b64encode(b"Confidential pricing is known to this context").decode()
    saved = await invoke(workflow_run, actor, "memory.write", {"key": key, "content": encoded})
    assert saved["status"] == "completed", saved
    child = broker.create_run(RunRequest(parent_id=workflow_run["id"], document_ids=[], allow_publish=True), actor)
    read = await invoke(child, actor, "memory.read", {"key": key})
    assert read["status"] == "completed" and read["label"] == "CONFIDENTIAL", read
    blocked = await invoke(child, actor, "reports.publish_demo", {"content": encoded, "recipient": "public_demo_sink"})
    assert blocked["status"] == "blocked"
    assert count_receipt(blocked["id"]) == 0
    with transaction() as db:
        ctx = db.get(RunContext, workflow_run["id"])
        assert ctx.label == 2 and ctx.label_version > 1


async def test_workload_root_binding_and_narrowing(workflow_run, actor, controlled_models):
    other = broker.create_run(RunRequest(document_ids=[]), actor)
    workload = {"sub": "workload:"+workflow_run["id"], "role": "agent", "tenant": actor["tenant"], "root_run_id": workflow_run["id"]}
    with pytest.raises(HTTPException):
        await invoke(other, workload, "calculator.evaluate", {"expression": "1+1"})
    with pytest.raises(HTTPException):
        broker.create_run(RunRequest(document_ids=[]), workload)
    restricted = broker.create_run(RunRequest(document_ids=[], tools=["memory.read"]), actor)
    with pytest.raises(HTTPException):
        broker.create_run(RunRequest(parent_id=restricted["id"], document_ids=[], tools=["reports.save"]), actor)


async def test_unknown_guard_fails_closed(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.semantic import SemanticGuard
    async def unknown(self, *args, **kwargs):
        return {"verdict": "unknown", "risk_level": 3, "complete": False, "usage": {"usage_unknown": True}}
    monkeypatch.setattr(SemanticGuard, "scan", unknown)
    result = await invoke(workflow_run, actor, "documents.read", {"document_id": "supplier-synthetic_test_tenant-1"})
    assert result["status"] == "blocked", result
    assert count_receipt(result["id"]) == 0
    with transaction() as db:
        reservations = list(db.scalars(select(Reservation).where(Reservation.operation_id == result["id"])))
        assert any(r.status == "usage_unknown" for r in reservations)


@pytest.mark.parametrize("stage", ["input", "output"])
async def test_all_semantic_denial_rules_survive_operation_history(workflow_run, actor, controlled_models, monkeypatch, stage):
    original = broker._opa
    async def policy(*args, **kwargs):
        if kwargs.get("stage", "input") == stage:
            return {"decision": "block", "rule_ids": ["semantic.review", "semantic.risk"]}
        return await original(*args, **kwargs)
    monkeypatch.setattr(broker, "_opa", policy)
    result = await invoke(workflow_run, actor, "reports.save", {"content": "Synthetic semantic policy regression."})
    assert result["status"] == ("blocked" if stage == "input" else "output_blocked"), result
    assert {"semantic.review", "semantic.risk"} <= set(result["rule_ids"])
    with transaction() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.operation_id == result["id"],
            AuditEvent.event == "operation." + result["status"]))
        assert {"semantic.review", "semantic.risk"} <= set(event.evidence["rule_ids"])


async def test_last_output_secret_is_never_returned(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.runtime import BusinessModel
    secret = "sk-proj-"+"b"*60
    async def dangerous(self, *args, **kwargs):
        return {"choices": [{"message": {"content": "A safe prefix followed by "+secret}}], "usage": {"total_tokens": 30}}
    monkeypatch.setattr(BusinessModel, "chat", dangerous)
    result = await invoke(workflow_run, actor, "models.chat", {"model": "local-business", "messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100})
    assert result["status"] == "output_blocked", result
    assert result["result"] is None
    assert secret not in json.dumps(result)
    assert result["settlement_status"] == "settled"


async def test_output_guard_capacity_is_reserved_before_business(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.semantic import SemanticGuard
    async def full(self, *args, **kwargs):
        raise RuntimeError("Guard queue full")
    monkeypatch.setattr(SemanticGuard, "reserve_output", full)
    result = await invoke(workflow_run, actor, "models.chat", {"model": "local-business", "messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100})
    assert result["status"] == "failed"
    assert controlled_models["business"] == 0
    with transaction() as db:
        assert all(r.status in ("settled", "released") for r in db.scalars(select(Reservation).where(Reservation.operation_id == result["id"])))


async def test_read_boundary_rejects_concurrent_publication(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.semantic import SemanticGuard
    original = SemanticGuard.scan
    reached = asyncio.Event()
    release = asyncio.Event()
    async def paused(self, *args, **kwargs):
        reached.set()
        await release.wait()
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(SemanticGuard, "scan", paused)
    task = asyncio.create_task(invoke(workflow_run, actor, "documents.read", {"document_id": "supplier-synthetic_test_tenant-1"}))
    await asyncio.wait_for(reached.wait(), 10)
    try:
        with pytest.raises(HTTPException) as error:
            await invoke(workflow_run, actor, "reports.publish_demo", {"content": "Attempted concurrent report", "recipient": "public_demo_sink"})
        assert error.value.status_code == 409
    finally:
        release.set()
    assert (await task)["status"] == "completed"


async def test_database_phase_keeps_event_loop_responsive_and_cancelled_client_keeps_root_lease(workflow_run, actor, controlled_models, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    worker_threads = []
    original = broker._preflight
    def paused(db, *args, **kwargs):
        # This hook runs inside the real State-locked PostgreSQL transaction.
        worker_threads.append(threading.get_ident())
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Test did not release the transaction")
        return original(db, *args, **kwargs)
    monkeypatch.setattr(broker, "_preflight", paused)
    prior = set(broker._executions)
    key = uid()
    caller = asyncio.create_task(invoke(workflow_run, actor, "reports.save", {
        "content": "The transaction can wait without freezing other requests."}, key))
    owned = set()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert worker_threads == [worker_threads[0]] and worker_threads[0] != loop_thread
        # Reaching here before the worker is released proves event-loop progress.
        assert not release.is_set() and not caller.done()
        owned = set(broker._executions) - prior
        assert len(owned) == 1
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        with pytest.raises(HTTPException) as conflict:
            await invoke(workflow_run, actor, "calculator.evaluate", {"expression": "2+2"})
        assert conflict.value.status_code == 409
        assert all(not task.done() for task in owned)
    finally:
        release.set()
        if owned:
            results = await asyncio.gather(*owned)
        else:
            await asyncio.gather(caller, return_exceptions=True)
    assert results[0]["status"] == "completed", results
    with transaction() as db:
        operation = db.scalar(select(Operation).where(Operation.idempotency_key == key))
        assert operation.status == "completed"
        assert all(row.status in ("settled", "released") for row in db.scalars(
            select(Reservation).where(Reservation.operation_id == operation.id)))


async def test_repeated_internal_cancellation_drains_real_database_phase_before_lease_release(workflow_run, actor, controlled_models, monkeypatch):
    import threading
    from contextlib import contextmanager
    entered, release, transaction_closed = threading.Event(), threading.Event(), threading.Event()
    original_transaction, original_preflight = broker.transaction, broker._preflight
    phase_thread = []
    @contextmanager
    def tracked_transaction():
        try:
            with original_transaction() as db:
                yield db
        finally:
            if phase_thread and threading.get_ident() == phase_thread[0]:
                transaction_closed.set()
    def paused(db, *args, **kwargs):
        phase_thread.append(threading.get_ident())
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Test did not release the transaction")
        return original_preflight(db, *args, **kwargs)
    monkeypatch.setattr(broker, "transaction", tracked_transaction)
    monkeypatch.setattr(broker, "_preflight", paused)
    task = asyncio.create_task(broker._execute_owned(ActionRequest(run_id=workflow_run["id"],
        tool="reports.save", arguments={"content": "Cancelled before admission."}, idempotency_key=uid()), actor))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done() and not transaction_closed.is_set()
        # The advisory lease remains owned even after the second cancellation.
        with pytest.raises(HTTPException) as conflict:
            await invoke(workflow_run, actor, "calculator.evaluate", {"expression": "3+3"})
        assert conflict.value.status_code == 409
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert transaction_closed.is_set()
    monkeypatch.setattr(broker, "_preflight", original_preflight)
    retry = await invoke(workflow_run, actor, "calculator.evaluate", {"expression": "3+3"})
    assert retry["status"] == "completed", retry


async def test_revocation_between_screening_and_dispatch(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.semantic import SemanticGuard
    original = SemanticGuard.scan
    async def revoke(self, *args, **kwargs):
        with transaction() as db:
            db.get(State, 1).revocation_epoch += 1
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(SemanticGuard, "scan", revoke)
    result = await invoke(workflow_run, actor, "documents.read", {"document_id": "supplier-synthetic_test_tenant-1"})
    assert result["status"] == "blocked" and "fence.changed" in result["rule_ids"], result
    assert count_receipt(result["id"]) == 0


async def test_changed_tool_description_is_not_trusted(workflow_run, actor, controlled_models):
    plane = policies.plane_for(policies.snapshot())
    tool = plane.tools["documents.read"]
    result = await invoke(workflow_run, actor, "models.chat", {"model": "local-business", "messages": [{"role": "user", "content": "Review supplier"}],
        "tools": [{"type": "function", "function": {"name": tool.name, "description": "Changed descriptor", "parameters": tool.input_schema}}]})
    assert result["status"] == "blocked" and "registry.tool_definition" in result["rule_ids"]
    assert controlled_models["business"] == 0


async def test_classifier_code_mismatch_blocks_before_inference(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate import semantic
    changed = {**semantic.guard_artifact(), "sha256": "f" * 64}
    monkeypatch.setattr(semantic, "guard_artifact", lambda: changed)
    result = await invoke(workflow_run, actor, "reports.save", {"content": "Ordinary supplier review"})
    assert result["status"] == "blocked" and "semantic.version" in result["rule_ids"], result
    assert controlled_models["guard"] == 0 and controlled_models["business"] == 0
    assert count_receipt(result["id"]) == 0


async def test_worker_contract_failure_preserves_known_usage(workflow_run, actor, controlled_models, monkeypatch):
    from actiongate.runtime import BusinessModel, RuntimeFailure
    async def mismatched_model(self, *args, **kwargs):
        raise RuntimeFailure("Worker returned a different model", {"total_tokens": 37, "inference_slot_seconds": .012, "usage_unknown": False})
    monkeypatch.setattr(BusinessModel, "chat", mismatched_model)
    result = await invoke(workflow_run, actor, "models.chat", {"model": "local-business",
        "messages": [{"role": "user", "content": "Review the supplier."}], "max_tokens": 100})
    assert result["result"] is None and result["status"] == "outcome_unknown"
    assert result["settlement_status"] == "settled"
    with transaction() as db:
        reservation = db.scalar(select(Reservation).where(Reservation.operation_id == result["id"], Reservation.kind == "execution"))
        assert reservation.status == "settled" and reservation.usage["total_tokens"] == 37


@pytest.fixture
def controlled_cloud_policy(actor, controlled_models, monkeypatch):
    """Explicit policy/provider fixture; real PostgreSQL, ledger, OPA and fences.

    No publication or external inference. Signed-policy validation is exercised
    separately by policy contracts; this fixture varies local admission limits.
    """
    original = policies.snapshot()
    snap = copy.deepcopy(original)
    snap["configuration"]["models"].update(cloud_enabled=True, allowed=["local-business", "cloud-business"])
    snap["configuration"]["budgets"].update(run_total_tokens=1_000_000, max_retries=1)
    verified = policies.verify_stored_snapshot(original)
    plane = policies.plane_for(original)
    monkeypatch.setattr(policies, "snapshot", lambda **kwargs: copy.deepcopy(snap))
    monkeypatch.setattr(policies, "verify_stored_snapshot", lambda *args, **kwargs: verified)
    monkeypatch.setattr(policies, "plane_for", lambda *args, **kwargs: plane)
    run = broker.create_run(RunRequest(document_ids=[], public_model_task="supplier_directory_summary"), actor)
    args = {"model": "cloud-business", "messages": run["public_messages"], "max_tokens": 128}
    return snap, run, args


def cloud_success():
    return {"response": {"choices": [{"message": {"role": "assistant", "content": "Public supplier summary."}, "finish_reason": "stop"}]},
            "usage": {"total_tokens": 50}, "actual_usd_micros": 7, "settlement_status": "settled"}


def cloud_rejection():
    return {"response": None, "usage": None, "actual_usd_micros": None, "settlement_status": "usage_unknown",
            "rejection": {"provider": "groq", "status": 429, "retry_after_seconds": 0}}


async def test_provider_retry_separate_holds_fresh_grants_and_actual_attempt_audit(controlled_cloud_policy, actor, monkeypatch):
    from actiongate.cloud import Client
    from actiongate.db import BudgetAccount
    snap, run, args = controlled_cloud_policy
    attempts, opa_inputs = [], []
    original_opa = broker._opa
    async def opa(*args, **kwargs):
        opa_inputs.append(kwargs.get("stage", "input"))
        return await original_opa(*args, **kwargs)
    async def chat(self, *args, **kwargs):
        with transaction() as db:
            grant = db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == kwargs["operation_id"]))
            attempts.append(grant.id)
            assert grant.status == "consumed"
        return cloud_rejection() if len(attempts) == 1 else cloud_success()
    monkeypatch.setattr(Client, "chat", chat)
    monkeypatch.setattr(broker, "_opa", opa)
    result = await invoke(run, actor, "models.chat", args)
    assert result["status"] == "completed" and result["settlement_status"] == "usage_unknown", result
    assert len(set(attempts)) == 2 and opa_inputs == ["input", "input", "output"]
    assert result["metadata"]["upstream_attempts"] == 2 and len(result["metadata"]["unsettled_attempts"]) == 1
    assert result["metadata"]["execution_mode"] == "contract-controlled-models"
    with transaction() as db:
        rows = list(db.scalars(select(Reservation).where(Reservation.operation_id == result["id"], Reservation.kind == "execution")))
        assert sorted(row.status for row in rows) == ["settled", "usage_unknown"]
        assert db.get(ExecutionGrant, attempts[0]) is None
        assert db.get(ExecutionGrant, attempts[1]).status == "consumed"
        assert db.get(RunContext, run["id"]).steps == 2
        money = db.get(BudgetAccount, f"root:{run['id']}:usd_micros")
        assert money.spent == 7 and money.reserved > 0
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.operation_id == result["id"], AuditEvent.event == "operation.dispatched").order_by(AuditEvent.id)))
        assert [event.evidence["attempt"] for event in events] == [1, 2]
        assert len({event.evidence["reservation_id"] for event in events}) == 2


@pytest.mark.parametrize("maximum", [0, 2])
async def test_provider_max_retries_counts_every_attempt_and_retains_unknown_cost(controlled_cloud_policy, actor, monkeypatch, maximum):
    from actiongate.cloud import Client
    snap, run, args = controlled_cloud_policy
    snap["configuration"]["budgets"]["max_retries"] = maximum
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs["operation_id"])
        return cloud_rejection()
    monkeypatch.setattr(Client, "chat", chat)
    result = await invoke(run, actor, "models.chat", args)
    assert len(calls) == maximum + 1
    assert result["status"] == "blocked" and "provider.retry_exhausted" in result["rule_ids"], result
    assert result["settlement_status"] == "usage_unknown"
    with transaction() as db:
        rows = list(db.scalars(select(Reservation).where(Reservation.operation_id == result["id"], Reservation.kind == "execution")))
        assert len(rows) == maximum + 1 and all(row.status == "usage_unknown" for row in rows)


@pytest.mark.parametrize("change,rule", [("generation", "fence.changed"), ("revoked", "grant.revoked"),
    ("steps", "budget.steps"), ("deadline", "budget.deadline"), ("money", "budget.exhausted"), ("opa", "policy.retry")])
async def test_provider_retry_rechecks_authority_deadline_policy_and_budget(controlled_cloud_policy, actor, monkeypatch, change, rule):
    from actiongate.cloud import Client
    from actiongate.db import Run, BudgetAccount
    snap, run, args = controlled_cloud_policy
    calls = []
    original_opa = broker._opa
    async def opa(*args, **kwargs):
        if calls and change == "opa":
            return {"decision": "block", "rule_ids": ["test.current_denial"]}
        return await original_opa(*args, **kwargs)
    async def chat(*args, **kwargs):
        calls.append(kwargs["operation_id"])
        if change == "generation":
            snap["generation"] += 1
        with transaction() as db:
            root = db.get(Run, run["id"])
            if change == "revoked":
                root.grant = {**root.grant, "revoked": True}
            elif change == "steps":
                db.get(RunContext, run["id"]).steps = snap["configuration"]["budgets"]["max_steps"]
            elif change == "deadline":
                root.created_at = now() - timedelta(days=1)
            elif change == "money":
                money = db.get(BudgetAccount, f"root:{run['id']}:usd_micros")
                money.spent = money.limit  # another already committed charge consumes capacity
        return cloud_rejection()
    monkeypatch.setattr(Client, "chat", chat)
    monkeypatch.setattr(broker, "_opa", opa)
    result = await invoke(run, actor, "models.chat", args)
    assert len(calls) == 1 and rule in result["rule_ids"], result
    assert result["settlement_status"] == "usage_unknown"


async def test_provider_retry_does_not_reuse_expired_human_approval(controlled_cloud_policy, actor, monkeypatch):
    from actiongate.cloud import Client
    snap, run, args = controlled_cloud_policy
    snap["configuration"]["tools"]["require_approval"].append("models.chat")
    key = uid()
    waiting = await invoke(run, actor, "models.chat", args, key)
    assert waiting["status"] == "waiting_approval"
    with transaction() as db:
        db.add(HumanApproval(operation_id=waiting["id"], tenant=actor["tenant"], approver="test-approver",
            payload_hash=waiting["payload_hash"], approved=True, generation=waiting["policy_generation"], expires_at=now()+timedelta(minutes=5)))
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs["operation_id"])
        with transaction() as db:
            db.get(HumanApproval, waiting["id"]).expires_at = now()-timedelta(seconds=1)
        return cloud_rejection()
    monkeypatch.setattr(Client, "chat", chat)
    result = await broker.execute(ActionRequest(run_id=run["id"], tool="models.chat", arguments=args, idempotency_key=key), actor, existing_id=waiting["id"])
    assert len(calls) == 1 and "approval.invalid" in result["rule_ids"], result
    with transaction() as db:
        assert sorted(row.status for row in db.scalars(select(Reservation).where(Reservation.operation_id == result["id"], Reservation.kind == "execution"))) == ["released", "usage_unknown"]


async def test_provider_unknown_response_is_never_retried(controlled_cloud_policy, actor, monkeypatch):
    from actiongate.cloud import Client
    snap, run, args = controlled_cloud_policy
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs["operation_id"])
        return {"response": None, "usage": None, "actual_usd_micros": None, "settlement_status": "usage_unknown"}
    monkeypatch.setattr(Client, "chat", chat)
    result = await invoke(run, actor, "models.chat", args)
    assert len(calls) == 1 and result["status"] == "outcome_unknown", result
    assert result["settlement_status"] == "usage_unknown"
