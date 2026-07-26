# Functional Specification

## First-slice outcome

An authenticated submitter uploads a bounded PDF. The system preserves the original immutably,
starts one durable case asynchronously, produces a bounded advisory AP-invoice extraction, runs
authoritative deterministic rules and confidence, pauses for application-owned human review, and
delivers an approved record through a transactional Zapier outbox. Every successful case requires
human review; agent output can never approve or reject authoritatively.

## Personas and capabilities

| Role | Capabilities |
|---|---|
| `SUBMITTER` | Upload and view own tenant documents |
| `REVIEWER` | View tenant queue, claim/unclaim, correct fields, request correction |
| `APPROVER` | Reviewer capabilities plus approve or reject |
| `OPERATIONS_ADMIN` | Tenant operations views and dead-letter replay |
| `AUDITOR` | Read-only tenant operations and audit views |

Server-side authorization is definitive. UI visibility is convenience only.

The local profile provides development-only one-click role sessions. FastAPI remains the identity
authority, configured bearer tokens are never returned to a UI, and production rejects this mode.

## Primary journey

1. Validate authentication, idempotency key, correlation ID, PDF type/magic, and size.
2. Stream hash and store the original under a non-guessable create-only key.
3. Commit document metadata, audit, graph run, and run request atomically.
4. The orchestrator leases the request and starts the persisted `thread_id`.
5. The model selects approved typed tools through the policy guard and submits an advisory result.
6. The parent independently validates, calculates confidence, creates review, and interrupts.
7. A human claims, inspects evidence, corrects fields, and records a decision.
8. FastAPI commits review state plus a resume request. The orchestrator resumes the same thread.
9. Approval atomically creates the approved record, audit, and one outbox event.
10. The integration worker delivers with an idempotency key and records every attempt.

## Acceptance boundaries

- Duplicate upload keys replay the original response; conflicting content returns RFC 9457 HTTP 409.
- Duplicate content is a tenant-scoped signal, not a global information leak.
- Provider, policy, schema, timeout, or limit failure becomes `AGENT_INCOMPLETE` and still creates review.
- Corrections create a new reviewed business-data version and preserve extraction history.
- Rejection creates no outbox event. Graph replay cannot duplicate an approval event.
- External delivery failure never rolls back approval and becomes retry/dead-letter visible.
- Angular and Streamlit communicate only with FastAPI.
