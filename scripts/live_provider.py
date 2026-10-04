"""One real synthetic Groq function-call smoke, bounded below USD 0.01.

Run through psst GROQ_API_KEY ACTIONGATE_CONNECTOR_KEY -- uv run python
scripts/live_provider.py. This tests the isolated connector, not the gateway
ledger. Full gateway accounting has separate integration tests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def main():
    target = ROOT / "artifacts/reports/live-provider.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {"suite": "live-provider", "mode": "real_provider_connector", "provider": "groq",
              "checked_at": datetime.now(timezone.utc).isoformat(), "max_spend_usd_micros": 10000,
              "scope": "authenticated isolated connector, usage and local function calling",
              "gateway_ledger_verified_by_this_report": False}
    if not os.getenv("GROQ_API_KEY") or not os.getenv("ACTIONGATE_CONNECTOR_KEY"):
        report.update(status="not_run", reason="Required psst credentials were not injected")
        target.write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report))
        return 3
    import httpx
    from actiongate.cloud import Client, MODEL, app, quote
    messages = [{"role": "system", "content": "Use the supplied calculator function for arithmetic."},
                {"role": "user", "content": "Call the calculator function with expression 2+2. Do not answer directly."}]
    tools = [{"type": "function", "function": {"name": "calculator", "description": "Evaluate simple arithmetic",
              "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                             "required": ["expression"], "additionalProperties": False}}}]
    quoted = quote("cloud-business", messages, 256, tools)
    if quoted.usd_micros > report["max_spend_usd_micros"]:
        raise RuntimeError("Verified reservation exceeds the explicitly bounded smoke budget")
    start = time.perf_counter()
    result = await Client("http://connector", transport=httpx.ASGITransport(app=app)).chat(
        "cloud-business", messages, 256, tools, label="PUBLIC", generation=1,
        operation_id="live-provider-"+uuid.uuid4().hex, reserved_usd_micros=quoted.usd_micros)
    response = result.get("response") or {}
    calls = [call for choice in response.get("choices", []) for call in choice.get("message", {}).get("tool_calls", [])]
    report.update(model=MODEL, latency_ms=round((time.perf_counter()-start)*1000, 2),
                  quote=result["quote"], usage=result.get("usage"),
                  actual_usd_micros=result.get("actual_usd_micros"),
                  settlement_status=result.get("settlement_status"), provider_response_id=response.get("id"),
                  function_calls=[call.get("function", {}).get("name") for call in calls],
                  status="passed" if result.get("settlement_status") == "settled" and
                      result["actual_usd_micros"] <= 10000 and calls else "failed")
    target.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
