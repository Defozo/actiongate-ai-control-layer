from datetime import timedelta
import hashlib
import hmac
import json

import jwt
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from sqlalchemy import select, func
from .db import Principal, State, AuditEvent, Outbox, now, transaction
from .settings import settings

LEVELS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def pseudonym(tenant, value):
    return hmac.new(settings()["audit_key"].encode(), f"{tenant}:{value}".encode(), hashlib.sha256).hexdigest()


def encrypt(value):
    return Fernet(settings()["encryption_key"].encode()).encrypt(canonical(value).encode()).decode()


def decrypt(value):
    return json.loads(Fernet(settings()["encryption_key"].encode()).decrypt(value.encode()))


def issue_token(principal, tenant, role, root_id=None, seconds=3600):
    cfg = settings()
    return jwt.encode({"sub": principal, "tenant": tenant, "role": role, "root_run_id": root_id,
        "iss": cfg["issuer"], "aud": cfg["audience"], "iat": now(), "nbf": now(),
        "exp": now() + timedelta(seconds=seconds)}, cfg["auth_key"], algorithm="HS256")


def identity(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not token:
        token = request.cookies.get("actiongate_session", "")
    cfg = settings()
    try:
        claims = jwt.decode(token, cfg["auth_key"], algorithms=["HS256"], audience=cfg["audience"],
            issuer=cfg["issuer"], options={"require": ["exp", "iat", "nbf", "sub", "tenant", "role"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "A valid ActionGate identity is required") from None
    with transaction() as db:
        principal = db.get(Principal, claims["sub"])
        if not principal or principal.revoked or principal.tenant != claims["tenant"] or principal.role != claims["role"]:
            raise HTTPException(403, "Identity is revoked or outside its assigned scope")
    if claims["role"] == "agent" and not claims.get("root_run_id"):
        raise HTTPException(403, "Workload identity must be bound to a root run")
    if claims["role"] == "agent":
        if not claims["sub"].startswith("workload:"):
            raise HTTPException(403, "Workload identity has no registered run binding")
        # Derive the exact scope from the authenticated principal, never from
        # a client-supplied run claim or request body. Roots remain the ledger key.
        claims["workload_run_id"] = claims["sub"].removeprefix("workload:")
    return claims


def require_role(actor, *roles):
    if actor["role"] not in roles:
        raise HTTPException(403, "This role cannot perform that operation")


def audit(db, tenant, event, evidence, run_id=None, operation_id=None, *, locked_state=None):
    """Append metadata; optionally reuse a State FOR UPDATE held by this caller."""
    if locked_state is None:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
    else:
        from sqlalchemy.orm import object_session
        if not isinstance(locked_state, State) or locked_state.id != 1 or object_session(locked_state) is not db:
            raise ValueError("Audit fence must belong to the current transaction")
        state = locked_state
    safe = {"tenant": tenant, "event": event, "run_id": run_id, "operation_id": operation_id,
            "evidence": evidence, "previous_hash": state.audit_head}
    row = AuditEvent(**safe, hash=digest(safe))
    db.add(row)
    db.flush()
    state.audit_head = row.hash
    # Transport provenance is outside the immutable AuditEvent/hash. Both
    # endpoints of server backlog timing use PostgreSQL's clock.
    db.add(Outbox(event_id=row.id, tenant=tenant, created_at=func.clock_timestamp(),
        payload={"id": row.id, **safe, "hash": row.hash, "_outbox_clock": "database"}))
    return row
