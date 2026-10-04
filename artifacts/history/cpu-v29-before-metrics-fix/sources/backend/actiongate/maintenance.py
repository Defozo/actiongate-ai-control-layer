"""Owner-only retention job. Archive acknowledgement precedes audit deletion."""
import asyncio
import json
from datetime import timedelta
from sqlalchemy import select, update, delete, func
from .db import (PolicyGeneration, State, Operation, DataObject, AuditEvent, Outbox,
                 ConnectorReceipt, HumanApproval, ExecutionGrant, transaction)
from .audit_integrity import checkpoint
from .security import audit


async def retain():
    with transaction() as db:
        state = db.get(State, 1)
        if not state:
            return {"status": "not_initialized"}
        configuration = db.get(PolicyGeneration, state.generation).configuration
        cutoff = db.scalar(select(func.now())) - timedelta(days=configuration["audit"]["retention_days"])
    anchored = await checkpoint(archive=True)
    if not anchored.get("archive_persisted"):
        raise RuntimeError("Retention requires an independently persisted archive")
    last_id = anchored["envelope"]["payload"]["last_event_id"]
    with transaction() as db:
        db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        cleared = db.execute(update(Operation).where(Operation.created_at < cutoff).values(encrypted_payload=None, encrypted_result=None)).rowcount
        expired = db.execute(delete(DataObject).where(DataObject.expires_at <= func.now())).rowcount
        receipts = db.execute(delete(ConnectorReceipt).where(ConnectorReceipt.created_at < cutoff,
            ConnectorReceipt.operation_id.in_(select(Operation.id).where(Operation.status.in_(["completed", "output_blocked"]))))).rowcount
        db.execute(delete(HumanApproval).where(HumanApproval.expires_at < cutoff))
        db.execute(delete(ExecutionGrant).where(ExecutionGrant.expires_at < cutoff, ExecutionGrant.status == "ready"))
        # Replica clocks need not produce monotonically increasing timestamps.
        # Purge only an archived prefix: deleting an old event after a retained
        # newer event would leave an unverifiable hole in the live hash chain.
        first_retained = db.scalar(select(func.min(AuditEvent.id)).where(
            AuditEvent.id <= last_id, AuditEvent.created_at >= cutoff))
        eligible = [AuditEvent.id <= last_id, AuditEvent.created_at < cutoff]
        if first_retained is not None:
            eligible.append(AuditEvent.id < first_retained)
        removed_outbox = db.execute(delete(Outbox).where(
            Outbox.event_id.in_(select(AuditEvent.id).where(*eligible)))).rowcount
        removed_events = db.execute(delete(AuditEvent).where(*eligible)).rowcount
        evidence = {"payloads_cleared": cleared, "expired_objects": expired, "settled_receipts_removed": receipts,
                    "audit_events_archived": removed_events, "outbox_removed": removed_outbox,
                    "archive_id": anchored["archive_id"], "cutoff": cutoff.isoformat()}
        audit(db, "synthetic_test_tenant", "retention.completed", evidence)
    return {"status": "completed", **evidence}


async def main():
    import sys
    once = "--once" in sys.argv
    while True:
        try:
            print(json.dumps(await retain()), flush=True)
        except Exception as exc:
            print(json.dumps({"status": "retention_failed", "reason": type(exc).__name__}), flush=True)
            if once:
                raise
        if once:
            return
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
