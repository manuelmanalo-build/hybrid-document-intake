# Threat Model

## Assets and trust boundaries

Assets are original PDFs, tenant business data, review decisions, approvals, audit history,
checkpoints, provider credentials, Zapier credentials, and access tokens. Trust boundaries exist at
the browser/API, API/storage, API/database, orchestrator/model, tool/domain-service, dashboard/API,
and integration-worker/Zapier interfaces.

## Threats and controls

| Threat | Principal controls |
|---|---|
| Cross-tenant disclosure or mutation | Server-derived identity, tenant predicate on every query, composite tenant foreign keys, denial tests |
| Malicious/non-PDF or oversized upload | Streaming size bound, declared/detected MIME, PDF magic, empty rejection, no extension trust |
| Original overwrite/path traversal | Opaque object key, resolved-root enforcement, create-only commit, no browser path response |
| Prompt/tool authority escalation | Nine-tool allowlist, Pydantic inputs, policy guard, identity matching, counters, no arbitrary node/function dispatch |
| Model data or reasoning leakage | Bounded context/evidence, no chain-of-thought persistence, no secrets or binaries in checkpoints/audit |
| Approval forgery | Application reviewer/approver roles, claim ownership, row locking/uniqueness, authoritative decision lookup in outbox node |
| Duplicate graph/external effects | Run/resume leases, stable operation keys, unique records, transactional outbox, delivery idempotency header |
| SSRF or secret exfiltration | No arbitrary URL tool; Zapier URL exists only in integration-worker configuration |
| Webhook spoof/replay | Optional bearer/HMAC delivery auth, authenticated idempotent callback, bounded response metadata |
| Dependency/secret compromise | Frozen locks, pinned images, GitHub dependency audit and Gitleaks jobs, non-root custom Python containers |
| Denial of service | Upload/context/evidence bounds, model/tool/agent timeouts, bounded attempts, database leases |
| Demo credential exposure | Configured tokens remain server-side; opaque HttpOnly role sessions; production rejection; loopback-only local ports |

## Residual risks

Local demo bearer tokens and role sessions are not production identity and must be replaced with
OIDC/JWT. Anyone with access to the loopback demo UI can select a demo role; sessions are
process-local and cleared on restart. Local storage is not encrypted by this repository. PostgreSQL
row-level security and distinct database roles are recommended deployment hardening. Zapier remains
an at-least-once external system and must honor idempotency before downstream side effects. PDF
malware scanning and production object storage are outside the first slice. The web/database images
retain their pinned upstream runtime user behavior and should be constrained by the production
container platform.

## Security verification

CI rejects secret patterns and vulnerable dependencies, checks forbidden runtime references, and
runs tenant/role, policy, malformed input, replay, timeout, and failure tests. Code contracts and
interrupt-size tests exclude credentials, hook URLs, binaries, and private reasoning; automated
inspection of serialized PostgreSQL checkpoint bytes remains a documented hardening item.
