"""OpenTelemetry spans contain control metadata only, never request bodies."""
import os
import time
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import httpx
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult


class OTLPJSONExporter(SpanExporter):
    """OTLP/HTTP JSON transport, enabled only with an operator endpoint."""
    def __init__(self, endpoint):
        self.endpoint = endpoint.rstrip("/") + "/v1/traces"

    def export(self, spans):
        def typed(value):
            if isinstance(value, bool):
                return {"boolValue": value}
            if isinstance(value, int):
                return {"intValue": str(value)}
            if isinstance(value, float):
                return {"doubleValue": value}
            return {"stringValue": str(value)}
        records = [{"traceId": f"{span.context.trace_id:032x}", "spanId": f"{span.context.span_id:016x}",
            "parentSpanId": f"{span.parent.span_id:016x}" if span.parent else "", "name": span.name,
            "kind": span.kind.value + 1, "startTimeUnixNano": str(span.start_time), "endTimeUnixNano": str(span.end_time),
            "attributes": [{"key": key, "value": typed(value)} for key, value in span.attributes.items()]}
            for span in spans]
        body = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "actiongate-gateway"}}]},
            "scopeSpans": [{"scope": {"name": "actiongate"}, "spans": records}]}]}
        try:
            with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
                client.post(self.endpoint, json=body).raise_for_status()
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE


provider = TracerProvider(resource=Resource.create({"service.name": "actiongate-gateway"}))
if endpoint := os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    provider.add_span_processor(BatchSpanProcessor(OTLPJSONExporter(endpoint), max_queue_size=2048))
tracer = provider.get_tracer("actiongate", "0.1.0")
_phases = ContextVar("actiongate_phase_timings", default=None)


@contextmanager
def capture_phases():
    token = _phases.set({})
    try:
        yield
    finally:
        _phases.reset(token)


def phase_snapshot():
    return {key: round(value, 3) for key, value in (_phases.get() or {}).items()}


@contextmanager
def timed_phase(name):
    began = time.perf_counter()
    try:
        yield
    finally:
        phases = _phases.get()
        if phases is not None:
            phases[name] = phases.get(name, 0) + (time.perf_counter()-began)*1000


def measured(name):
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            with timed_phase(name):
                return function(*args, **kwargs)
        return wrapper
    return decorate


def span(name, **metadata):
    return tracer.start_as_current_span(name, attributes=metadata, record_exception=False, set_status_on_exception=False)
