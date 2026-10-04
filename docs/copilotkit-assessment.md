# Agent client integration

ActionGate exposes HTTP actions, a supported Chat Completions subset and MCP.
The shipped [Python and TypeScript examples](../packages/clients/README.md)
show how a trusted application creates a workflow and passes its scoped
credential to an agent. The [architecture](architecture.md) describes the
enforcement path.

The operator dashboard reads persisted actions and submits exact-payload
approvals or validated policy generations. A conversational frontend can use
the same gateway interfaces, but it must preserve workflow identity, grants,
labels, approval hashes and resource reservations. Browser components do not
hold signing keys or grant themselves authority.

No CopilotKit adapter is included in this release. Framework-specific clients
must be integrated and verified against the gateway contract before use.
