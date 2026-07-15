# Audit Model

## Authority and shape

`audit_events` is the append-only business audit log. LangGraph checkpoints, logs, LangSmith traces,
and webhook delivery responses are not substitutes. Each event carries an immutable ID, tenant,
optional document/graph/agent references, actor type and ID, correlation ID, UTC timestamp, and
sanitized metadata. Stable IDs make replayed milestones idempotent.

## Material event families

- Intake: upload received, validation passed/failed, document stored, graph requested.
- Graph/agent: run started/completed/failed; material node outcomes through the domain-specific
  events below; agent started; tool requested/rejected/completed/failed; extraction completed;
  recommendation submitted. Generic per-node start/complete rows are not emitted in this slice.
- Authority: independent validation, confidence, review created/claimed/unclaimed/completed, field
  correction, graph resumed, approved/rejected.
- Integration: outbox created, attempt, delivered, retry scheduled, exhausted, replay requested,
  callback received.

Before/after data is included only for bounded corrected fields or statuses where needed. Document
contents, full extraction payloads, credentials, private reasoning, hook URLs, unrestricted paths,
and unbounded provider or HTTP bodies are forbidden.

## Reconstruction and retention

Order tenant/document events by timestamp plus event ID. Correlation ID reconstructs an HTTP/case
flow; stable graph and agent IDs join detailed authoritative records. Tenant scope is mandatory for
every audit query. Enterprise deployments should use write-restricted roles, database backup/WAL,
and optional hash anchoring. The demo recommends seven-year audit retention, configurable by the
deploying organization and legal policy.
