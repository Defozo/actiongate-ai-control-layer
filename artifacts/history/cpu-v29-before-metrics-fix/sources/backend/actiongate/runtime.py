"""Authenticated clients for independent, supervised local inference workers."""
from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from tokenizers import Tokenizer

STOP_GRACE_SECONDS = 5.0


@lru_cache(maxsize=1)
def pinned_tokenizer() -> Tokenizer:
    root = Path(__file__).resolve().parents[2]
    path = root / "models/tokenizer.json"
    if not path.exists():
        path = root.parent / "models/tokenizer.json"
    return Tokenizer.from_file(str(path))


class RuntimeFailure(Exception):
    def __init__(self, reason: str, usage: dict | None = None, stop: dict | None = None):
        super().__init__(reason)
        self.usage = usage or {"usage_unknown": True}
        self.stop = stop


class WorkerClient:
    def __init__(self, role: str):
        self.role = role
        self.url = os.getenv("OLLAMA_GUARD_URL" if role == "guard" else "OLLAMA_AGENT_URL", f"http://{role}-worker:8090")
        self.token = os.getenv("ACTIONGATE_GUARD_KEY" if role == "guard" else "ACTIONGATE_BUSINESS_KEY", "")

    async def ready(self) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(self.url + "/health/ready")
            response.raise_for_status()
            return response.json()

    async def status(self) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(self.url + "/status", headers={"Authorization": "Bearer " + self.token})
            response.raise_for_status()
            return response.json()

    async def configure(self, generation: int, config: dict) -> dict:
        configuration = {"semantic": config["semantic"], "local_resources": config["local_resources"]}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.url+"/configuration", json={"generation": generation, "config": configuration}, headers={"Authorization": "Bearer "+self.token})
            response.raise_for_status()
            result = response.json()
            expected_digest = hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if result.get("generation") != generation or result.get("prepared") is not True or result.get("configuration_digest") != expected_digest:
                raise RuntimeFailure("Worker did not acknowledge the staged generation", {"usage_unknown": False})
            return result

    async def reserve(self, windows: int, generation: int | None = None) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(self.url+"/tickets", json={"windows": windows, "generation": generation}, headers={"Authorization": "Bearer "+self.token})
            response.raise_for_status()
            return response.json()

    async def release(self, ticket_id: str):
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.delete(self.url+"/tickets/"+ticket_id, headers={"Authorization": "Bearer "+self.token})
            response.raise_for_status()

    async def infer(self, messages: list[dict], max_tokens: int, *, schema=None, tools=None, deadline_seconds=60, ticket_id=None, generation=None, expected_model_digest=None, expected_manifest_sha256=None) -> dict:
        request = {"messages": messages, "max_tokens": max_tokens, "format": schema, "tools": tools, "deadline_seconds": deadline_seconds, "request_id": str(uuid.uuid4()), "ticket_id": ticket_id, "generation": generation}
        try:
            async with httpx.AsyncClient(timeout=deadline_seconds+40) as client:
                response = await client.post(self.url + "/infer", json=request, headers={"Authorization": "Bearer " + self.token})
            if not response.is_success:
                detail = response.json().get("detail", {})
                # These statuses are emitted before inference starts. Preserve a
                # known zero charge, rather than retaining an uncertain budget
                # reservation for a queue/context/authentication rejection.
                rejected_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "inference_slot_seconds": 0.0, "cpu_seconds": 0.0, "usage_unknown": False} if response.status_code in {401, 409, 413, 422, 429, 503} else None
                if isinstance(detail, dict):
                    raise RuntimeFailure(f"worker_http_{response.status_code}", detail.get("usage", rejected_usage), detail.get("stop"))
                raise RuntimeFailure(f"worker_http_{response.status_code}", rejected_usage)
            result = response.json()
            if result.get("generation") != generation:
                raise RuntimeFailure("Worker returned another control generation", result.get("usage"))
            if expected_model_digest is not None and result.get("model_digest") != expected_model_digest:
                raise RuntimeFailure("Worker returned another signed model digest", result.get("usage"))
            if expected_manifest_sha256 is not None and result.get("model_manifest_sha256") != expected_manifest_sha256:
                raise RuntimeFailure("Worker returned another signed model or tokenizer artifact", result.get("usage"))
            return result
        except (httpx.HTTPError, ValueError) as exc:
            # An HTTP timeout is never labelled confirmed stop. The worker's own
            # watchdog owns cancellation and its slot until process confirmation.
            raise RuntimeFailure("worker_transport_unavailable") from exc


class BusinessModel:
    def __init__(self):
        self.worker = WorkerClient("business")

    async def ready(self):
        return await self.worker.ready()

    @staticmethod
    def normalize_messages(messages: list[dict]) -> list[dict]:
        normalized = []
        call_names = {}
        for message in messages:
            value = {"role": message["role"], "content": message.get("content") or ""}
            if "tool_calls" in message:
                value["tool_calls"] = []
                for index, call in enumerate(message["tool_calls"]):
                    fn = dict(call["function"])
                    if isinstance(fn.get("arguments"), str):
                        fn["arguments"] = json.loads(fn["arguments"])
                    fn.setdefault("index", index)
                    normalized_call = {"function": fn}
                    if call.get("id"):
                        normalized_call["id"] = call["id"]
                        call_names[call["id"]] = fn["name"]
                    value["tool_calls"].append(normalized_call)
            if message["role"] == "tool":
                if message.get("tool_call_id"):
                    value["tool_call_id"] = message["tool_call_id"]
                tool_name = call_names.get(message.get("tool_call_id"), message.get("name"))
                if tool_name:
                    value["tool_name"] = tool_name
            normalized.append(value)
        return normalized

    def input_upper_bound(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        normalized = self.normalize_messages(messages)
        full = json.dumps({"messages": normalized, "tools": tools, "format": None}, ensure_ascii=False, separators=(",", ":"))
        return len(pinned_tokenizer().encode(full).ids) + 32 * len(normalized) + 256

    async def chat(self, messages: list[dict], max_tokens: int = 512, tools: list[dict] | None = None, **kwargs) -> dict[str, Any]:
        normalized = self.normalize_messages(messages)
        result = await self.worker.infer(normalized, max_tokens, tools=tools, deadline_seconds=kwargs.get("deadline_seconds", 120), generation=kwargs.get("generation"),
            expected_model_digest=kwargs.get("expected_model_digest"), expected_manifest_sha256=kwargs.get("expected_manifest_sha256"))
        msg = {"role": "assistant", "content": result["message"].get("content", "")}
        calls = result["message"].get("tool_calls")
        if calls:
            msg["tool_calls"] = [{"id": "call_"+uuid.uuid4().hex, "type": "function", "function": {"name": c["function"]["name"], "arguments": json.dumps(c["function"]["arguments"], ensure_ascii=False)}} for c in calls]
        return {"id": "chatcmpl_"+uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()), "model": "local-business", "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "length" if result.get("done_reason") == "length" else "stop"}], "usage": result["usage"], "model_digest": result["model_digest"]}
