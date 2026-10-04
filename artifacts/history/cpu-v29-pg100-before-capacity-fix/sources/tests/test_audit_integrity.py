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


@pytest.fixture
def isolated_maintenance_db(platform, monkeypatch):
    """Real PostgreSQL and exact ORM DDL, isolated from the live demo and audit.

    TEMP tables belong to this runtime-role session. No schema-owner credential,
    shared audit deletion or model inference is involved. Archive acknowledgement
    is controlled below; the separate live checkpoint test verifies the publisher.
    """
    from contextlib import contextmanager
    from sqlalchemy import MetaData, text
    from sqlalchemy.orm import sessionmaker
    from actiongate import app, audit_integrity, maintenance, seed
    from actiongate.db import Base, State, PolicyGeneration, engine

    with engine().connect() as connection:
        temporary = MetaData()
        for table in Base.metadata.sorted_tables:
            clone = table.to_metadata(temporary)
            clone._prefixes = ["TEMPORARY"]
            clone.create(connection, checkfirst=False)
        connection.commit()

        @contextmanager
        def isolated():
            with sessionmaker(bind=connection, expire_on_commit=False)() as db:
                with db.begin():
                    yield db

        for module in (app, audit_integrity, maintenance, seed):
            monkeypatch.setattr(module, "transaction", isolated)
        with isolated() as db:
            assert db.scalar(text("SELECT current_user")) == "actiongate"
            assert db.scalar(text("SELECT relpersistence FROM pg_class WHERE oid = 'audit_events'::regclass")) == "t"
            db.add(State(id=1, generation=platform["generation"]))
            db.add(PolicyGeneration(id=platform["generation"], configuration=copy.deepcopy(platform["configuration"]),
                yaml="synthetic isolated retention configuration", digest="isolated-contract", signature="fixture"))
        try:
            yield isolated
        finally:
            # Pool connections survive a context manager; explicitly remove all
            # session-local tables before returning this connection to the pool.
            connection.rollback()
            for table in reversed(list(temporary.sorted_tables)):
                table.drop(connection, checkfirst=False)
            connection.commit()


def maintenance_rows(db, model):
    from sqlalchemy import select
    return [dict(row) for row in db.execute(select(*model.__table__.columns)
        .order_by(*model.__table__.primary_key.columns)).mappings()]


def add_retained_financial_state(db):
    from actiongate.db import BudgetAccount, Reservation, UsageEntry
    db.add(BudgetAccount(id="isolated:account", tenant="synthetic_test_tenant", scope="root", unit="tokens",
        limit=1000, spent=17, reserved=29, period="2026-10-01"))
    db.add(Reservation(id="isolated:unknown", tenant="synthetic_test_tenant", operation_id="isolated:uncertain",
        kind="execution", accounts={"isolated:account": {"unit": "tokens", "amount": 29}}, status="usage_unknown"))
    db.add(Reservation(id="isolated:settled", tenant="synthetic_test_tenant", operation_id="isolated:completed",
        kind="execution", accounts={}, status="settled", usage={"total_tokens": 17}))
    db.add(UsageEntry(reservation_id="isolated:settled", tenant="synthetic_test_tenant", usage={"total_tokens": 17}))


