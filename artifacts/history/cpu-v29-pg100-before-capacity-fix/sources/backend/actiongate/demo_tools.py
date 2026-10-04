"""Authenticated synthetic resource server with durable idempotent receipts."""
import hmac
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func
from .db import (State, Operation, ExecutionGrant, Run, RunContext, ConnectorReceipt,
                 DataObject, Principal, PolicyGeneration, transaction, now)
from .security import decrypt, encrypt, LEVELS, audit
from .settings import settings

@asynccontextmanager
async def lifespan(app):
    async with mcp_manager.run():
        yield


app = FastAPI(title="ActionGate controlled demo resources", lifespan=lifespan)


class Execution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str


def authenticate(value):
    if not value or not hmac.compare_digest(value, settings()["connector_key"]):
        raise HTTPException(401, "A connector identity is required")


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.post("/execute")
def execute(body: Execution, x_connector_key: str | None = Header(default=None)):
    authenticate(x_connector_key)
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        op = db.get(Operation, body.operation_id)
        if not op or op.status != "dispatched":
            raise HTTPException(409, "No admitted dispatch for this operation")
        prior = db.get(ConnectorReceipt, op.id)
        if prior:
            if prior.payload_hash != op.payload_hash:
                raise HTTPException(409, "Idempotency payload conflict")
            return decrypt(prior.encrypted_result)
        grant = db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == op.id))
        ctx, run = db.get(RunContext, op.root_id), db.get(Run, op.run_id)
        current = db.scalar(select(func.clock_timestamp()))
        if not grant or grant.status != "consumed" or grant.payload_hash != op.payload_hash or grant.expires_at <= current:
            raise HTTPException(403, "A valid admitted execution grant is required")
        root, principal = db.get(Run, op.root_id), db.get(Principal, op.actor)
        policy = db.get(PolicyGeneration, op.policy_generation)
        if (not run or not root or not policy or not principal or principal.revoked
                or run.status != "active" or run.grant.get("revoked")
                or root.status != "active" or root.grant.get("revoked")
                or (current - root.created_at).total_seconds() >= policy.configuration["budgets"]["run_deadline_seconds"]):
            raise HTTPException(403, "Workflow authority expired before the effect")
        if (ctx.publication_uncertain or state.kill_switch or state.generation != op.policy_generation
                or ctx.label_version != op.label_version or ctx.fence != op.metadata_.get("owner_fence")
                or state.revocation_epoch != op.revocation_epoch):
            raise HTTPException(409, "Context changed before effect admission")
        args = decrypt(op.encrypted_payload)
        recipient = None
        if op.tool == "documents.read":
            obj = db.scalar(select(DataObject).where(DataObject.tenant == op.tenant,
                                                    DataObject.name == args["document_id"]))
            principal = db.get(Principal, op.actor)
            if (not obj or obj.kind != "document" or obj.expires_at <= now()
                    or args["document_id"] not in run.grant["document_ids"]
                    or obj.purpose != run.purpose or not principal or principal.revoked
                    or principal.role not in obj.acl or run.status != "active" or run.grant.get("revoked")):
                raise HTTPException(403, "Resource is outside the granted tenant")
            if (obj.label > ctx.label or not set(obj.origins) <= set(ctx.origins)
                    or not set(obj.compartments) <= set(ctx.compartments)):
                raise HTTPException(409, "Resource restrictions changed after admission")
            result = {"document_id": obj.name, "content": decrypt(obj.encrypted), "label": LEVELS[obj.label]}
        elif op.tool == "reports.publish_demo":
            recipient = args["recipient"]
            if recipient != "internal_demo_sink" or not run.grant["allow_publish"] or ctx.label > 2:
                raise HTTPException(403, "Sink does not accept this context")
            result = {"receipt_id": op.id, "recipient": recipient, "content": args["content"],
                      "label": LEVELS[ctx.label], "delivered": True}
        else:
            raise HTTPException(403, "No connector for this tool")
        db.add(ConnectorReceipt(operation_id=op.id, tenant=op.tenant, tool=op.tool,
            recipient=recipient, payload_hash=op.payload_hash, encrypted_result=encrypt(result)))
        audit(db, op.tenant, "connector.receipt", {"receipt_id": op.id, "tool": op.tool,
              "recipient": recipient}, run.id, op.id)
        return result


@app.get("/receipts/{operation_id}")
def receipt(operation_id: str, x_connector_key: str | None = Header(default=None)):
    authenticate(x_connector_key)
    with transaction() as db:
        item = db.get(ConnectorReceipt, operation_id)
        if item is None:
            raise HTTPException(404, "No effect recorded")
        return {"operation_id": operation_id, "tenant": item.tenant, "tool": item.tool,
                "result": decrypt(item.encrypted_result)}


def create_upstream_mcp():
    from mcp.server import Server
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp import types
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from .mcp_upstream import DOCUMENT_TOOL, DEFINITION_DIGEST

    server = Server("actiongate-controlled-documents", version="1.0.0")

    @server.list_tools()
    async def tools_list():
        return [types.Tool(**DOCUMENT_TOOL, _meta={"actiongate_definition_digest": DEFINITION_DIGEST})]

    @server.call_tool(validate_input=True)
    async def tools_call(name, arguments):
        if name != "documents.read":
            raise ValueError("Unregistered upstream tool")
        request = server.request_context.request
        authenticate(request.headers.get("x-connector-key"))
        with transaction() as db:
            op = db.get(Operation, arguments["operation_id"])
            if op is None or op.tool != "documents.read":
                raise ValueError("The admitted operation is not a document read")
        result = execute(Execution(**arguments), request.headers.get("x-connector-key"))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], structuredContent=result)

    manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=["demo-tools:8010", "localhost:*", "127.0.0.1:*"], allowed_origins=[]))

    class AuthenticatedTransport:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return
            request = Request(scope, receive)
            try:
                authenticate(request.headers.get("x-connector-key"))
                if request.method != "POST":
                    await manager.handle_request(scope, receive, send)
                    return
                body = await request.body()
                if len(body) > 4096:
                    raise HTTPException(413, "Upstream MCP request too large")
                payload = json.loads(body)
                if not isinstance(payload, dict) or payload.get("method") not in {
                        "initialize", "notifications/initialized", "ping", "tools/list", "tools/call"}:
                    raise HTTPException(400, "Unapproved upstream MCP method")
                delivered = False
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()
                await manager.handle_request(scope, replay, send)
            except HTTPException as exc:
                await JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)(scope, receive, send)
            except (ValueError, UnicodeError):
                await JSONResponse({"error": "Invalid upstream MCP request"}, status_code=400)(scope, receive, send)

    return AuthenticatedTransport(), manager


mcp_application, mcp_manager = create_upstream_mcp()
app.mount("/mcp", mcp_application)
