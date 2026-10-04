# Requirement, control and evidence map

Team: DEFOZO SOFTWARE HOUSE. Author: Michał Kiełtyka.

This map identifies implementation and verification locations. It does not assert
that every acceptance check has passed. Read the generated report for status,
time, model/version, execution mode and any unresolved failures.

| Official requirement | Implementation | Positive / negative evidence |
| --- | --- | --- |
| Flexible integration | HTTP actions, Chat Completions subset, official MCP adapter, Python/TypeScript examples | `tests/test_api.py`, `tests/test_mcp_gateway_contract.py`, live client acceptance |
| Central configuration | Strict schema, signed publisher and database generation fence | `tests/test_controls.py`, policy activation and feed mutation acceptance |
| Deterministic controls | ACL, DLP checksums, typed tools, artifact and feed admission | Benign/control pairs in `tests/test_controls.py`; wrong-tenant and forbidden recipient cases |
| Semantic controls | Dedicated local worker, bounded windows and validated verdict | Frozen EN/PL corpus, real-model quality report and unknown/timeout cases |
| Memory and agent scope | Exact-run identity, shared root budget and labels, delegated grants | Broker regression and real memory workflow; public sink absence checked separately |
| Budgets and resources | PostgreSQL reservations in fixed lock order, worker stop confirmation | `tests/test_ledger.py`, concurrent replicas and watchdog reports |
| Exact approval | Canonical payload binding, current policy recheck and connector receipt | `tests/test_api.py` approval/replay/substitution and broker fencing cases |
| Historical attacks | Inert serialization/template fixtures, approved weights and typed execution | `tests/test_controls.py`, legal counterparts and loader non-invocation evidence |
| Reporting | Five dashboard views, outbox SSE, role-aware JSONL/CSV | `ui/tests/contract.spec.ts`, API role/tenant/export tests and real browser check |
| Audit retention | Independent signed archive precedes deletion of an old contiguous event prefix | `tests/test_audit_integrity.py`: real publisher checkpoint, archive refusal, nonmonotonic dates, unarchived concurrent event and preserved financial obligations |
| Synthetic demo reset | Admin-only synthetic resources and run revocation; history, policy and commitments remain | `tests/test_audit_integrity.py`: PostgreSQL reset, foreign-tenant resources and rejected role/tenant/disabled-demo requests |
| Safe policy comparison | Metadata replay with explicit insufficient evidence; charged calibration-corpus replay | API/OPA comparison asserts zero additional business effects and distinct synthetic budgets |
| Interrupted test recovery | Database owner lease, heartbeat and persisted job deadline | `tests/test_job_recovery.py`: late-result refusal, duplicate-owner denial and preserved reservations |
| Reproducible delivery | Compose, dependency locks, psst bootstrap and model manifest | Clean installation, offline local acceptance, SBOM and release checksums |
| Local and commercial models | Required local business/guard workers; optional scoped Groq adapter | Distinct all-local and live-provider reports; fixture costs never presented as invoices |

The command-line `all-local` suite is the release acceptance boundary. Interactive
Test Lab checks are a subset intended for jury exploration. A contract pass with
controlled model responses cannot establish real semantic quality. Any required
dependency failure must produce a failing exit status.
