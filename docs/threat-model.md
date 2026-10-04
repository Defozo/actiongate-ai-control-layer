# Threat model and operational limits

Team: DEFOZO SOFTWARE HOUSE. Author: Michał Kiełtyka.

The attacker may control prompts, retrieved supplier text, a model's output,
tool descriptions, tool results and an agent's code. They may replay requests,
change arguments, quote false administrator instructions, encode sensitive data,
attempt cross-tenant access and spend resources concurrently. The reference
deployment trusts its host, gateway, publisher, connector identities, model
supervisors and PostgreSQL administrators.

| Threat | Boundary and evidence |
| --- | --- |
| Impersonation and tenant substitution | Signed issuer/audience/expiry checks, durable principal scope, unknown-field rejection and tenant-scoped lookups |
| Prompt injection and goal drift | Dedicated local classifier with a trusted purpose and effect; independent ACL, recipient and resource checks |
| Data laundering through paraphrase or memory | Monotonic root labels and provenance survive text transformations, memory and delegation |
| Incorrect semantic benign verdict | Forced-benign contract cases must still block unauthorized resources and recipients |
| Approval substitution and replay | Hash-bound approval, current-generation recheck, one-use execution grant and sink receipt |
| Runaway loops and concurrent spending | Shared transactionally reserved accounts, token/step/depth/deadline bounds and confirmed process stop |
| Unsafe imports and model supply chain | Format, digest, loader and metadata admission; inert fixtures never launch a vulnerable loader |
| Feed tampering and rollback | Ed25519 verification, monotonic revisions, expiry, parser bounds and a limited data-only rule grammar |
| Unchecked output streaming | Full bounded buffering and output inspection before any content release |
| Browser and export injection | Text rendering, CSP, origin checks, tenant/role filtering and CSV formula neutralization |
| Direct upstream bypass | Isolated reference agent network and separate connector secrets; verify from inside the agent process |
| Worker or database interruption | Durable dispatch state, retained uncertain reservations, fencing and protected recovery metadata |

Local demo identity selection is intentionally powerful. It is available on the
loopback installation for jury role tests and is not enterprise authentication.
A public deployment requires a production issuer, TLS, restricted origins and a
new boundary review. The shared HS256 demo issuer does not implement an external
OIDC/JWKS identity provider.

Protection applies to controlled channels and known data provenance. It does not
prove that an answer is true, that every secret can be recognized by DLP, that a
container survives every kernel exploit, or that an administrator cannot modify
storage. The audit chain detects inconsistency against a trusted checkpoint; it
is not immutable against an administrator who controls all copies.

The semantic corpus is small and synthetic. English and Polish results must be
reported separately. Model failure, malformed output and incomplete scanning
produce unknown and a closed required path. Recall and false-positive targets are
not promises about unseen attacks. A detector change informed by holdout errors
requires a newly authored independent holdout; the old cases become regressions.

There is no distributed ACID transaction between PostgreSQL and an arbitrary
external API. The demo sink supports idempotency and receipt lookup. An uncertain
mutation on a connector without a comparable contract must not be retried blindly.
The cloud cost boundary depends on the pinned provider model and verified pricing
contract. Charges from use of the same provider key outside ActionGate are outside
the local ledger. Local slot-seconds are occupancy measurements, not GPU-seconds
or a provider invoice.

Current evidence, execution mode, missing checks and unresolved acceptance failures
belong in the dated reports. Configuration and unit tests alone do not establish
clean installation, runtime isolation, semantic quality or public reachability.