def test_demo_reset_preserves_foreign_resources_history_finances_and_policy(isolated_maintenance_db):
    from datetime import timedelta
    from sqlalchemy import select
    from actiongate.app import demo_reset
    from actiongate.db import (BudgetAccount, Reservation, UsageEntry, Run, DataObject, State, PolicyGeneration, now)
    from actiongate.seed import seed
    seed()
    with isolated_maintenance_db() as db:
        for tenant in ("synthetic_test_tenant", "acme"):
            db.add(Run(id=tenant+":run", root_id=tenant+":run", actor="isolated", tenant=tenant, purpose="review", grant={}))
            db.add(DataObject(id=tenant+":note", tenant=tenant, name="isolated-note", kind="memory", owner="isolated",
                encrypted="synthetic encrypted payload", expires_at=now()+timedelta(days=1)))
        add_retained_financial_state(db)
        audit(db, "synthetic_test_tenant", "test.before_reset", {})
        db.flush()
        finances = {model: maintenance_rows(db, model) for model in (BudgetAccount, Reservation, UsageEntry)}
        history = maintenance_rows(db, AuditEvent)
        policy = maintenance_rows(db, PolicyGeneration)
        foreign = copy.deepcopy(db.get(DataObject, "acme:note").encrypted)
        old_seed = db.scalar(select(DataObject.id).where(DataObject.name == "supplier-synthetic_test_tenant-1"))
        epoch, generation = db.get(State, 1).revocation_epoch, db.get(State, 1).generation
    result = demo_reset({"role": "admin", "tenant": "synthetic_test_tenant", "sub": "isolated"})
    assert result["reset"] and result["revoked_runs"] == 1
    with isolated_maintenance_db() as db:
        assert db.get(Run, "synthetic_test_tenant:run").status == "revoked"
        assert db.get(Run, "acme:run").status == "active"
        assert db.get(DataObject, "synthetic_test_tenant:note") is None
        assert db.get(DataObject, "acme:note").encrypted == foreign
        restored = db.scalar(select(DataObject).where(DataObject.name == "supplier-synthetic_test_tenant-1"))
        assert restored and restored.id != old_seed and restored.label == 2
        assert db.get(State, 1).revocation_epoch == epoch+1 and db.get(State, 1).generation == generation
        assert maintenance_rows(db, PolicyGeneration) == policy
        assert all(maintenance_rows(db, model) == rows for model, rows in finances.items())
        after = maintenance_rows(db, AuditEvent)
        assert after[:len(history)] == history and after[-1]["event"] == "demo.reset"


@pytest.mark.parametrize("role,tenant,demo_enabled", [("analyst", "synthetic_test_tenant", True),
    ("admin", "acme", True), ("admin", "synthetic_test_tenant", False)])
def test_demo_reset_denies_wrong_role_tenant_or_disabled_demo_without_mutation(
        isolated_maintenance_db, monkeypatch, role, tenant, demo_enabled):
    from fastapi import HTTPException
    from actiongate import app
    from actiongate.db import State, DataObject
    monkeypatch.setattr(app, "settings", lambda: {"demo_auth": demo_enabled})
    with isolated_maintenance_db() as db:
        before = maintenance_rows(db, State)
    with pytest.raises(HTTPException) as denied:
        app.demo_reset({"role": role, "tenant": tenant, "sub": "isolated"})
    assert denied.value.status_code == 403
    with isolated_maintenance_db() as db:
        assert maintenance_rows(db, State) == before
        assert maintenance_rows(db, AuditEvent) == [] and maintenance_rows(db, DataObject) == []


@pytest.mark.parametrize("archive_failure", ["unconfirmed", "unavailable"])
async def test_retention_without_confirmed_archive_changes_nothing(isolated_maintenance_db, monkeypatch, archive_failure):
    from datetime import timedelta
    from actiongate import maintenance
    from actiongate.db import BudgetAccount, Reservation, UsageEntry, DataObject, Outbox, State, now
    with isolated_maintenance_db() as db:
        db.add(DataObject(tenant="synthetic_test_tenant", name="expired", kind="memory", owner="isolated",
            encrypted="still protected", expires_at=now()-timedelta(days=2)))
        add_retained_financial_state(db)
        audit(db, "synthetic_test_tenant", "test.unarchived", {})
        db.flush()
        before = {model: maintenance_rows(db, model) for model in
            (BudgetAccount, Reservation, UsageEntry, DataObject, AuditEvent, Outbox, State)}
    async def unavailable(*, archive):
        assert archive is True
        if archive_failure == "unavailable":
            raise ConnectionError("Controlled archive outage")
        return {"archive_persisted": False}
    monkeypatch.setattr(maintenance, "checkpoint", unavailable)
    with pytest.raises((ConnectionError, RuntimeError)):
        await maintenance.retain()
    with isolated_maintenance_db() as db:
        assert all(maintenance_rows(db, model) == rows for model, rows in before.items())


