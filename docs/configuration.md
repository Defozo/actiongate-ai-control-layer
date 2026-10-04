# Configuration and API contract

Team: DEFOZO SOFTWARE HOUSE. Author: Michał Kiełtyka.

`policy/control.yaml` is the public control catalog. The authoritative active
version is a verified signed snapshot referenced by the database generation.
The dashboard reads and edits this same YAML through role-checked endpoints.
The [field reference](configuration-reference.md) documents every implemented
field's checkout value, constraints and meaning; [JSON Schema](policy.schema.json)
is generated from the actual strict Pydantic models. Unknown keys are rejected.
Regenerate with `PYTHONPATH=backend python docs/generate_configuration.py`.

## Profiles and boundaries

Balanced redacts enabled PII and blocks semantic risk at level two or higher;
level one may require review. Strict blocks PII and semantic risk from level one.
Observe is restricted to `synthetic_test_tenant` and controlled test sinks.
Observe can make PII and semantic findings advisory, but identity, confidentiality
and budget invariants remain mandatory. The risk scale is operational, not a
probability. A failed required scan is unknown and must fail closed.

Recipient IDs are logical registry values, never arbitrary request URLs.
`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED` is fixed. Arbitrary text defaults
to confidential. `cloud_enabled` and an allowlisted commercial model alone do not
permit confidential input. The complete context must be an approved public task.

Financial values are integer micro-USD. Token totals include guard work. Guard
sublimits sit within root totals. Local slot-seconds measure occupied inference
capacity; they are not API charges. Reducing a limit can leave an existing account
overcommitted. Costs and reservations remain intact and new admissions stop.

## Environment and secrets

| Setting | Use and boundary |
| --- | --- |
| `DATABASE_URL` | Restricted runtime database role; migrations use separate owner credentials |
| `POLICY_PATH`, `MODEL_MANIFEST_PATH`, `TOOL_REGISTRY_PATH` | Public read-only artifacts |
| `OPA_URL`, `OPA_REPLICA_URLS` | Registered private policy endpoints |
| `OLLAMA_GUARD_URL`, `OLLAMA_AGENT_URL` | Private supervised workers |
| `DEMO_TOOLS_URL` | Registered synthetic MCP/resource server |
| `PUBLISHER_URL`, `FEED_URL` | Private publisher and signed data feed |
| `AUTH_ISSUER`, `AUTH_AUDIENCE` | Verified demo token claims |
| `ALLOWED_ORIGINS`, `PUBLIC_BASE_URL`, `ACTIONGATE_PORT` | Loopback browser/API origin configuration |
| `ACTIONGATE_DEMO_AUTH` | Local-only demo identity selection; disable for a non-demo deployment |
| `ACTIONGATE_AUTH_KEY`, `ACTIONGATE_ENCRYPTION_KEY`, `ACTIONGATE_AUDIT_KEY` | Distinct identity, protected payload and tenant correlation keys |
| `ACTIONGATE_CONNECTOR_KEY`, `ACTIONGATE_GUARD_KEY`, `ACTIONGATE_BUSINESS_KEY` | Component-scoped credentials; never agent credentials |
| `ACTIONGATE_POLICY_SIGNING_KEY`, `ACTIONGATE_FEED_SIGNING_KEY` | Publisher-only private signing keys |
| `ACTIONGATE_POLICY_PUBLIC_KEY`, `ACTIONGATE_FEED_PUBLIC_KEY` | Gateway verification keys |
| `ACTIONGATE_SPOOL_KEY` | Protected recovery journal encryption |
| `GROQ_API_KEY` | Optional cloud connector secret from psst |

No credential is passed as a Vite variable. Keys with `_FILE` support are read
from protected files when configured; temporary files require restricted access.
The launch helper reads psst entries without displaying values. Per-service
Compose mapping is the inventory of which secret each runtime can receive.

## Semantic classification contract

