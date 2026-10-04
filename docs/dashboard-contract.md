# Dashboard evidence contract

The dashboard presents persisted or explicitly measured server data. A configured
control, an available service and a completed workflow are separate observations.
This map was checked against the five production views and their API responses.

| View | Server evidence and interpretation |
| --- | --- |
| Overview | `/api/overview` supplies tenant decision counts, completed runs, signed generation, configured controls and live dependency readiness. Operational p95 covers only completed operations with recorded `metadata.latency_ms`, within the latest 200 completed records. Sample and considered counts, execution modes, failures and unknown outcomes are explicit. These are retained operational observations, never the acceptance benchmark. |
| Investigate | `/api/runs`, `/api/operations` and their detail endpoints provide workflow purpose, creation time, inherited context, exact inspected operation, recorded generation/feed revision, safe audit events and actual connector receipt. The all-operations timeline is a bounded tenant history; selecting a run loads that run's ordered events. Manager responses omit protected argument and result content. |
| Policies & feeds | `/api/policies` exposes the active generation, publisher state, stored staging/history rows and actual replica readiness. The active record's creation time is labelled as such, rather than as the activation time of a later rejected record. Feed signature evidence refers to the verified control snapshot containing the approved feed. Metadata comparison counts only evaluable records; unavailable inputs remain explicit. |
| Budgets | `/api/budgets` returns real scoped balances, reservations, uncertain usage, worker telemetry and the verified current price catalog. Historical reservations retain their own price revision and generation. Overlapping scope reservations are displayed once per resource, not summed into invented cost. Tokens, slot seconds and API currency remain distinct. |
| Test Lab | Playground input executes in the displayed current tenant. Inspected content, released result, exact guard model and recorded timing use the operation response. Fixed jobs record the active profile, generation, reporting tenant and synthetic execution tenant. Advisory and disabled protection cannot earn protection passes. |

SSE resumes after the last event cursor. Each delivered copy can include
`_delivery.server_backlog_age_ms`, calculated using the database clock and the
Outbox record's database-generated creation time. A transport-only
`_outbox_clock: database` marker identifies new records and is outside the
immutable AuditEvent/hash. Historical records without that marker have an
unverified clock and no numeric backlog estimate. It describes server waiting time for that delivered
event, excluding network and rendering. A future event timestamp yields an
unavailable age. Delivery metadata never changes the persisted payload or hashed
audit record. The separate browser measurement uses a monotonic request-to-DOM
upper bound for the one-second delivery target and tests an actual reconnection.

API contracts use real PostgreSQL and signed configuration; any controlled model
responses or manually constructed history are explicitly named fixtures. The
Playwright contract suite checks production response shapes with API fixtures.
Actual browser navigation, client examples, policy replay and the recorded
interactive job have separate evidence reports.

Reproduce live navigation with `node scripts/verify_live_browser.cjs` and the
five delivery measurements, native EventSource disconnect/reconnect and tenant
exports with `node scripts/verify_dashboard_events.cjs`. Set
`ACTIONGATE_BASE_URL` to the configured loopback edge when it differs from port
8080. These two checks perform no model inference. Reports include SHA-256 hashes
of their producer scripts and verify that those sources stayed unchanged during
execution. They write `artifacts/reports/live-browser.json` and
`artifacts/submission/dashboard-events.json`; screenshots alone are not passes.
