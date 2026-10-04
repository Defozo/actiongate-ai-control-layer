"""ActionGate HTTP API and same-origin operator dashboard."""
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
import asyncio
import csv
import io
import json
import logging
import math
import os
import time
import socket

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, Depends, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import select, func, text

from . import broker, policies, ledger
from .contracts import (SessionRequest, RunRequest, ActionRequest, ApprovalRequest, YamlRequest,
                        PlaygroundRequest, ScenarioRequest, TestRequest, ChatRequest)
from .db import (Principal, Run, RunContext, State, Operation, HumanApproval, PolicyGeneration,
                 AuditEvent, Outbox, TestRun, ConnectorReceipt, transaction, now, uid)
from .security import identity, require_role, issue_token, audit, decrypt, encrypt, LEVELS, digest
from .settings import settings, ROOT
from .seed import seed, SUPPLIERS

logger = logging.getLogger("actiongate")
REQUESTS = Counter("actiongate_http_requests", "Completed HTTP requests", ["method", "status"])
LATENCY = Histogram("actiongate_http_seconds", "HTTP request duration", buckets=(.005,.01,.025,.05,.1,.25,.5,1,5,30,120))
ready_error = "Services are starting"
initialization_done = False
tasks = set()


def background(coroutine):
    task = asyncio.create_task(coroutine)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def initialize():
    global ready_error, initialization_done
    for attempt in range(60):
        try:
            await policies.initialize()
            seed()
            from .recovery import recover_spool
            recover_spool()
            initialization_done, ready_error = True, ""
            background(policies.watch_configuration())
            background(policies.watch_feed())
            background(policies.watch_components())
            from .test_jobs import recover_jobs_forever
            background(recover_jobs_forever())
            return
        except Exception as exc:
            ready_error = f"Initialization pending: {type(exc).__name__}"
            logger.warning("Initialization attempt %s failed (%s)", attempt + 1, type(exc).__name__)
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    from .controls.opa import pooled_opa_clients
    async with pooled_opa_clients():
        background(initialize())
        async with session_manager.run():
            yield
        # Keep finalization tasks until they durably settle or mark unknown.
        if tasks:
            await asyncio.wait(tasks, timeout=20)


