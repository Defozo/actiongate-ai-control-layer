"""An actual official MCP client. Uses the same run-bound identity as the SDK."""
import asyncio
import os
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx


async def main():
    headers = {"Authorization": "Bearer " + os.environ["ACTIONGATE_WORKLOAD_TOKEN"],
               "X-ActionGate-Run-Id": os.environ["ACTIONGATE_RUN_ID"], "Idempotency-Key": str(uuid.uuid4())}
    url = os.getenv("ACTIONGATE_BASE_URL", "http://127.0.0.1:8080").rstrip("/") + "/mcp/"
    async with httpx.AsyncClient(headers=headers, timeout=300) as http_client, streamable_http_client(url, http_client=http_client) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            catalog = await session.list_tools()
            print("Authorized tools:", [tool.name for tool in catalog.tools])
            prompt = await session.get_prompt("supplier_review_v1")
            print("Approved prompt:", prompt.description)
            result = await session.call_tool("documents.read", {"document_id": os.getenv("ACTIONGATE_DOCUMENT_ID", "supplier-acme-1")})
            evidence = result.structuredContent or {}
            print({key: evidence.get(key) for key in ("id", "decision", "status", "label")})
            if result.isError or evidence.get("status") != "completed":
                raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