@pytest.mark.parametrize("retain_middle", [True, False], ids=["nonmonotonic_age", "whole_old_prefix"])
async def test_retention_keeps_hash_chain_across_nonmonotonic_dates_and_unknown_obligations(
        isolated_maintenance_db, monkeypatch, retain_middle):
    from datetime import timedelta
    from sqlalchemy import select
    from actiongate import maintenance, audit_integrity
    from actiongate.db import (BudgetAccount, Reservation, UsageEntry, DataObject, Outbox, Operation,
        ConnectorReceipt, HumanApproval, ExecutionGrant, now)
    old, current = now()-timedelta(days=40), now()
    with isolated_maintenance_db() as db:
        add_retained_financial_state(db)
        for name, status, created in (("completed", "completed", old), ("uncertain", "outcome_unknown", old), ("recent", "completed", current)):
            identifier = "isolated:"+name
            db.add(Operation(id=identifier, tenant="synthetic_test_tenant", actor="isolated", run_id="isolated:run", root_id="isolated:run",
                tool="reports.save", idempotency_key=identifier, payload_hash="synthetic", input_hash="synthetic", status=status,
                encrypted_payload="protected payload", encrypted_result="protected result", created_at=created))
            db.add(ConnectorReceipt(operation_id=identifier, tenant="synthetic_test_tenant", tool="reports.save",
                payload_hash="synthetic", encrypted_result="protected receipt", created_at=created))
        for name, expiry in (("expired", old), ("current", current+timedelta(days=1))):
            db.add(DataObject(id=name, tenant="synthetic_test_tenant", name=name, kind="memory", owner="isolated", encrypted="protected", expires_at=expiry))
            db.add(HumanApproval(operation_id=name, tenant="synthetic_test_tenant", approver="isolated", payload_hash="synthetic", approved=True, generation=1, expires_at=expiry))
        for name, status in (("expired-ready", "ready"), ("expired-used", "used")):
            db.add(ExecutionGrant(id=name, operation_id=name, tenant="synthetic_test_tenant", payload_hash="synthetic",
                generation=1, label_version=1, revocation_epoch=0, connector="demo", status=status, expires_at=old))
        event_ids = []
        for index, created in enumerate((old, current if retain_middle else old, old)):
            event = audit(db, "synthetic_test_tenant", "test.retention", {"index": index})
            event.created_at = created
            event_ids.append(event.id)
        db.flush()
        finances = {model: maintenance_rows(db, model) for model in (BudgetAccount, Reservation, UsageEntry)}
    archive_record = {}
    async def confirmed_archive(*, archive):
        assert archive is True
        chain = audit_integrity.read_verified_chain()
        archive_record.update(chain)
        # A concurrent event after this archive is never eligible, even when its
        # timestamp is old. Its bytes were not included in the archive boundary.
        with isolated_maintenance_db() as db:
            audit(db, "synthetic_test_tenant", "test.after_archive", {}).created_at = old
        return {"archive_persisted": True, "verified": True, "archive_id": "controlled-confirmed-archive",
            "envelope": {"payload": {"last_event_id": chain["last_id"]}}}
    monkeypatch.setattr(maintenance, "checkpoint", confirmed_archive)
    outcome = await maintenance.retain()
    assert outcome["status"] == "completed"
    removed = 1 if retain_middle else 3
    assert outcome["audit_events_archived"] == outcome["outbox_removed"] == removed
    assert outcome["payloads_cleared"] == 2 and outcome["expired_objects"] == outcome["settled_receipts_removed"] == 1
    remaining = audit_integrity.read_verified_chain()
    assert remaining["records"][0]["previous_hash"] == archive_record["records"][removed-1]["hash"]
    if retain_middle:
        assert [event["id"] for event in remaining["records"][:2]] == event_ids[1:]
    assert remaining["records"][-2]["event"] == "test.after_archive"
    assert remaining["records"][-1]["event"] == "retention.completed"
    with isolated_maintenance_db() as db:
        assert all(maintenance_rows(db, model) == rows for model, rows in finances.items())
        assert db.get(Operation, "isolated:uncertain").status == "outcome_unknown"
        assert db.get(Operation, "isolated:uncertain").encrypted_payload is None
        assert db.get(Operation, "isolated:completed").encrypted_result is None
        assert db.get(Operation, "isolated:recent").encrypted_payload == "protected payload"
        assert db.get(ConnectorReceipt, "isolated:completed") is None
        assert db.get(ConnectorReceipt, "isolated:uncertain") is not None
        assert db.get(ConnectorReceipt, "isolated:recent") is not None
        assert db.get(DataObject, "expired") is None and db.get(DataObject, "current") is not None
        assert db.get(HumanApproval, "expired") is None and db.get(HumanApproval, "current") is not None
        assert db.get(ExecutionGrant, "expired-ready") is None and db.get(ExecutionGrant, "expired-used") is not None
        assert set(db.scalars(select(Outbox.event_id))) == {event["id"] for event in remaining["records"]}
