"""Pinned upstream MCP connector. Never forwards a downstream actor token."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .settings import settings

DOCUMENT_TOOL = {
    "name": "documents.read",
    "description": "Read the supplier document for a durably admitted ActionGate operation. Version 1.",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {"operation_id": {"type": "string", "minLength": 1, "maxLength": 80}},
        "required": ["operation_id"],
    },
}
DEFINITION_DIGEST = hashlib.sha256(json.dumps(DOCUMENT_TOOL, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_descriptor(tool):
    observed = {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
    digest = hashlib.sha256(json.dumps(observed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != DEFINITION_DIGEST:
        raise ValueError("Registered MCP tool definition changed; approval is required")


async def invoke_document(operation_id: str) -> dict:
    cfg = settings()
    base = cfg["demo_tools_url"]
    parsed = urlsplit(base)
    # This connector has one intentionally internal registry entry. No request
    # argument, tool descriptor or redirect is allowed to select another URL.
    if (parsed.scheme != "http" or parsed.hostname != "demo-tools" or parsed.port != 8010
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("MCP upstream is not the approved internal resource server")
    async with httpx.AsyncClient(headers={"X-Connector-Key": cfg["connector_key"]},
            timeout=httpx.Timeout(20), follow_redirects=False, trust_env=False) as http_client:
        async with streamable_http_client(base.rstrip("/") + "/mcp/", http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                catalog = await session.list_tools()
                if len(catalog.tools) != 1 or catalog.nextCursor is not None:
                    raise ValueError("Unexpected registered MCP catalog")
                validate_descriptor(catalog.tools[0])
                response = await session.call_tool("documents.read", {"operation_id": operation_id})
                if response.isError or response.structuredContent is None:
                    raise ValueError("Controlled upstream MCP read did not complete")
                result = response.structuredContent
                if (set(result) != {"document_id", "content", "label"}
                        or not all(isinstance(value, str) for value in result.values())):
                    raise ValueError("Invalid document result from upstream MCP")
                return result
