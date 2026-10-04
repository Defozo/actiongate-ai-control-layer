# Optional commercial model

ActionGate supports Groq `openai/gpt-oss-20b` as the logical model `cloud-business`. The default local policy leaves cloud calls disabled. `policy/cloud.yaml` enables this allowlisted model and raises the run token ceiling to 300,000 so its conservative reservation can fit alongside protection. Local guard inference remains mandatory.

The `cloud-connector` service alone receives `GROQ_API_KEY` from psst. Gateway receives only the internal connector credential. The connector resolves the fixed `api.groq.com` hostname, rejects non-public DNS results, connects to a validated IP with the official hostname as TLS SNI and HTTP Host, verifies certificates, and disables redirects, environment proxies and automatic retries. Provider error bodies and credentials are never returned to callers.

Only complete PUBLIC contexts created by the trusted application can reach this adapter. Create a workflow with `document_ids: []` and `public_model_task: supplier_directory_summary`; submit exactly the returned `public_messages` using that workflow's credential. Adding arbitrary text raises the context to CONFIDENTIAL and blocks commercial inference. The gateway never accepts a caller's assertion that arbitrary text is public.

## Price and token contract

The verified catalog lives in `policy/prices.json` and is bound to each signed policy generation. Its review interval is seven days. Enabling cloud with missing, expired or mismatched prices fails closed; optional price expiry does not disable an all-local deployment.

The [Groq model specification](https://console.groq.com/docs/model/openai/gpt-oss-20b) gives a 131,072 token context, 65,536 maximum completion tokens and standard prices of USD 0.075 per million input tokens and USD 0.30 per million output tokens, verified on 3 October 2026. The adapter reserves the entire context window as an input upper bound, plus `max_completion_tokens` for paid output. It makes no tokenizer estimate and assumes no cache discount. The output cap includes hidden reasoning; reasoning is excluded from the returned message while still included in usage and cost. See [Groq reasoning](https://console.groq.com/docs/reasoning).

For a 256 token completion the conservative reservation is 9,908 micro-USD (USD 0.009908). Actual provider usage is settled with integer upward rounding. Missing or malformed usage retains the reservation. Usage beyond the contract preserves the real cost and is reported as a contract violation. No built-in web, code execution or other separately billed tools are enabled. Client function schemas are supported by the connector; invoking a returned function remains the broker's responsibility.

The broker applies `budgets.max_retries` only to an authenticated, complete Groq HTTP 429 rejection of a model proposal. It respects a numeric `Retry-After` of at most 30 seconds; absent headers use one second. Other errors, partial responses, timeouts and effectful tool calls are never retried automatically. [Groq documents the rate-limit status and header](https://console.groq.com/docs/rate-limits); that documentation does not establish zero billing, so the rejected attempt keeps its monetary/token reservation as `usage_unknown`. A new attempt obtains a separate reservation, fresh OPA decision and rotated one-use grant after current authority, workflow step/deadline, label, approval TTL and policy checks. An inspected later answer can complete with unresolved prior usage explicitly visible in operation metadata and balances. Rejected attempts are counted in the shared workflow step limit. Tests use controlled rejections and do not claim a real provider rate-limit event.

## Verification

Start the optional profile with `pwsh -File scripts/start.ps1 -Profile cloud`. The equivalent shell script uses the same Compose services. Activate `policy/cloud.yaml` through Policies or the policy publisher after checking its current price revision.

`psst GROQ_API_KEY ACTIONGATE_CONNECTOR_KEY -- uv run python scripts/live_provider.py` performs one real connector request with a maximum USD 0.01 reservation and tests genuine function calling and usage. `artifacts/reports/live-provider.json` identifies this as connector verification and does not claim gateway ledger coverage.

`uv run python scripts/live_gateway.py --url http://127.0.0.1:8080` performs a separate real request through the authenticated gateway and local guard, verifies the durable ledger and public-context restriction, and restores the previous policy in a `finally` block. Use the configured local port if it differs. Its report is `artifacts/reports/live-provider-gateway.json`. This script receives no provider key. Do not run it concurrently with holdout evaluation or other policy changes.

The recorded 3 October 2026 gateway run completed under generation 7, settled all three reservations and charged 20 micro-USD for 107 prompt tokens and 39 completion tokens, including 6 reasoning tokens. Both local inspections ran. Altering the trusted public messages produced HTTP 403 with `flow.cloud`; the original local policy was restored as generation 8. The preceding generation-race failure is preserved separately and incurred no provider charge.

Offline contract tests use explicit controlled HTTP responses in `tests/test_cloud.py`. They cover valid/invalid reservations, hidden reasoning accounting, missing usage, contract violations, authentication, context classification, operation binding, price expiry, DNS destinations, redirects, response-buffer limits and tool-call preservation. These tests do not count as live provider evidence.

`pwsh -File scripts/verify.ps1 -Suite live-provider` (or `sh scripts/verify.sh live-provider`) explicitly runs the bounded gateway smoke after checking the isolated connector's readiness. A stopped optional connector or missing credential produces `not_run` reports and exit code 2, never PASS. A configured connector with an invalid price fails before inference. The readiness response reveals only configuration booleans, not credentials. Start the cloud profile first; the provider key stays inside that connector.
