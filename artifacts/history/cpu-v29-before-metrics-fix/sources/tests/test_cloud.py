"""Offline commercial-adapter contracts; no API calls or paid inference."""
from datetime import datetime, timedelta, timezone
import json
import socket

import httpx
import pytest

from actiongate import cloud
from actiongate.controls import ControlError

MESSAGES = [{"role": "user", "content": "Summarize public supplier registry fields."}]
TOOLS = [{"type": "function", "function": {"name": "calculator", "description": "Decimal arithmetic",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                   "required": ["expression"], "additionalProperties": False}}}]


@pytest.fixture
def catalog(monkeypatch):
    data = json.loads((cloud.ROOT / "policy/prices.json").read_text())
    now = datetime.now(timezone.utc)
    data.update(verified_at=(now-timedelta(minutes=1)).isoformat(),
                valid_until=(now+timedelta(days=1)).isoformat())
    original = cloud.load_price
    monkeypatch.setattr(cloud, "load_price", lambda value=None: original(data if value is None else value))
    monkeypatch.setenv("ACTIONGATE_CONNECTOR_KEY", "contract-connector-key-value-0123456789")
    return data


def provider_response(**usage):
    return {"id": "contract-provider", "model": cloud.MODEL, "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Synthetic answer"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                      "completion_tokens_details": {"reasoning_tokens": 30}, **usage}}


def test_quote_full_context_and_reasoning_upper_bound(catalog):
    result = cloud.quote("cloud-business", MESSAGES, 128)
    assert result.input_tokens_upper == 131072
    assert result.output_tokens_upper == 128
    assert result.usd_micros == 9869
    assert result == cloud.quote(cloud.MODEL, MESSAGES, 128)
    assert cloud.quote("cloud-business", MESSAGES * 2, 128).usd_micros == result.usd_micros
    assert cloud.quote("cloud-business", MESSAGES, 128, TOOLS).payload_hash != result.payload_hash
    payload = cloud._payload("cloud-business", MESSAGES, 128, TOOLS, cloud.load_price())
    assert payload["max_completion_tokens"] == 128
    assert payload["include_reasoning"] is False
    assert payload["parallel_tool_calls"] is False
    assert "max_tokens" not in payload


@pytest.mark.parametrize("configured", [False, True])
async def test_ready_reports_configuration_without_exposing_credentials(catalog, monkeypatch, configured):
    monkeypatch.setenv("GROQ_API_KEY", "contract-provider-private-value" if configured else "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud.app), base_url="http://connector") as client:
        response = await client.get("/health/ready")
    assert response.status_code == (200 if configured else 503)
    assert response.json()["configured"] is configured
    assert response.json()["price_ready"] is True
    assert "private-value" not in response.text and "connector-key" not in response.text


async def test_ready_rejects_expired_price(catalog, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "contract-provider-private-value")
    def expired(*args):
        raise ControlError("Invalid reviewed catalog")
    monkeypatch.setattr(cloud, "load_price", expired)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud.app), base_url="http://connector") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "price_unavailable", "provider": "groq", "configured": True, "price_ready": False}


@pytest.mark.parametrize("model,maximum,tools,messages", [
    ("arbitrary-model", 128, None, MESSAGES), ("cloud-business", 0, None, MESSAGES),
    ("cloud-business", 65537, None, MESSAGES), ("cloud-business", True, None, MESSAGES),
    ("cloud-business", 128, [{"type": "browser_search"}], MESSAGES),
    ("cloud-business", 128, None, [{"role": "user", "content": [{"type": "image_url"}]}]),
    ("cloud-business", 128, None, [{"role": "user", "content": "x"*66000}]),
    ("cloud-business", 128, None, [{"role": "user", "content": "hello", "url": "https://evil.test"}]),
])
def test_unsupported_payloads_fail_before_network(catalog, model, maximum, tools, messages):
    with pytest.raises(ControlError):
        cloud.quote(model, messages, maximum, tools)


def test_price_missing_invalid_expired_and_unbounded_rejected(catalog):
    for update in ({"model": "unapproved"}, {"valid_until": "2020-01-01T00:00:00Z"},
        {"valid_until": "2040-01-01T00:00:00Z"}, {"input_usd_micros_per_million": -1},
        {"input_usd_micros_per_million": True}, {"output_includes_reasoning": False}):
        with pytest.raises(ControlError):
            cloud.quote("cloud-business", MESSAGES, 128, price={**catalog, **update})


