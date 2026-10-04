# ActionGate integration clients

The server owns identity checks, grants, label propagation, screening, approval,
reservations and release. These clients only send requests and inspect results.
The gateway URL alone does not isolate an agent that still has open network access.

## Trusted application and workload identity

1. Authenticate the trusted application to the gateway.
2. `POST /runs` with a granted purpose, explicit document IDs and optional
   publication authority. The response includes `id` and `workload_token`.
3. Inject that token and the run ID into the isolated agent process. Do not give
   the agent the trusted application's cookie, administrator identity or provider keys.
4. For `/actions`, pass `run_id` in the validated action body. For model and MCP
   clients, pass `X-ActionGate-Run-Id`. Each workload identity is restricted to its
   exact run; a child cannot borrow its parent's or a sibling's wider grant. The
   root still binds the shared budget and context label. Omitting the header
   selects the exact run derived from the authenticated workload principal.
5. Preserve an idempotency key when retrying the same exact action. A different
   payload requires a new key. Inspect uncertain outcomes before retrying a mutation.

## Python

`python/actiongate_client.py` uses the standard library. Run the loopback-only
trusted demo after starting the system:

```sh
python packages/clients/python/trusted_demo.py
```

`agent_example.py` expects `ACTIONGATE_WORKLOAD_TOKEN` and `ACTIONGATE_RUN_ID`
to be injected by the trusted launcher. It never prints credentials.

## TypeScript

The client is a standalone import with no runtime dependencies. With Node 24:

```sh
node packages/clients/typescript/agent-example.ts
```

Do not bundle server workload credentials into public browser code. The dashboard
uses its separate HttpOnly session cookie and the server enforces each role.

To execute both shipped examples against a ready installation, run:

```sh
python scripts/verify_clients.py --base-url http://127.0.0.1:8080
```

This performs real guarded operations in the synthetic test tenant and consumes
its local inference budget. The launcher creates two separate workload identities,
executes the example files, then correlates completed operations with the actual
connector receipt and audit. Its report is
`artifacts/submission/client-examples.json`; no session or workload token is saved.

## Existing Chat Completions clients

Set the client's base URL to `http://127.0.0.1:8080/v1`, its API key to the
run-bound workload token, and its model to `local-business`. If selecting a
delegated run, also send `X-ActionGate-Run-Id`. The gateway supports the documented
text subset, inspected tool calls and buffered streaming. Unsupported modalities
and request fields are rejected. There is no automatic cloud fallback.

```python
# Optional third-party client, installed separately in your application.
from openai import OpenAI
client = OpenAI(base_url=base_url + '/v1', api_key=workload_token,
    default_headers={'X-ActionGate-Run-Id': run_id})
response = client.chat.completions.create(model='local-business',
    messages=[{'role': 'user', 'content': 'Summarize the permitted supplier risks.'}],
    max_tokens=256)
```

## Official MCP client

`python/mcp_example.py` uses the pinned official MCP package in the project lock.
The endpoint is `/mcp/`; transport is authenticated Streamable HTTP. Authorized
tools and their schemas are returned by `tools/list`. Call them with original
registry arguments, without adding authority or run fields to the tool payload.
`resources/read` routes document and memory reads through the same broker.
Only `supplier_review_v1` is available through `prompts/get`; it has no dynamic
arguments. Sampling, elicitation and unimplemented protocol methods are denied.

Use a stable `Idempotency-Key` header for one logical MCP action when retrying it.
Create a new client transport/header for a different mutating action. Omitting
the header creates a fresh server operation and does not provide retry deduplication.

## Registered stdio process

`python/mcp_stdio_example.py` uses the official SDK's `stdio_client` with
`stdio_launcher.registered_stdio`. The launcher verifies the wrapper version and
SHA-256 in `stdio-wrapper.json`, then starts only the current Python interpreter
with `-m actiongate.mcp_stdio`. It accepts no executable, shell command or install
step from an agent. The lockfile supplies the MCP dependency.

The child receives only the root workload token, the registered local gateway
origin and its Python module path. It forwards tools, resources and the approved
prompt to authenticated `/mcp/`. It cannot execute a tool locally or bypass the
broker. Model, database and connector credentials are never injected. Resource
subscriptions, sampling and unimplemented protocol capabilities are denied.
Redirects and proxy environment settings are disabled. Allowed gateway hosts are
`127.0.0.1`, `localhost`, `gateway-a` and `gateway-b` over local HTTP; the container
network supplies the external isolation boundary.

Keep the process inside the agent's network sandbox. Pinning this one adapter
does not turn arbitrary other stdio programs into trusted tools. Rebuild and
review the manifest whenever changing its source. The stdio example does not
automatically retry mutations; inspect the operation ledger after transport loss.

## Reading results

`decision` is independent of `status` and `settlement_status`. `allow` does not
mean a connector completed. `waiting_approval` is not a completed effect.
`output_blocked` can still have incurred upstream cost. `outcome_unknown` and
`usage_unknown` retain commitments until reconciliation. Approval binds the exact
payload hash and does not lower data classification or increase the grant.
