from datetime import timedelta
from contextvars import copy_context
from functools import partial
import asyncio
import json
import math
import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, func
from . import policies, ledger
from .contracts import RunRequest, ActionRequest
from .db import (Run, RunContext, DataObject, Operation, ExecutionGrant, HumanApproval,
                 State, Principal, Reservation, transaction, execution_boundary, now, uid)
from .security import encrypt, decrypt, digest, canonical, audit, pseudonym, LEVELS, issue_token, require_role

# Evidence provenance only; this flag never grants authority or changes controls.
EXECUTION_MODE = "local"
_executions = set()


async def _offload(work, *args, **kwargs):
    """A cancelled request cannot leave a synchronous DB phase still running."""
    # Equivalent to to_thread's context propagation, but keep the executor
    # Future rather than a child Task that global shutdown cancellation could
    # cancel while its underlying thread is still using the database.
    task = asyncio.get_running_loop().run_in_executor(None, copy_context().run, partial(work, *args, **kwargs))
    cancellation = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            # Every drain wait must remain shielded: shutdown or a second
            # disconnect cancellation must not cancel the worker wrapper.
            cancellation = exc
            if task.cancelled():
                raise
        except Exception:
            if cancellation is not None:
                raise cancellation
            raise
    if cancellation is not None:
        raise cancellation
    return result


async def _transaction_phase(work):
    """Run an entire synchronous transaction away from the event loop.

    The Session is created, used and closed in one worker invocation. Returned
    ORM values are detached snapshots; a later phase always reads current rows.
    The offload helper preserves the request's telemetry and test-job context.
    """
    def invoke():
        with transaction() as db:
            return work(db)
    return await _offload(invoke)


def offered_model_tools(args):
    """Keep declared schemas in inspected evidence; honor none at the adapter."""
    return None if args.get("tool_choice") == "none" else args.get("tools")


class _LocalWriteReceipt(dict):
    """Provenance for identifiers created by our storage code, never by JSON input.

    A randomly generated UUID can contain a valid phone-number substring. Its
    exact storage reference is not content to redact. Caller-selected memory
    keys and every other field remain subject to the ordinary content scanner.
    The complete original receipt still passes semantic and release controls.
    """
    def __init__(self, object_id, key, label, *, generated_key):
        super().__init__(id=object_id, key=key, label=label, saved=True)
        self.generated_identifiers = {"id": object_id}
        if generated_key:
            self.generated_identifiers["key"] = key


class Denied(Exception):
    def __init__(self, rule, reason, stage="preflight"):
        self.rules = [rule] if isinstance(rule, str) else list(rule)
        if not self.rules or any(not isinstance(item, str) or not item for item in self.rules):
            raise ValueError("A denial requires nonempty rule identifiers")
        self.rule, self.reason, self.stage = self.rules[0], reason, stage
        super().__init__(reason)


def owned_run(db, run_id, actor):
    run = db.get(Run, run_id)
    if not run or run.tenant != actor["tenant"]:
        raise HTTPException(404, "Run not found")
    if actor["role"] == "agent" and (actor.get("root_run_id") != run.root_id
            or actor["sub"] != "workload:" + run.id):
        raise HTTPException(403, "Workload identity is bound to its exact delegated run")
    if actor["role"] not in ("agent", "admin", "analyst", "approver"):
        raise HTTPException(403, "This role cannot execute a workflow")
    return run


def object_for(db, tenant, name, kind=None):
    query = select(DataObject).where(DataObject.tenant == tenant, DataObject.name == name)
    if kind:
        query = query.where(DataObject.kind == kind)
    obj = db.execute(query).scalar_one_or_none()
    if not obj or obj.expires_at <= now():
        raise Denied("acl.resource", "Resource is unavailable within this tenant and grant", "authorization")
    return obj


def remaining_run_seconds(db, run, cfg):
    root = db.get(Run, run.root_id)
    return cfg["budgets"]["run_deadline_seconds"] - (db.scalar(select(func.clock_timestamp())) - root.created_at).total_seconds()


def memory_write_target(db, run, role, key):
    obj = db.scalar(select(DataObject).where(DataObject.tenant == run.tenant, DataObject.name == key))
    if obj is not None and (obj.kind != "memory" or obj.expires_at <= now()
            or obj.purpose != run.purpose or role not in obj.acl):
        raise Denied("acl.memory_write", "Existing object does not permit this memory write", "authorization")
    return obj


def join_label(ctx, label, origins, compartments=None):
    new_origins = sorted(set(ctx.origins) | set(origins))
    new_compartments = sorted(set(ctx.compartments) | set(compartments or []))
    if label > ctx.label or new_origins != ctx.origins or new_compartments != ctx.compartments:
        ctx.label = max(ctx.label, label)
        ctx.origins = new_origins
        ctx.compartments = new_compartments
        ctx.label_version += 1