def test_settlement_includes_hidden_reasoning_and_ignores_cache_discount(catalog):
    quoted, price = cloud.quote("cloud-business", MESSAGES, 128), cloud.load_price()
    result = cloud.settle(provider_response(prompt_tokens_details={"cached_tokens": 100}), quoted, price)
    assert result["actual_usd_micros"] == 23
    assert result["settlement_status"] == "settled"


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 1},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4},
    {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": 1, "completion_tokens": -2, "total_tokens": -1},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3,
     "completion_tokens_details": {"reasoning_tokens": 10}}])
def test_missing_or_invalid_usage_preserves_uncertainty(catalog, usage):
    response = provider_response()
    response["usage"] = usage
    result = cloud.settle(response, cloud.quote("cloud-business", MESSAGES, 128), cloud.load_price())
    assert result["actual_usd_micros"] is None
    assert result["settlement_status"] == "usage_unknown"


def test_contract_violation_keeps_true_cost(catalog):
    response = provider_response(prompt_tokens=200000, completion_tokens=66000, total_tokens=266000)
    result = cloud.settle(response, cloud.quote("cloud-business", MESSAGES, 128), cloud.load_price())
    assert result["actual_usd_micros"] == 34800
    assert result["settlement_status"] == "contract_violation"


async def test_client_connector_roundtrip_and_function_preservation(catalog, monkeypatch):
    received = []
    async def fake_provider(payload):
        received.append(payload)
        response = provider_response()
        response["choices"][0]["message"] = {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "calculator",
                "arguments": '{"expression":"2+2"}'}}]}
        return response
    monkeypatch.setattr(cloud, "_provider", fake_provider)
    client = cloud.Client("http://connector", transport=httpx.ASGITransport(app=cloud.app))
    result = await client.chat("cloud-business", MESSAGES, 128, TOOLS, label="PUBLIC", generation=7,
                              operation_id="test-1", reserved_usd_micros=10000)
    assert len(received) == 1 and received[0]["tools"] == TOOLS
    assert result["settlement_status"] == "settled"
    assert result["response"]["choices"][0]["message"]["tool_calls"][0]["id"] == "call-1"
    assert result["generation"] == 7 and result["operation_id"] == "test-1"


async def test_no_paid_call_for_label_or_reservation_failure(catalog, monkeypatch):
    async def forbidden(_):
        pytest.fail("Provider must not be called")
    monkeypatch.setattr(cloud, "_provider", forbidden)
    client = cloud.Client("http://connector", transport=httpx.ASGITransport(app=cloud.app))
    for label, money in [("CONFIDENTIAL", 10000), ("PUBLIC", 9868)]:
        with pytest.raises(ControlError):
            await client.chat("cloud-business", MESSAGES, 128, label=label, generation=1,
                              operation_id="test", reserved_usd_micros=money)


async def test_connector_auth_hash_price_and_label_validation(catalog, monkeypatch):
    async def forbidden(_):
        pytest.fail("Provider must not be called")
    monkeypatch.setattr(cloud, "_provider", forbidden)
    body = {"model": "cloud-business", "messages": MESSAGES, "max_tokens": 128, "label": "PUBLIC",
            "generation": 1, "operation_id": "test", "reserved_usd_micros": 10000, "price": catalog,
            "payload_hash": cloud.quote("cloud-business", MESSAGES, 128).payload_hash}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud.app), base_url="http://connector") as client:
        assert (await client.post("/chat", json=body)).status_code == 401
        for update, code in [({"payload_hash": "0"*64}, 403), ({"label": "INTERNAL"}, 422),
                             ({"price": {**catalog, "input_usd_micros_per_million": 1}}, 403)]:
            response = await client.post("/chat", json={**body, **update},
                headers={"X-Connector-Key": "contract-connector-key-value-0123456789"})
            assert response.status_code == code
            assert MESSAGES[0]["content"] not in response.text


async def test_timeout_no_retry_and_unknown_usage(catalog, monkeypatch):
    calls = []
    async def timeout(payload):
        calls.append(payload)
        raise httpx.ReadTimeout("sensitive upstream error")
    monkeypatch.setattr(cloud, "_provider", timeout)
    result = await cloud.Client("http://connector", transport=httpx.ASGITransport(app=cloud.app)).chat(
        "cloud-business", MESSAGES, 128, label="PUBLIC", generation=1,
        operation_id="test", reserved_usd_micros=10000)
    assert len(calls) == 1
    assert result["settlement_status"] == "usage_unknown" and result["actual_usd_micros"] is None
    assert "sensitive" not in json.dumps(result)