app = FastAPI(title="ActionGate", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def envelope(request: Request, call_next):
    started = time.monotonic()
    origin = request.headers.get("origin")
    if origin and origin not in settings()["origins"]:
        return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.cookies.get("actiongate_session") and not request.headers.get("authorization") and not origin:
            return JSONResponse({"detail": "Browser mutations require an Origin header"}, status_code=403)
        try:
            declared_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if declared_length < 0 or declared_length > 262144:
            return JSONResponse({"detail": "Request exceeds maximum bytes"}, status_code=413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 262144:
                return JSONResponse({"detail": "Request exceeds maximum bytes"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        if request.headers.get("content-encoding"):
            return JSONResponse({"detail": "Compressed requests are not accepted"}, status_code=415)
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error("Request failed (%s)", type(exc).__name__)
        response = JSONResponse({"detail": "Required service unavailable; no uninspected content was released"}, status_code=503)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith(("/api", "/v1", "/actions", "/runs", "/mcp")):
        response.headers["Cache-Control"] = "no-store"
    REQUESTS.labels(request.method, str(response.status_code)).inc()
    LATENCY.observe(time.monotonic()-started)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Nested locations and extra-field names are also attacker-controlled data.
    # Return only the fixed request scope and validator type, never raw keys.
    scopes = {"body", "query", "path", "header", "cookie"}
    return JSONResponse({"detail": [{"loc": [e["loc"][0] if e.get("loc") and e["loc"][0] in scopes else "request"],
                                     "type": e["type"], "msg": "Invalid request field"}
                                     for e in exc.errors()]}, status_code=422)


@app.get("/health/live")
def live():
    return {"status": "live", "instance": settings()["instance"]}


@app.get("/health/ready")
async def ready():
    checks = {}
    if not initialization_done:
        return JSONResponse({"status": "not_ready", "reason": ready_error}, status_code=503)
    try:
        with transaction() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ready"
        snap = policies.snapshot()
        policies.plane_for(snap)
        checks["policy"] = "ready"
        from .semantic import guard_artifact
        checks["classifier"] = "ready" if (policies.verify_stored_snapshot(snap).get("guard_artifact") == guard_artifact()
            and snap["configuration"]["semantic"]["prompt_version"] == guard_artifact()["prompt_version"]) else "generation_not_prepared"
        from .runtime import WorkerClient
        from .controls import OPAClient
        checks["opa"] = "ready" if await OPAClient(settings()["opa_url"]).ready(snap["generation"]) else "generation_not_prepared"
        worker_status = {}
        for worker in ("guard", "business"):
            result = await WorkerClient(worker).ready()
            worker_status[worker] = result
            checks[worker] = "ready" if snap["generation"] in result.get("prepared_generations", []) else "generation_not_prepared"
        from .controls.model_artifacts import verify_worker_artifacts
        await verify_worker_artifacts(policies.verify_stored_snapshot(snap), worker_status)
        if any(value != "ready" for value in checks.values()):
            return JSONResponse({"status": "not_ready", "generation": snap["generation"], "checks": checks}, status_code=503)
        return {"status": "ready", "generation": snap["generation"], "checks": checks}
    except Exception as exc:
        return JSONResponse({"status": "not_ready", "checks": checks, "reason": type(exc).__name__}, status_code=503)


@app.post("/api/demo/session")
def demo_session(body: SessionRequest, request: Request, response: Response):
    # Host and Origin are attacker-controlled. Trust the TCP peer on the edge
    # network, which is unreachable from the untrusted agent network.
    trusted_peers = {"127.0.0.1", "::1"}
    try:
        trusted_peers.update(socket.gethostbyname_ex("edge")[2])
    except OSError:
        pass
    if not request.client or request.client.host not in trusted_peers:
        raise HTTPException(403, "Demo identity issuance requires the local operator edge")
    if not settings()["demo_auth"] or request.url.hostname not in ("127.0.0.1", "localhost", "testserver"):
        raise HTTPException(403, "Demo identity issuance is restricted to the local installation")
    if not initialization_done:
        raise HTTPException(503, ready_error)
    principal_id = f"demo:{body.tenant}:{body.role}"
    with transaction() as db:
        if db.get(Principal, principal_id) is None:
            db.add(Principal(id=principal_id, tenant=body.tenant, role=body.role))
    response.set_cookie("actiongate_session", issue_token(principal_id, body.tenant, body.role),
                        httponly=True, samesite="strict", max_age=3600, secure=False)
    return {"id": principal_id, "role": body.role, "tenant": body.tenant, "mode": "local_demo"}


@app.get("/api/session")
def session(actor=Depends(identity)):
    return {"id": actor["sub"], "role": actor["role"], "tenant": actor["tenant"], "mode": "local_demo"}


@app.delete("/api/session")
def logout(response: Response):
    response.delete_cookie("actiongate_session")
    return {"signed_out": True}


def operator(actor):
    require_role(actor, "admin", "analyst", "approver", "manager")
    return actor


@app.post("/runs")
@app.post("/api/runs")
async def runs_create(body: RunRequest, actor=Depends(identity)):
    created = broker.create_run(body, actor)
    if body.public_document_ids:
        await public_projection(created["id"], actor)
    return created


@app.get("/api/runs")
def runs_list(actor=Depends(identity)):
    operator(actor)
    with transaction() as db:
        rows = db.scalars(select(Run).where(Run.tenant == actor["tenant"]).order_by(Run.created_at.desc()).limit(100))
        return {"runs": [{"id": x.id, "root_id": x.root_id, "purpose": x.purpose, "status": x.status,
                         "created_at": x.created_at.isoformat(), "label": LEVELS[db.get(RunContext, x.root_id).label]}
                        for x in rows]}


def event_json(event):
    return {"id": event.id, "tenant": event.tenant, "run_id": event.run_id, "operation_id": event.operation_id,
            "event": event.event, "evidence": event.evidence, "previous_hash": event.previous_hash,
            "hash": event.hash, "created_at": event.created_at.isoformat()}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str, actor=Depends(identity)):
    operator(actor)
    with transaction() as db:
        run = db.get(Run, run_id)
        if not run or run.tenant != actor["tenant"]:
            raise HTTPException(404, "Run not found")
        ctx = db.get(RunContext, run.root_id)
        return {"run": {"id": run.id, "purpose": run.purpose, "status": run.status,
                "tenant": run.tenant, "created_at": run.created_at.isoformat(),
                "root_id": run.root_id, "grant": run.grant, "label": LEVELS[ctx.label], "origins": ctx.origins,
                "label_version": ctx.label_version, "steps": ctx.steps, "tool_calls": ctx.tool_calls},
            "operations": [broker.serialize_operation(x, actor["role"] != "manager") for x in db.scalars(
                select(Operation).where(Operation.run_id == run_id).order_by(Operation.created_at))],
            "events": [event_json(x) for x in db.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.id))]}


@app.post("/actions")
@app.post("/api/actions")
async def action(body: ActionRequest, actor=Depends(identity)):
    return await asyncio.shield(background(broker.execute(body, actor)))


@app.get("/api/operations")
def operations(actor=Depends(identity)):
    operator(actor)
    with transaction() as db:
        return {"operations": [broker.serialize_operation(x, actor["role"] != "manager") for x in db.scalars(
            select(Operation).where(Operation.tenant == actor["tenant"]).order_by(Operation.created_at.desc()).limit(150))],
            "events": [event_json(x) for x in reversed(list(db.scalars(select(AuditEvent).where(
                AuditEvent.tenant == actor["tenant"]).order_by(AuditEvent.id.desc()).limit(150))))]}


@app.get("/actions/{operation_id}")
@app.get("/api/operations/{operation_id}")
def operation_detail(operation_id: str, actor=Depends(identity)):
    with transaction() as db:
        op = db.get(Operation, operation_id)
        if not op or op.tenant != actor["tenant"] or (actor["role"] == "agent" and
                (op.root_id != actor["root_run_id"] or actor["sub"] != "workload:" + op.run_id)):
            raise HTTPException(404, "Operation not found")
        result = broker.serialize_operation(op, actor["role"] != "manager")
        run = db.get(Run, op.run_id)
        generation = db.get(PolicyGeneration, op.policy_generation)
        result["purpose"] = run.purpose if run else None
        result["feed_version"] = generation.feed.get("revision") if generation else None
        result["events"] = [event_json(x) for x in db.scalars(select(AuditEvent).where(AuditEvent.operation_id == op.id).order_by(AuditEvent.id))]
        receipt = db.get(ConnectorReceipt, op.id)
        result["effect"] = {"recorded": receipt is not None, "receipt_id": op.id if receipt else None}
        return result


@app.post("/api/approvals/{operation_id}")
async def approve(operation_id: str, body: ApprovalRequest, actor=Depends(identity)):
    require_role(actor, "approver", "admin")
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        op = db.get(Operation, operation_id)
        if not op or op.tenant != actor["tenant"]:
            raise HTTPException(404, "Operation not found")
        if op.status != "waiting_approval" or op.payload_hash != body.payload_hash:
            raise HTTPException(409, "Approval must match the exact waiting operation")
        if db.get(HumanApproval, op.id):
            raise HTTPException(409, "This operation already has an approval decision")
        db.add(HumanApproval(operation_id=op.id, tenant=op.tenant, approver=actor["sub"], payload_hash=op.payload_hash,
               approved=body.approved, generation=state.generation,
               expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5)))
        audit(db, op.tenant, "approval.recorded", {"approved": body.approved, "payload_hash": op.payload_hash}, op.run_id, op.id)
        if not body.approved:
            op.status, op.decision, op.reason = "cancelled", "block", "Approver declined the exact operation"
            return broker.serialize_operation(op)
        principal = db.get(Principal, op.actor)
        original = {"sub": op.actor, "tenant": op.tenant, "role": principal.role, "root_run_id": op.root_id}
        req = ActionRequest(run_id=op.run_id, tool=op.tool, arguments=decrypt(op.encrypted_payload), idempotency_key=op.idempotency_key)
    return await asyncio.shield(background(broker.execute(req, original, existing_id=operation_id)))