def create_run(request: RunRequest, actor):
    require_role(actor, "admin", "analyst", "agent")
    snap = policies.snapshot()
    cfg = snap["configuration"]
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        if request.parent_id:
            parent = owned_run(db, request.parent_id, actor)
            root = db.get(Run, parent.root_id)
            if (db.scalar(select(func.now())) - root.created_at).total_seconds() > cfg["budgets"]["run_deadline_seconds"]:
                raise HTTPException(403, "Workflow tree deadline has elapsed")
            root = db.get(Run, parent.root_id)
            if (parent.status != "active" or parent.grant.get("revoked") or not root
                    or root.status != "active" or root.grant.get("revoked")):
                raise HTTPException(403, "A revoked workflow cannot create a new delegation")
            delegated = db.scalar(select(func.count()).select_from(Run).where(Run.root_id == root.id, Run.id != root.id))
            if delegated >= cfg["budgets"]["max_steps"]:
                raise HTTPException(403, "Root workflow delegation count exhausted")
            if parent.depth >= cfg["budgets"]["max_delegation_depth"]:
                raise HTTPException(403, "Delegation depth exhausted")
            requested_tools = request.tools if request.tools is not None else parent.grant["tools"]
            if not set(requested_tools) <= set(parent.grant["tools"]) or not set(request.document_ids) <= set(parent.grant["document_ids"]):
                raise HTTPException(403, "Delegation can only narrow the parent grant")
            if request.allow_publish and not parent.grant["allow_publish"]:
                raise HTTPException(403, "Delegation cannot add publication authority")
            if request.public_document_ids or request.public_model_task:
                raise HTTPException(403, "Public projection cannot be delegated by a confidential workflow")
            root_id, depth = parent.root_id, parent.depth + 1
            tools = requested_tools
        else:
            if actor["role"] not in ("admin", "analyst"):
                raise HTTPException(403, "Only a trusted application identity can create a new root")
            root_id, depth = uid(), 0
            tools = request.tools if request.tools is not None else cfg["tools"]["allowed"]
            if not set(tools) <= set(cfg["tools"]["allowed"]):
                raise HTTPException(403, "Requested tool is outside the current policy")
        for document_id in set(request.document_ids + request.public_document_ids):
            try:
                object_for(db, actor["tenant"], document_id, "document")
            except Denied:
                raise HTTPException(403, "Workflow contains a resource outside the tenant") from None
        run_id = uid() if request.parent_id else root_id
        grant = {"tools": tools, "document_ids": request.document_ids, "allow_publish": request.allow_publish,
                 "recipients": ["internal_demo_sink"] if request.allow_publish else [],
                 "public_document_ids": request.public_document_ids, "revoked": False}
        if request.public_model_task:
            from .seed import SUPPLIERS
            if request.document_ids or request.allow_publish or request.parent_id:
                raise HTTPException(422, "A public model task cannot carry confidential resource grants")
            grant["tools"] = ["models.chat"]
            grant["public_messages"] = [
                {"role": "system", "content": "Summarize this approved public supplier directory in one sentence."},
                {"role": "user", "content": canonical(SUPPLIERS[actor["tenant"]])}]
        run = Run(id=run_id, root_id=root_id, parent_id=request.parent_id, tenant=actor["tenant"],
                  actor=actor["sub"], purpose=request.purpose, grant=grant, depth=depth, created_at=db.scalar(select(func.now())))
        db.add(run)
        if not request.parent_id:
            db.add(RunContext(root_id=root_id, tenant=actor["tenant"], label=0))
        if request.public_document_ids:
            from .db import BudgetAccount
            db.add(BudgetAccount(id=f"public:{root_id}:operations", tenant=actor["tenant"], scope="public_projection",
                unit="operations", limit=1, spent=0, reserved=0, period="fixed-approved-request"))
        workload = "workload:" + run_id
        db.add(Principal(id=workload, tenant=actor["tenant"], role="agent"))
        db.flush()
        audit(db, actor["tenant"], "run.created", {"purpose": request.purpose, "depth": depth,
              "root_id": root_id, "actor": pseudonym(actor["tenant"], actor["sub"])}, run_id, locked_state=state)
        return {"id": run_id, "root_id": root_id, "purpose": run.purpose, "status": run.status,
                "workload_token": issue_token(workload, actor["tenant"], "agent", root_id),
                "public_projection_pending": bool(request.public_document_ids),
                "public_messages": grant.get("public_messages")}


def serialize_operation(op, include_payload=True):
    result = {"id": op.id, "tenant": op.tenant, "run_id": op.run_id, "root_id": op.root_id,
              "tool": op.tool, "payload_hash": op.payload_hash, "decision": op.decision,
              "status": op.status, "settlement_status": op.settlement_status, "rule_ids": op.rule_ids,
              "reason": op.reason, "stage": op.stage, "label": LEVELS[op.label],
              "label_version": op.label_version, "policy_generation": op.policy_generation,
              "created_at": op.created_at.isoformat(), "metadata": op.metadata_}
    if include_payload:
        result["arguments"] = decrypt(op.encrypted_payload) if op.encrypted_payload else None
        result["result"] = decrypt(op.encrypted_result) if op.encrypted_result and op.status == "completed" else None
    return result


def _preflight(db, op, run, ctx, args, actor, cfg):
    if ctx.publication_uncertain:
        raise Denied("fence.uncertain", "Workflow requires reconciliation before further actions")
    if run.status != "active" or run.grant.get("revoked"):
        raise Denied("grant.revoked", "Workflow grant is not active")
    if (db.scalar(select(func.now())) - db.get(Run, run.root_id).created_at).total_seconds() > cfg["budgets"]["run_deadline_seconds"]:
        raise Denied("budget.deadline", "Workflow deadline has elapsed")
    if ctx.steps >= cfg["budgets"]["max_steps"]:
        raise Denied("budget.steps", "Workflow step limit reached")
    if ctx.tool_calls >= cfg["budgets"]["max_tool_calls"] and op.tool != "models.chat":
        raise Denied("budget.tool_calls", "Workflow tool call limit reached")
    if op.tool not in run.grant["tools"]:
        raise Denied("grant.tool", "Tool is not permitted by the workflow grant", "authorization")
    if op.tool not in cfg["tools"]["allowed"]:
        raise Denied("policy.tool", "Tool is not enabled in the active policy")
    if run.grant.get("public_document_ids") and not run.grant.get("public_projection_complete"):
        raise Denied("flow.public_first", "Trusted public projection must complete before confidential work")
    if op.tool == "documents.read":
        if args["document_id"] not in run.grant["document_ids"]:
            raise Denied("acl.document", "Document is outside this workflow grant", "authorization")
        obj = object_for(db, run.tenant, args["document_id"], "document")
        if actor["role"] not in obj.acl or obj.purpose != run.purpose:
            raise Denied("acl.object", "Object ACL or purpose denies this read")
        join_label(ctx, obj.label, obj.origins, obj.compartments)
    elif op.tool == "memory.read":
        obj = object_for(db, run.tenant, args["key"], "memory")
        if obj.purpose != run.purpose or actor["role"] not in obj.acl:
            raise Denied("acl.memory", "Memory is outside this actor's permitted purpose")
        join_label(ctx, obj.label, obj.origins, obj.compartments)
    else:
        if op.tool == "memory.write":
            existing = memory_write_target(db, run, actor["role"], args["key"])
            if existing is not None:
                join_label(ctx, existing.label, existing.origins, existing.compartments)
        # Free text is never evidence of a public source, even for a fresh root.
        approved_public = (op.tool == "models.chat" and run.grant.get("public_messages") == args.get("messages")
                           and bool(run.grant.get("public_messages")) and not args.get("tools"))
        if any(key in args for key in ("content", "messages", "text")) and not approved_public:
            join_label(ctx, 2, ["untrusted_prompt"])
    if op.tool == "reports.publish_demo":
        recipient = args["recipient"]
        if not run.grant["allow_publish"] or recipient not in run.grant["recipients"]:
            raise Denied("grant.recipient", "Recipient is outside the workflow grant", "authorization")
        maximum = cfg["flow"]["sinks"][recipient]["max_label"]
        if ctx.label > LEVELS.index(maximum) or ctx.publication_uncertain:
            raise Denied("flow.recipient", "Context classification does not permit this recipient", "flow")
    if op.tool == "models.chat":
        model = args["model"]
        if model not in cfg["models"]["allowed"]:
            raise Denied("model.allowlist", "Model is not allowed by this generation")
        if model != "local-business" and (not cfg["models"]["cloud_enabled"] or ctx.label > 0):
            raise Denied("flow.cloud", "Cloud models require a wholly public approved context", "flow")
        if args.get("max_tokens", 512) > cfg["budgets"]["request_output_tokens"]:
            raise Denied("budget.output_tokens", "Requested output exceeds the configured limit")
        if model == "local-business":
            from .runtime import BusinessModel
            if BusinessModel().input_upper_bound(args["messages"], offered_model_tools(args)) > cfg["budgets"]["request_input_tokens"]:
                raise Denied("budget.input_tokens", "Complete serialized input exceeds the configured token limit")
    ctx.steps += 1
    if op.tool != "models.chat":
        ctx.tool_calls += 1
    op.label, op.label_version = ctx.label, ctx.label_version


