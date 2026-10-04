import copy
import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from actiongate.audit_integrity import verify_records, checkpoint
from actiongate.db import transaction, AuditEvent
from actiongate.security import audit


def test_audit_detects_changed_removed_and_reordered_metadata(actor):
    from actiongate.audit_integrity import event_record
    with transaction() as db:
        first = audit(db, actor["tenant"], "test.integrity", {"index": 1})
        second = audit(db, actor["tenant"], "test.integrity", {"index": 2})
        rows = [event_record(first), event_record(second)]
    assert verify_records(rows)[0] == rows[-1]["hash"]
    changed = copy.deepcopy(rows)
    changed[0]["evidence"]["index"] = 9
    with pytest.raises(ValueError):
        verify_records(changed)
    with pytest.raises(ValueError):
        verify_records(list(reversed(rows)))
    with pytest.raises(ValueError):
        verify_records(rows[1:], rows[0]["previous_hash"])


def test_runtime_database_identity_cannot_edit_audit(actor):
    with transaction() as db:
        row = audit(db, actor["tenant"], "test.append_only", {})
        event_id = row.id
    with pytest.raises(DBAPIError):
        with transaction() as db:
            db.execute(update(AuditEvent).where(AuditEvent.id == event_id).values(event="tampered"))


async def test_live_checkpoint_signed_and_persisted_outside_database(platform):
    result = await checkpoint(archive=True)
    assert result["verified"] and result["archive_persisted"]
    assert result["envelope"]["payload"]["event_count"] > 0
    assert result["storage"] == "independent publisher volume"