@app.get("/api/policies")
async def policy_read(actor=Depends(identity)):
    operator(actor)
    snap = policies.snapshot()
    with transaction() as db:
        history = [{"generation": x.id, "status": x.status, "digest": x.digest, "created_at": x.created_at.isoformat()}
                   for x in db.scalars(select(PolicyGeneration).order_by(PolicyGeneration.id.desc()).limit(50))]
    urls = os.getenv("GATEWAY_REPLICA_URLS", "http://gateway-a:8000,http://gateway-b:8000").split(",")
    async def inspect(url, index):
        try:
            async with httpx.AsyncClient(timeout=6, trust_env=False, follow_redirects=False) as client:
                response = await client.get(url+"/health/ready")
                data = response.json()
            return {"id": f"gateway-{index}", "name": f"Gateway {index}", "generation": data.get("generation"),
                    "status": "ready" if response.status_code == 200 and data.get("generation") == snap["generation"] else "unavailable",
                    "checks": data.get("checks", {})}
        except Exception:
            return {"id": f"gateway-{index}", "name": f"Gateway {index}", "generation": None, "status": "unavailable"}
    replicas = await asyncio.gather(*(inspect(url, index+1) for index, url in enumerate(urls)))
    return {**snap, "history": history, "controls": control_status(snap), "publication": policies.publication_state,
            "replicas": replicas, "component_recovery": policies.component_state}


