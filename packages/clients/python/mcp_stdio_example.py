"""Official MCP SDK over the registered local stdio process."""
import asyncio
import os
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from stdio_launcher import registered_stdio


async def main():
    process = registered_stdio(os.environ["ACTIONGATE_WORKLOAD_TOKEN"],
        os.getenv("ACTIONGATE_BASE_URL", "http://127.0.0.1:8080"))
    async with stdio_client(process) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Authorized tools:", [tool.name for tool in (await session.list_tools()).tools])
            result = await session.call_tool("calculator.evaluate", {"expression": "12 * 8"})
            print({key: (result.structuredContent or {}).get(key) for key in ("id", "status", "decision")})


if __name__ == "__main__":
    asyncio.run(main())
