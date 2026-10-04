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

This lets an application team centralize action authorization and resource
accounting instead of implementing a different policy in every agent client.
Security operators can inspect why an action was allowed or stopped and verify
its recorded effect. Cost and capacity owners can distinguish spending,
reservations and unresolved usage.

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
