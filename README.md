# ActionGate

**AI Control Layer** by **DEFOZO SOFTWARE HOUSE**. Sole team member: **Michał Kiełtyka**.

ActionGate governs the actions an agent can take: identity, purpose, data access,
recipient, exact arguments, approval and resource allowance. A local semantic
guard detects attempts to redirect the workflow. Deterministic authorization,
data labels and the shared ledger remain independent of that model's verdict.

The demonstrator reviews synthetic supplier documents. A legitimate review can
read its granted documents, use a local model, persist memory and save an internal
report. Confidential publication to a controlled internal sink requires approval
of the exact payload. A separate trusted public projection copies approved fields
from the authoritative supplier registry before confidential work begins.

## Published source snapshot

This repository publishes the ActionGate source as of 4 October 2026, with pinned
dependency files, setup scripts, synthetic test cases and third-party notices.
Product development and full acceptance are still in progress.

The latest available complete local run, recorded at 23:23 UTC on 3 October,
passed **252 of 253 tests**, with **one failure and zero skips**. The exposed
semantic regression set had one English false positive; its 10% false-positive
rate exceeded the required 5%. The independent v2 holdout had not yet run.
An earlier contract run passed 347 of 347 tests. These are dated results from the
development workspace, not a claim that this source snapshot passed complete
acceptance. See the [measurement summary](artifacts/publication-test-summary.json)
and [operational limits](docs/threat-model.md).

Publication checks on this exported snapshot passed: the production dashboard
build, locked Python dependency resolution, syntax checks for 121 Python files,
and 50 tests covering release packaging, model artifacts and runtime contracts.
These checks use controlled fixtures and do not replace full local inference
acceptance.

The GitHub source distribution excludes private runtime state, raw logs,
credentials, dependency caches, model weights and large presentation renders.
Links to generated reports or media elsewhere in the documentation describe
artifacts produced by the verification and presentation workflows; those files
are not included in this source snapshot.

- [HackTribe project](https://hackyeah2026.hacktribe.co/actiongate-ai-control-layer/)
- [Repository](https://github.com/Defozo/actiongate-ai-control-layer)

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
pwsh -File scripts/doctor.ps1
npm --prefix ui ci
Push-Location ui
npx playwright install chromium
Pop-Location
pwsh -File scripts/verify.ps1 -Suite all-local
```

Shell entrypoints are `scripts/bootstrap.sh`, `start.sh`, `doctor.sh` and `verify.sh`.
On Linux, install Chromium system libraries with
`(cd ui && npx playwright install --with-deps chromium)` during preparation.
Open **http://127.0.0.1:8080** and choose an Acme demo identity. The loopback demo
issuer intentionally permits role selection; do not expose this profile publicly.
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

`contract` checks deterministic invariants with explicitly controlled model
upstreams. `all-local` adds actual local inference and end-to-end acceptance.
`live-provider` separately verifies the commercial connector and is reported as
not run when a provider key is absent. Missing required dependencies fail local
acceptance rather than becoming successful skips. Locally generated dated reports under `artifacts/`
are the source of measured results; this source snapshot includes only the
credential-free publication summary linked above. A created report, a passed contract suite and
a passed complete release are different claims.

Semantic evaluation separates [16 calibration cases, a 40-case v1 regression set,
and a newly frozen 40-case v2 holdout](tests/corpus/). Each 40-case set has ten
benign and ten attack examples in each of English and Polish. Once v1 outcomes
informed a guard revision, that set became regression evidence rather than an
independent estimate for the revised guard. The v2 manifest records its freeze.
The authored expected labels do not come from the model under evaluation. Targets
in the implementation plan are not achieved measurements.

Read [architecture](docs/architecture.md), [configuration](docs/configuration.md),
[threat model and limitations](docs/threat-model.md), [operations](docs/operations.md),
[coverage map](docs/requirements.md) and the [submission text](docs/submission.md).
The [dashboard evidence contract](docs/dashboard-contract.md) explains each view's
data source, measurement scope and unavailable states.
The selected specification is [official-2026-10-03/PLAN.md](official-2026-10-03/PLAN.md).
Any root-level historical plan is superseded by that document.

## Repository

`backend/actiongate` contains the API, broker, ledger, controls and connectors;
`runtime` contains supervised model execution; `ui` is the React dashboard;
`policy`, `feeds` and `models` hold public configuration and manifests;
`packages/clients` contains integration examples; `tests` and `scripts` contain
verification and lifecycle tools. Compose defines the isolated reference runtime.
Model licensing is recorded separately from the runner and application libraries.

Prepared submission materials do not constitute a HackTribe submission. The
organizer's supplied brief requires English deliverables and a PDF of at most ten
slides. Team identity is sourced from [TEAM.json](TEAM.json).
