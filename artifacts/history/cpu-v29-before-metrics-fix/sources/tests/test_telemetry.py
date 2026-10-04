"""Metadata-only traces and per-operation timing isolation."""
import asyncio
import json

import httpx
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from actiongate import telemetry


async def test_concurrent_operations_keep_phase_timings_separate():
    async def operation(name):
        with telemetry.capture_phases():
            with telemetry.timed_phase(name):
                await asyncio.sleep(.002)
            return telemetry.phase_snapshot()
    first, second = await asyncio.gather(operation("guard_ms"), operation("upstream_ms"))
    assert set(first) == {"guard_ms"} and first["guard_ms"] > 0
    assert set(second) == {"upstream_ms"} and second["upstream_ms"] > 0
    assert telemetry.phase_snapshot() == {}


def test_otlp_export_omits_exception_payload_and_uses_valid_numeric_kind(monkeypatch):
    exporter = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(exporter))
    secret = "synthetic-sensitive-exception-content"
    try:
        with telemetry.span("broker.action", root_id="synthetic-root", tool="reports.save", generation=2):
            raise ValueError(secret)
    except ValueError:
        pass
    spans = exporter.get_finished_spans()
    assert len(spans) == 1 and not spans[0].events
    sent = []
    original = httpx.Client
    def receive(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(receive))
    monkeypatch.setattr(telemetry.httpx, "Client", client)
    assert telemetry.OTLPJSONExporter("http://operator-collector:4318").export(spans) == SpanExportResult.SUCCESS
    record = sent[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert record["kind"] == 1 and len(record["traceId"]) == 32
    assert secret not in json.dumps(sent)
    assert {item["key"] for item in record["attributes"]} == {"root_id", "tool", "generation"}
