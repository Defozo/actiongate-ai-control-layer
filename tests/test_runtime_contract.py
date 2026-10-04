"""Accounting boundaries of the authenticated local-worker transport."""
import functools
import json
from types import SimpleNamespace

import httpx
import pytest

from actiongate import runtime


@pytest.mark.parametrize("status", [401, 409, 413, 422, 429, 503])
async def test_pre_inference_rejections_have_known_zero_usage(monkeypatch, status):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json={"detail": "Rejected before inference"}))
    monkeypatch.setattr(runtime.httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(runtime.RuntimeFailure) as error:
        await runtime.WorkerClient("guard").infer([{"role": "user", "content": "Synthetic input"}], 32)
    assert error.value.usage["usage_unknown"] is False
    assert error.value.usage["total_tokens"] == 0
    assert error.value.usage["inference_slot_seconds"] == 0


async def test_confirmed_stop_does_not_invent_token_usage(monkeypatch):
    usage = {"inference_slot_seconds": 1.2, "cpu_seconds": .9, "usage_unknown": True}
    stop = {"confirmed": True, "process_count": 2}
    transport = httpx.MockTransport(lambda _: httpx.Response(504, json={"detail": {"usage": usage, "stop": stop}}))
    monkeypatch.setattr(runtime.httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(runtime.RuntimeFailure) as error:
        await runtime.WorkerClient("business").infer([{"role": "user", "content": "Synthetic input"}], 32)
    assert error.value.usage == usage
    assert error.value.stop == stop


async def test_transport_failure_never_claims_confirmed_stop(monkeypatch):
    def disconnected(_):
        raise httpx.ReadTimeout("Disconnected from supervisor")
    transport = httpx.MockTransport(disconnected)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(runtime.RuntimeFailure) as error:
        await runtime.WorkerClient("guard").infer([{"role": "user", "content": "Synthetic input"}], 32)
    assert error.value.usage["usage_unknown"] is True
    assert error.value.stop is None


async def test_generation_response_mismatch_preserves_real_usage(monkeypatch):
    usage = {"total_tokens": 42, "usage_unknown": False}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"generation": 8, "usage": usage}))
    monkeypatch.setattr(runtime.httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(runtime.RuntimeFailure) as error:
        await runtime.WorkerClient("guard").infer([{"role": "user", "content": "Synthetic input"}], 32, generation=9)
    assert error.value.usage == usage
    assert "another control generation" in str(error.value)


@pytest.mark.parametrize("field", ["expected_model_digest", "expected_manifest_sha256"])
async def test_inference_digest_mismatch_retains_actual_usage(monkeypatch, field):
    usage = {"total_tokens": 42, "inference_slot_seconds": 1.5, "usage_unknown": False}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"generation": 9, "model_digest": "wrong", "model_manifest_sha256": "wrong", "usage": usage}))
    monkeypatch.setattr(runtime.httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(runtime.RuntimeFailure, match="signed model") as error:
        await runtime.WorkerClient("guard").infer([{"role": "user", "content": "Synthetic input"}], 32, generation=9, **{field: "approved"})
    assert error.value.usage == usage


async def test_configuration_ack_binds_exact_config_digest(monkeypatch):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"generation": 9, "prepared": True, "configuration_digest": "wrong"}))
    monkeypatch.setattr(runtime.httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    with pytest.raises(runtime.RuntimeFailure):
        await runtime.WorkerClient("guard").configure(9, {"semantic": {"deadline_seconds": 60}, "local_resources": {"max_waiting_jobs": 2}})


async def test_input_budget_counts_exact_worker_messages_and_tools(monkeypatch):
    model = runtime.BusinessModel()
    messages = [
        {"role": "user", "content": "Oblicz wartość"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "calculate", "arguments": '{"value":123}'}}]},
        {"role": "tool", "name": "calculate", "tool_call_id": "call_1", "content": "123"},
    ]
    tools = [{"type": "function", "function": {"name": "calculate", "description": "Numerical calculator", "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}}}}]
    sent = {}

    async def infer(normalized, max_tokens, **kwargs):
        sent.update(messages=normalized, tools=kwargs["tools"], format=None)
        return {"message": {"content": "123"}, "usage": {}, "model_digest": "synthetic"}

    monkeypatch.setattr(model.worker, "infer", infer)
    upper = model.input_upper_bound(messages, tools)
    await model.chat(messages, 16, tools=tools)
    serialized = json.dumps(sent, ensure_ascii=False, separators=(",", ":"))
    expected = len(runtime.pinned_tokenizer().encode(serialized).ids) + 32 * len(messages) + 256
    assert upper == expected
    assert sent["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"value": 123}
    assert sent["messages"][2]["tool_name"] == "calculate"
    assert upper > model.input_upper_bound(messages)


def test_guard_and_business_share_loaded_pinned_tokenizer():
    from actiongate.semantic import SemanticGuard
    assert SemanticGuard().tokenizer is SemanticGuard().tokenizer is runtime.pinned_tokenizer()


def test_tool_result_mapping_preserves_calls_without_optional_name():
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "first", "function": {"name": "lookup", "arguments": '{"id":1}'}},
            {"id": "second", "function": {"name": "calculate", "arguments": '{"value":2}'}},
            {"id": "third", "function": {"name": "lookup", "arguments": '{"id":3}'}},
        ]},
        {"role": "tool", "tool_call_id": "third", "content": "third result"},
        {"role": "tool", "tool_call_id": "second", "content": "second result"},
        {"role": "tool", "tool_call_id": "first", "content": "first result"},
    ]
    normalized = runtime.BusinessModel.normalize_messages(messages)
    assert [(item["tool_name"], item["content"]) for item in normalized[1:]] == [
        ("lookup", "third result"), ("calculate", "second result"), ("lookup", "first result")]
    assert [item["tool_call_id"] for item in normalized[1:]] == ["third", "second", "first"]
    assert [item["id"] for item in normalized[0]["tool_calls"]] == ["first", "second", "third"]
    assert [item["function"]["index"] for item in normalized[0]["tool_calls"]] == [0, 1, 2]


async def test_semantic_deadline_stops_before_next_window_and_preserves_usage(monkeypatch):
    from actiongate import semantic
    guard = semantic.SemanticGuard()
    clock = [95.0]
    calls = []
    monkeypatch.setattr(semantic, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(guard, "_windows", lambda text, cfg: ["first", "second"])
    async def infer(messages, max_tokens, **kwargs):
        calls.append(kwargs["deadline_seconds"])
        clock[0] = 101.0
        return {"message": {"content": json.dumps({"risk_level": 0, "category": "none", "evidence": [], "reason": "Ordinary data"})},
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "inference_slot_seconds": 4.9, "cpu_seconds": 4.0}, "model_digest": "synthetic"}
    monkeypatch.setattr(guard.worker, "infer", infer)
    result = await guard.scan("first second", "review", "read", [], 1, deadline_at_monotonic=100.0)
    assert calls == [5.0]
    assert result["verdict"] == "unknown" and result["complete"] is False
    assert result["usage"]["total_tokens"] == 5
    assert result["usage"]["usage_unknown"] is False
    calls.clear()
    expired = await guard.scan("first second", "review", "read", [], 1, deadline_at_monotonic=100.0)
    assert not calls and expired["usage"]["total_tokens"] == 0


def test_semantic_reservation_includes_confirmed_stop_tolerance():
    from actiongate.semantic import SemanticGuard
    assert SemanticGuard().estimate("one short window", {"deadline_seconds": 90})["slot_seconds"] == 95