@app.post("/api/policies/validate")
async def policy_validate(body: YamlRequest, actor=Depends(identity)):
    require_role(actor, "admin")
    try:
        cfg = policies.validate_yaml(body.yaml)
        return {"valid": True, "diff": policies.diff(body.yaml), "configuration": cfg}
    except Exception as exc:
        return {"valid": False, "errors": [{"type": type(exc).__name__,
            "message": "Policy validation failed. Check the schema, registered resources and signed dependency status."}]}


@app.post("/api/policies/activate")
async def policy_activate(body: YamlRequest, actor=Depends(identity)):
    require_role(actor, "admin")
    try:
        return await policies.activate(body.yaml, actor["tenant"])
    except Exception as exc:
        raise HTTPException(422, f"Policy was not activated: {type(exc).__name__}") from None


@app.post("/api/policies/compare")
def policy_compare(body: YamlRequest, actor=Depends(identity)):
    require_role(actor, "admin")
    candidate = policies.validate_yaml(body.yaml)
    with transaction() as db:
        rows = list(db.scalars(select(Operation).where(Operation.tenant == actor["tenant"]).limit(200)))
        comparisons = []
        for op in rows:
            tool_allowed = op.tool in candidate["tools"]["allowed"]
            comparisons.append({"operation_id": op.id, "before": op.decision,
                "after": "block" if not tool_allowed else "insufficient_evidence",
                "reason": "Tool removed from allowlist" if not tool_allowed else "Raw content was not retained for DLP or semantic reevaluation"})
        return {"comparisons": comparisons, "coverage": {"evaluated": sum(x["after"] != "insufficient_evidence" for x in comparisons),
                "total": len(comparisons)}, "changed": sum(x["after"] != "insufficient_evidence" and x["before"] != x["after"] for x in comparisons),
                "effects_executed": 0, "diff": policies.diff(body.yaml)}


@app.get("/api/feed")
def feed_read(actor=Depends(identity)):
    operator(actor)
    snap = policies.snapshot(require_fresh=False)
    return {**snap["feed"], "availability": policies.feed_state, "signature_verified": True,
            "signature_status": "Verified in signed control generation", "generation": snap["generation"]}


@app.post("/api/feed/publish")
async def feed_publish(body: dict, actor=Depends(identity)):
    require_role(actor, "admin")
    if set(body) != {"rules"}:
        raise HTTPException(422, "Only feed rules may be edited")
    old = policies.snapshot()
    try:
        envelope = await policies.publisher_call("POST", "/feed", {**body, "expected_revision": old["feed"].get("revision", 0)})
        feed = await policies.verified_feed(envelope, old["feed"].get("revision", 0))
        result = await policies.activate(old["yaml"], actor["tenant"], feed=feed, expected_generation=old["generation"])
        return {**result, "feed": feed}
    except Exception as exc:
        raise HTTPException(422, f"Feed update was not activated: {type(exc).__name__}") from None


