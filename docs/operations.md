# Installation, operation and recovery

Team: DEFOZO SOFTWARE HOUSE. Author: Michał Kiełtyka.

## Installation contract

Use Linux containers. Windows operates the same Compose stack through Docker
Desktop or WSL2. The planning baseline is eight CPU threads, 24 GB RAM and 20 GB
disk; it is an assumption to validate on the actual machine, not a proven minimum.
The current Compose file specifies per-container CPU, memory and process limits.
The pinned manifest identifies model weights and tokenizer separately.

Run `scripts/bootstrap.ps1 -Profile local` once with network access. It prepares
the images and weights and initializes secrets without overwriting existing psst
entries. Run `scripts/start.ps1 -Profile local`, then `scripts/doctor.ps1` and
`scripts/verify.ps1 -Suite all-local`. Shell wrappers expose the same lifecycle.
No `.env` containing author credentials belongs in a jury archive. A clean
installation must create its own identities and cryptographic material.

After preparation, validate the offline path by disabling external access for
the acceptance run while preserving internal Compose communication. A model list
or HTTP 200 health response does not replace an actual inference or functional
allow/block test. Preserve the clean-install and offline reports with the release.

## Health and operating state

`/health/live` reports the gateway process. `/health/ready` checks the database,
verified generation, OPA and both required model workers. The dashboard separates
configuration state from service readiness. A ready stack still needs functional
verification. Inspect operations by execution and settlement status, not only the
policy decision.

The management CSV aggregates committed resources. JSONL audit is for authorized
security roles. The protected operation store holds necessary payloads separately
from metadata-only audit. Keep raw payloads, signing keys, tokens and secrets out
of screenshots, diagnostics and support tickets. Never print a resolved Compose
configuration when its environment contains secrets.

## Policy and feed updates

Edit `policy/control.yaml` or its dashboard representation. Validate the complete
candidate before activation. A generation combines YAML, tool/model registries,
Rego and the signed feed. Invalid updates retain the last valid generation.
Restore earlier content by activating a new revision; do not decrease counters.
Read back the active generation and verify behavior through both replicas.

The feed is data, with bounded operators and trusted provenance. Use the local
publisher to issue a new signed revision after adding or removing a rule. A feed
outage can use only a still-valid accepted snapshot. Expired data cannot silently
be treated as a successful update. Fix publisher time, signature, parser or
revision errors rather than disabling verification.

## Incident recovery

1. Preserve operation IDs, run IDs, active generation, reservation state and the
   latest reports. Use the admin kill switch to stop new actions where needed.
2. Restore missing infrastructure. Keep database and signing volumes persistent;
   removing them is a reset, not a repair.
3. Inspect `dispatched`, `outcome_unknown` and `usage_unknown` operations. Check the
   controlled connector receipt before retrying. A timed-out mutation might have
   completed. Never release its reservation solely because its TTL elapsed.
4. Confirm an interrupted model process stopped before reusing its slot. Confirm
   the other worker remains functional and that no old publication channel lives.
5. Rerun readiness, the legal workflow and the relevant failure regression. Clear
   the kill switch only after read-back proves recovery.

Backing up this deployment requires PostgreSQL data, model artifacts, the public
policy/feed configuration, protected recovery journals and psst-managed keys.
Protect secrets separately and test restoration. Retention configuration alone is
not a backup policy. Do not remove another tenant's history to reset a demo.

## Retention and audit archives

The default Compose `maintenance` service runs the owner-only retention job
hourly. It uses `audit.retention_days` from the active configuration, seven days
by default. Before removing an old audit segment it requests a signed archive
from the separate publisher and verifies the acknowledgement. Failure to store
that archive stops the purge. Archive files reside in the publisher volume;
back them up to a separately controlled store when rollback detection must
survive loss of the host.

The application database role cannot update or delete audit rows. Maintenance
also clears expired encrypted operation payloads, removes objects after their
TTL, and removes eligible settled receipts. It never automatically releases
unresolved reservations or deletes financial obligations. Preserve uncertain
operations for reconciliation, even when their payload retention has elapsed.

Administrators can create a metadata checkpoint from **Investigate** or
`POST /api/audit/checkpoint`. The signed checkpoint proves a particular chain
head. Its creation alone is not an external backup or an immutable storage claim.

## Interrupted interactive test jobs

Interactive suites and synthetic policy comparisons have a PostgreSQL owner,
heartbeat and a persisted deadline. A live owner refreshes its heartbeat every
10 seconds. A 90-second heartbeat gap, or the stored deadline, changes the job
to `failed` with a `job.incomplete` result. Both replicas and the test list recover
expired jobs; admission is serialized so one tenant cannot start two concurrent
jobs. The deadline derives from the configured workflow lifetime and the number
of workflows in the selected suite.

Recovery preserves completed case evidence and all financial or resource
obligations. It does not restart actions, reset budgets or turn an incomplete
case into a pass. A worker that resumes after losing its lease cannot append
results or dispatch the next case. Already admitted work is allowed to finish
its normal settlement path. Review uncertain operations before starting a new
job. Upgrade with the schema-owner migration service before starting a gateway
version that uses these lease fields.

## Optional telemetry collector

Set `OTEL_EXPORTER_OTLP_ENDPOINT` in the gateway's operator-managed environment
to enable the OpenTelemetry HTTP JSON exporter. The base URL receives
`/v1/traces`; redirects and proxy environment settings are disabled. The
exporter uses a bounded queue and reports transport failures independently of
the mandatory security audit. The collector must be reachable from the gateway
network and access-controlled by the deployment operator.

Spans contain phase names, registered tool identifiers, root run identifiers and
control generation metadata. Prompts, results, authorization headers and secret
values are excluded. Exporting telemetry is optional and does not replace the
PostgreSQL audit, budget ledger or acceptance evidence.

## Optional commercial profile

The Groq profile is opt-in. Run `scripts/start.ps1 -Profile cloud` and the separate
`live-provider` verification after preparing its key in psst, approved model ID
and current price record. The connector receives provider credentials; the agent
and browser do not. The full context must be from an approved PUBLIC source.
Arbitrary user text is confidential by default. A missing key is reported as not
run, and simulated contract responses are explicitly labeled as fixtures.