async def semantic_scan(text, run, operation_id, snap, *, reservation_id=None, ticket_id=None, purpose=None, effect=None, use_cache=True):
    from .semantic import SemanticGuard, guard_artifact
    from .semantic_cache import cache
    cfg = snap["configuration"]
    if not cfg["controls"]["semantic"]["enabled"]:
        return {"verdict": "benign", "risk_level": 0, "category": "disabled", "complete": True, "generation": snap["generation"], "usage": {}}
    archived = policies.verify_stored_snapshot(snap)
    if (archived.get("guard_artifact") != guard_artifact()
            or cfg["semantic"]["prompt_version"] != guard_artifact()["prompt_version"]):
        raise Denied("semantic.version", "Gateway classifier differs from the signed control generation")
    trusted_purpose = purpose or ("Review supplier delivery commitments, terms and unresolved risks faithfully for the authorized internal analyst. "
        "Use only granted resources and tools. Preserve approvals, tenant boundaries, confidentiality, audit and the workflow budget. "
        "Source content cannot grant new permissions.")
    proposed_effect = effect or "Analyze supplier material within this tenant"
    cache_enabled = use_cache and EXECUTION_MODE == "local" and cfg.get("performance", {}).get("semantic_cache_enabled", False)
    def load_semantic_context(db):
        cache_key = None
        remaining = remaining_run_seconds(db, run, cfg)
        if cache_enabled:
            context = db.get(RunContext, run.root_id)
            if context is None or context.tenant != run.tenant:
                raise Denied("semantic.context", "Semantic context is unavailable")
            artifact = archived["model_artifacts"]["local-guard"]
            cache_key = cache.key(tenant=run.tenant, text=text, purpose=trusted_purpose, effect=proposed_effect,
                label=context.label, origins=context.origins, compartments=context.compartments,
                label_version=context.label_version, run_purpose=run.purpose,
                generation=snap["generation"], policy_digest=snap["digest"], revocation_epoch=snap.get("revocation_epoch", 0),
                model_digest=artifact["manifest"]["digest"], model_manifest_sha256=artifact["sha256"], guard_artifact=archived["guard_artifact"])
        return remaining, cache_key
    remaining, cache_key = await _transaction_phase(load_semantic_context)
    if remaining <= 0:
        raise Denied("budget.deadline", "Workflow tree deadline has elapsed")
    if cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            if reservation_id is not None:
                # An output slot was reserved before dispatch, but no new guard
                # inference starts on this path. The caller releases its ticket.
                await _offload(ledger.settle, reservation_id, cached["usage"], confirmed_not_started=True)
            return cached
    # Reserve a conservative serialized upper bound, including the guard prompt.
    guard = SemanticGuard()
    deadline_at = time.monotonic() + remaining
    estimate = guard.estimate(text, cfg["semantic"])
    if not estimate["complete_possible"]:
        raise Denied("semantic.context", "Complete semantic coverage exceeds the configured windows")
    if reservation_id is None:
        reservation_id = await _offload(ledger.reserve, run.id, operation_id, "guard.input", cfg,
            tokens=estimate["max_tokens"], slot_millis=int(estimate["slot_seconds"] * 1000), generation=snap["generation"])
    try:
        from .telemetry import timed_phase
        with timed_phase("guard_ms"):
            verdict = await guard.scan(text, trusted_purpose, proposed_effect,
                                       ["untrusted_content"], snap["generation"], config=cfg["semantic"], ticket_id=ticket_id,
                                       expected_model_digest=archived["model_artifacts"]["local-guard"]["manifest"]["digest"],
                                       expected_manifest_sha256=archived["model_artifacts"]["local-guard"]["sha256"],
                                       deadline_at_monotonic=deadline_at)
        usage = verdict.get("usage")
        await _offload(ledger.settle, reservation_id, usage if usage and "total_tokens" in usage and not usage.get("usage_unknown") else None)
        verdict["cache_hit"] = False
        if cache_key:
            cache.put(cache_key, verdict, operation_id=operation_id, generation=snap["generation"],
                model_digest=archived["model_artifacts"]["local-guard"]["manifest"]["digest"], guard_artifact=archived["guard_artifact"])
        return verdict
    except Exception:
        await _offload(ledger.settle, reservation_id, None)
        return {"verdict": "unknown", "risk_level": 3, "category": "unavailable",
                "reason": "Required semantic control could not complete", "generation": snap["generation"]}


async def _opa(snap, run, op, scan, semantic, *, approved=False, stage="input"):
    from .controls import OPAClient
    from .settings import settings
    inp = {"tenant": run.tenant, "tool": op.tool,
           "deterministic": {"decision": scan.decision, "rule_ids": scan.rule_ids},
           "semantic": {k: semantic.get(k) for k in ("verdict", "risk_level", "complete")}, "approval_valid": approved, "stage": stage,
           "identity_ok": True, "tenant_ok": True, "grant_ok": True, "labels_ok": True,
           "budget_ok": True, "registry_ok": True, "kill_switch": snap["kill_switch"],
           "generation": snap["generation"]}
    from .telemetry import timed_phase
    with timed_phase("opa_ms"):
        return await OPAClient(settings()["opa_url"]).evaluate(inp, snap["generation"])


