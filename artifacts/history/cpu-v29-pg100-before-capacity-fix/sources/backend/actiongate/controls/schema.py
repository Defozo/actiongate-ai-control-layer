"""Validated public policy contract. Secrets never belong in this model."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Level = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
Positive = Annotated[int, Field(ge=1, le=2_000_000_000)]
Nonnegative = Annotated[int, Field(ge=0, le=2_000_000_000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Profile(StrictModel):
    pii_action: Literal["block", "redact"] = Field(default="redact", description="PII action; secrets always use their own control.")
    semantic_block_level: Annotated[int, Field(ge=1, le=3)] = Field(default=2, description="Minimum operational risk level blocked by the semantic control.")
    semantic_review_level: Annotated[int, Field(ge=1, le=3)] | None = Field(default=1, description="Minimum risk requiring approval, or null to disable review.")
    semantic_unknown: Literal["block"] = Field(default="block", description="Required semantic failures always block.")
    restrict_to: str | None = Field(default=None, description="Tenant restriction for the observe profile.")
    effects: Literal["test_sinks_only"] | None = Field(default=None, description="Observe profile effects must be synthetic demo sinks.")
    advisory_controls: list[Literal["pii", "semantic"]] = Field(default_factory=list, description="Content controls recorded without enforcement in the restricted observe sandbox.")
    enforce_identity_labels_budgets: Literal[True] = Field(default=True, description="Platform invariants cannot be disabled by a profile.")

    @model_validator(mode="after")
    def thresholds(self):
        if self.semantic_review_level is not None and self.semantic_review_level >= self.semantic_block_level:
            raise ValueError("Review threshold must be lower than block threshold")
        return self


class Profiles(StrictModel):
    strict: Profile = Field(description="Strict content screening settings.")
    balanced: Profile = Field(description="Balanced content screening settings.")
    observe: Profile = Field(description="Synthetic tenant advisory settings.")


class IdentityConfig(StrictModel):
    tenant_source: Literal["verified_identity"] = Field(default="verified_identity", description="Tenant originates from a verified token.")
    grants_source: Literal["trusted_application"] = Field(default="trusted_application", description="Only trusted application identities issue grants.")
    delegation: Literal["intersect_parent_grant"] = Field(default="intersect_parent_grant", description="Delegation only narrows the parent grant.")


class Sink(StrictModel):
    max_label: Level = Field(description="Highest confidentiality accepted by this recipient.")
    same_tenant: bool = Field(default=False, description="Require recipient tenant to match the run tenant.")
    compartments: list[str] = Field(default_factory=list, description="Compartments the recipient is authorized to receive.")


class FlowConfig(StrictModel):
    levels: list[Level] = Field(default=["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"], description="Immutable confidentiality ordering.")
    unknown_source: Literal["CONFIDENTIAL"] = Field(default="CONFIDENTIAL", description="Default classification for arbitrary user or model text.")
    track_untrusted_origins: Literal[True] = Field(default=True, description="Persist source provenance independently of confidentiality.")
    propagate_run_and_delegation: Literal[True] = Field(default=True, description="All contexts in a delegation tree inherit restrictions.")
    label_override_by_agent: Literal[False] = Field(default=False, description="Agents cannot downgrade labels.")
    sinks: dict[str, Sink] = Field(description="Logical recipient allowlist and clearance.")
    public_release_function: Literal["supplier_public_view_v1"] = Field(default="supplier_public_view_v1", description="Trusted projection copies approved authoritative public fields only.")

    @model_validator(mode="after")
    def levels_valid(self):
        if self.levels != ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]:
            raise ValueError("Confidentiality ordering is a platform invariant")
        for name in ("cloud_model", "public_demo_sink"):
            if name not in self.sinks or self.sinks[name].max_label != "PUBLIC":
                raise ValueError(f"{name} must accept PUBLIC only")
        return self


class ModelsConfig(StrictModel):
    default: str = Field(default="local-business", description="Logical business model ID from the registry.")
    registry: str = Field(default="model-registry.json", description="Relative approved registry filename.")
    allowed: list[str] = Field(default=["local-business"], description="Models available to agent requests.")
    cloud_enabled: bool = Field(default=False, description="Explicit opt-in for approved commercial adapters.")


class SemanticConfig(StrictModel):
    model_ref: str = Field(default="local-guard", description="Dedicated local guard model registry reference.")
    prompt_version: Literal["actiongate-guard-v2.9", "actiongate-guard-v2.8", "actiongate-guard-v2.7", "actiongate-guard-v2.6", "actiongate-guard-v2.5", "actiongate-guard-v2.4", "actiongate-guard-v2.3", "actiongate-guard-v2.2", "actiongate-guard-v2.1", "actiongate-guard-v2", "actiongate-guard-v1", "guard-v1"] = Field(default="actiongate-guard-v2.9", description="Pinned trusted guard instructions, long-context goal-action review and evidence validation. Earlier versions remain readable in historical signatures but cannot be activated by this deployment.")
    required_on: list[Literal["untrusted_input", "proposed_action", "tool_output", "memory_write", "model_output"]] = Field(default=["untrusted_input", "proposed_action", "tool_output", "memory_write", "model_output"], description="Pipeline boundaries requiring guard inference.")
    risk_scale: list[int] = Field(default=[0, 1, 2, 3], description="Operational levels; never probabilities.")
    context_tokens: Positive = Field(default=8192, description="Maximum complete serialized guard context.")
    max_input_tokens_per_call: Positive = Field(default=4096, description="Maximum guard input, including instructions and JSON schema.")
    max_output_tokens: Positive = Field(default=256, description="Reserved upper bound for guard generation.")
    window_tokens: Positive = Field(default=2048, description="Content tokens per overlapping scan window.")
    overlap_tokens: Nonnegative = Field(default=256, description="Shared content tokens between adjacent windows.")
    max_windows: Annotated[int, Field(ge=1, le=128)] = Field(default=8, description="Upper bound on billed windows per scan.")
    deadline_seconds: Annotated[int, Field(ge=1, le=3600)] = Field(default=30, description="Guard process deadline, followed by confirmed stop.")
    incomplete_scan: Literal["block"] = Field(default="block", description="Missing windows cannot be treated as benign.")

    @model_validator(mode="after")
    def limits(self):
        if self.risk_scale != [0, 1, 2, 3]:
            raise ValueError("Risk scale must be [0,1,2,3]")
        if self.overlap_tokens >= self.window_tokens:
            raise ValueError("Window overlap must be smaller than window size")
        if self.window_tokens > self.max_input_tokens_per_call or self.max_input_tokens_per_call + self.max_output_tokens > self.context_tokens:
            raise ValueError("Guard token limits exceed context")
        return self


class Toggle(StrictModel):
    enabled: bool = Field(default=True, description="Enable this configurable content control.")


class InvariantToggle(StrictModel):
    enabled: Literal[True] = Field(default=True, description="Mandatory platform identity invariant.")


class SecretControl(Toggle):
    action: Literal["block"] = Field(default="block", description="Detected credentials cannot be redacted into an executable operation.")


class PIIControl(Toggle):
    entities: list[Literal["EMAIL", "PHONE", "PESEL", "IBAN"]] = Field(default=["EMAIL", "PHONE", "PESEL", "IBAN"], description="Enabled PII recognizers; PESEL and IBAN require checksums.")


class ControlsConfig(StrictModel):
    authentication: InvariantToggle = Field(default_factory=InvariantToggle, description="Verified identity enforcement.")
    tenant_isolation: InvariantToggle = Field(default_factory=InvariantToggle, description="Tenant boundary enforcement.")
    secrets: SecretControl = Field(default_factory=SecretControl, description="Credential signature detection.")
    pii: PIIControl = Field(default_factory=PIIControl, description="Presidio and checksum PII recognition.")
    semantic: Toggle = Field(default_factory=Toggle, description="Local AI content screening.")
    historical_attacks: Toggle = Field(default_factory=Toggle, description="Historical exploit and approved feed controls.")


class ToolsConfig(StrictModel):
    registry: str = Field(default="tool-registry.json", description="Relative approved tool registry filename.")
    allowed: list[str] = Field(description="Allowed logical tool IDs; each still requires a workflow grant.")
    require_approval: list[str] = Field(default=["reports.publish_demo"], description="Allowed tools requiring exact-payload human approval.")
    grant_ttl_seconds: Annotated[int, Field(ge=1, le=300)] = Field(default=30, description="Lifetime of an unconsumed execution grant.")


class BudgetsConfig(StrictModel):
    currency: Literal["USD"] = Field(default="USD", description="Commercial accounting currency; amounts are integer micro-USD.")
    period_timezone: Literal["UTC"] = Field(default="UTC", description="Database-clock budget period timezone.")
    tenant_daily_usd_micros: Nonnegative = Field(default=5_000_000, description="Daily tenant monetary ceiling, including reservations.")
    run_usd_micros: Nonnegative = Field(default=250_000, description="Shared run-tree monetary ceiling.")
    run_total_tokens: Positive = Field(default=120_000, description="Shared run-tree token ceiling including guard work.")
    guard_subbudget_tokens: Positive = Field(default=96_000, description="Protection token ceiling inside total run tokens.")
    request_input_tokens: Positive = Field(default=4096, description="Maximum complete serialized business request tokens.")
    request_output_tokens: Positive = Field(default=1024, description="Maximum business output tokens reserved before dispatch.")
    max_steps: Positive = Field(default=12, description="Maximum actions and, separately, maximum delegated runs in the shared workflow tree.")
    max_tool_calls: Positive = Field(default=8, description="Maximum dispatched tool calls in the shared tree.")
    max_retries: Annotated[int, Field(ge=0, le=10)] = Field(default=1, description="Maximum separately reserved retries.")
    max_delegation_depth: Annotated[int, Field(ge=0, le=10)] = Field(default=2, description="Maximum nested delegation depth.")
    run_deadline_seconds: Positive = Field(default=900, description="Maximum elapsed run lifetime measured against database time.")
    reserve_output_inspection: Literal[True] = Field(default=True, description="Output protection resources are reserved before upstream execution.")
    unknown_usage: Literal["retain_reservation"] = Field(default="retain_reservation", description="Unknown upstream cost remains committed until reconciliation.")

    @model_validator(mode="after")
    def subbudget(self):
        if self.guard_subbudget_tokens > self.run_total_tokens:
            raise ValueError("Guard subbudget cannot exceed the run budget")
        return self


class LocalResourcesConfig(StrictModel):
    business_slots: Annotated[int, Field(ge=1, le=32)] = Field(default=1, description="Maximum independent supervised business process groups.")
    guard_slots: Annotated[int, Field(ge=1, le=32)] = Field(default=1, description="Maximum independent supervised guard process groups.")
    max_waiting_jobs: Annotated[int, Field(ge=0, le=1000)] = Field(default=8, description="Bounded work queue capacity including output reservations.")
    queue_wait_seconds: Positive = Field(default=10, description="Maximum waiting time before new work is rejected.")
    business_call_deadline_seconds: Positive = Field(default=60, description="Business worker inference deadline.")
    run_slot_seconds: Positive = Field(default=600, description="Run-tree inference slot-seconds ceiling.")
    guard_subbudget_slot_seconds: Positive = Field(default=480, description="Protection slot-seconds ceiling inside the run total.")
    require_confirmed_stop: Literal[True] = Field(default=True, description="Timeout cannot free an inference slot before process stop confirmation.")


class TransportConfig(StrictModel):
    max_request_bytes: Annotated[int, Field(ge=1024, le=4_194_304)] = Field(default=262144, description="Maximum encoded HTTP body size.")
    max_response_bytes: Annotated[int, Field(ge=1024, le=4_194_304)] = Field(default=65536, description="Maximum buffered complete upstream response.")
    output_release: Literal["after_full_inspection"] = Field(default="after_full_inspection", description="No output bytes are streamed before final inspection.")


class FeedConfig(StrictModel):
    source_ref: str = Field(default="approved-threat-feed", description="Trusted publisher logical ID, never a rule-supplied URL.")
    signature_required: Literal[True] = Field(default=True, description="Ed25519 verification is mandatory.")
    reject_rollback: Literal[True] = Field(default=True, description="Revision must increase against the durable accepted counter.")
    max_age_hours: Annotated[int, Field(ge=1, le=168)] = Field(default=24, description="Maximum lifetime and age of a signed feed.")


class AuditConfig(StrictModel):
    raw_payloads: Literal[False] = Field(default=False, description="Audit metadata never contains raw operation content or credentials.")
    retention_days: Annotated[int, Field(ge=1, le=3650)] = Field(default=7, description="Protected operation and audit retention policy in days.")
    required_before_dispatch_and_release: Literal[True] = Field(default=True, description="Persist mandatory security evidence before either boundary.")


class PerformanceConfig(StrictModel):
    control_cache_enabled: bool = Field(default=True, description="Reuse up to 32 verified immutable control snapshots and compiled registries per replica; feed freshness and authority are checked for every operation. No model responses or user content are cached.")
    semantic_cache_enabled: bool = Field(default=True, description="Reuse complete guard judgments within the same tenant, exact content, trusted purpose/effect, restrictions and signed generation. Per-process cache: TTL 120 seconds, at most 512 entries / 8 MiB serialized. No unknown results or authorization decisions; hits incur zero new inference usage.")


class PolicyConfig(StrictModel):
    schema_version: Literal[1] = Field(default=1, description="Supported policy schema revision.")
    policy_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")] = Field(default="actiongate-demo", description="Stable logical policy ID.")
    revision: Positive = Field(default=1, description="Monotonically increasing policy revision.")
    default_decision: Literal["block"] = Field(default="block", description="Unrecognized operations are denied.")
    active_profile: Literal["strict", "balanced", "observe"] = Field(default="balanced", description="Active profile from the same signed configuration.")
    profiles: Profiles = Field(description="All named profile definitions.")
    identity: IdentityConfig = Field(default_factory=IdentityConfig, description="Immutable identity and grant provenance.")
    flow: FlowConfig = Field(description="Recipient clearance and data restriction propagation.")
    models: ModelsConfig = Field(default_factory=ModelsConfig, description="Approved business model references.")
    semantic: SemanticConfig = Field(default_factory=SemanticConfig, description="Guard inference bounds and classification contract.")
    controls: ControlsConfig = Field(default_factory=ControlsConfig, description="Configurable content controls and invariant controls.")
    tools: ToolsConfig = Field(description="Approved actions and exact-effect approval requirements.")
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig, description="Durable shared financial and token ceilings.")
    local_resources: LocalResourcesConfig = Field(default_factory=LocalResourcesConfig, description="Local resource and worker ceilings.")
    transport: TransportConfig = Field(default_factory=TransportConfig, description="Bounded request and buffered publication settings.")
    feed: FeedConfig = Field(default_factory=FeedConfig, description="Approved external rule feed requirements.")
    audit: AuditConfig = Field(default_factory=AuditConfig, description="Evidence persistence and retention controls.")
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig, description="Bounded caching of immutable control artifacts and complete context-bound semantic judgments.")

    @model_validator(mode="after")
    def references(self):
        if self.models.default not in self.models.allowed:
            raise ValueError("Default model must be in allowed models")
        if not set(self.tools.require_approval).issubset(self.tools.allowed):
            raise ValueError("Approval requirements must refer to allowed tools")
        if self.profiles.observe.restrict_to != "synthetic_test_tenant" or self.profiles.observe.effects != "test_sinks_only":
            raise ValueError("Observe profile must be restricted to synthetic test sinks")
        for value in (self.models.registry, self.tools.registry):
            if not value or any(char in value for char in ("/", "\\", ":", "\x00")) or value.startswith("."):
                raise ValueError("Registry must be a filename inside the policy directory")
        if self.local_resources.guard_subbudget_slot_seconds > self.local_resources.run_slot_seconds:
            raise ValueError("Guard slot subbudget exceeds run ceiling")
        return self

    @property
    def profile(self) -> Profile:
        return getattr(self.profiles, self.active_profile)
