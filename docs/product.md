# Product and operating model

ActionGate provides a shared enforcement point for applications that let AI
agents read documents, call models and perform business actions. The included
supplier-review workflow uses synthetic data and controlled document/report
sinks. It demonstrates the integration; it does not send real payments or email.

## What an adopter gets

- A gateway for HTTP actions, a supported Chat Completions subset and MCP, with
  Python and TypeScript client examples.
- Workflow grants that bind the authenticated tenant, purpose, permitted data,
  destination and tools. Delegated work inherits labels and a shared budget.
- Exact-payload approval and durable receipts that connect a decision to the
  actual effect, resource reservation and settlement.
- An operator dashboard for investigation, signed policy/feed changes, resource
  accounts, test jobs and role-scoped JSONL/CSV exports.

Application teams can apply the same controls to HTTP, Chat Completions and
MCP clients. Security operators can inspect the policy behind an action, the
approval it used and its recorded effect. Cost and capacity owners can distinguish
completed spending from reservations and unresolved usage across delegated work.

## From an approval to an accountable action

An approval binds the tenant, actor, tool, tool version and exact arguments.
Before dispatch, the broker checks the active policy generation, workflow labels,
expiry and revocation state again. It consumes a one-time execution grant and
records the resulting effect. A changed recipient or payload cannot reuse the
original approval.

The shared PostgreSQL ledger reserves tokens, local inference occupancy and
monetary commitments across the tenant, user and root workflow. Delegation keeps
the root allowance. If usage is unknown after dispatch, the reservation remains
open for reconciliation. Operators therefore see both available capacity and
commitments that have not yet been resolved.

Signed policy and feed generations provide a recorded version for each decision.
Policy comparison runs synthetic calibration cases against the active and
candidate configuration without executing business effects. It reports when a
semantic runtime change requires separate verification.

## Integration and ownership

Start with the [local installation](../README.md#run-locally), then adapt the
[client examples](../packages/clients/README.md). The trusted application issues
workflow grants; an agent cannot grant itself a wider purpose. Register each
business connector and keep direct credentials and network routes outside the
agent's reach. An SDK endpoint change alone does not enforce that boundary.

The deployment owner manages signing keys, database backups, model artifacts,
connector permissions, retention and incident recovery. Policy owners validate
and activate complete signed generations. Approvers review the exact payload
and recipient. [Operations](operations.md) documents these procedures, while
[configuration](configuration.md) describes supported controls.

The reference stack runs locally with Compose and two gateway replicas sharing
PostgreSQL. Local inference has hardware, queue and latency costs. The optional
Groq connector is disabled by default and requires separate credentials, a
current price catalog and a permitted PUBLIC context. Its latest recorded
gateway smoke failed output inspection; see [cloud verification](cloud.md).

## Deployment limits

The demonstration identity selector is not enterprise authentication. Production
adoption requires a trusted identity integration, deployment-specific connector
authorization and operational capacity validation. Local semantic classification
can misclassify unfamiliar inputs; deterministic grants, labels and budgets
remain separate controls. The measured benchmark did not meet the initial
50 ms deterministic overhead target. Consult [measurements](benchmark.md),
[acceptance](acceptance.md) and the [threat model](threat-model.md) before choosing
capacity or relying on a particular protection boundary.