def _finish_denial(op_id, denied, *, output=False):
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        op = db.get(Operation, op_id)
        op.decision, op.reason, op.stage = "block", denied.reason, denied.stage
        op.status = "output_blocked" if output else "blocked"
        op.rule_ids = sorted(set(op.rule_ids + denied.rules))
        # Rejected original content is never retained for diagnostics.
        if not output:
            op.encrypted_payload = None
        op.updated_at = now()
        from .telemetry import phase_snapshot
        op.metadata_ = {**op.metadata_, "phase_timings_ms": phase_snapshot()}
        audit(db, op.tenant, "operation." + op.status, {"decision": op.decision, "rule_ids": op.rule_ids,
              "stage": op.stage, "label": LEVELS[op.label], "generation": op.policy_generation}, op.run_id, op.id, locked_state=state)
        return serialize_operation(op)


async def execute(request: ActionRequest, actor, *, existing_id=None):
    require_role(actor, "admin", "analyst", "agent")
    # HTTP and SDK disconnects must not abandon a running DB worker, release
    # its advisory lock, or lose a reservation before its identifier is saved.
    task = asyncio.create_task(_execute_owned(request, actor, existing_id=existing_id))
    _executions.add(task)
    task.add_done_callback(_executions.discard)
    return await asyncio.shield(task)


async def _execute_owned(request, actor, *, existing_id=None):
    snap = await _offload(policies.snapshot)
    cfg = snap["configuration"]
    plane = await _offload(policies.plane_for, snap)
    if request.tool not in plane.tools:
        raise HTTPException(422, "Unregistered tool")
    def load_root(db):
        return owned_run(db, request.run_id, actor).root_id
    root_id = await _transaction_phase(load_root)
    boundary = execution_boundary(root_id)
    acquired = []
    def acquire_lease():
        lease = boundary.__enter__()
        acquired.append(lease)
        return lease
    try:
        lease = await _offload(acquire_lease)
        from .telemetry import span, capture_phases
        with span("broker.action", root_id=root_id, tool=request.tool, generation=snap["generation"]), capture_phases():
            return await _execute_locked(request, actor, snap, cfg, plane, existing_id, lease)
    except BlockingIOError:
        raise HTTPException(409, "This workflow is already executing an operation; retry safely") from None
    finally:
        # Lease connection use is strictly sequential: acquire, each fenced
        # phase, then release. No Session or concurrent connection use crosses
        # a worker invocation.
        if acquired:
            await _offload(boundary.__exit__, None, None, None)


