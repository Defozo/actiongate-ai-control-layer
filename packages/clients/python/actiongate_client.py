"""Small transport-only client. All authorization and control stay in the server."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError
from uuid import uuid4


class ActionGateError(RuntimeError):
    def __init__(self, status: int, detail: str):
        self.status = status
        super().__init__(detail)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ActionGateError(code, "Gateway redirects are not followed with workload credentials")


@dataclass
class ActionGate:
    base_url: str
    workload_token: str = field(repr=False)
    run_id: str
    timeout_seconds: float = 300

    def _request(self, path: str, payload: dict | None = None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.workload_token}", "Accept": "application/json",
                   "X-ActionGate-Run-Id": self.run_id}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(self.base_url.rstrip("/") + path, headers=headers,
            data=None if payload is None else json.dumps(payload).encode(), method="GET" if payload is None else "POST")
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.timeout_seconds) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get("detail", "ActionGate rejected the request")
            except (ValueError, AttributeError):
                detail = "ActionGate rejected the request"
            raise ActionGateError(exc.code, str(detail)) from None

    def action(self, tool: str, arguments: dict, *, idempotency_key: str | None = None) -> dict:
        """Reuse the same key only when retrying the same exact operation."""
        return self._request("/actions", {"run_id": self.run_id, "tool": tool,
            "arguments": arguments, "idempotency_key": idempotency_key or str(uuid4())})

    def operation(self, operation_id: str) -> dict:
        from urllib.parse import quote
        return self._request("/actions/" + quote(operation_id, safe=""))

    def chat(self, messages: list[dict], *, model: str = "local-business", max_tokens: int = 512,
             tools: list[dict] | None = None, idempotency_key: str | None = None) -> dict:
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        if tools is not None:
            payload["tools"] = tools
        return self._request("/v1/chat/completions", payload, idempotency_key=idempotency_key or str(uuid4()))
