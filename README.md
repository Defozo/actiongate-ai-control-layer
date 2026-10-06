# ActionGate

**AI Control Layer** by **DEFOZO SOFTWARE HOUSE**. Sole team member: **Michał Kiełtyka**.

ActionGate gives application teams one place to control what an AI agent may
read, spend and publish. It checks each governed action against the agent's
identity, business purpose, data permissions, recipient and available budget
before dispatch. Operators can trace the decision through execution and settlement.

For a supplier review, this means an agent can read its assigned documents,
summarize them locally and save an internal report. Publishing confidential
content to an allowed internal recipient requires approval of that exact content.
Changing the payload or active policy requires a new valid authorization.

## Controls that follow the work

| Operational need | What ActionGate provides |
| --- | --- |
| Review a sensitive action before it happens | Approval bound to the exact arguments, tool version and policy generation, checked again before dispatch |
| Keep delegated agents within the same allowance | Shared root-workflow limits for tokens, local inference occupancy and monetary commitments, reserved in PostgreSQL transactions |
| Keep confidential context protected across steps | Workflow labels that carry through model calls, memory and delegated work, with checks on permitted destinations |
| Investigate a timeout or disputed action | Linked decisions, execution grants, effect records and settlements; unresolved usage stays visible and reserved until reconciled |
| Try a policy change before activating it | Signed control generations and comparison on synthetic cases without executing business effects |

A local semantic guard adds input and output inspection. Grants, confidentiality
rules and resource accounting remain independent of its verdict. This combines
semantic detection with explicit authorization boundaries; it does not guarantee
recognition of every prompt injection. Enforcement depends on routing governed
actions through the gateway and isolating direct credentials and network access.

## Explore a supplier review

The demonstrator uses synthetic documents and controlled report sinks. Follow a
permitted review, inspect an exact-payload approval, then examine how the gateway
handles an unauthorized document or destination. A separate trusted public
projection copies approved fields from the supplier registry before confidential
work begins.

