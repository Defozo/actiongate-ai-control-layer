"""PostgreSQL is the authority for fences, money, labels and durable outbox."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import os
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .settings import settings


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class State(Base):
    __tablename__ = "platform_state"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    generation: Mapped[int] = mapped_column(default=1)
    revocation_epoch: Mapped[int] = mapped_column(default=0)
    kill_switch: Mapped[bool] = mapped_column(default=False)
    audit_head: Mapped[str] = mapped_column(default="0" * 64)


class Principal(Base):
    __tablename__ = "principals"
    id: Mapped[str] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(index=True)
    role: Mapped[str]
    revoked: Mapped[bool] = mapped_column(default=False)


class PolicyGeneration(Base):
    __tablename__ = "policy_generations"
    id: Mapped[int] = mapped_column(primary_key=True)
    configuration: Mapped[dict] = mapped_column(JSON)
    yaml: Mapped[str] = mapped_column(Text)
    digest: Mapped[str]
    signature: Mapped[str] = mapped_column(Text, default="")
    feed: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(default="active")


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(index=True)
    actor: Mapped[str]
    root_id: Mapped[str] = mapped_column(index=True)
    parent_id: Mapped[str | None]
    purpose: Mapped[str]
    depth: Mapped[int] = mapped_column(default=0)
    grant: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunContext(Base):
    __tablename__ = "run_contexts"
    root_id: Mapped[str] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(index=True)
    label: Mapped[int] = mapped_column(default=0)
    label_version: Mapped[int] = mapped_column(default=1)
    origins: Mapped[list] = mapped_column(JSON, default=list)
    compartments: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[int] = mapped_column(default=0)
    tool_calls: Mapped[int] = mapped_column(default=0)
    publication_uncertain: Mapped[bool] = mapped_column(default=False)
    fence: Mapped[int] = mapped_column(default=0)


class DataObject(Base):
    __tablename__ = "objects"
    __table_args__ = (UniqueConstraint("tenant", "name"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    kind: Mapped[str]
    owner: Mapped[str]
    purpose: Mapped[str] = mapped_column(default="supplier_review")
    label: Mapped[int] = mapped_column(default=2)
    origins: Mapped[list] = mapped_column(JSON, default=list)
    compartments: Mapped[list] = mapped_column(JSON, default=list)
    acl: Mapped[list] = mapped_column(JSON, default=lambda: ["analyst", "admin", "agent"])
    version: Mapped[int] = mapped_column(default=1)
    encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("tenant", "actor", "idempotency_key"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(index=True)
    actor: Mapped[str]
    run_id: Mapped[str] = mapped_column(index=True)
    root_id: Mapped[str] = mapped_column(index=True)
    tool: Mapped[str]
    idempotency_key: Mapped[str]
    payload_hash: Mapped[str]
    input_hash: Mapped[str]
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(default="block")
    status: Mapped[str] = mapped_column(default="proposed")
    settlement_status: Mapped[str] = mapped_column(default="released")
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(default="Pending screening")
    stage: Mapped[str] = mapped_column(default="identity")
    label: Mapped[int] = mapped_column(default=2)
    label_version: Mapped[int] = mapped_column(default=1)
    policy_generation: Mapped[int] = mapped_column(default=1)
    revocation_epoch: Mapped[int] = mapped_column(default=0)
    tool_version: Mapped[str] = mapped_column(default="1")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExecutionGrant(Base):
    __tablename__ = "execution_grants"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    operation_id: Mapped[str] = mapped_column(unique=True)
    tenant: Mapped[str]
    payload_hash: Mapped[str]
    generation: Mapped[int]
    label_version: Mapped[int]
    revocation_epoch: Mapped[int]
    connector: Mapped[str]
    status: Mapped[str] = mapped_column(default="ready")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HumanApproval(Base):
    __tablename__ = "human_approvals"
    operation_id: Mapped[str] = mapped_column(primary_key=True)
    tenant: Mapped[str]
    approver: Mapped[str]
    payload_hash: Mapped[str]
    approved: Mapped[bool]
    generation: Mapped[int]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BudgetAccount(Base):
    __tablename__ = "budget_accounts"
    id: Mapped[str] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(index=True)
    scope: Mapped[str]
    unit: Mapped[str]
    limit: Mapped[int] = mapped_column(BigInteger)
    spent: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    period: Mapped[str]


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    tenant: Mapped[str] = mapped_column(index=True)
    operation_id: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str]
    accounts: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="reserved")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UsageEntry(Base):
    __tablename__ = "usage_entries"
    reservation_id: Mapped[str] = mapped_column(primary_key=True)
    tenant: Mapped[str]
    usage: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant: Mapped[str] = mapped_column(index=True)
    run_id: Mapped[str | None] = mapped_column(index=True)
    operation_id: Mapped[str | None] = mapped_column(index=True)
    event: Mapped[str]
    evidence: Mapped[dict] = mapped_column(JSON)
    previous_hash: Mapped[str]
    hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(unique=True)
    tenant: Mapped[str] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TestRun(Base):
    __tablename__ = "test_runs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    tenant: Mapped[str]
    suite: Mapped[str]
    status: Mapped[str] = mapped_column(default="running")
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    owner: Mapped[str | None]
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorReceipt(Base):
    __tablename__ = "connector_receipts"
    operation_id: Mapped[str] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(index=True)
    tool: Mapped[str]
    recipient: Mapped[str | None]
    payload_hash: Mapped[str]
    encrypted_result: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


@lru_cache
def engine():
    return create_engine(os.environ["DATABASE_URL"], pool_size=20, max_overflow=40, pool_pre_ping=True)


@contextmanager
def transaction():
    with sessionmaker(engine(), expire_on_commit=False)() as session:
        with session.begin():
            yield session


@contextmanager
def root_boundary(root_id):
    """A session advisory lock serializes read/release boundaries across replicas.

    A disconnected connection releases its lock, so every process must refuse
    publication once its fence cannot be revalidated. Network sinks also check
    operation idempotency and root label in their own transaction.
    """
    lock_id = int.from_bytes(hashlib.sha256(root_id.encode()).digest()[:8], "big", signed=True)
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_id}).scalar():
            raise BlockingIOError("Run has an active read or publication; retry after it completes")
        try:
            yield conn
        finally:
            if not conn.invalidated and not conn.closed:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_id})


class OwnerLost(RuntimeError):
    pass


class ExecutionLease:
    def __init__(self, connection, root_id, fence):
        self.connection, self.root_id, self.fence = connection, root_id, fence
        self.backend_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()

    def ensure_owned(self, db, context):
        try:
            if self.connection.invalidated or self.connection.closed:
                raise OwnerLost("Workflow lock session was lost")
            pid = self.connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            if pid != self.backend_pid or context.fence != self.fence:
                raise OwnerLost("Workflow ownership changed")
        except OwnerLost:
            raise
        except Exception as exc:
            raise OwnerLost("Workflow ownership could not be confirmed") from exc


@contextmanager
def execution_boundary(root_id):
    with root_boundary(root_id) as connection:
        with transaction() as db:
            select = __import__("sqlalchemy").select
            state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
            context = db.get(RunContext, root_id)
            context.fence += 1
            fence = context.fence
            for operation in db.scalars(select(Operation).where(Operation.root_id == root_id,
                    Operation.status.in_(("screening", "ready", "retry_wait", "dispatched")))):
                previous_status = operation.status
                possible_effect = previous_status == "dispatched"
                if possible_effect:
                    context.publication_uncertain = True
                operation.status = "outcome_unknown" if possible_effect else "failed"
                operation.reason = ("Previous workflow owner lost its execution session; reconcile the admitted effect"
                    if possible_effect else "Previous workflow owner stopped before a completed dispatch; submit a new operation")
                operation.stage, operation.updated_at = "recovery", now()
                uncertain_usage = False
                for reservation in db.scalars(select(Reservation).where(Reservation.operation_id == operation.id,
                        Reservation.status.in_(("reserved", "usage_unknown")))):
                    reservation.status = "usage_unknown"
                    uncertain_usage = True
                operation.settlement_status = "usage_unknown" if uncertain_usage else operation.settlement_status
                from .security import audit
                audit(db, context.tenant, "operation.owner_lost", {"new_fence": fence, "result_released": False,
                      "previous_status": previous_status, "possible_business_effect": possible_effect,
                      "retained_uncertain_usage": uncertain_usage}, operation.run_id, operation.id, locked_state=state)
        yield ExecutionLease(connection, root_id, fence)
