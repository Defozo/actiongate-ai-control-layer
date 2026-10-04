"""Optional Groq connector. Only this service receives the provider credential.

The input reservation deliberately uses the model's entire context limit. It is
an upper bound, not a tokenizer estimate. max_completion_tokens includes hidden
reasoning. No discounted cache prices, built-in tools, redirects or retries.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hmac
import ipaddress
import os
import socket
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
import jsonschema
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .controls.signed import ControlError, bounded_structure, canonical_json, digest, load_json
from .settings import ROOT, secret

HOST = "api.groq.com"
MODEL = "openai/gpt-oss-20b"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 1_048_576


class RateLimited(ControlError):
    """A complete provider rejection, safe to repeat only for model proposals.

    This is deliberately NOT proof of zero billed usage. Each rejected attempt
    retains its reservation until independently reconciled.
    """
    def __init__(self, retry_after_seconds: float):
        super().__init__("Approved provider rejected the request at its rate limit")
        self.retry_after_seconds = retry_after_seconds


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    revision: str = Field(min_length=1, max_length=120)
    provider: Literal["groq"]
    model: Literal["openai/gpt-oss-20b"]
    currency: Literal["USD"]
    unit: Literal["per_1000000_tokens"]
    input_usd_micros_per_million: int = Field(gt=0)
    output_usd_micros_per_million: int = Field(gt=0)
    context_tokens: Literal[131072]
    max_completion_tokens: Literal[65536]
    output_includes_reasoning: Literal[True]
    additional_fees: Literal["none_for_text_and_local_function_calls"]
    source: Literal["https://console.groq.com/docs/model/openai/gpt-oss-20b"]
    verified_at: str
    valid_until: str

    @model_validator(mode="after")
    def fresh(self):
        try:
            verified = datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
            expiry = datetime.fromisoformat(self.valid_until.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if not verified.tzinfo or not expiry.tzinfo or not verified <= now < expiry:
                raise ValueError("Price is not currently valid")
            if (expiry - verified).total_seconds() > 7 * 86400:
                raise ValueError("Price verification is older than the seven-day review interval")
        except (TypeError, ValueError) as exc:
            raise ValueError("A currently valid, recently verified price is required") from exc
        return self


def load_price(value: dict | Price | None = None) -> Price:
    try:
        if value is None:
            value = load_json((ROOT / "policy/prices.json").read_bytes())
        if isinstance(value, Price):
            value = value.model_dump()
        return Price.model_validate(value)
    except (OSError, ValidationError, ValueError) as exc:
        raise ControlError("Cloud price is absent, invalid or expired") from exc


@dataclass(frozen=True)
class Quote:
    input_tokens_upper: int
    output_tokens_upper: int
    usd_micros: int
    price_revision: str
    payload_hash: str


def _cost(prompt: int, completion: int, price: Price) -> int:
    # Integer ceiling prevents binary floating-point and sub-micro rounding gaps.
    return (prompt * price.input_usd_micros_per_million +
            completion * price.output_usd_micros_per_million + 999_999) // 1_000_000


def _payload(model: str, messages: list[dict], max_tokens: int, tools: list[dict] | None, price: Price):
    if model not in {"cloud-business", MODEL}:
        raise ControlError("Model is outside the approved cloud registry")
    if type(max_tokens) is not int or not 1 <= max_tokens <= price.max_completion_tokens:
        raise ControlError("Invalid total completion token limit")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        raise ControlError("Unsupported cloud message sequence")
    for message in messages:
        if not isinstance(message, dict) or set(message) - {"role", "content", "tool_calls", "tool_call_id", "name"}:
            raise ControlError("Unsupported cloud message field")
        if message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ControlError("Unsupported cloud message role")
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ControlError("Only text cloud messages are supported")
        if not message.get("content") and not message.get("tool_calls"):
            raise ControlError("Cloud message has no content")
    if tools is not None:
        if not isinstance(tools, list) or not 1 <= len(tools) <= 32:
            raise ControlError("Invalid cloud function list")
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) != {"type", "function"} or tool["type"] != "function":
                raise ControlError("Only local function calling is approved")
            function = tool["function"]
            if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
                raise ControlError("Unsupported cloud function field")
            if not isinstance(function.get("name"), str) or not function["name"]:
                raise ControlError("Cloud function name is required")
            parameters = function.get("parameters", {})
            if not isinstance(parameters, dict) or parameters.get("type") != "object" or parameters.get("additionalProperties") is not False:
                raise ControlError("Cloud function schema must be a closed object")
            bounded_structure(parameters)
            def validate_refs(item):
                if isinstance(item, dict):
                    if "$ref" in item or "$dynamicRef" in item:
                        raise ControlError("Cloud schema references are not supported")
                    for child in item.values():
                        validate_refs(child)
                elif isinstance(item, list):
                    for child in item:
                        validate_refs(child)
            validate_refs(parameters)
            try:
                jsonschema.Draft202012Validator.check_schema(parameters)
            except jsonschema.SchemaError as exc:
                raise ControlError("Invalid cloud function schema") from exc
    payload = {"model": MODEL, "messages": messages, "max_completion_tokens": max_tokens,
               "stream": False, "temperature": 0, "reasoning_effort": "low", "include_reasoning": False}
    if tools:
        payload.update(tools=tools, parallel_tool_calls=False, tool_choice="auto")
    bounded_structure(payload)
    if len(canonical_json(payload)) > MAX_REQUEST_BYTES:
        raise ControlError("Cloud request exceeds the complete payload limit")
    return payload


def quote(model, messages, max_tokens, tools=None, price=None) -> Quote:
    verified = load_price(price)
    payload = _payload(model, messages, max_tokens, tools, verified)
    return Quote(verified.context_tokens, max_tokens, _cost(verified.context_tokens, max_tokens, verified),
                 verified.revision, digest(payload))


def settle(response: dict, quoted: Quote, price: Price) -> dict:
    """Malformed/missing usage retains the reservation; excess preserves real cost."""
    usage = response.get("usage") if isinstance(response, dict) else None
    result = {"response": response, "usage": usage, "actual_usd_micros": None,
              "settlement_status": "usage_unknown", "quote": asdict(quoted)}
    if not isinstance(usage, dict):
        return result
    if any(type(usage.get(key)) is not int or usage[key] < 0
           for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return result
    prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
    if usage["total_tokens"] != prompt + completion:
        return result
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens", 0) if isinstance(details, dict) else -1
    if type(reasoning) is not int or not 0 <= reasoning <= completion:
        return result
    actual = _cost(prompt, completion, price)
    violated = (prompt > quoted.input_tokens_upper or completion > quoted.output_tokens_upper
                or actual > quoted.usd_micros or response.get("model") != MODEL)
    result.update(actual_usd_micros=actual, settlement_status="contract_violation" if violated else "settled")
    return result


async def _public_ip() -> str:
    # Resolve once and connect to this literal address. Host and TLS SNI remain
    # fixed to the approved host, eliminating a second DNS/rebinding lookup.
    records = await asyncio.to_thread(socket.getaddrinfo, HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
    addresses = [item[4][0] for item in records]
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ControlError("Cloud destination did not resolve to approved public addresses")
    return addresses[0]


async def _provider(payload: dict) -> dict:
    key = secret("GROQ_API_KEY")
    address = await _public_ip()
    async with httpx.AsyncClient(timeout=45, follow_redirects=False, trust_env=False) as client:
        async with client.stream("POST", f"https://{address}/openai/v1/chat/completions",
                headers={"Authorization": "Bearer " + key, "Host": HOST}, json=payload,
                extensions={"sni_hostname": HOST}) as response:
            if response.status_code == 429:
                # Groq documents Retry-After as seconds for rate-limit replies.
                # Never parse, store or relay the potentially sensitive body.
                delay = response.headers.get("retry-after", "1")
                try:
                    delay = float(delay)
                except (TypeError, ValueError):
                    delay = float("inf")
                if 0 <= delay <= 30:
                    raise RateLimited(delay)
            if response.status_code != 200:
                # Never include upstream errors: they can echo credentials or input.
                raise ControlError("Cloud upstream did not return a complete successful response")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ControlError("Cloud response exceeds the protected buffer")
    result = load_json(bytes(raw), max_bytes=MAX_RESPONSE_BYTES)
    if not isinstance(result, dict):
        raise ControlError("Invalid cloud response structure")
    return result


class Dispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str
    messages: list[dict]
    max_tokens: int = Field(ge=1, le=65536)
    tools: list[dict] | None = None
    label: Literal["PUBLIC"]
    generation: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")
    reserved_usd_micros: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    price: dict


app = FastAPI(title="ActionGate isolated cloud connector", docs_url=None, redoc_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _error):
    return JSONResponse(status_code=422, content={"detail": "Invalid cloud dispatch contract"})


@app.get("/health/live")
async def live():
    return {"status": "live", "provider": "groq"}


@app.get("/health/ready")
async def ready():
    configured = bool(os.getenv("GROQ_API_KEY", "").strip() and os.getenv("ACTIONGATE_CONNECTOR_KEY", "").strip())
    try:
        load_price()
        price_ready = True
    except ControlError:
        price_ready = False
    is_ready = configured and price_ready
    return JSONResponse(status_code=200 if is_ready else 503, content={
        "status": "ready" if is_ready else "not_configured" if not configured else "price_unavailable",
        "provider": "groq", "configured": configured, "price_ready": price_ready})


@app.post("/chat")
async def dispatch(body: Dispatch, x_connector_key: str | None = Header(default=None)):
    if not x_connector_key or not hmac.compare_digest(x_connector_key, secret("ACTIONGATE_CONNECTOR_KEY")):
        raise HTTPException(401, "Authenticated connector identity is required")
    try:
        price = load_price(body.price)
        # Signed policy snapshot may pin this exact approved price. An arbitrary
        # caller-supplied price cannot lower the independently loaded connector rate.
        if price.model_dump() != load_price().model_dump():
            raise ControlError("Cloud price differs from the connector's approved catalog")
        quoted = quote(body.model, body.messages, body.max_tokens, body.tools, price)
        if body.payload_hash != quoted.payload_hash or body.reserved_usd_micros < quoted.usd_micros:
            raise ControlError("Cloud dispatch is not bound to a sufficient reservation")
        payload = _payload(body.model, body.messages, body.max_tokens, body.tools, price)
    except ControlError as exc:
        raise HTTPException(403, str(exc)) from None
    try:
        response = await _provider(payload)
        result = settle(response, quoted, price)
    except RateLimited as exc:
        result = {"response": None, "usage": None, "actual_usd_micros": None,
                  "settlement_status": "usage_unknown", "quote": asdict(quoted),
                  "rejection": {"provider": "groq", "status": 429,
                                "retry_after_seconds": exc.retry_after_seconds}}
    except (httpx.HTTPError, OSError, ControlError, RuntimeError):
        # A failed network response does not establish zero provider usage.
        result = {"response": None, "usage": None, "actual_usd_micros": None,
                  "settlement_status": "usage_unknown", "quote": asdict(quoted)}
    return {**result, "generation": body.generation, "operation_id": body.operation_id}


class Client:
    def __init__(self, url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        self.url = (url or os.getenv("CLOUD_CONNECTOR_URL", "http://cloud-connector:8030")).rstrip("/")
        self.transport = transport

    async def chat(self, model, messages, max_tokens, tools=None, *, label, generation,
                   operation_id, reserved_usd_micros, price=None):
        if label != "PUBLIC":
            raise ControlError("Cloud connector accepts only approved PUBLIC context")
        verified = load_price(price)
        quoted = quote(model, messages, max_tokens, tools, verified)
        if reserved_usd_micros < quoted.usd_micros:
            raise ControlError("Cloud call requires a sufficient prior reservation")
        body = Dispatch(model=model, messages=messages, max_tokens=max_tokens, tools=tools, label=label,
            generation=generation, operation_id=operation_id, reserved_usd_micros=reserved_usd_micros,
            payload_hash=quoted.payload_hash, price=verified.model_dump())
        async with httpx.AsyncClient(timeout=55, follow_redirects=False, trust_env=False,
                                     transport=self.transport) as client:
            try:
                response = await client.post(self.url + "/chat", json=body.model_dump(),
                    headers={"X-Connector-Key": secret("ACTIONGATE_CONNECTOR_KEY")})
                if response.status_code != 200:
                    raise ControlError("Cloud connector did not confirm a completed dispatch")
                result = load_json(response.content, max_bytes=MAX_RESPONSE_BYTES)
                if (result.get("generation") != generation or result.get("operation_id") != operation_id
                        or result.get("quote") != asdict(quoted)):
                    raise ControlError("Cloud connector result is not bound to the admitted operation")
                if result.get("response") is not None:
                    return {**settle(result["response"], quoted, verified),
                            "generation": generation, "operation_id": operation_id}
                if result.get("settlement_status") != "usage_unknown":
                    raise ControlError("Invalid cloud settlement response")
                if "rejection" in result:
                    rejection = result["rejection"]
                    if (not isinstance(rejection, dict) or set(rejection) != {"provider", "status", "retry_after_seconds"}
                            or rejection["provider"] != "groq" or rejection["status"] != 429
                            or type(rejection["retry_after_seconds"]) not in (float, int)
                            or not 0 <= rejection["retry_after_seconds"] <= 30
                            or result.get("usage") is not None or result.get("actual_usd_micros") is not None):
                        raise ControlError("Invalid rate-limit rejection contract")
                return result
            except (httpx.HTTPError, ControlError, AttributeError, TypeError):
                return {"response": None, "usage": None, "actual_usd_micros": None,
                        "settlement_status": "usage_unknown", "quote": asdict(quoted)}