def pricing_provenance():
    """Present only the catalog embedded in the verified active generation."""
    try:
        snap = policies.snapshot(require_fresh=False)
        payload = policies.verify_stored_snapshot(snap, require_fresh=False)
    except Exception:
        return {"status": "unavailable", "signature_verified": False, "catalog": None,
                "note": "The active signed catalog could not be verified."}
    catalog = payload.get("prices")
    result = {"status": "absent", "signature_verified": True, "catalog": catalog,
              "generation": snap["generation"], "policy_digest": snap["digest"],
              "cloud_enabled": snap["configuration"]["models"]["cloud_enabled"],
              "note": "Current catalog only. Historical usage retains its recorded price revision and amount."}
    if catalog is not None:
        from .cloud import load_price
        try:
            load_price(catalog)
            result["status"] = "valid"
        except ValueError:
            result["status"] = "invalid_or_expired"
    return result


@app.get("/api/budgets")
async def budgets(actor=Depends(identity)):
    operator(actor)
    from .runtime import WorkerClient
    from .db import Reservation
    workers = {}
    for role in ("guard", "business"):
        try:
            workers[role] = await WorkerClient(role).status()
        except Exception:
            workers[role] = {"status": "unavailable"}
    with transaction() as db:
        reservations = [{"id": r.id, "operation_id": r.operation_id, "kind": r.kind, "status": r.status,
             "accounts": r.accounts, "usage": r.usage, "created_at": r.created_at.isoformat(),
             "price_revision": op.metadata_.get("price_revision") if op and r.kind == "execution" else None,
             "policy_generation": op.policy_generation if op else None,
             "execution_mode": op.metadata_.get("execution_mode") if op else None} for r, op in db.execute(
            select(Reservation, Operation).outerjoin(Operation, (Operation.id == Reservation.operation_id) &
                (Operation.tenant == Reservation.tenant)).where(Reservation.tenant == actor["tenant"])
                .order_by(Reservation.created_at.desc()).limit(100))]
    return {"accounts": ledger.balances(actor["tenant"]), "currency": "USD", "local_cost_mode": "measured tokens and slot seconds",
            "simulated_cost": False, "workers": workers, "reservations": reservations,
            "pricing": await asyncio.to_thread(pricing_provenance)}


@app.post("/api/kill-switch")
def kill_switch(body: dict, actor=Depends(identity)):
    require_role(actor, "admin")
    if set(body) != {"enabled"} or type(body["enabled"]) is not bool:
        raise HTTPException(422, "Expected enabled boolean")
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        state.kill_switch = body["enabled"]
        state.revocation_epoch += 1
        audit(db, actor["tenant"], "platform.kill_switch", {"enabled": state.kill_switch, "epoch": state.revocation_epoch})
    return body


def control_status(snap):
    return [{"id": name, "name": name.replace("_", " ").title(), "status": "enabled" if value["enabled"] else "disabled",
             "reason": "Configured in generation " + str(snap["generation"])}
            for name, value in snap["configuration"]["controls"].items()]


@app.get("/api/overview")
async def overview(actor=Depends(identity)):
    operator(actor)
    snap = policies.snapshot()
    with transaction() as db:
        counts = dict(db.execute(select(Operation.decision, func.count()).where(Operation.tenant == actor["tenant"]).group_by(Operation.decision)).all())
        recent = list(db.scalars(select(Operation).where(Operation.tenant == actor["tenant"]).order_by(Operation.created_at.desc()).limit(12)))
        completed = db.scalar(select(func.count()).select_from(Run).where(Run.tenant == actor["tenant"], Run.status == "completed"))
        rows = [broker.serialize_operation(x, False) for x in recent]
        statuses = dict(db.execute(select(Operation.status, func.count()).where(Operation.tenant == actor["tenant"])
            .group_by(Operation.status)).all())
        observations = list(db.scalars(select(Operation.metadata_).where(Operation.tenant == actor["tenant"],
            Operation.status == "completed").order_by(Operation.created_at.desc()).limit(200)))
    samples, modes = [], {}
    for observation in observations:
        value = observation.get("latency_ms")
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            samples.append(value)
            mode = observation.get("execution_mode", "not_recorded")
            modes[mode] = modes.get(mode, 0) + 1
    samples.sort()
    latency = {"p95_ms": samples[math.ceil(.95 * len(samples))-1] if samples else None,
        "sample_count": len(samples), "considered_count": len(observations), "maximum_records": 200,
        "execution_modes": modes, "scope": "Latest completed operations in this tenant with recorded latency; operational observations, not a benchmark."}
    account_rows = ledger.balances(actor["tenant"])
    totals = [x for x in account_rows if x["scope"] == "tenant" and x["unit"] == "usd_micros"]
    readiness = await ready()
    if isinstance(readiness, JSONResponse):
        readiness = json.loads(readiness.body)
    return {"counts": {key: counts.get(key, 0) for key in ("allow", "redact", "block", "require_approval")},
            "controls": control_status(snap), "recent_operations": rows, "generation": snap["generation"],
            "feed": snap["feed"], "completed_runs": completed, "spent_usd_micros": sum(x["spent"] for x in totals),
            "reserved_usd_micros": sum(x["reserved"] for x in totals), "kill_switch": snap["kill_switch"],
            "latency": latency, "execution_outcomes": {"total_operations": sum(statuses.values()), "statuses": statuses,
                "scope": "All retained operations in this tenant; policy blocks are separate from execution failures."},
            "mode": "all-local", "services": readiness, "profile": snap["configuration"]["active_profile"]}