For source windows, the local model returns exactly four JSON keys in this order:
`category`, `risk_level`, `reason`, `evidence`. It chooses one category, with a
fixed public verdict projection:

| Model category | Public verdict | Required consistency |
| --- | --- | --- |
| `none` | `benign` | Risk 0 and an empty evidence list |
| `prompt_injection`, `goal_drift`, `data_exfiltration`, `memory_poisoning`, `code_execution` | `suspicious` | Risk 1, 2 or 3 and at least one source evidence span |
| `uncertain`, `unsupported_language` | `unknown` | Risk 3; the required inspection remains incomplete |

The strict model schema rejects extra keys, including a separate model-generated
`verdict`, incorrect types, invalid categories and inconsistent category/risk pairs.
The reason is a bounded operational assessment, not a request for a private chain
of thought. The prompt asks for at most 180 characters; the schema's hard bound is
400 characters. Source-window evidence has at most two nonempty literal spans. A uniquely
matching case-insensitive quotation is mapped back to the original source bytes;
translated, paraphrased or ambiguous substitute evidence is rejected.

The public `verdict` is a deterministic projection of the accepted model category.
It is not a second model opinion and does not override a model classification
after the fact. The projection, strict schema, both prompts and evidence/window
contracts contribute to the guard artifact hash bound into the signed control
generation. An altered classification mapping requires a new signed artifact.

Long input receives complete overlapping source-window scans and an additional
goal/action assessment of their validated findings. Its evidence uses server-owned
`effect` and `window:N` references with explicit `derived_goal_action_context`
scope. The server resolves each exact ID to the actual proposed effect or the
complete validated finding, including its original source quotations. It records
the full context hash. A reference never confers permission, and quoting an ID
with extra characters or inventing a window fails validation.

The goal review's native JSON schema encodes the same existing category, risk
and evidence invariants as the independent application validator. Both the schema
template and reference contract are included in the signed guard artifact. This
avoids requiring the model to quote escaped JSON inside derived assessments;
source-window evidence still requires literal source spans. No goal result can
turn an earlier threat or unknown window into a benign result.
Unknown output, malformed JSON, unsupported material, failed
coverage or unknown usage closes a required path. Quality reports count unknown
or incomplete inspection separately, never as a true-positive threat detection.
No post-hoc keyword rule converts a mistaken or unknown model result into a
claimed semantic success. Deterministic ACL, DLP and policy decisions remain
separate evidence.

The source-window native schema encodes these same consistency rules in three
disjoint alternatives: benign, suspicious and unknown. Classification precedes
the short reason and literal evidence. The independent application validator and
source matching still validate every response. Only nonvalidating `title`
annotations are omitted from this native schema to conserve serialized context;
no input, output or evidence limits change. The actual schema and an explicit
field-order array are signed, so canonical JSON hashing cannot hide an order
change. The goal/action prompt and its separate schema retain their existing
contract.

## Bounded semantic reuse

`performance.semantic_cache_enabled` permits reuse of complete guard judgments.
Each gateway process retains at most 512 entries, 8 MiB of serialized values and
64 KiB per entry for 120 seconds. Its keyed content hash uses a random process
secret. The key binds tenant, exact text, trusted purpose and proposed effect,
the actual root restrictions and label version, run purpose, revocation epoch,
signed generation, policy digest, model manifest/digest and guard artifact.
Equivalent contexts in the same tenant can reuse a judgment across runs. Raw
input text is not retained as a cache key or logged.

Unknown or incompletely metered judgments are not cached. A hit carries
`cache_hit` and `cache_source` provenance, zero new inference usage at every nested
level, and no copied timing measurement. Authorization, current labels, budgets,
OPA decisions, approvals and release fences still run. This cache cannot grant an
action or make a prior approval valid again. Controlled contract-model fixtures
bypass it. Semantic quality and repeat-variation evaluations explicitly request
fresh inference; product replay can use valid cached observations and reports the
hit count separately from actual token use.

