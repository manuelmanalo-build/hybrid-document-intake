# Deterministic Intake Boundary

`POST /api/v1/documents` accepts one multipart PDF, requires `Idempotency-Key`, and accepts an
optional `X-Correlation-ID`. FastAPI validates a local-demo bearer credential with constant-time
comparison and derives tenant, submitter, and `SUBMITTER` role from trusted configuration.

The request streams in configured chunks to private staging storage. The pass enforces the byte
limit, retains a bounded header sample, and calculates SHA-256. Acceptance requires a non-empty body,
declared `application/pdf`, `%PDF-` signature, and independent `application/pdf` content detection.
Filename and extension are metadata only.

One PostgreSQL transaction writes:

- a document and immutable version/object reference;
- initial status history;
- completed idempotency response;
- tenant-scoped duplicate-digest signal;
- sanitized append-only audit events;
- a `graph_runs` row with unique `graph_run_id` and `thread_id`, configured graph version,
  tenant/document/correlation IDs, and `REQUESTED` status; and
- a `graph_run_requests` dispatch row with bounded attempts, initial due timestamp, and `PENDING`
  status.

The final local object uses a random 256-bit create-only key. Neither key nor path appears in HTTP.
If the database transaction fails, the unreferenced object is removed. The API does not invoke a
graph. The orchestrator leases the committed request and uses the run's persisted thread ID.

The response contract is replayable through the idempotency record and contains document/version,
duplicate signal, correlation, graph-run ID, and request status. The pre-release baseline migration
defines this boundary directly.

The parent graph executes the bounded resolution agent, independent validation and confidence,
creates an application-owned review task, and durably pauses at `human_review_interrupt`. A
validated resume can loop for correction, finalize rejection, or create one approval outbox event.
Both are real deterministic nodes. The orchestrator compiles the graph with `AsyncPostgresSaver`,
uses row leases with bounded retry/backoff, and checks the persisted completion checkpoint before a
retry so a crash after graph completion does not create a duplicate logical start.

Deferred intake hardening includes structural PDF limits (encryption, page and decompression policy),
malware/content safety inspection, authorized artifact download, production object storage, and
production OIDC/JWT identity. Agent/extraction, review, integration delivery, and metrics remain
later implementation increments tracked explicitly in the checklist.
