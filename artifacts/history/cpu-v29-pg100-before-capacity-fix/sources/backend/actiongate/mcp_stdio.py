"""Pinned stdio adapter for the authenticated ActionGate HTTP boundary.

This process is a transport, not an execution host. It receives only a workload
token, never database, model or connector credentials. No dynamic command or
package installation is accepted.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

VERSION = "1.0.0"


def gateway_endpoint(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "gateway-a", "gateway-b")
            or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("Stdio requires a registered local ActionGate gateway origin")
    return value.rstrip("/") + "/mcp/"


def forwarding_server(remote: ClientSession) -> Server:
    server = Server("actiongate-stdio", version=VERSION)

    @server.list_tools()
    async def tools_list():
        catalog = await remote.list_tools()
        if catalog.nextCursor:
            raise ValueError("Paginated tool definitions are not supported")
        return catalog.tools

    @server.call_tool(validate_input=False)
    async def tools_call(name: str, arguments: dict):
        return await remote.call_tool(name, arguments)

    @server.list_resources()
    async def resources_list():
        catalog = await remote.list_resources()
        if catalog.nextCursor:
            raise ValueError("Paginated resources are not supported")
        return catalog.resources

    @server.read_resource()
    async def resources_read(uri):
        result = await remote.read_resource(uri)
        return [ReadResourceContents(content=item.text if isinstance(item, types.TextResourceContents) else
            base64.b64decode(item.blob, validate=True), mime_type=item.mimeType,
            meta=item.meta) for item in result.contents]

    @server.list_prompts()
    async def prompts_list():
        return (await remote.list_prompts()).prompts

    @server.get_prompt()
    async def prompts_get(name, arguments):
        return await remote.get_prompt(name, arguments)

    return server


async def main():
    if len(sys.argv) != 1:
        raise ValueError("The pinned adapter accepts no command arguments")
    token = os.environ["ACTIONGATE_WORKLOAD_TOKEN"]
    if not token or "\r" in token or "\n" in token:
        raise ValueError("A workload token is required")
    endpoint = gateway_endpoint(os.getenv("ACTIONGATE_BASE_URL", "http://127.0.0.1:8080"))
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(headers=headers, timeout=300, follow_redirects=False, trust_env=False) as client:
        async with streamable_http_client(endpoint, http_client=client) as (remote_read, remote_write, _):
            async with ClientSession(remote_read, remote_write) as remote:
                initialized = await remote.initialize()
                if initialized.serverInfo.name != "actiongate" or initialized.serverInfo.version != "1.0.0":
                    raise ValueError("The registered gateway identity changed")
                server = forwarding_server(remote)
                async with stdio_server() as (read, write):
                    await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # Never include an HTTP request or environment value in diagnostics.
        print("ActionGate stdio stopped: authenticated transport unavailable or rejected", file=sys.stderr)
        raise SystemExit(1) from None
