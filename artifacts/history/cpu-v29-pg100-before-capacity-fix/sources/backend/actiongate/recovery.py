"""Bounded encrypted metadata spool and explicit, non-reexecuting reconciliation."""
import base64
import hmac
import hashlib
import json
import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select
from .db import (Operation, RunContext, Reservation, ConnectorReceipt, State, transaction, now)
from .security import audit, canonical
from .settings import secret

ALLOWED = {"operation_id", "run_id", "tenant", "dispatched", "reservation_id", "usage", "status"}
MAX_JOURNAL_BYTES = 16 * 1024 * 1024


def directory():
    path = Path(os.getenv("ACTIONGATE_SPOOL_DIRECTORY", "/app/spool"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def spool(metadata):
    if set(metadata) - ALLOWED:
        raise ValueError("Spool accepts only typed operation metadata")
    payload = Fernet(secret("ACTIONGATE_SPOOL_KEY").encode()).encrypt(canonical(metadata).encode())
    folder = directory()
    if sum(p.stat().st_size for p in folder.glob("*.sealed")) + len(payload) > MAX_JOURNAL_BYTES:
        return False
    target = folder / (secrets.token_hex(16) + ".sealed")
    # Each record is independently authenticated and atomically created.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def recover_spool():
    from .ledger import settle
    count = 0
    key = Fernet(secret("ACTIONGATE_SPOOL_KEY").encode())
    for path in sorted(directory().glob("*.sealed")):
        metadata = json.loads(key.decrypt(path.read_bytes()))
        if metadata.get("reservation_id"):
            settle(metadata["reservation_id"], metadata.get("usage"))
        with transaction() as db:
            db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
            op = db.get(Operation, metadata["operation_id"])
            if op and op.status == "dispatched":
                op.status = "outcome_unknown"
                op.settlement_status = "settled" if metadata.get("usage") and not metadata["usage"].get("usage_unknown") else "usage_unknown"
                db.get(RunContext, op.root_id).publication_uncertain = True
                audit(db, op.tenant, "operation.recovered_metadata", {"status": op.status}, op.run_id, op.id)
        # The record is deleted only after durable reconciliation of its metadata.
        path.unlink()
        count += 1
    return count


def reconcile(operation_id, tenant):
    """Read a controlled sink's durable receipt. Never repeat the mutation."""
    from .ledger import settle
    with transaction() as db:
        op = db.get(Operation, operation_id)
        if not op or op.tenant != tenant:
            raise HTTPException(404, "Operation not found")
        if op.status not in ("outcome_unknown", "dispatched"):
            raise HTTPException(409, "Operation does not require reconciliation")
        receipt = db.get(ConnectorReceipt, operation_id)
        if not receipt:
            return {"operation_id": op.id, "status": "outcome_unknown", "effect_confirmed": False,
                    "reason": "Absence of a receipt cannot prove that an external action never started"}
        if receipt.payload_hash != op.payload_hash:
            raise HTTPException(409, "Receipt does not match the admitted payload")
        reservations = list(db.scalars(select(Reservation).where(Reservation.operation_id == op.id,
                              Reservation.kind == "execution")))
    for reservation in reservations:
        settle(reservation.id, {"total_tokens": 0, "usd_micros": 0})
    with transaction() as db:
        op = db.get(Operation, operation_id)
        op.status, op.settlement_status = "output_blocked", "settled"
        op.reason = "Effect confirmed by idempotent sink receipt; result remains quarantined pending a new inspection"
        if not db.scalar(select(Operation.id).where(Operation.root_id == op.root_id,
            Operation.id != op.id, Operation.status.in_(["dispatched", "outcome_unknown"])).limit(1)):
            db.get(RunContext, op.root_id).publication_uncertain = False
        audit(db, tenant, "operation.reconciled", {"effect_confirmed": True, "receipt_id": operation_id,
              "result_released": False, "mutation_repeated": False}, op.run_id, op.id)
    return {"operation_id": operation_id, "status": "output_blocked", "effect_confirmed": True,
            "result_released": False, "mutation_repeated": False}
