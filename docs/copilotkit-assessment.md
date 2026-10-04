# CopilotKit assessment

Reviewed on 2026-10-03 for ActionGate by DEFOZO SOFTWARE HOUSE, Michał Kiełtyka.

Decision: keep the existing operator dashboard and gateway clients in this release.

CopilotKit supports embedded chat, shared application state, generative interfaces
and human interaction with agents. Its frontend tools execute in the browser and
can operate React state or browser APIs. These capabilities fit an assistant that
helps users navigate a business application. [Official frontend tools documentation](https://docs.copilotkit.ai/frontend-tools)

ActionGate's operator task is to inspect a concrete action, compare its exact
arguments with authority and budget, then approve a specific payload or publish a
reviewed configuration. The current dashboard exposes those records and controls
directly. Adding a conversational operator here would introduce another agent's
interpretation between the reviewer and that exact record. Navigation or filter
commands alone would not materially improve the demonstrated security workflow.
This is a product-fit decision, not a limitation of CopilotKit.

The existing HTTP, Chat Completions subset and MCP clients demonstrate how a
business agent uses ActionGate. CopilotKit could be a future client surface for
such an agent, with its backend invoking ActionGate under an exact run identity.
Every model and business-tool request would still need the broker's grants,
labels, reservations and release checks. A browser approval component would
display the persisted payload hash and submit it to the existing approval API;
it would not become an independent authorization mechanism.

The current CopilotKit quickstart explicitly distinguishes its built-in agent,
which calls a model itself, from connecting an existing agent. For this product,
a future integration would connect the controlled backend through AG-UI rather
than introduce a direct model route beside the gateway. The Vite frontend is
supported, so there is no need to replace the application framework.
[Official quickstart](https://docs.copilotkit.ai/quickstart)

No CopilotKit package, hosted runtime or new model route was installed. The
verified dashboard and its shipped integration examples remain the product
demonstrated in the presentation and video.
