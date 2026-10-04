"""OPA REST integration. Missing policy, network failure, and stale generations deny."""
from pathlib import Path
from typing import Any
from functools import lru_cache
from contextlib import asynccontextmanager
import asyncio
import ssl

import certifi
import httpx

from .schema import PolicyConfig
from .signed import ControlError, digest


@lru_cache(maxsize=1)
def verified_tls_context():
    # Same CA source and verification as httpx with trust_env=False. Loading
    # the immutable packaged trust store for every short OPA request adds
    # unnecessary work, including when the private endpoint uses HTTP.
    return ssl.create_default_context(cafile=certifi.where())


_loop_pools = {}


@asynccontextmanager
async def pooled_opa_clients():
    """Reuse OPA connections only inside an explicitly owned event-loop lifetime.

    Standalone commands and tests without this owner use short-lived clients.
    Never reuse an asynchronous transport across asyncio.run calls or replicas.
    Each endpoint has its own pool; no response or authority decision is cached.
    """
    loop = asyncio.get_running_loop()
    if loop in _loop_pools:
        raise RuntimeError("OPA connection pool already has an owner")
    clients = {}
    _loop_pools[loop] = clients
    try:
        yield
    finally:
        del _loop_pools[loop]
        await asyncio.gather(*(client.aclose() for client in clients.values()))


class OPAClient:
    def __init__(self, url: str, *, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    @asynccontextmanager
    async def _client(self):
        clients = _loop_pools.get(asyncio.get_running_loop())
        if clients is None:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False,
                                         verify=verified_tls_context()) as client:
                yield client
            return
        key = (self.url, self.timeout)
        if key not in clients:
            clients[key] = httpx.AsyncClient(timeout=self.timeout, trust_env=False,
                verify=verified_tls_context(), limits=httpx.Limits(max_connections=64, max_keepalive_connections=16))
        yield clients[key]

    async def stage(self, generation: int, config: PolicyConfig | dict, rego_path: Path | str | None = None, *, rego_source: str | None = None) -> dict:
        if generation < 1:
            raise ControlError("Invalid policy generation")
        config = config if isinstance(config, PolicyConfig) else PolicyConfig.model_validate(config)
        path = Path(rego_path) if rego_path else Path(__file__).resolve().parents[3] / "policy" / "control.rego"
        # Install compiled logic before making the immutable generation addressable.
        async with self._client() as client:
            module = (rego_source if rego_source is not None else path.read_text(encoding="utf-8")).replace("package actiongate\n", f"package actiongate.g{generation}\n", 1)
            body = {"generation": generation, "config": config.model_dump(), "digest": digest(config.model_dump())}
            endpoint = self.url + f"/v1/data/actiongate_snapshots/{generation}"
            existing = await client.get(endpoint)
            if existing.status_code == 200 and "result" in existing.json() and existing.json()["result"] != body:
                raise ControlError("OPA generation is immutable")
            policy_endpoint = self.url + f"/v1/policies/actiongate-g{generation}"
            existing_module = await client.get(policy_endpoint)
            if existing_module.status_code == 200 and existing_module.json().get("result", {}).get("raw") != module:
                raise ControlError("OPA generation module is immutable")
            response = await client.put(policy_endpoint, content=module.encode(), headers={"Content-Type": "text/plain"})
            if response.status_code != 200:
                raise ControlError("OPA rejected policy compilation")
            response = await client.put(endpoint, json=body)
            response.raise_for_status()
            loaded = await client.get(endpoint)
            loaded.raise_for_status()
            if loaded.json().get("result") != body:
                raise ControlError("OPA staging read-back mismatch")
            return {"generation": generation, "digest": body["digest"], "staged": True}

    async def evaluate(self, input: dict[str, Any], generation: int) -> dict:
        # Only precomputed control evidence goes to OPA; never raw prompts or keys.
        allowed = {"generation", "identity_ok", "tenant_ok", "grant_ok", "labels_ok", "budget_ok", "registry_ok", "kill_switch", "tenant", "tool", "deterministic", "semantic", "approval_valid", "stage", "profile", "label", "recipient"}
        if set(input) - allowed:
            raise ControlError("Unexpected policy evaluator input")
        payload = {**input, "generation": generation}
        try:
            async with self._client() as client:
                response = await client.post(self.url + f"/v1/data/actiongate/g{generation}/decision", json={"input": payload})
                response.raise_for_status()
                result = response.json().get("result")
            if not isinstance(result, dict) or result.get("generation") != generation or result.get("decision") not in {"allow", "redact", "block", "require_approval"}:
                raise ControlError("OPA returned an invalid or stale decision")
            return result
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return {"decision": "block", "rule_ids": ["availability.opa"], "generation": generation,
                    "policy_revision": None, "reason": "Required policy engine unavailable or inconsistent", "available": False}

    async def ready(self, generation: int) -> bool:
        try:
            async with self._client() as client:
                response = await client.get(self.url + f"/v1/data/actiongate_snapshots/{generation}")
                return response.status_code == 200 and response.json().get("result", {}).get("generation") == generation
        except (httpx.HTTPError, ValueError):
            return False