## HTTP and MCP

Create a trusted workflow with `POST /runs`, then use its workload token for
`POST /actions` or Chat Completions. Trusted operator clients may additionally
supply `X-ActionGate-Run-Id`; an agent cannot use it to leave its exact workload
run or borrow a parent's or sibling's grant. The shared root remains the budget
and label boundary.
`Idempotency-Key` is supported by Chat Completions/MCP, while typed actions carry
`idempotency_key` in their body. Reusing a key with altered payload is a conflict.

Operator clients using the demo session cookie must send an allowed `Origin` on
mutations. Bearer-authenticated workload calls use their exact-run token. Demo
identity issuance verifies the TCP peer against the local operator edge; a forged
`Host: localhost` or `Origin` from the isolated agent network grants no identity.
The shipped trusted launcher supplies its loopback base URL as the origin.

The Chat Completions subset accepts textual messages and approved tool schemas.
Full arbitrary provider compatibility, multimodal input and unsupported options
are not claimed. Streaming is buffered through final inspection. Errors include
the controlled operation outcome rather than an unchecked upstream body.

The deployment pins generation to `temperature=0`; omit the field or supply zero.
Any other value returns 422 before inference. `tool_choice` accepts `auto` and
`none`. Both are retained in the typed, inspected request. With `auto`, approved
schemas are offered to the adapter. With `none`, the same supplied schemas remain
available to the guard and audit but are not sent to the business model; any
returned tool call blocks the complete response, including a streaming response.
Quoting and dispatch use the same actual offered schemas. Named forced choices
and other provider-specific options are outside this documented subset.

The downstream MCP server supports initialization, ping, registered tools,
granted resources and `supplier_review_v1` without dynamic arguments. Every read
and call uses the same broker. The upstream document connector verifies the exact
approved description and JSON Schema before calling its registered tool. Client
tokens are never forwarded upstream. Unknown methods fail explicitly.

The registered stdio adapter forwards the same operations to `/mcp/`. Its local
launcher checks the exact module version and digest before spawning the current
Python interpreter. It carries only a workflow token, not upstream credentials.
See `packages/clients/README.md` for the process manifest and example.

`POST /api/policies/compare-synthetic` starts a charged, effect-free comparison on
the calibration corpus. A passing comparison case means the guard completed and
both policy decisions were evaluated; it is not a held-out semantic accuracy
measurement. `POST /api/audit/checkpoint` signs the current audit head.
`POST /api/demo/reset` restores only synthetic-tenant resources while preserving
audit history and commitments. These endpoints require an administrator; reset
also requires the authenticated synthetic tenant.

Interactive test jobs have one durable owner and a 90-second worker lease.
The response from `GET /api/tests` includes heartbeat and deadline timestamps.
An expired job is recorded as failed without retrying effects or releasing any
outstanding reservation; see the recovery procedure in `operations.md`.
Each interactive result records the generation, profile, reporting tenant and
synthetic execution tenant. A changed generation interrupts the job. Disabled
or advisory protection produces an `advisory` assessment, separate from both
passed and failed counts; an allowed injection under Observe is not a protection
pass. The ad hoc playground executes in the tenant shown on its own badge.

`GET /api/budgets` presents price provenance from the verified signed generation,
including its revision, signature status and validity interval. It independently
checks current catalog validity. Each execution reservation can also expose the
price revision recorded in its operation, original generation and execution mode.
The display never recalculates historical usage using the current catalog. Guard
reservations report local resource usage without an API price revision.

Administrative endpoints require server-side roles. Analysts can inspect their
tenant and run workflows; approvers can decide exact operations; managers receive
aggregates and operation metadata without protected payloads; administrators can
activate policy/feed changes. JSONL export is for admin/analyst roles. The local
demo role selector does not weaken these checks on individual API requests.
