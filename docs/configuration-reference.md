# Configuration field reference

Generated from `backend/actiongate/controls/schema.py` and the checked-in `policy/control.yaml`. The configured values are defaults for this checkout, not measured performance results. Unknown fields are rejected. Cross-field validation also requires ordered confidentiality, public-only public/cloud sinks, valid registry filenames, lower review than block threshold, overlapping windows smaller than the window, a context that fits input plus output, and guard sublimits inside root limits.

| Field | Checkout value / schema default | Allowed values or bounds | Meaning |
| --- | --- | --- | --- |
| `schema_version` | `1` | `{"type":"integer","const":1}` | Supported policy schema revision. |
| `policy_id` | `"actiongate-demo"` | `{"type":"string","pattern":"^[a-z][a-z0-9-]{1,63}$"}` | Stable logical policy ID. |
| `revision` | `1` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Monotonically increasing policy revision. |
| `default_decision` | `"block"` | `{"type":"string","const":"block"}` | Unrecognized operations are denied. |
| `active_profile` | `"balanced"` | `{"type":"string","enum":["strict","balanced","observe"]}` | Active profile from the same signed configuration. |
| `profiles.strict.pii_action` | `"block"` | `{"type":"string","enum":["block","redact"]}` | PII action; secrets always use their own control. |
| `profiles.strict.semantic_block_level` | `1` | `{"type":"integer","minimum":1,"maximum":3}` | Minimum operational risk level blocked by the semantic control. |
| `profiles.strict.semantic_review_level` | `1` | `{"allowed":[{"maximum":3,"minimum":1,"type":"integer"},{"type":"null"}]}` | Minimum risk requiring approval, or null to disable review. |
| `profiles.strict.semantic_unknown` | `"block"` | `{"type":"string","const":"block"}` | Required semantic failures always block. |
| `profiles.strict.restrict_to` | `null` | `{"allowed":[{"type":"string"},{"type":"null"}]}` | Tenant restriction for the observe profile. |
| `profiles.strict.effects` | `null` | `{"allowed":[{"const":"test_sinks_only","type":"string"},{"type":"null"}]}` | Observe profile effects must be synthetic demo sinks. |
| `profiles.strict.advisory_controls` | `"required"` | `{"type":"array"}` | Content controls recorded without enforcement in the restricted observe sandbox. |
| `profiles.strict.enforce_identity_labels_budgets` | `true` | `{"type":"boolean","const":true}` | Platform invariants cannot be disabled by a profile. |
| `profiles.balanced.pii_action` | `"redact"` | `{"type":"string","enum":["block","redact"]}` | PII action; secrets always use their own control. |
| `profiles.balanced.semantic_block_level` | `2` | `{"type":"integer","minimum":1,"maximum":3}` | Minimum operational risk level blocked by the semantic control. |
| `profiles.balanced.semantic_review_level` | `1` | `{"allowed":[{"maximum":3,"minimum":1,"type":"integer"},{"type":"null"}]}` | Minimum risk requiring approval, or null to disable review. |
| `profiles.balanced.semantic_unknown` | `"block"` | `{"type":"string","const":"block"}` | Required semantic failures always block. |
| `profiles.balanced.restrict_to` | `null` | `{"allowed":[{"type":"string"},{"type":"null"}]}` | Tenant restriction for the observe profile. |
| `profiles.balanced.effects` | `null` | `{"allowed":[{"const":"test_sinks_only","type":"string"},{"type":"null"}]}` | Observe profile effects must be synthetic demo sinks. |
| `profiles.balanced.advisory_controls` | `"required"` | `{"type":"array"}` | Content controls recorded without enforcement in the restricted observe sandbox. |
| `profiles.balanced.enforce_identity_labels_budgets` | `true` | `{"type":"boolean","const":true}` | Platform invariants cannot be disabled by a profile. |
| `profiles.observe.pii_action` | `"redact"` | `{"type":"string","enum":["block","redact"]}` | PII action; secrets always use their own control. |
| `profiles.observe.semantic_block_level` | `2` | `{"type":"integer","minimum":1,"maximum":3}` | Minimum operational risk level blocked by the semantic control. |
| `profiles.observe.semantic_review_level` | `1` | `{"allowed":[{"maximum":3,"minimum":1,"type":"integer"},{"type":"null"}]}` | Minimum risk requiring approval, or null to disable review. |
| `profiles.observe.semantic_unknown` | `"block"` | `{"type":"string","const":"block"}` | Required semantic failures always block. |
| `profiles.observe.restrict_to` | `"synthetic_test_tenant"` | `{"allowed":[{"type":"string"},{"type":"null"}]}` | Tenant restriction for the observe profile. |
| `profiles.observe.effects` | `"test_sinks_only"` | `{"allowed":[{"const":"test_sinks_only","type":"string"},{"type":"null"}]}` | Observe profile effects must be synthetic demo sinks. |
| `profiles.observe.advisory_controls` | `["pii","semantic"]` | `{"type":"array"}` | Content controls recorded without enforcement in the restricted observe sandbox. |
| `profiles.observe.enforce_identity_labels_budgets` | `true` | `{"type":"boolean","const":true}` | Platform invariants cannot be disabled by a profile. |
| `identity.tenant_source` | `"verified_identity"` | `{"type":"string","const":"verified_identity"}` | Tenant originates from a verified token. |
| `identity.grants_source` | `"trusted_application"` | `{"type":"string","const":"trusted_application"}` | Only trusted application identities issue grants. |
| `identity.delegation` | `"intersect_parent_grant"` | `{"type":"string","const":"intersect_parent_grant"}` | Delegation only narrows the parent grant. |
| `flow.levels` | `["PUBLIC","INTERNAL","CONFIDENTIAL","RESTRICTED"]` | `{"type":"array"}` | Immutable confidentiality ordering. |
| `flow.unknown_source` | `"CONFIDENTIAL"` | `{"type":"string","const":"CONFIDENTIAL"}` | Default classification for arbitrary user or model text. |
| `flow.track_untrusted_origins` | `true` | `{"type":"boolean","const":true}` | Persist source provenance independently of confidentiality. |
| `flow.propagate_run_and_delegation` | `true` | `{"type":"boolean","const":true}` | All contexts in a delegation tree inherit restrictions. |
| `flow.label_override_by_agent` | `false` | `{"type":"boolean","const":false}` | Agents cannot downgrade labels. |
| `flow.sinks.local_model.max_label` | `"RESTRICTED"` | `{"type":"string","enum":["PUBLIC","INTERNAL","CONFIDENTIAL","RESTRICTED"]}` | Highest confidentiality accepted by this recipient. |
| `flow.sinks.local_model.same_tenant` | `true` | `{"type":"boolean"}` | Require recipient tenant to match the run tenant. |
| `flow.sinks.local_model.compartments` | `"required"` | `{"type":"array"}` | Compartments the recipient is authorized to receive. |
| `flow.sinks.internal_report.max_label` | `"CONFIDENTIAL"` | `{"type":"string","enum":["PUBLIC","INTERNAL","CONFIDENTIAL","RESTRICTED"]}` | Highest confidentiality accepted by this recipient. |
| `flow.sinks.internal_report.same_tenant` | `true` | `{"type":"boolean"}` | Require recipient tenant to match the run tenant. |
| `flow.sinks.internal_report.compartments` | `"required"` | `{"type":"array"}` | Compartments the recipient is authorized to receive. |
| `flow.sinks.internal_demo_sink.max_label` | `"CONFIDENTIAL"` | `{"type":"string","enum":["PUBLIC","INTERNAL","CONFIDENTIAL","RESTRICTED"]}` | Highest confidentiality accepted by this recipient. |
| `flow.sinks.internal_demo_sink.same_tenant` | `true` | `{"type":"boolean"}` | Require recipient tenant to match the run tenant. |
| `flow.sinks.internal_demo_sink.compartments` | `"required"` | `{"type":"array"}` | Compartments the recipient is authorized to receive. |
| `flow.sinks.public_demo_sink.max_label` | `"PUBLIC"` | `{"type":"string","enum":["PUBLIC","INTERNAL","CONFIDENTIAL","RESTRICTED"]}` | Highest confidentiality accepted by this recipient. |
| `flow.sinks.public_demo_sink.same_tenant` | `false` | `{"type":"boolean"}` | Require recipient tenant to match the run tenant. |
| `flow.sinks.public_demo_sink.compartments` | `"required"` | `{"type":"array"}` | Compartments the recipient is authorized to receive. |
| `flow.sinks.cloud_model.max_label` | `"PUBLIC"` | `{"type":"string","enum":["PUBLIC","INTERNAL","CONFIDENTIAL","RESTRICTED"]}` | Highest confidentiality accepted by this recipient. |
| `flow.sinks.cloud_model.same_tenant` | `false` | `{"type":"boolean"}` | Require recipient tenant to match the run tenant. |
| `flow.sinks.cloud_model.compartments` | `"required"` | `{"type":"array"}` | Compartments the recipient is authorized to receive. |
| `flow.public_release_function` | `"supplier_public_view_v1"` | `{"type":"string","const":"supplier_public_view_v1"}` | Trusted projection copies approved authoritative public fields only. |
| `models.default` | `"local-business"` | `{"type":"string"}` | Logical business model ID from the registry. |
| `models.registry` | `"model-registry.json"` | `{"type":"string"}` | Relative approved registry filename. |
| `models.allowed` | `["local-business"]` | `{"type":"array"}` | Models available to agent requests. |
| `models.cloud_enabled` | `false` | `{"type":"boolean"}` | Explicit opt-in for approved commercial adapters. |
| `semantic.model_ref` | `"local-guard"` | `{"type":"string"}` | Dedicated local guard model registry reference. |
| `semantic.prompt_version` | `"actiongate-guard-v2.8"` | `{"type":"string","enum":["actiongate-guard-v2.8","actiongate-guard-v2.7","actiongate-guard-v2.6","actiongate-guard-v2.5","actiongate-guard-v2.4","actiongate-guard-v2.3","actiongate-guard-v2.2","actiongate-guard-v2.1","actiongate-guard-v2","actiongate-guard-v1","guard-v1"]}` | Pinned trusted guard instructions, long-context goal-action review and evidence validation. Earlier versions remain readable in historical signatures but cannot be activated by this deployment. |
| `semantic.required_on` | `["untrusted_input","proposed_action","tool_output","memory_write","model_output"]` | `{"type":"array"}` | Pipeline boundaries requiring guard inference. |
| `semantic.risk_scale` | `[0,1,2,3]` | `{"type":"array"}` | Operational levels; never probabilities. |
| `semantic.context_tokens` | `8192` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum complete serialized guard context. |
| `semantic.max_input_tokens_per_call` | `4096` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum guard input, including instructions and JSON schema. |
| `semantic.max_output_tokens` | `256` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Reserved upper bound for guard generation. |
| `semantic.window_tokens` | `2048` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Content tokens per overlapping scan window. |
| `semantic.overlap_tokens` | `256` | `{"type":"integer","minimum":0,"maximum":2000000000}` | Shared content tokens between adjacent windows. |
| `semantic.max_windows` | `8` | `{"type":"integer","minimum":1,"maximum":128}` | Upper bound on billed windows per scan. |
| `semantic.deadline_seconds` | `180` | `{"type":"integer","minimum":1,"maximum":3600}` | Guard process deadline, followed by confirmed stop. |
| `semantic.incomplete_scan` | `"block"` | `{"type":"string","const":"block"}` | Missing windows cannot be treated as benign. |
| `controls.authentication.enabled` | `true` | `{"type":"boolean","const":true}` | Mandatory platform identity invariant. |
| `controls.tenant_isolation.enabled` | `true` | `{"type":"boolean","const":true}` | Mandatory platform identity invariant. |
| `controls.secrets.enabled` | `true` | `{"type":"boolean"}` | Enable this configurable content control. |
| `controls.secrets.action` | `"block"` | `{"type":"string","const":"block"}` | Detected credentials cannot be redacted into an executable operation. |
| `controls.pii.enabled` | `true` | `{"type":"boolean"}` | Enable this configurable content control. |
| `controls.pii.entities` | `["EMAIL","PHONE","PESEL","IBAN"]` | `{"type":"array"}` | Enabled PII recognizers; PESEL and IBAN require checksums. |
| `controls.semantic.enabled` | `true` | `{"type":"boolean"}` | Enable this configurable content control. |
| `controls.historical_attacks.enabled` | `true` | `{"type":"boolean"}` | Enable this configurable content control. |
| `tools.registry` | `"tool-registry.json"` | `{"type":"string"}` | Relative approved tool registry filename. |
| `tools.allowed` | `["documents.read","memory.read","memory.write","reports.save","reports.publish_demo","calculator.evaluate","models.chat","supplier_public_view_v1"]` | `{"type":"array"}` | Allowed logical tool IDs; each still requires a workflow grant. |
| `tools.require_approval` | `["reports.publish_demo"]` | `{"type":"array"}` | Allowed tools requiring exact-payload human approval. |
| `tools.grant_ttl_seconds` | `30` | `{"type":"integer","minimum":1,"maximum":300}` | Lifetime of an unconsumed execution grant. |
| `budgets.currency` | `"USD"` | `{"type":"string","const":"USD"}` | Commercial accounting currency; amounts are integer micro-USD. |
| `budgets.period_timezone` | `"UTC"` | `{"type":"string","const":"UTC"}` | Database-clock budget period timezone. |
| `budgets.tenant_daily_usd_micros` | `5000000` | `{"type":"integer","minimum":0,"maximum":2000000000}` | Daily tenant monetary ceiling, including reservations. |
| `budgets.run_usd_micros` | `250000` | `{"type":"integer","minimum":0,"maximum":2000000000}` | Shared run-tree monetary ceiling. |
| `budgets.run_total_tokens` | `120000` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Shared run-tree token ceiling including guard work. |
| `budgets.guard_subbudget_tokens` | `96000` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Protection token ceiling inside total run tokens. |
| `budgets.request_input_tokens` | `4096` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum complete serialized business request tokens. |
| `budgets.request_output_tokens` | `1024` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum business output tokens reserved before dispatch. |
| `budgets.max_steps` | `12` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum actions and, separately, maximum delegated runs in the shared workflow tree. |
| `budgets.max_tool_calls` | `8` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum dispatched tool calls in the shared tree. |
| `budgets.max_retries` | `1` | `{"type":"integer","minimum":0,"maximum":10}` | Maximum separately reserved retries. |
| `budgets.max_delegation_depth` | `2` | `{"type":"integer","minimum":0,"maximum":10}` | Maximum nested delegation depth. |
| `budgets.run_deadline_seconds` | `1800` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum elapsed run lifetime measured against database time. |
| `budgets.reserve_output_inspection` | `true` | `{"type":"boolean","const":true}` | Output protection resources are reserved before upstream execution. |
| `budgets.unknown_usage` | `"retain_reservation"` | `{"type":"string","const":"retain_reservation"}` | Unknown upstream cost remains committed until reconciliation. |
| `local_resources.business_slots` | `1` | `{"type":"integer","minimum":1,"maximum":32}` | Maximum independent supervised business process groups. |
| `local_resources.guard_slots` | `1` | `{"type":"integer","minimum":1,"maximum":32}` | Maximum independent supervised guard process groups. |
| `local_resources.max_waiting_jobs` | `8` | `{"type":"integer","minimum":0,"maximum":1000}` | Bounded work queue capacity including output reservations. |
| `local_resources.queue_wait_seconds` | `10` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Maximum waiting time before new work is rejected. |
| `local_resources.business_call_deadline_seconds` | `120` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Business worker inference deadline. |
| `local_resources.run_slot_seconds` | `1800` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Run-tree inference slot-seconds ceiling. |
| `local_resources.guard_subbudget_slot_seconds` | `1700` | `{"type":"integer","minimum":1,"maximum":2000000000}` | Protection slot-seconds ceiling inside the run total. |
| `local_resources.require_confirmed_stop` | `true` | `{"type":"boolean","const":true}` | Timeout cannot free an inference slot before process stop confirmation. |
| `transport.max_request_bytes` | `262144` | `{"type":"integer","minimum":1024,"maximum":4194304}` | Maximum encoded HTTP body size. |
| `transport.max_response_bytes` | `65536` | `{"type":"integer","minimum":1024,"maximum":4194304}` | Maximum buffered complete upstream response. |
| `transport.output_release` | `"after_full_inspection"` | `{"type":"string","const":"after_full_inspection"}` | No output bytes are streamed before final inspection. |
| `feed.source_ref` | `"approved-threat-feed"` | `{"type":"string"}` | Trusted publisher logical ID, never a rule-supplied URL. |
| `feed.signature_required` | `true` | `{"type":"boolean","const":true}` | Ed25519 verification is mandatory. |
| `feed.reject_rollback` | `true` | `{"type":"boolean","const":true}` | Revision must increase against the durable accepted counter. |
| `feed.max_age_hours` | `24` | `{"type":"integer","minimum":1,"maximum":168}` | Maximum lifetime and age of a signed feed. |
| `audit.raw_payloads` | `false` | `{"type":"boolean","const":false}` | Audit metadata never contains raw operation content or credentials. |
| `audit.retention_days` | `7` | `{"type":"integer","minimum":1,"maximum":3650}` | Protected operation and audit retention policy in days. |
| `audit.required_before_dispatch_and_release` | `true` | `{"type":"boolean","const":true}` | Persist mandatory security evidence before either boundary. |
| `performance.control_cache_enabled` | `true` | `{"type":"boolean"}` | Reuse up to 32 verified immutable control snapshots and compiled registries per replica; feed freshness and authority are checked for every operation. No model responses or user content are cached. |
| `performance.semantic_cache_enabled` | `true` | `{"type":"boolean"}` | Reuse complete guard judgments within the same tenant, exact content, trusted purpose/effect, restrictions and signed generation. Per-process cache: TTL 120 seconds, at most 512 entries / 8 MiB serialized. No unknown results or authorization decisions; hits incur zero new inference usage. |