[Explore the project](https://hackyeah-2026-projekty.defozo.chatgpt.site/#ai-control-layer),
[watch the demonstration and open the presentation](https://defozo.github.io/actiongate-ai-control-layer/),
or [run the synthetic examples locally](#run-locally).
The visitor interface offers four fixed synthetic examples when its backend is
running. The full operator dashboard at `/operator` requires separate access from
Michał Kiełtyka. The [operator runbook](docs/jury-runbook.md) covers editable scenarios.
The [downloadable release](https://github.com/Defozo/actiongate-ai-control-layer/releases/latest)
contains the source distribution and recorded verification evidence. Its manifest
identifies the tested base release and later public-entry and documentation changes.

## Run locally

Prerequisites: Docker Desktop using Linux containers or Docker Engine with Compose,
PowerShell 7 or a POSIX shell, Python, `uv` and `psst` on PATH. Full local
verification also requires Node.js 24, npm and the Playwright Chromium browser.
Start from this project directory. Preparation downloads pinned dependencies and
model weights; subsequent local acceptance does not require a paid API.

Install the secrets CLI with `npm install -g psst-cli` using the
[upstream installation guide](https://github.com/Michaelliv/psst#installation).
On a new machine, initialize a project vault with `psst init` before bootstrap;
the bootstrap helper creates this installation's application keys.

```powershell
pwsh -File scripts/bootstrap.ps1 -Profile local
pwsh -File scripts/start.ps1 -Profile local
npm --prefix ui ci
Push-Location ui
npx playwright install chromium
Pop-Location
pwsh -File scripts/doctor.ps1
uv run python scripts/deployed_source.py
uv run python scripts/runtime_hardware.py
pwsh -File scripts/verify.ps1 -Suite all-local
```

Shell entrypoints are `scripts/bootstrap.sh`, `start.sh`, `doctor.sh` and `verify.sh`.
The two Python commands record the deployed source and actual model placement
on your machine. Run them after the model preflight, before local acceptance.
On Linux, install Chromium system libraries with
`(cd ui && npx playwright install --with-deps chromium)` during preparation.
Open **http://127.0.0.1:8080** and choose an Acme demo identity. The loopback demo
issuer intentionally permits role selection. Keep direct edge access on loopback.
The hosted visitor configuration exposes only fixed examples without an account. Its full
operator interface has a separate authenticated HTTPS gateway; selectable demo
roles do not represent enterprise identities. See [public access](demo/public/visitor.md).
`ACTIONGATE_PORT` selects an alternative loopback port when 8080 is occupied.
The API contract is available at `/api/docs`. See the [jury runbook](docs/jury-runbook.md)
for independent, editable scenarios and expected evidence.

No key belongs in the source tree. Bootstrap reads or creates application-specific
entries in `psst`; the launch helpers map each secret only to its required service.
The optional Groq connector receives its own provider key. The local profile does
not silently fall back to a cloud model.

## What to inspect

| Interface | Use |
| --- | --- |
| Overview | Active generation, configured controls, service readiness and persisted decisions |
| Investigate | Workflow provenance, labels, exact approval, execution state and recorded effect |
| Policies & feeds | Edit, validate, compare and activate a signed control generation |
| Budgets | Tokens, local occupancy, monetary commitments and signed price provenance |
| Test Lab | Arbitrary input, profile-aware interactive checks and explicit advisory results; full acceptance remains the CLI command |
| `/v1/chat/completions` | Controlled text Chat Completions subset with fully inspected buffered streaming |
| `/mcp/` | Authenticated MCP Streamable HTTP tools, resources and approved prompt |
| Registered MCP stdio launcher | Digest-pinned local wrapper forwarding the same authenticated workflow |
| `/runs`, `/actions` | Trusted workflow creation and typed actions; exact-run credentials share root limits and labels |

The [Python and TypeScript examples](packages/clients/README.md) show endpoint
integration. Network isolation is provided by the reference Compose deployment;
changing an SDK URL alone does not isolate an otherwise unrestricted agent.

## Evidence and scope

`contract` checks deterministic invariants with controlled model upstreams.
`all-local` adds actual local inference and end-to-end acceptance, while
`live-provider` covers the optional commercial connector. The
[verification guide](docs/acceptance.md) links dated reports and explains each
suite's prerequisites, measured results and release scope.

The [evaluation corpus](tests/corpus/) and [verification guide](docs/acceptance.md)
separate calibration, regression and frozen holdout evidence. Recorded results
describe the evaluated inputs and deployment; use the [benchmark](docs/benchmark.md)
and [threat model](docs/threat-model.md) to assess a proposed integration.

Read [architecture](docs/architecture.md), [configuration](docs/configuration.md),
[threat model and limitations](docs/threat-model.md), [operations](docs/operations.md),
[coverage map](docs/requirements.md) and the [submission text](docs/submission.md).
The [dashboard evidence contract](docs/dashboard-contract.md) explains each view's
data source, measurement scope and unavailable states.
The [product guide](docs/product.md) describes integration, operating responsibilities and deployment limits.
The dated [design specification](official-2026-10-03/PLAN.md) records original requirements;
current operation and measurements are documented in the guides above.

## Repository

`backend/actiongate` contains the API, broker, ledger, controls and connectors;
`runtime` contains supervised model execution; `ui` is the React dashboard;
`policy`, `feeds` and `models` hold public configuration and manifests;
`packages/clients` contains integration examples; `tests` and `scripts` contain
verification and lifecycle tools. Compose defines the isolated reference runtime.
Model licensing is recorded separately from the runner and application libraries.

Presentation, videos and source downloads are available from the linked material
page and GitHub release. Credits and dependency licenses are recorded in
[TEAM.json](TEAM.json), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[media provenance](docs/media-provenance.md).
