"""Failure injection at dispatch and durable-result boundaries with real state."""
from contextlib import contextmanager
import subprocess
import sys
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from actiongate import broker, recovery
from actiongate.contracts import ActionRequest
from actiongate.db import Operation, Reservation, RunContext, transaction, uid


def request(run):
    return ActionRequest(run_id=run["id"], tool="models.chat", idempotency_key=uid(), arguments={
        "model": "local-business", "messages": [{"role": "user", "content": "Review supplier delivery."}], "max_tokens": 100})


async def test_lost_upstream_outcome_retains_uncertain_reservation(workflow_run, actor, controlled_models, monkeypatch):
    original = broker.dispatch
    async def lost(*args, **kwargs):
        await original(*args, **kwargs)
        raise TimeoutError("Controlled transport lost the response after work")
    monkeypatch.setattr(broker, "dispatch", lost)
    outcome = await broker.execute(request(workflow_run), actor)
    assert outcome["status"] == "outcome_unknown"
    assert outcome["settlement_status"] == "usage_unknown"
    assert outcome["result"] is None
    with transaction() as db:
        execution = db.scalar(select(Reservation).where(Reservation.operation_id == outcome["id"], Reservation.kind == "execution"))
        assert execution.status == "usage_unknown"
        assert any(entry["amount"] > 0 for entry in execution.accounts.values())
        assert db.get(RunContext, workflow_run["id"]).publication_uncertain


@pytest.mark.parametrize("full", [False, True])
async def test_database_loss_after_dispatch_uses_bounded_metadata_journal_and_never_releases_result(
        workflow_run, actor, controlled_models, monkeypatch, tmp_path, full):
    original_dispatch = broker.dispatch
    original_transaction = broker.transaction
    monkeypatch.setenv("ACTIONGATE_SPOOL_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("ACTIONGATE_SPOOL_KEY", Fernet.generate_key().decode())
    if full:
        monkeypatch.setattr(recovery, "MAX_JOURNAL_BYTES", 0)
    @contextmanager
    def unavailable():
        raise ConnectionError("Injected database failure after confirmed upstream return")
        yield
    async def completed_then_db_lost(*args, **kwargs):
        result = await original_dispatch(*args, **kwargs)
        monkeypatch.setattr(broker, "transaction", unavailable)
        return result
    monkeypatch.setattr(broker, "dispatch", completed_then_db_lost)
    action = request(workflow_run)
    with pytest.raises(HTTPException) as failed:
        await broker.execute(action, actor)
    assert failed.value.status_code == 503
    monkeypatch.setattr(broker, "transaction", original_transaction)
    with transaction() as db:
        operation = db.scalar(select(Operation).where(Operation.idempotency_key == action.idempotency_key))
        op_id = operation.id
        assert operation.encrypted_result is None
        assert operation.status == "dispatched"
    journals = list(tmp_path.glob("*.sealed"))
    assert len(journals) == (0 if full else 1)
    if journals:
        assert b"Supplier review completed" not in journals[0].read_bytes()
        assert recovery.recover_spool() == 1
        with transaction() as db:
            recovered = db.get(Operation, op_id)
            assert recovered.status == "outcome_unknown"
            assert recovered.encrypted_result is None
            assert db.get(RunContext, workflow_run["id"]).publication_uncertain
    assert controlled_models["business"] == 1


@pytest.mark.parametrize("phase", ["screening", "ready", "retry_wait", "dispatched", "waiting_approval"])
def test_new_owner_recovers_abandoned_phases_without_freeing_unknown_budget(workflow_run, actor, phase):
    from actiongate import ledger, policies
    from actiongate.db import BudgetAccount, execution_boundary
    op_id = uid()
    with transaction() as db:
        db.add(Operation(id=op_id, tenant=actor["tenant"], actor=actor["sub"], run_id=workflow_run["id"],
            root_id=workflow_run["id"], tool="models.chat", idempotency_key=uid(),
            payload_hash="synthetic-recovery", input_hash="synthetic-recovery", status="created"))
    snapshot = policies.snapshot()
    held = ledger.reserve(workflow_run["id"], op_id, "execution", snapshot["configuration"], tokens=100,
                          usd_micros=100, generation=snapshot["generation"])
    code = """
import os, sys
from actiongate.db import execution_boundary, transaction, Operation
with execution_boundary(sys.argv[1]):
    with transaction() as db:
        db.get(Operation, sys.argv[2]).status = sys.argv[3]
    os._exit(17)  # real process death; advisory session closes without cleanup
"""
    child = subprocess.run([sys.executable, "-c", code, workflow_run["id"], op_id, phase], capture_output=True, timeout=20)
    assert child.returncode == 17
    with execution_boundary(workflow_run["id"]):
        with transaction() as db:
            operation = db.get(Operation, op_id)
            reservation = db.get(Reservation, held)
            if phase == "waiting_approval":
                assert operation.status == "waiting_approval" and reservation.status == "reserved"
            else:
                assert operation.status == ("outcome_unknown" if phase == "dispatched" else "failed")
                assert operation.settlement_status == reservation.status == "usage_unknown"
            assert db.get(RunContext, workflow_run["id"]).publication_uncertain is (phase == "dispatched")
            assert db.get(BudgetAccount, f"root:{workflow_run['id']}:tokens").reserved == 100
            assert db.get(BudgetAccount, f"root:{workflow_run['id']}:usd_micros").reserved == 100
