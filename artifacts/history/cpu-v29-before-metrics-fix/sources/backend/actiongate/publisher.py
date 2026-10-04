"""Isolated signing publisher. It has no database or connector execution access."""
from __future__ import annotations

import base64
import hmac
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI, Header, HTTPException
from pydantic import Field

from .controls import ControlError, ControlPlane, FeedRule, PolicyConfig, SignedFeed, canonical_json, digest, load_json, sign_document
from .controls.schema import StrictModel

app = FastAPI(title="ActionGate isolated publisher", docs_url=None, redoc_url=None)
_lock = threading.Lock()


def _key(kind: str) -> str:
    key = os.getenv(f"ACTIONGATE_{kind}_SIGNING_KEY", "")
    if not key:
        raise HTTPException(503, "Publisher signing key is unavailable")
    return key


def _authenticate(value: str | None):
    expected = os.getenv("ACTIONGATE_CONNECTOR_KEY", "")
    supplied = value.removeprefix("Bearer ") if value else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Publisher authentication required")


def _feed_file() -> Path:
    directory = Path(os.getenv("ACTIONGATE_PUBLISHER_DATA", "/data/publisher"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "feed.json"


def _save(path: Path, content: bytes):
    temporary = path.with_suffix(".staged")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _new_feed(rules: list[dict], previous_revision: int) -> dict:
    now = datetime.now(timezone.utc)
    payload = SignedFeed.model_validate({"schema_version": 1, "revision": previous_revision + 1,
        "publisher": "approved-threat-feed", "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(), "rules": rules}).model_dump()
    signed = sign_document(payload, _key("FEED"), "demo-feed-v1")
    _save(_feed_file(), canonical_json(signed))
    return signed


@app.get("/health/live")
def live():
    return {"status": "live", "role": "publisher", "executes_business_actions": False}


@app.get("/keys")
def public_keys():
    result = {}
    for kind in ("POLICY", "FEED"):
        private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(_key(kind), altchars=b"-_", validate=True))
        result[f"demo-{kind.lower()}-v1"] = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return {"keys": result, "trust": "Diagnostic only. Verifiers must anchor keys from bootstrap."}


@app.get("/feed")
def current_feed():
    with _lock:
        path = _feed_file()
        if path.exists():
            saved = load_json(path.read_bytes())
            parsed = SignedFeed.model_validate(saved["payload"])
            try:
                parsed.check_freshness()
                return saved
            except ControlError:
                # Renew only the previously approved rule data, and issue a new revision.
                return _new_feed([rule.model_dump() for rule in parsed.rules], parsed.revision)
        template = Path(os.getenv("ACTIONGATE_FEED_TEMPLATE", "/app/feeds/demo-rules.json"))
        rules = load_json(template.read_bytes())["rules"]
        return _new_feed(rules, 0)


class FeedRequest(StrictModel):
    rules: list[FeedRule] = Field(max_length=256)
    expected_revision: int = Field(ge=0)


@app.post("/feed")
def publish_feed(request: FeedRequest, authorization: str | None = Header(default=None)):
    _authenticate(authorization)
    with _lock:
        path = _feed_file()
        revision = load_json(path.read_bytes())["payload"]["revision"] if path.exists() else 0
        if revision != request.expected_revision:
            raise HTTPException(409, "Feed changed; refresh and retry with the current revision")
        try:
            return _new_feed([rule.model_dump() for rule in request.rules], revision)
        except ValueError as exc:
            raise HTTPException(422, "Invalid feed update") from exc


class PolicyRequest(StrictModel):
    payload: dict[str, Any]


class AuditCheckpointRequest(StrictModel):
    payload: dict[str, Any]
    records: list[dict[str, Any]] | None = Field(default=None, max_length=100_000)


@app.post("/audit/checkpoint")
def audit_checkpoint(request: AuditCheckpointRequest, authorization: str | None = Header(default=None)):
    _authenticate(authorization)
    from .audit_integrity import verify_records
    expected = {"kind", "last_event_id", "head", "generation", "first_previous_hash", "event_count", "events_digest"}
    payload = request.payload
    if set(payload) != expected or payload["kind"] != "actiongate-audit-checkpoint-v1":
        raise HTTPException(422, "Invalid checkpoint schema")
    if any(not isinstance(payload[key], str) or len(payload[key]) != 64 for key in ("head", "first_previous_hash", "events_digest")):
        raise HTTPException(422, "Invalid checkpoint digest")
    if any(type(payload[key]) is not int or payload[key] < 0 for key in ("last_event_id", "event_count", "generation")):
        raise HTTPException(422, "Invalid checkpoint counters")
    if request.records is not None:
        try:
            head, last_id = verify_records(request.records, payload["first_previous_hash"])
            if (digest(request.records) != payload["events_digest"] or len(request.records) != payload["event_count"]
                    or (request.records and (head != payload["head"] or last_id != payload["last_event_id"]))):
                raise ValueError("Archive differs from checkpoint")
        except (ValueError, KeyError):
            raise HTTPException(422, "Audit archive verification failed") from None
    with _lock:
        directory = _feed_file().parent / "audit"
        directory.mkdir(exist_ok=True)
        path = directory / (str(payload["last_event_id"]) + "-" + payload["head"][:16] + "-" + payload["events_digest"][:16] + ".json")
        envelope = sign_document(payload, _key("POLICY"), "demo-policy-v1")
        if request.records is not None or not path.exists():
            _save(path, canonical_json({"envelope": envelope, "records": request.records}))
        _save(directory / "latest.json", canonical_json(envelope))
    return {"envelope": envelope, "archive_id": path.name, "archive_persisted": request.records is not None,
            "storage": "independent publisher volume"}


class SourceRequest(StrictModel):
    yaml: str = Field(max_length=100_000)
    generation: int = Field(ge=1)


@app.post("/policy/source")
def persist_source(request: SourceRequest, authorization: str | None = Header(default=None)):
    _authenticate(authorization)
    from .controls import load_yaml
    PolicyConfig.model_validate(load_yaml(request.yaml))
    with _lock:
        revision_path = _feed_file().parent / "source-generation.json"
        previous = load_json(revision_path.read_bytes()) if revision_path.exists() else {"generation": 0}
        if request.generation < previous["generation"]:
            raise HTTPException(409, "Policy source rollback is forbidden")
        target = Path(os.getenv("POLICY_PATH", "/app/policy/control.yaml"))
        _save(target, request.yaml.encode("utf-8"))
        _save(revision_path, canonical_json({"generation": request.generation}))
        return {"generation": request.generation, "persisted": True}


@app.post("/policy/sign")
def publish_policy(request: PolicyRequest, authorization: str | None = Header(default=None)):
    _authenticate(authorization)
    payload = request.payload
    try:
        # Supports full snapshots and the policy-only form for staged API updates.
        config = PolicyConfig.model_validate(payload.get("policy", payload))
        if "generation" in payload:
            if type(payload["generation"]) is not int or payload["generation"] < 1:
                raise ControlError("Invalid generation")
            if "snapshot_digest" in payload:
                if digest({k: v for k, v in payload.items() if k != "snapshot_digest"}) != payload["snapshot_digest"]:
                    raise ControlError("Snapshot digest mismatch")
            # Complete snapshots must reference valid registries and a current feed.
            if "tools" in payload and "models" in payload:
                ControlPlane(Path(os.getenv("POLICY_PATH", "/app/policy/control.yaml")).parent,
                             config=config, feed=payload.get("feed"), tools=payload["tools"], models=payload["models"])
        return sign_document(payload, _key("POLICY"), "demo-policy-v1")
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, "Policy or control snapshot validation failed") from exc
