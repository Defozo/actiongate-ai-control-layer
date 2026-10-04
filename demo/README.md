# Controlled local demonstration

The demo uses synthetic suppliers and tenants. The gateway creates grants, the
resource service records idempotent receipts, and both local model workers run
without Internet access. The `agent-probe` Compose profile verifies the network
boundary from the same restricted network available to untrusted agent code.

The local UI is served at `http://127.0.0.1:8080`. Its demo identities are generated
from the owner's psst secrets; no author credentials are included in this project.