@app.get("/api/events")
async def events(request: Request, actor=Depends(identity)):
    operator(actor)
    raw_cursor = request.headers.get("last-event-id", request.query_params.get("cursor"))
    initial_tail = raw_cursor is None and request.query_params.get("tail") == "true"
    try:
        cursor = max(0, int(raw_cursor or "0"))
    except ValueError:
        raise HTTPException(422, "Invalid event cursor") from None
    if initial_tail:
        with transaction() as db:
            cursor = db.scalar(select(func.max(Outbox.id)).where(Outbox.tenant == actor["tenant"])) or 0
    async def stream():
        nonlocal cursor
        if initial_tail:
            # A ready-triggered snapshot refresh closes the race between initial
            # dashboard queries and the cursor selected above. Reconnect always
            # honors Last-Event-ID and replays every event after that cursor.
            yield f"id: {cursor}\nevent: ready\ndata: {json.dumps({'cursor': cursor})}\n\n"
        while not await request.is_disconnected():
            if actor["exp"] <= time.time():
                break
            with transaction() as db:
                principal = db.get(Principal, actor["sub"])
                if not principal or principal.revoked or principal.role != actor["role"] or principal.tenant != actor["tenant"]:
                    break
                rows = list(db.scalars(select(Outbox).where(Outbox.tenant == actor["tenant"], Outbox.id > cursor).order_by(Outbox.id).limit(100)))
                observed_at = db.scalar(select(func.clock_timestamp())) if rows else None
            for row in rows:
                cursor = row.id
                verified_clock = row.payload.get("_outbox_clock") == "database"
                age_ms = (observed_at-row.created_at).total_seconds()*1000 if verified_clock else None
                delivered = {**row.payload, "_delivery": {"server_backlog_age_ms": round(age_ms, 3) if age_ms is not None and age_ms >= 0 else None,
                    "clock": "database" if verified_clock else "unverified",
                    "scope": "Outbox creation to server dispatch; excludes network and browser rendering."}}
                yield f"id: {cursor}\nevent: audit\ndata: {json.dumps(delivered)}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(.5)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/exports/audit.jsonl")
def audit_export(actor=Depends(identity)):
    require_role(actor, "admin", "analyst")
    with transaction() as db:
        lines = [json.dumps(event_json(e), ensure_ascii=False) for e in db.scalars(select(AuditEvent).where(AuditEvent.tenant == actor["tenant"]).order_by(AuditEvent.id))]
    return Response("\n".join(lines)+"\n", media_type="application/x-ndjson", headers={"Content-Disposition": "attachment; filename=actiongate-audit.jsonl"})


@app.post("/api/audit/checkpoint")
async def audit_checkpoint(actor=Depends(identity)):
    require_role(actor, "admin")
    from .audit_integrity import checkpoint
    return await checkpoint()