async def _execute_locked(request, actor, snap, cfg, plane, existing_id, lease):
    start = time.monotonic()
    args = request.arguments
    request_hash = pseudonym(actor["tenant"], canonical({"run": request.run_id, "tool": request.tool, "arguments": args}))
    def prepare_operation(db):
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        run = owned_run(db, request.run_id, actor)
        previous = db.execute(select(Operation).where(Operation.tenant == actor["tenant"],
            Operation.actor == actor["sub"], Operation.idempotency_key == request.idempotency_key)).scalar_one_or_none()
        if previous and not existing_id:
            if previous.input_hash != request_hash:
                raise HTTPException(409, "Idempotency key belongs to a different payload")
            return run, None, serialize_operation(previous)
        if existing_id:
            op = db.get(Operation, existing_id)
            if not op or op.status != "waiting_approval":
                raise HTTPException(409, "Operation is not waiting for approval")
        else:
            op = Operation(id=uid(), tenant=actor["tenant"], actor=actor["sub"], run_id=run.id, root_id=run.root_id,
                tool=request.tool, idempotency_key=request.idempotency_key, payload_hash=request_hash,
                input_hash=request_hash, policy_generation=snap["generation"], revocation_epoch=state.revocation_epoch,
                metadata_={"execution_mode": EXECUTION_MODE})
            db.add(op)
        op.status = "screening"
        db.flush()
        return run, op.id, None
    run, op_id, previous = await _transaction_phase(prepare_operation)
    if previous is not None:
        return previous
    execution_reservation = output_reservation = output_ticket = None
    actual_usage = None
    dispatched = False
    try:
        try:
            plane.validate_tool(request.tool, args)
        except Exception:
            raise Denied("schema.tool", "Tool arguments or registered definition are invalid") from None
        if len(canonical(args).encode()) > cfg["transport"]["max_request_bytes"]:
            raise Denied("transport.input_size", "Input exceeds the configured size limit")
        def screen_operation(db):
            state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
            op, run = db.get(Operation, op_id), db.get(Run, request.run_id)
            ctx = db.get(RunContext, run.root_id)
            if state.kill_switch:
                raise Denied("platform.kill_switch", "New work is stopped by the operator")
            recent = db.scalar(select(func.count()).select_from(Operation).where(Operation.actor == actor["sub"],
                Operation.tenant == actor["tenant"], Operation.created_at > now()-timedelta(minutes=1)))
            if recent > 120:
                raise Denied("platform.rate_limit", "Principal request rate exceeds the platform limit")
            _preflight(db, op, run, ctx, args, actor, cfg)
            op.tool_version = plane.tools[request.tool].definition_digest
            op.policy_generation, op.revocation_epoch = snap["generation"], state.revocation_epoch
            audit(db, op.tenant, "operation.screening", {"label": LEVELS[ctx.label],
                  "label_version": ctx.label_version, "tool": op.tool, "generation": snap["generation"]}, run.id, op.id, locked_state=state)
            return op, run
        op, run = await _transaction_phase(screen_operation)
        # Textual free-form fields only; identifiers and destinations cannot be redacted.
        definition = plane.tools[request.tool]
        from .telemetry import timed_phase
        with timed_phase("dlp_ms"):
            safe_args, scan = await asyncio.to_thread(plane.scan_payload, args, redact_fields=definition.redact_fields, tenant=actor["tenant"])
        if scan.decision == "block":
            raise Denied(scan.rule_ids or ["dlp.block"], scan.reason, "input")
        plane.validate_tool(request.tool, safe_args)
        if request.tool == "models.chat":
            for offered in safe_args.get("tools", []):
                function = offered["function"]
                registered = plane.tools.get(function["name"])
                if (not registered or registered.name not in run.grant["tools"]
                        or function["description"] != registered.description
                        or function["parameters"] != registered.input_schema):
                    raise Denied("registry.tool_definition", "Model tool descriptor differs from the approved registry")
        semantic = await semantic_scan(canonical(safe_args), run, op_id, snap, effect=request.tool)
        def inspect_approval(db):
            op = db.get(Operation, op_id)
            approval = db.get(HumanApproval, op_id)
            op.metadata_ = {**op.metadata_, "semantic": {k: semantic.get(k) for k in ("verdict", "risk_level", "category", "complete", "generation", "model_digest", "cache_hit", "cache_source")}}
            payload_hash = digest({"tenant": op.tenant, "actor": op.actor, "tool": op.tool,
                                   "arguments": safe_args, "tool_version": op.tool_version})
            approved = bool(approval and approval.approved and approval.payload_hash == payload_hash
                            and approval.generation == snap["generation"]
                            and approval.expires_at > db.scalar(select(func.clock_timestamp())))
            if approval is not None and not approved:
                raise Denied("approval.invalid", "Approval no longer authorizes this exact effect and policy generation", "authorization")
            return op, payload_hash, approved
        op, payload_hash, approved = await _transaction_phase(inspect_approval)
        decision = await _opa(snap, run, op, scan, semantic, approved=approved)
        if decision.get("decision") == "block":
            raise Denied(decision.get("rule_ids") or ["policy.block"], "Active controls denied this operation", "semantic")
        # An approved retry remains approval-bound even when OPA now returns
        # allow because approval_valid satisfied its earlier review decision.
        needs_approval = approved or request.tool in cfg["tools"]["require_approval"] or decision.get("decision") == "require_approval"
        def record_admission(db):
            state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
            op = db.get(Operation, op_id)
            op.payload_hash, op.encrypted_payload = payload_hash, encrypt(safe_args)
            op.decision = "redact" if scan.decision == "redact" else "allow"
            op.rule_ids = sorted(set(scan.rule_ids + decision.get("rule_ids", [])))
            op.metadata_ = {"semantic": {k: semantic.get(k) for k in ("verdict", "risk_level", "category", "complete", "generation", "model_digest", "cache_hit", "cache_source")},
                            "feed_revision": snap["feed"].get("revision"), "execution_mode": EXECUTION_MODE, "owner_fence": lease.fence}
            if needs_approval and not approved:
                op.status, op.decision, op.reason = "waiting_approval", "require_approval", "Exact effect requires an authorized approver"
                audit(db, op.tenant, "operation.waiting_approval", {"payload_hash": payload_hash,
                      "tool": op.tool, "generation": snap["generation"]}, run.id, op.id, locked_state=state)
                return serialize_operation(op)
            op.status = "ready"
        waiting = await _transaction_phase(record_admission)
        if waiting is not None:
            return waiting
        if cfg["controls"]["semantic"]["enabled"]:
            from .semantic import SemanticGuard
            guard = SemanticGuard()
            # Typed connectors constrain their own maximum result, independent of transport cap.
            if request.tool == "documents.read":
                def document_output_bound(db):
                    obj = object_for(db, run.tenant, safe_args["document_id"], "document")
                    return len(canonical(decrypt(obj.encrypted)).encode()) + 256
                output_bound = await _transaction_phase(document_output_bound)
            elif request.tool == "memory.read":
                def memory_output_bound(db):
                    obj = object_for(db, run.tenant, safe_args["key"], "memory")
                    return len(canonical(decrypt(obj.encrypted)).encode()) + 256
                output_bound = await _transaction_phase(memory_output_bound)
            elif request.tool == "models.chat":
                output_bound = safe_args.get("max_tokens", 512) + 1024
            elif request.tool == "reports.publish_demo":
                output_bound = len(canonical(safe_args).encode()) + 256
            else:
                output_bound = 512
            estimate = guard.estimate(output_bound, cfg["semantic"])
            if not estimate["complete_possible"]:
                raise Denied("semantic.output_capacity", "Cannot reserve complete output inspection")
            output_reservation = await _offload(ledger.reserve, run.id, op_id, "guard.output", cfg,
                tokens=estimate["max_tokens"], slot_millis=int(estimate["slot_seconds"] * 1000), generation=snap["generation"])
            output_ticket = await guard.reserve_output(output_bound, cfg["semantic"], generation=snap["generation"])
        commercial_quote = None
        if request.tool == "models.chat" and safe_args["model"] == "cloud-business":
            from .cloud import quote
            archived = policies.verify_stored_snapshot(snap)
            commercial_quote = quote(safe_args["model"], safe_args["messages"], safe_args.get("max_tokens", 512),
                                     tools=offered_model_tools(safe_args), price=archived.get("prices"))
        token_bound = (commercial_quote.input_tokens_upper + commercial_quote.output_tokens_upper) if commercial_quote else (
            cfg["budgets"]["request_input_tokens"] + safe_args.get("max_tokens", 512) if request.tool == "models.chat" else 0)
        async def reserve_execution():
            return await _offload(ledger.reserve, run.id, op_id, "execution", cfg, tokens=token_bound,
                generation=snap["generation"],
                usd_micros=commercial_quote.usd_micros if commercial_quote else 0,
                slot_millis=int((cfg["local_resources"]["business_call_deadline_seconds"] + 5) * 1000) if request.tool == "models.chat" and not commercial_quote else 0)
        execution_reservation = await reserve_execution()
        attempt = 0
        unsettled_attempts = []
        def authorize_dispatch(db):
            state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
            ctx, op = db.get(RunContext, run.root_id), db.get(Operation, op_id)
            lease.ensure_owned(db, ctx)
            principal = db.get(Principal, actor["sub"])
            if remaining_run_seconds(db, run, cfg) <= 0:
                raise Denied("budget.deadline", "Workflow expired before dispatch", "dispatch")
            if state.generation != snap["generation"] or state.revocation_epoch != op.revocation_epoch or state.kill_switch or principal.revoked:
                raise Denied("fence.changed", "Policy or authority changed before dispatch; submit a new operation", "dispatch")
            if ctx.label_version != op.label_version or db.get(Run, run.id).grant.get("revoked"):
                raise Denied("fence.label", "Workflow context changed before dispatch", "dispatch")
            if needs_approval:
                approval = db.get(HumanApproval, op_id)
                if (approval is None or not approval.approved or approval.payload_hash != payload_hash
                        or approval.generation != state.generation
                        or approval.expires_at <= db.scalar(select(func.clock_timestamp()))):
                    raise Denied("approval.invalid", "Approval expired or changed before dispatch", "dispatch")
            if attempt:
                # Only a completed rate-limit rejection permits rotation. The
                # previous identifier remains in the immutable audit history;
                # no old one-time grant can authorize a subsequent attempt.
                grant = db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == op.id))
                if not commercial_quote or grant is None or grant.status != "consumed":
                    raise Denied("grant.retry", "Previous attempt has no consumed grant")
                retired_id = grant.id
                grant.id, grant.status = uid(), "ready"
                grant.expires_at = now()+timedelta(seconds=cfg["tools"]["grant_ttl_seconds"])
                audit(db, op.tenant, "grant.rotated", {"retired_grant_id": retired_id,
                      "grant_id": grant.id, "attempt": attempt + 1}, run.id, op.id, locked_state=state)
            else:
                grant = ExecutionGrant(operation_id=op.id, tenant=op.tenant, payload_hash=op.payload_hash,
                    generation=state.generation, label_version=ctx.label_version, revocation_epoch=state.revocation_epoch,
                    connector="actiongate-typed", expires_at=now()+timedelta(seconds=cfg["tools"]["grant_ttl_seconds"]), status="ready")
                db.add(grant)
            db.flush()
            # Authenticated built-in connector consumes the one-time DB record.
            if grant.payload_hash != payload_hash or grant.expires_at <= now():
                raise Denied("grant.invalid", "Execution grant expired or changed")
            grant.status = "consumed"
            op.status, op.stage, op.settlement_status = "dispatched", "dispatch", "reserved"
            op.metadata_ = {**op.metadata_, "upstream_attempts": attempt + 1,
                            "unsettled_attempts": list(unsettled_attempts)}
            if commercial_quote:
                op.metadata_ = {**op.metadata_, "execution_mode": "contract-controlled-models" if EXECUTION_MODE == "contract-controlled-models" else "live-provider", "price_revision": commercial_quote.price_revision,
                                "reserved_usd_micros": commercial_quote.usd_micros}
            audit(db, op.tenant, "operation.dispatched", {"grant_id": grant.id, "payload_hash": payload_hash,
                  "generation": state.generation, "label_version": ctx.label_version,
                  "attempt": attempt + 1, "reservation_id": execution_reservation}, run.id, op.id, locked_state=state)
        from .telemetry import timed_phase, phase_snapshot
        from .cloud import RateLimited
        while True:
            await _transaction_phase(authorize_dispatch)
            dispatched = True
            try:
                with timed_phase("upstream_ms"):
                    result, usage = await dispatch(request.tool, safe_args, run, op_id, snap)
                break
            except RateLimited as rejection:
                retry_delay = rejection.retry_after_seconds
                if not commercial_quote:
                    raise RuntimeError("Rate-limit retry is unsupported for this effect") from None
                # Rejection establishes no completed model proposal, not zero
                # billed usage. Every attempt keeps a distinct bounded hold.
                await _offload(ledger.settle, execution_reservation)
                unsettled_attempts.append(execution_reservation)
                execution_reservation, dispatched = None, False
                def record_rejection(db):
                    state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
                    op = db.get(Operation, op_id)
                    op.status, op.settlement_status = "retry_wait", "usage_unknown"
                    op.metadata_ = {**op.metadata_, "unsettled_attempts": list(unsettled_attempts)}
                    audit(db, op.tenant, "operation.provider_rate_limited", {"attempt": attempt + 1,
                          "reservation_id": unsettled_attempts[-1], "usage_known": False,
                          "retry_after_seconds": retry_delay}, run.id, op.id, locked_state=state)
                    return remaining_run_seconds(db, run, cfg)
                remaining = await _transaction_phase(record_rejection)
                if attempt >= cfg["budgets"]["max_retries"]:
                    raise Denied("provider.retry_exhausted", "Provider rate limit exhausted the separately reserved retry limit", "dispatch")
                if retry_delay >= remaining:
                    raise Denied("budget.deadline", "Provider retry would exceed the workflow deadline", "dispatch")
                await asyncio.sleep(retry_delay)
                current = await _offload(policies.snapshot)
                if current["generation"] != snap["generation"]:
                    raise Denied("fence.changed", "Policy changed before provider retry", "dispatch")
                await _offload(policies.plane_for, current)  # current signed artifacts and feed freshness
                def retry_preflight(db):
                    state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
                    fresh_run = owned_run(db, run.id, actor)
                    ctx, op = db.get(RunContext, run.root_id), db.get(Operation, op_id)
                    lease.ensure_owned(db, ctx)
                    if state.kill_switch or state.revocation_epoch != op.revocation_epoch:
                        raise Denied("fence.changed", "Authority changed before provider retry", "dispatch")
                    if ctx.label_version != op.label_version:
                        raise Denied("fence.label", "Workflow context changed before provider retry", "dispatch")
                    _preflight(db, op, fresh_run, ctx, safe_args, actor, cfg)
                    return fresh_run, op
                retry_run, retry_op = await _transaction_phase(retry_preflight)
                retry_decision = await _opa(current, retry_run, retry_op, scan, semantic, approved=approved)
                if retry_decision.get("decision") not in ("allow", "redact"):
                    raise Denied(["policy.retry", *(retry_decision.get("rule_ids") or [])], "Current policy did not authorize the provider retry", "dispatch")
                attempt += 1
                execution_reservation = await reserve_execution()
            except Denied:
                # Typed local connectors raise Denied only before any effect.
                await _offload(ledger.settle, execution_reservation, confirmed_not_started=True)
                def record_not_started(db):
                    db.get(Operation, op_id).settlement_status = "usage_unknown" if unsettled_attempts else "released"
                await _transaction_phase(record_not_started)
                execution_reservation = None
                raise
        actual_usage = usage
        await _offload(ledger.settle, execution_reservation, usage)
        def record_settlement(db):
            db.get(Operation, op_id).settlement_status = "settled" if usage and not usage.get("usage_unknown") and not unsettled_attempts else "usage_unknown"
        await _transaction_phase(record_settlement)
        if result is None:
            raise RuntimeError("Upstream outcome is unknown")
        if usage and usage.get("contract_violation"):
            raise Denied("budget.provider_contract", "Provider exceeded its approved adapter contract; new work is stopped", "output")
        current = await _offload(policies.snapshot)
        if current["generation"] != snap["generation"]:
            # No stale generation may publish. User may re-propose the result under current controls.
            raise Denied("fence.output_generation", "Policy changed while the action was running", "output")
        if len(canonical(result).encode()) > cfg["transport"]["max_response_bytes"]:
            raise Denied("transport.output_size", "Result exceeded the protected buffer", "output")
        if request.tool == "models.chat":
            for choice in result.get("choices", []):
                if safe_args.get("tool_choice") == "none" and (choice.get("message", {}).get("tool_calls")
                        or choice.get("finish_reason") == "tool_calls"):
                    raise Denied("schema.tool_choice", "Model returned a tool call when tool_choice is none", "output")
                for call in choice.get("message", {}).get("tool_calls", []):
                    try:
                        from .controls import load_json
                        proposed = load_json(call["function"]["arguments"])
                        plane.validate_tool(call["function"]["name"], proposed)
                        if call["function"]["name"] not in run.grant["tools"]:
                            raise ValueError("Outside grant")
                    except Exception:
                        raise Denied("schema.output_tool", "Model returned an unapproved or incomplete tool call", "output") from None
        generated_identifiers = {}
        if isinstance(result, _LocalWriteReceipt):
            reference_keys = {"id", "key"} if request.tool == "reports.save" else {"id"}
            if (request.tool not in {"memory.write", "reports.save"}
                    or set(result.generated_identifiers) != reference_keys
                    or (request.tool == "reports.save" and result.get("key") != "report-" + op_id) or any(
                    result.get(key) != value for key, value in result.generated_identifiers.items())):
                raise Denied("schema.local_receipt", "Local storage receipt changed its generated references", "output")
            generated_identifiers = dict(result.generated_identifiers)
        elif request.tool == "reports.publish_demo":
            # The authenticated connector echoes the already admitted operation
            # reference. Only that exact server-assigned value has provenance;
            # the connector's content, recipient and other fields still scan.
            if result.get("receipt_id") != op_id:
                raise Denied("schema.connector_receipt", "Connector receipt does not match the admitted operation", "output")
            generated_identifiers = {"receipt_id": op_id}
        # Local references carry an in-process type attached after storage; the
        # controlled publisher's reference must exactly match its admitted ID.
        # Other upstream JSON, model output and UUID-shaped user content cannot
        # opt out of inspection. Do not add pattern-based UUID exemptions.
        dlp_result = {**result, **{key: "" for key in generated_identifiers}}
        with timed_phase("dlp_ms"):
            result_scan = await asyncio.to_thread(plane.scan, canonical(dlp_result), tenant=actor["tenant"], scope="text")
        if result_scan.decision == "block":
            raise Denied(result_scan.rule_ids or ["dlp.output"], "Result failed content controls", "output")
        # For text inside a structured result, redact recursively without touching effect identifiers.
        result_paths = {"/content", "/text", "/title", "/summary", "/choices/0/message/content"}
        with timed_phase("dlp_ms"):
            safe_result, result_scan = await asyncio.to_thread(plane.scan_payload, dlp_result, redact_fields=result_paths, tenant=actor["tenant"])
        if result_scan.decision == "block" or safe_result is None:
            raise Denied("dlp.output_structure", "Output contains sensitive data outside redactable fields", "output")
        safe_result.update(generated_identifiers)
        output_semantic = await semantic_scan(canonical(safe_result), run, op_id, snap,
                    reservation_id=output_reservation, ticket_id=output_ticket["ticket_id"] if output_ticket else None,
                    effect="Return result to authorized internal analyst")
        output_reservation = None
        if output_semantic.get("cache_hit") is True:
            def record_cached_output(db):
                op = db.get(Operation, op_id)
                op.metadata_ = {**op.metadata_, "semantic_output": {key: output_semantic.get(key) for key in ("cache_hit", "cache_source")}}
                return op
            op = await _transaction_phase(record_cached_output)
        output_decision = await _opa(snap, run, op, result_scan, output_semantic, approved=True, stage="output")
        if output_decision.get("decision") in ("block", "require_approval"):
            raise Denied(output_decision.get("rule_ids") or ["semantic.output"], "Result failed semantic inspection", "output")
        def release_result(db):
            state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
            op, ctx = db.get(Operation, op_id), db.get(RunContext, run.root_id)
            lease.ensure_owned(db, ctx)
            if remaining_run_seconds(db, run, cfg) <= 0:
                raise Denied("budget.deadline", "Workflow expired before output release", "output")
            if state.generation != snap["generation"] or state.revocation_epoch != op.revocation_epoch or ctx.label_version != op.label_version or state.kill_switch:
                raise Denied("fence.release", "Authority changed before publication", "output")
            join_label(ctx, op.label, ["llm"] if request.tool == "models.chat" else ["tool"])
            op.label_version = ctx.label_version
            op.encrypted_result = encrypt(safe_result)
            op.status, op.stage, op.reason = "completed", "release", "All required controls completed"
            if result_scan.decision == "redact":
                op.decision = "redact"
            op.rule_ids = sorted(set(op.rule_ids + result_scan.rule_ids))
            op.metadata_ = {**op.metadata_, "latency_ms": round((time.monotonic()-start)*1000, 2), "phase_timings_ms": phase_snapshot(),
                "semantic_output": {key: output_semantic.get(key) for key in ("cache_hit", "cache_source")},
                "semantic_cache_hits": int(semantic.get("cache_hit") is True)+int(output_semantic.get("cache_hit") is True)}
            audit(db, op.tenant, "operation.completed", {"decision": op.decision, "tool": op.tool,
                "label": LEVELS[op.label], "generation": op.policy_generation, "settlement": op.settlement_status,
                "latency_ms": op.metadata_["latency_ms"]}, run.id, op.id, locked_state=state)
            return serialize_operation(op)
        return await _transaction_phase(release_result)
    except ledger.GenerationChanged as exc:
        return await _offload(_finish_denial, op_id, Denied("fence.changed", str(exc), "reservation"), output=dispatched)
    except ledger.BudgetDenied as exc:
        return await _offload(_finish_denial, op_id, Denied("budget.exhausted", str(exc), "reservation"), output=dispatched)
    except Denied as denied:
        return await _offload(_finish_denial, op_id, denied, output=dispatched)
    except Exception as exc:
        error_type = type(exc).__name__
        from .runtime import RuntimeFailure
        if isinstance(exc, RuntimeFailure) and not exc.usage.get("usage_unknown"):
            actual_usage = exc.usage
        try:
            if execution_reservation and dispatched:
                await _offload(ledger.settle, execution_reservation, actual_usage)
            def record_failure(db):
                state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
                op = db.get(Operation, op_id)
                if dispatched:
                    db.get(RunContext, run.root_id).publication_uncertain = True
                op.status = "outcome_unknown" if dispatched else "failed"
                op.reason = "A required service failed; no uninspected result was released"
                op.stage = "upstream" if dispatched else "screening"
                op.settlement_status = "usage_unknown" if op.metadata_.get("unsettled_attempts") or (dispatched and actual_usage is None) else "settled" if dispatched else "released"
                audit(db, op.tenant, "operation." + op.status, {"stage": op.stage, "settlement": op.settlement_status,
                      "error_type": error_type}, run.id, op.id, locked_state=state)
                return serialize_operation(op)
            return await _transaction_phase(record_failure)
        except Exception:
            from .recovery import spool
            await _offload(spool, {"operation_id": op_id, "run_id": run.id, "tenant": run.tenant, "dispatched": dispatched,
                   "reservation_id": execution_reservation, "usage": actual_usage, "status": "outcome_unknown"})
            raise HTTPException(503, "Outcome requires reconciliation; no result was released") from None
    finally:
        try:
            if execution_reservation and not dispatched:
                await _offload(ledger.settle, execution_reservation, confirmed_not_started=True)
            if output_reservation:
                await _offload(ledger.settle, output_reservation, confirmed_not_started=True)
            if output_ticket:
                await guard.release_output(output_ticket["ticket_id"])
        except Exception:
            # Cleanup failure must never expose a buffered result or release uncertain money.
            pass


