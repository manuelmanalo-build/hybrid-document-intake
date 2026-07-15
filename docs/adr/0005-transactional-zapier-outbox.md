# ADR 0005: Transactional Zapier Outbox

- Status: Accepted
- Date: 2026-07-14

## Context

Zapier is an external, fallible HTTP boundary. Approval must not be lost when delivery fails, and
graph retries/checkpoint replay must not send duplicate requests.

## Decision

The authoritative approval transaction creates a unique outbox event and approval audit event with
the approved business state. A separate integration worker leases committed events, builds a
versioned allow-listed payload, and sends the configured Catch Hook using server-side credentials.

Record each attempt in a delivery ledger. Use stable idempotency/correlation headers, per-request
timeout, bounded exponential backoff with jitter, lease expiry recovery, and terminal dead-letter
status. Store bounded response metadata, never secret hook URLs or unbounded bodies. Optional
callbacks require authentication, event/idempotency-key correlation, and idempotent application.
Timestamp-based freshness validation is not implemented in the optional first-slice callback.

Graph nodes, FastAPI review transactions, Angular, and Streamlit never call Zapier directly.

## Consequences

Approval commits independently of Zapier availability, retries are observable, and graph replay
cannot directly duplicate calls. End-to-end delivery is at least once, so the receiving Zap must use
the supplied idempotency key when downstream effects require de-duplication.

## Alternatives rejected

- Synchronous webhook in approval: couples authority to network availability and creates ambiguous
  commits.
- Sending from graph nodes: checkpoint replay can duplicate external effects.
- Browser delivery: exposes hook URLs and bypasses server authorization/audit.