@app.post("/api/demo/reset")
def demo_reset(actor=Depends(identity)):
    require_role(actor, "admin")
    if actor["tenant"] != "synthetic_test_tenant" or not settings()["demo_auth"]:
        raise HTTPException(403, "Reset is restricted to the synthetic test tenant")
    from sqlalchemy import delete, update
    from .db import DataObject
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        state.revocation_epoch += 1
        changed = db.execute(update(Run).where(Run.tenant == "synthetic_test_tenant", Run.status == "active").values(status="revoked")).rowcount
        removed = db.execute(delete(DataObject).where(DataObject.tenant == "synthetic_test_tenant")).rowcount
        audit(db, actor["tenant"], "demo.reset", {"revoked_runs": changed, "removed_objects": removed,
            "financial_obligations_preserved": True, "history_preserved": True})
    seed()
    return {"tenant": "synthetic_test_tenant", "reset": True, "revoked_runs": changed,
            "financial_obligations_preserved": True, "history_preserved": True}


def csv_safe(value):
    value = str(value)
    return "'"+value if value.lstrip().startswith(("=", "+", "-", "@")) else value


@app.get("/api/exports/management.csv")
def management_export(actor=Depends(identity)):
    operator(actor)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["scope", "unit", "period", "limit", "spent", "reserved", "available", "overcommitted"])
    for row in ledger.balances(actor["tenant"]):
        writer.writerow([csv_safe(row[k]) for k in ("scope", "unit", "period", "limit", "spent", "reserved", "available", "overcommitted")])
    return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=actiongate-management.csv"})


@app.get("/api/metrics")
def metrics(actor=Depends(identity)):
    require_role(actor, "admin")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/operations/{operation_id}/reconcile")
def reconcile_operation(operation_id: str, actor=Depends(identity)):
    require_role(actor, "admin")
    from .recovery import reconcile
    return reconcile(operation_id, actor["tenant"])


@app.post("/api/runs/{run_id}/revoke")
def revoke_run(run_id: str, actor=Depends(identity)):
    require_role(actor, "admin")
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        run = broker.owned_run(db, run_id, actor)
        for child in db.scalars(select(Run).where(Run.root_id == run.root_id)):
            child.grant = {**child.grant, "revoked": True}
            child.status = "revoked"
        state.revocation_epoch += 1
        audit(db, run.tenant, "run.revoked", {"epoch": state.revocation_epoch}, run.id)
    return {"run_id": run_id, "status": "revoked"}


@app.post("/v1/chat/completions")
async def chat(body: ChatRequest, request: Request, actor=Depends(identity)):
    run_id = request.headers.get("x-actiongate-run-id") or actor.get("workload_run_id") or actor.get("root_run_id")
    if not run_id:
        raise HTTPException(422, "Create a workflow and supply its workload token or X-ActionGate-Run-Id")
    args = {"model": body.model, "messages": [m.model_dump(exclude_none=True) for m in body.messages], "max_tokens": body.max_tokens}
    if body.tools is not None:
        args["tools"] = body.tools
    if body.tool_choice is not None:
        args["tool_choice"] = body.tool_choice
    result = await asyncio.shield(background(broker.execute(ActionRequest(run_id=run_id, tool="models.chat", arguments=args,
        idempotency_key=request.headers.get("idempotency-key", uid())), actor)))
    if result["status"] != "completed":
        return JSONResponse({"error": {"message": result["reason"], "type": "actiongate_block", "code": result["status"]}, "actiongate": result}, status_code=403)
    answer = result["result"]
    answer["actiongate"] = {"operation_id": result["id"], "decision": result["decision"], "policy_generation": result["policy_generation"]}
    if not body.stream:
        return answer
    async def buffered():
        message = answer["choices"][0]["message"]
        chunk = {"id": answer.get("id", result["id"]), "object": "chat.completion.chunk", "model": body.model,
                 "choices": [{"index": 0, "delta": message, "finish_reason": None}]}
        yield "data: " + json.dumps(chunk) + "\n\n"
        chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": answer["choices"][0].get("finish_reason", "stop")}]
        yield "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
    return StreamingResponse(buffered(), media_type="text/event-stream", headers={"X-ActionGate-Buffering": "full-inspection"})


