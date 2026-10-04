"""Metadata-only audit verification and independently persisted signed anchors."""
from sqlalchemy import select
from .db import AuditEvent, State, transaction
from .security import digest


def event_record(row):
    return {"id": row.id, "tenant": row.tenant, "event": row.event,
            "run_id": row.run_id, "operation_id": row.operation_id,
            "evidence": row.evidence, "previous_hash": row.previous_hash,
            "hash": row.hash, "created_at": row.created_at.isoformat()}


def verify_records(records, previous_hash=None):
    previous = previous_hash
    last_id = 0
    for event in records:
        if event["id"] <= last_id:
            raise ValueError("Audit sequence is not monotonic")
        if previous is not None and event["previous_hash"] != previous:
            raise ValueError("Audit chain continuity failed")
        safe = {key: event[key] for key in ("tenant", "event", "run_id", "operation_id", "evidence", "previous_hash")}
        if event["hash"] != digest(safe):
            raise ValueError("Audit event hash failed")
        previous, last_id = event["hash"], event["id"]
    return previous, last_id


def read_verified_chain():
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        records = [event_record(row) for row in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]
        head, last_id = verify_records(records)
        if records and head != state.audit_head:
            raise ValueError("Audit head differs from its committed platform fence")
        return {"records": records, "head": state.audit_head, "last_id": last_id,
                "generation": state.generation, "first_previous_hash": records[0]["previous_hash"] if records else state.audit_head}


async def checkpoint(archive=False):
    from .policies import publisher_call, public_keys
    from .controls.signed import verify_document
    chain = read_verified_chain()
    payload = {"kind": "actiongate-audit-checkpoint-v1", "last_event_id": chain["last_id"],
               "head": chain["head"], "generation": chain["generation"],
               "first_previous_hash": chain["first_previous_hash"], "event_count": len(chain["records"]),
               "events_digest": digest(chain["records"])}
    response = await publisher_call("POST", "/audit/checkpoint", {"payload": payload, "records": chain["records"] if archive else None})
    verified = verify_document(response["envelope"], public_keys("POLICY"))
    if verified != payload:
        raise ValueError("Publisher signed another audit checkpoint")
    return {**response, "verified": True}
