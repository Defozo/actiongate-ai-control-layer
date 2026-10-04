"""One bounded synthetic function-call through the deployed cloud connector.

The trusted runner receives only ACTIONGATE_CONNECTOR_KEY. GROQ_API_KEY stays in
the separate service. This proves a provider-emitted function proposal and its
usage contract, not a guard decision, gateway ledger or executed business tool.
"""
from __future__ import annotations

import asyncio
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def producer_sources():
    paths = ("scripts/live_provider.py", "backend/actiongate/cloud.py",
        "policy/prices.json", "models/cloud-model-manifest.json")
    return {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}


def remaining_budget(first_report, next_quote_usd_micros, total_cap=10000):
    """Unknown cost retains the whole first reservation; never assume zero."""
    actual = first_report.get("actual_usd_micros")
    if (type(total_cap) is not int or not 1 <= total_cap <= 10000
            or first_report.get("status") != "passed" or first_report.get("settlement_status") != "settled"
            or type(actual) is not int or not 0 < actual <= total_cap
            or type(next_quote_usd_micros) is not int or next_quote_usd_micros <= 0):
        raise ValueError("A passed, positively settled first attempt is required")
    available = total_cap-actual
    if next_quote_usd_micros > available:
        raise ValueError("Remaining shared budget cannot cover the next full reservation")
    return available


def validated_function_call(response):
    from actiongate.controls.signed import load_json
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("Expected one complete function-call choice")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("Expected an assistant message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if (choice.get("finish_reason") != "tool_calls" or message.get("role") != "assistant"
            or not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict)):
        raise ValueError("Expected one completed assistant tool call")
    call = calls[0]
    function = call.get("function")
    if (call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"]
            or not isinstance(function, dict) or function.get("name") != "calculator"
            or not isinstance(function.get("arguments"), str)):
        raise ValueError("Provider did not emit the requested calculator function")
    arguments = load_json(function["arguments"].encode(), max_bytes=1024)
    if arguments != {"expression": "2+2"}:
        raise ValueError("Provider function arguments differ from the synthetic task")
    return {"name": "calculator", "arguments": arguments, "call_id": call["id"]}


async def main(args):
    sources = producer_sources()
    target = ROOT / "artifacts/reports/live-provider.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {"suite": "live-provider", "mode": "real_provider_connector", "provider": "groq",
              "checked_at": datetime.now(timezone.utc).isoformat(), "max_spend_usd_micros": args.max_spend_usd_micros,
              "status": "running", "scope": "authenticated deployed connector, actual provider function proposal and usage; no function is executed",
              "gateway_ledger_verified_by_this_report": False, "guard_verified_by_this_report": False,
              "connector_url": args.connector_url, "connector_correlation_generation": args.generation,
              "producer_sources": sources}
    target.write_text(json.dumps(report, indent=2)+"\n")
    if not os.getenv("ACTIONGATE_CONNECTOR_KEY"):
        report.update(status="not_run", reason="Required psst credentials were not injected")
        target.write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report))
        return 3
    import httpx
    from actiongate.cloud import Client, MODEL, quote, load_price
    messages = [{"role": "system", "content": "Use the supplied calculator function for arithmetic."},
                {"role": "user", "content": "Call the calculator function with expression 2+2. Do not answer directly."}]
    tools = [{"type": "function", "function": {"name": "calculator", "description": "Evaluate simple arithmetic",
              "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                             "required": ["expression"], "additionalProperties": False}}}]
    try:
        # The only remote endpoint is the operator-selected private connector;
        # its own fixed-host adapter independently enforces provider DNS/TLS/price.
        address = httpx.URL(args.connector_url)
        if address.scheme != "http" or address.host != "cloud-connector" or address.port != 8030 or address.path not in {"", "/"} or address.userinfo or address.query or address.fragment:
            raise ValueError("Smoke requires the private registered cloud-connector:8030 endpoint")
        price = load_price()
        quoted = quote("cloud-business", messages, 256, tools, price)
        report.update(price_catalog=price.model_dump(), cloud_model_manifest=json.loads((ROOT/"models/cloud-model-manifest.json").read_text()),
            quote=asdict(quoted), binding_scope="exact runner source, current approved price and cloud manifest; connector independently checks the identical price")
        if quoted.usd_micros > args.max_spend_usd_micros:
            raise ValueError("Verified reservation exceeds the explicitly bounded smoke budget")
        async with httpx.AsyncClient(timeout=6, follow_redirects=False, trust_env=False) as readiness:
            ready = await readiness.get(args.connector_url.rstrip("/")+"/health/ready")
        state = ready.json()
        if ready.status_code != 200 or state.get("configured") is not True or state.get("price_ready") is not True:
            raise ValueError("Deployed connector did not confirm readiness")
        start = time.perf_counter()
        result = await Client(args.connector_url).chat(
            "cloud-business", messages, 256, tools, label="PUBLIC", generation=args.generation,
            operation_id="live-provider-"+uuid.uuid4().hex, reserved_usd_micros=quoted.usd_micros, price=price)
        response = result.get("response") or {}
        report.update(model=MODEL, latency_ms=round((time.perf_counter()-start)*1000, 2),
            quote=result["quote"], usage=result.get("usage"), actual_usd_micros=result.get("actual_usd_micros"),
            settlement_status=result.get("settlement_status"), provider_response_id=response.get("id"))
        actual = result.get("actual_usd_micros")
        if result.get("settlement_status") != "settled" or type(actual) is not int or not 0 < actual <= args.max_spend_usd_micros:
            raise ValueError("Provider usage is unknown, invalid or beyond its verified bound")
        call = validated_function_call(response)
        report.update(status="passed", function_calls=[call["name"]], validated_function_call=call,
            tool_executed=False, connector_operation_id=result["operation_id"])
    except Exception as exc:
        # Provider errors must never be copied into logs or artifact text.
        report.update(status="failed", failure=type(exc).__name__)
    finally:
        report["producer_source_stable"] = sources == producer_sources()
        if not report["producer_source_stable"]:
            report.update(status="failed", failure="SourceChangedDuringExecution")
        target.write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--connector-url", default="http://cloud-connector:8030")
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--max-spend-usd-micros", type=int, default=10000)
    args = parser.parse_args()
    if args.generation < 1 or not 1 <= args.max_spend_usd_micros <= 10000:
        parser.error("A positive correlation generation and a budget of 1..10000 micro-USD are required")
    raise SystemExit(asyncio.run(main(args)))
