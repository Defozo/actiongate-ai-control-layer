from __future__ import annotations

from pathlib import Path

from .dlp import DLPScanner, ScanResult
from .registry import ModelDefinition, ToolDefinition
from .model_artifacts import load_model_artifacts, validate_model_artifacts
from .schema import PolicyConfig
from .signed import ControlError, SignedFeed, digest, load_json, load_yaml, verify_document


class ControlPlane:
    def __init__(self, policy_dir: Path | str, config: PolicyConfig | dict | None = None,
                 feed: SignedFeed | dict | None = None, *, require_feed: bool = True,
                 scanner: DLPScanner | None = None, tools: list[dict] | None = None,
                 models: list[dict] | None = None, prices: dict | None = None, model_artifacts: dict | None = None):
        self.policy_dir = Path(policy_dir)
        self.config = config if isinstance(config, PolicyConfig) else PolicyConfig.model_validate(
            config if config is not None else load_yaml((self.policy_dir / "control.yaml").read_text(encoding="utf-8")))
        self.feed = feed if isinstance(feed, SignedFeed) or feed is None else SignedFeed.model_validate(feed)
        self.require_feed = require_feed
        self.scanner = scanner or DLPScanner()
        tool_data = tools if tools is not None else load_json((self.policy_dir / self.config.tools.registry).read_bytes())
        model_data = models if models is not None else load_json((self.policy_dir / self.config.models.registry).read_bytes())
        self.tools = {item.name: item for item in [ToolDefinition.model_validate(item) for item in tool_data]}
        self.models = {item.id: item for item in [ModelDefinition.model_validate(item) for item in model_data]}
        self.model_artifacts = load_model_artifacts(model_data) if model_artifacts is None else validate_model_artifacts(model_data, model_artifacts)
        price_path = self.policy_dir / "prices.json"
        self.prices = prices if prices is not None else load_json(price_path.read_bytes()) if price_path.exists() else None
        if len(self.tools) != len(tool_data) or len(self.models) != len(model_data):
            raise ControlError("Duplicate registry identifier")
        if not set(self.config.tools.allowed).issubset(self.tools):
            raise ControlError("Policy references an unknown tool")
        if not (set(self.config.models.allowed) | {self.config.semantic.model_ref}).issubset(self.models):
            raise ControlError("Policy references an unknown model")
        if self.models[self.config.semantic.model_ref].purpose != "guard":
            raise ControlError("Semantic model is not a guard")
        if any(not self.models[name].enabled for name in set(self.config.models.allowed) | {self.config.semantic.model_ref}):
            raise ControlError("Policy references a disabled model")
        if any(self.models[name].provider != "ollama" for name in self.config.models.allowed) and not self.config.models.cloud_enabled:
            raise ControlError("Cloud model requires explicit enablement")
        for item in self.tools.values():
            if item.recipient not in self.config.flow.sinks and item.recipient not in {"dynamic", "agent"}:
                raise ControlError("Tool references an unknown recipient")
        if self.config.models.cloud_enabled:
            from actiongate.cloud import load_price
            if self.prices is None:
                raise ControlError("Cloud models require a versioned price catalog")
            price = load_price(self.prices)
            for name in self.config.models.allowed:
                item = self.models[name]
                if item.provider == "groq" and (item.pricing_ref != price.revision or item.model != price.model
                                                or item.context_tokens != price.context_tokens):
                    raise ControlError("Cloud registry differs from its verified price contract")

    def scan(self, text: str, *, tenant: str = "acme", scope: str = "text", profile: str | None = None) -> ScanResult:
        if self.require_feed and self.feed is None and self.config.controls.historical_attacks.enabled:
            return ScanResult("block", "", ["availability.feed"], [], "Required signed threat feed unavailable")
        return self.scanner.scan(text, self.config, self.feed, tenant=tenant, scope=scope, profile=profile)

    def scan_payload(self, payload: dict, redact_fields: list[str] | set[str], *, tenant: str = "acme", scope: str = "text"):
        if self.require_feed and self.feed is None and self.config.controls.historical_attacks.enabled:
            return None, ScanResult("block", "", ["availability.feed"], [], "Required signed threat feed unavailable")
        return self.scanner.scan_payload(payload, self.config, self.feed, redact_fields=redact_fields, tenant=tenant, scope=scope)

    def validate_tool(self, name: str, arguments: dict) -> ToolDefinition:
        if name not in self.config.tools.allowed or name not in self.tools:
            raise ControlError("Tool is not in the approved registry")
        tool = self.tools[name]
        tool.validate(arguments)
        if self.feed and self.config.controls.historical_attacks.enabled:
            self.feed.check_freshness(max_age_hours=self.config.feed.max_age_hours)
            if self.feed.match("tool", {"name": tool.name, "description": tool.description, "sha256": tool.definition_digest}):
                raise ControlError("Tool blocked by signed threat feed")
        return tool

    def snapshot(self, generation: int) -> dict:
        from actiongate.semantic import guard_artifact
        payload = {"generation": generation, "policy": self.config.model_dump(),
                   "guard_artifact": guard_artifact(),
                   "tools": [item.model_dump() for item in self.tools.values()],
                   "models": [item.model_dump() for item in self.models.values()],
                   "model_artifacts": self.model_artifacts,
                   "feed": self.feed.model_dump() if self.feed else None,
                   "prices": self.prices,
                   "rego_source": (self.policy_dir / "control.rego").read_text(encoding="utf-8"),
                   "rego_sha256": __import__("hashlib").sha256((self.policy_dir / "control.rego").read_text(encoding="utf-8").encode()).hexdigest()}
        return {**payload, "snapshot_digest": digest(payload)}


def validate_snapshot(envelope: dict, public_keys: dict[str, str], *, minimum_generation: int = 0, require_fresh_feed: bool = True, allow_legacy: bool = False) -> dict:
    payload = verify_document(envelope, public_keys)
    if type(payload.get("generation")) is not int or payload["generation"] <= minimum_generation:
        raise ControlError("Policy generation rollback or replay")
    unsigned = {k: v for k, v in payload.items() if k != "snapshot_digest"}
    if digest(unsigned) != payload.get("snapshot_digest"):
        raise ControlError("Control snapshot digest mismatch")
    config = PolicyConfig.model_validate(payload["policy"])
    for tool in payload["tools"]:
        ToolDefinition.model_validate(tool)
    for model in payload["models"]:
        ModelDefinition.model_validate(model)
    if not allow_legacy or "model_artifacts" in payload:
        validate_model_artifacts(payload["models"], payload.get("model_artifacts"))
    if not allow_legacy or "guard_artifact" in payload:
        import re
        artifact = payload.get("guard_artifact", {})
        if (set(artifact) != {"prompt_version", "sha256"} or not isinstance(artifact["prompt_version"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"]))):
            raise ControlError("Invalid signed classifier artifact")
    if config.models.cloud_enabled:
        from actiongate.cloud import load_price
        if payload.get("prices") is None:
            raise ControlError("Cloud snapshot is missing its price catalog")
        load_price(payload["prices"])
    if payload.get("feed"):
        feed = SignedFeed.model_validate(payload["feed"])
        if require_fresh_feed:
            feed.check_freshness()
    return payload