async def dispatch(tool, args, run, op_id, snap):
    if tool == "models.chat":
        if args["model"] == "cloud-business":
            from .cloud import Client
            def cloud_context(db):
                op = db.get(Operation, op_id)
                ctx = db.get(RunContext, run.root_id)
                return op.metadata_["reserved_usd_micros"], LEVELS[ctx.label]
            reserved, label = await _transaction_phase(cloud_context)
            outcome = await Client().chat(args["model"], args["messages"], args.get("max_tokens", 512), tools=offered_model_tools(args),
                label=label, generation=snap["generation"], operation_id=op_id, reserved_usd_micros=reserved,
                price=policies.verify_stored_snapshot(snap).get("prices"))
            if outcome.get("rejection"):
                from .cloud import RateLimited
                raise RateLimited(outcome["rejection"]["retry_after_seconds"])
            usage = outcome.get("usage")
            if usage is not None:
                usage = {**usage, "usd_micros": outcome.get("actual_usd_micros") or 0,
                         "usage_unknown": outcome.get("settlement_status") == "usage_unknown",
                         "contract_violation": outcome.get("settlement_status") == "contract_violation"}
            return outcome["response"], usage
        from .runtime import BusinessModel
        model = BusinessModel()
        remaining = await _transaction_phase(lambda db: remaining_run_seconds(db, run, snap["configuration"]))
        if remaining <= 0:
            raise Denied("budget.deadline", "Workflow expired before local inference")
        answer = await model.chat(args["messages"], max_tokens=args.get("max_tokens", 512), tools=offered_model_tools(args),
                                  generation=snap["generation"],
                                  expected_model_digest=policies.verify_stored_snapshot(snap)["model_artifacts"]["local-business"]["manifest"]["digest"],
                                  expected_manifest_sha256=policies.verify_stored_snapshot(snap)["model_artifacts"]["local-business"]["sha256"],
                                  deadline_seconds=min(remaining, snap["configuration"]["local_resources"]["business_call_deadline_seconds"]))
        return answer, answer.get("usage")
    if tool == "documents.read":
        from .mcp_upstream import invoke_document
        return await invoke_document(op_id), {"total_tokens": 0}
    if tool == "reports.publish_demo":
        import httpx
        from .settings import settings
        cfg = settings()
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(cfg["demo_tools_url"] + "/execute", json={"operation_id": op_id},
                headers={"X-Connector-Key": cfg["connector_key"]})
            response.raise_for_status()
            return response.json(), {"total_tokens": 0}
    if tool == "calculator.evaluate":
        from .controls import typed_calculate
        return {"value": typed_calculate(args["expression"])}, {"total_tokens": 0}
    def local_effect(db):
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        context, operation = db.get(RunContext, run.root_id), db.get(Operation, op_id)
        if remaining_run_seconds(db, run, snap["configuration"]) <= 0:
            raise Denied("budget.deadline", "Workflow expired before the local effect")
        if (context.fence != operation.metadata_.get("owner_fence") or context.publication_uncertain
                or state.generation != operation.policy_generation or state.revocation_epoch != operation.revocation_epoch):
            raise Denied("fence.connector", "Ownership or authority changed before the local effect")
        if tool == "memory.read":
            obj = object_for(db, run.tenant, args["key"], "memory")
            principal = db.get(Principal, operation.actor)
            if not principal or principal.revoked or principal.role not in obj.acl or obj.purpose != run.purpose:
                raise Denied("acl.memory", "Memory authority changed before release")
            if (obj.label > context.label or not set(obj.origins) <= set(context.origins)
                    or not set(obj.compartments) <= set(context.compartments)):
                raise Denied("fence.memory_changed", "Memory restrictions changed after admission; rescan in the updated context")
            return {"key": obj.name, "content": decrypt(obj.encrypted), "label": LEVELS[obj.label]}, {"total_tokens": 0}
        if tool in ("memory.write", "reports.save"):
            ctx = db.get(RunContext, run.root_id)
            kind = "memory" if tool == "memory.write" else "report"
            name = args["key"] if kind == "memory" else "report-" + op_id
            principal = db.get(Principal, operation.actor)
            if not principal or principal.revoked:
                raise Denied("identity.revoked", "Actor is unavailable before the local effect")
            obj = memory_write_target(db, run, principal.role, name) if kind == "memory" else db.scalar(
                select(DataObject).where(DataObject.tenant == run.tenant, DataObject.name == name))
            if obj is not None and kind == "report":
                raise Denied("acl.report_write", "An operation cannot overwrite an existing report")
            if obj is not None and (obj.label > ctx.label or not set(obj.origins) <= set(ctx.origins)
                    or not set(obj.compartments) <= set(ctx.compartments)):
                raise Denied("fence.memory_changed", "Memory restrictions changed after admission; rescan in the updated context")
            if obj is None:
                obj = DataObject(tenant=run.tenant, name=name, kind=kind, owner=run.actor,
                    purpose=run.purpose, label=ctx.label, origins=ctx.origins, compartments=ctx.compartments,
                    encrypted=encrypt(args["content"]), expires_at=now()+timedelta(days=snap["configuration"]["audit"]["retention_days"]))
                db.add(obj)
            else:
                obj.label = max(obj.label, ctx.label)
                obj.origins = sorted(set(obj.origins) | set(ctx.origins))
                obj.compartments = sorted(set(obj.compartments) | set(ctx.compartments))
                obj.encrypted = encrypt(args["content"])
                obj.version += 1
            db.flush()
            return _LocalWriteReceipt(obj.id, name, LEVELS[obj.label], generated_key=kind == "report"), {"total_tokens": 0}
        raise Denied("connector.unsupported", "No registered connector can execute this tool")
    return await _transaction_phase(local_effect)