async def test_result_rebind_rejected(catalog):
    async def substituted(request):
        return httpx.Response(200, json={"generation": 1, "operation_id": "other"})
    result = await cloud.Client("http://connector", transport=httpx.MockTransport(substituted)).chat(
        "cloud-business", MESSAGES, 128, label="PUBLIC", generation=1,
        operation_id="test", reserved_usd_micros=10000)
    assert result["settlement_status"] == "usage_unknown"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0"])
async def test_dns_private_destinations_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ControlError):
        await cloud._public_ip()


async def test_dns_public_pinned(monkeypatch):
    looked_up = []
    def lookup(*args):
        looked_up.append(args)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    assert await cloud._public_ip() == "8.8.8.8"
    assert looked_up == [("api.groq.com", 443, socket.AF_INET, socket.SOCK_STREAM)]


async def test_rate_limit_is_bound_to_operation_and_keeps_cost_unknown(catalog, monkeypatch):
    async def rejected(_payload):
        raise cloud.RateLimited(2)
    monkeypatch.setattr(cloud, "_provider", rejected)
    quoted = cloud.quote("cloud-business", MESSAGES, 128)
    result = await cloud.Client("http://connector", transport=httpx.ASGITransport(app=cloud.app)).chat(
        "cloud-business", MESSAGES, 128, label="PUBLIC", generation=4,
        operation_id="retry-contract", reserved_usd_micros=quoted.usd_micros)
    assert result["rejection"] == {"provider": "groq", "status": 429, "retry_after_seconds": 2}
    assert result["generation"] == 4 and result["operation_id"] == "retry-contract"
    assert result["usage"] is None and result["actual_usd_micros"] is None
    assert result["settlement_status"] == "usage_unknown"


@pytest.mark.parametrize("status,delay,retryable", [(429, "2", True), (429, None, True),
    (429, "NaN", False), (429, "-1", False), (429, "31", False), (503, "2", False)])
async def test_only_provider_429_with_bounded_delay_is_retryable(monkeypatch, status, delay, retryable):
    calls = []
    async def transport(request):
        calls.append(request)
        return httpx.Response(status, content=b"private error body never exposed",
                              headers={} if delay is None else {"retry-after": delay})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)))
    async def address():
        return "8.8.8.8"
    monkeypatch.setattr(cloud, "_public_ip", address)
    monkeypatch.setenv("GROQ_API_KEY", "contract-placeholder-key-not-a-secret")
    with pytest.raises(ControlError) as error:
        await cloud._provider({"model": cloud.MODEL})
    assert isinstance(error.value, cloud.RateLimited) is retryable
    assert "private error body" not in str(error.value)
    assert len(calls) == 1  # connector never owns a hidden retry loop


@pytest.mark.parametrize("status,content,headers", [
    (302, b"", {"Location": "https://attacker.invalid/collect"}),
    (200, b"x" * (cloud.MAX_RESPONSE_BYTES + 1), {}),
    (200, b'{"usage": NaN}', {}), (429, b"Sensitive provider error", {}),
], ids=["redirect", "response_buffer_limit", "nonfinite_json", "provider_error"])
async def test_provider_redirect_error_and_buffer_limits_do_not_retry(monkeypatch, status, content, headers):
    calls = []
    async def transport(request):
        calls.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "api.groq.com"
        assert request.extensions["sni_hostname"] == "api.groq.com"
        return httpx.Response(status, content=content, headers=headers)
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(transport))
    async def address():
        return "8.8.8.8"
    monkeypatch.setattr(cloud, "_public_ip", address)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("GROQ_API_KEY", "contract-placeholder-key-not-a-secret")
    with pytest.raises(ControlError):
        await cloud._provider({"model": cloud.MODEL})
    assert len(calls) == 1


@pytest.mark.live_provider
def test_live_provider_through_guarded_gateway():
    import os
    import subprocess
    import sys
    if os.getenv("ACTIONGATE_LIVE_PROVIDER") != "1":
        pytest.skip("not run: explicit cloud profile and bounded live-provider execution are required")
    completed = subprocess.run([sys.executable, str(cloud.ROOT / "scripts/live_gateway.py"),
        "--url", os.getenv("SERVICE_BASE_URL", "http://127.0.0.1:8080")],
        cwd=cloud.ROOT, capture_output=True, text=True, timeout=900)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((cloud.ROOT / "artifacts/reports/live-provider-gateway.json").read_text())
    assert report["status"] == "passed" and report["original_policy_restored"] is True
    assert 0 < report["actual_usd_micros"] <= report["max_spend_usd_micros"]