async def public_projection(run_id, actor):
    # Fixed fields from the authoritative registry, chosen before confidential I/O.
    require_role(actor, "admin", "analyst")
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        run = broker.owned_run(db, run_id, actor)
        ctx = db.get(RunContext, run.root_id)
        from .db import BudgetAccount
        budget = db.get(BudgetAccount, f"public:{run.root_id}:operations")
        if broker.remaining_run_seconds(db, run, policies.snapshot()["configuration"]) <= 0:
            raise HTTPException(403, "Workflow tree deadline has elapsed")
        if (state.kill_switch or run.status != "active" or run.grant.get("revoked")
                or not run.grant.get("public_document_ids") or not budget or budget.spent + budget.reserved >= budget.limit):
            raise HTTPException(403, "No available approved public projection grant")
        if ctx.label != 0 or ctx.steps or run.grant.get("public_projection_complete"):
            raise HTTPException(409, "Public projection must precede confidential work exactly once")
        records = [{"document_id": doc, **SUPPLIERS[run.tenant]} for doc in run.grant["public_document_ids"]]
        receipt_id = uid()
        result = {"recipient": "public_demo_sink", "function": "supplier_public_view_v1", "records": records, "label": "PUBLIC"}
        db.add(ConnectorReceipt(operation_id=receipt_id, tenant=run.tenant, tool="supplier_public_view_v1", recipient="public_demo_sink",
             payload_hash=digest(result), encrypted_result=encrypt(result)))
        run.grant = {**run.grant, "public_projection_complete": True, "public_receipt_id": receipt_id}
        budget.spent += 1
        audit(db, run.tenant, "projection.published", {"receipt_id": receipt_id, "function": "supplier_public_view_v1", "record_count": len(records),
              "budget_scope": "public_projection", "publications_spent": 1, "generation": state.generation}, run.id)
        return result


@app.post("/api/playground")
async def playground(body: PlaygroundRequest, actor=Depends(identity)):
    require_role(actor, "admin", "analyst")
    snap = policies.snapshot()
    if body.profile and body.profile != snap["configuration"]["active_profile"]:
        raise HTTPException(409, "Activate that profile in Policies first; playground uses the active generation")
    run = broker.create_run(RunRequest(document_ids=[]), actor)
    return await asyncio.shield(background(broker.execute(ActionRequest(run_id=run["id"], tool="reports.save",
        arguments={"content": body.text}, idempotency_key=uid()), actor)))


@app.post("/api/demo/workflow")
async def demo_workflow(body: ScenarioRequest, actor=Depends(identity)):
    require_role(actor, "admin", "analyst")
    from .scenarios import workflow
    return await asyncio.shield(background(workflow(body.scenario, actor)))


@app.post("/api/tests")
async def tests_start(body: TestRequest, actor=Depends(identity)):
    require_role(actor, "admin", "analyst")
    from .test_jobs import create_job, run_owned
    test_id = create_job(actor["tenant"], body.suite, policies.snapshot()["configuration"])
    from .scenarios import run_lab
    background(run_owned(test_id, run_lab, body.suite))
    return {"id": test_id, "suite": body.suite, "status": "running", "results": []}


@app.get("/api/tests")
def tests_list(actor=Depends(identity)):
    operator(actor)
    with transaction() as db:
        from .test_jobs import recover_expired
        recover_expired(db, actor["tenant"])
        return {"runs": [{"id": r.id, "suite": r.suite, "status": r.status, "results": r.results,
                          "passed": sum(x.get("passed", False) for x in r.results),
                          "failed": sum(not x.get("passed", False) and x.get("assessment") != "advisory" for x in r.results),
                          "advisory": sum(x.get("assessment") == "advisory" for x in r.results), "total": len(r.results),
                          "created_at": r.created_at.isoformat(),
                          "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
                          "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None}
                         for r in db.scalars(select(TestRun).where(TestRun.tenant == actor["tenant"]).order_by(TestRun.created_at.desc()).limit(30))]}


from .mcp_gateway import create_mcp_gateway
from .replay import router as replay_router
app.include_router(replay_router)
mcp_application, session_manager = create_mcp_gateway()
app.mount("/mcp", mcp_application)

ui_path = ROOT / "ui" / "dist"
if ui_path.exists():
    app.mount("/assets", StaticFiles(directory=ui_path / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "v1/", "health/")):
            raise HTTPException(404, "Endpoint not found")
        return FileResponse(ui_path / "index.html")
