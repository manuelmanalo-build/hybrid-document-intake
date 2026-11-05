# Implementation Checklist

Checked items have repository evidence; unchecked items are future increments, not placeholder
implementations.

## Constitution and migration

- [x] Establish governing LangChain/LangGraph constitution, scope, authority, state, tool, evaluation,
  and ADR documents.
- [x] Remove obsolete engine runtime, connector, SDK, process model, deployer/dispatcher, container,
  environment, contract, and test artifacts.
- [x] Replace the intake transaction's workflow-start record with tenant-scoped `graph_run_requests`
  identity/status and `GRAPH_RUN_REQUESTED` audit.
- [x] Preserve the secure deterministic upload, immutable storage, API, Angular, dashboard shell,
  PostgreSQL, CI, and integration-test foundation.
- [x] Add pinned LangGraph and PostgreSQL-checkpointer packages and regenerate lock data.
- [x] Add pinned LangChain provider integrations with the agent implementation.

## Deterministic intake boundary

- [x] Required idempotency key and optional/generated validated correlation ID.
- [x] Authenticated tenant-scoped submitter identity.
- [x] Streamed bounded upload, empty rejection, declared/detected MIME and PDF magic validation, and
  streamed SHA-256.
- [x] Non-guessable immutable local object key behind a storage port with compensation.
- [x] PostgreSQL document/version/status/storage/idempotency/audit records and duplicate-hash signal.
- [x] Graph-run request written in the same transaction; no graph invocation in upload.
- [x] RFC 9457 errors, structured logging, unit tests, and optional PostgreSQL integration tests.
- [ ] Add production object storage adapter and malware/content safety inspection.
- [ ] Replace local demo bearer identity with production OIDC/JWT validation.
- [x] Add loopback-only, production-rejected local role sessions so Angular and Streamlit do not
  require bearer-token copy/paste.

## Durable orchestration

- [x] Add separate async orchestrator package/container; a distinct least-privilege database role
  remains deployment hardening.
- [x] Implement graph-run request leasing, expiry recovery, bounded retry/backoff/jitter, idempotent
  initial invocation, and terminal visibility. Resume dispatch follows with human review.
- [x] Initialize `AsyncPostgresSaver`, compile the versioned initial parent graph, and persist unique
  thread configuration.
- [ ] Implement graph/state version registry and in-flight compatibility policy.
- [x] Replace the placeholder with the complete parent nodes, correction gateway, terminal routes,
  and mandatory review interrupt.
- [x] Add checkpoint restart/replay and concurrent-leasing PostgreSQL integration coverage.

## Agent subgraph

- [x] Implement provider-neutral model port, OpenAI, Gemini, and Anthropic adapters, and deterministic stub.
- [x] Implement recommendation/AP extraction Pydantic contracts using `Decimal`.
- [x] Implement immutable nine-tool registry and policy guard.
- [x] Enforce 10 total, 3 extraction, 2 supplier, 2 PO, serial execution, and configured timeouts.
- [x] Store agent runs/tool calls/extractions with bounded audit summaries and no private reasoning.
- [x] Implement controlled malformed/provider/timeout/limit outcomes as `AGENT_INCOMPLETE`.
- [ ] Add per-tool unit and PostgreSQL integration tests from the catalog.

## Deterministic validation and confidence

- [x] Implement versioned BR-001 through BR-012 and use the same service for advisory feedback and
  independent parent validation.
- [x] Implement independent schema/business validation stages and revalidation after corrections.
- [x] Implement `decision-confidence-v2` with exact Decimal test vectors and separate component
  persistence.
- [x] Ensure every initial outcome, including `AGENT_INCOMPLETE`, routes to human review.

## Human review

- [x] Add application review-task, correction, decision, and graph-resume-request tables/migrations.
- [x] Implement tenant/role-aware list, claim, unclaim, correct, request-correction, reject, and
  approve APIs with row locking and uniqueness-based concurrency control.
- [x] Implement idempotent task creation, sanitized `interrupt()`, and orchestrator-only
  `Command(resume=...)`.
- [x] Build Angular review queues/forms and Streamlit API-only operations pages.
- [x] Complete PostgreSQL-backed race, tenant-isolation, restart, and full audit-history tests.

## Zapier boundary

- [x] Add approved-record, outbox, delivery-ledger, and dead-letter schema.
- [x] Create approval review state, audit, and unique outbox event atomically.
- [x] Implement separate integration worker, bounded payload, authentication, timeouts,
  exponential retry with jitter, lease expiry, and dead-letter status.
- [x] Implement optional authenticated/idempotent callback.
- [x] Prove graph replay/repeated approval cannot duplicate the outbox business effect.

## Operations and quality

- [x] Expand dashboard through FastAPI with run/review/agent/rule/confidence/outbox operations views.
- [x] Add local/test mock Zapier, deterministic readable evaluation PDFs with fixture-content
  regression coverage, the offline harness, and optional LangSmith export.
- [x] Add split GitHub Actions for Python, graph/checkpoint/E2E, Angular, containers, secrets, and dependencies.
- [ ] Add safe health/readiness, lease lag, failure, checkpoint, model usage, and delivery metrics.
- [x] Add CI static forbidden-reference checks for legacy runtime references, direct Streamlit
  persistence, direct graph webhook calls, unfinished markers, and unbounded production reads.
- [ ] Add serialized checkpoint-size and secret-bearing-state tests.
- [ ] Run Ruff, strict Pyright, Pytest unit/PostgreSQL integration, Angular format/lint/test/build,
  migrations, Docker builds, and Compose validation in CI.
- [x] Document local end-to-end demo, backup/restore, dead-letter replay, audit, threat, and retention runbooks.

## Claims slice (ADR 0008)

- [x] Record the claim-packet decision, claims scope, CR-001 through CR-014, and
  `claim-routing-v1` queue policy; amend the constitution and first-slice scope.
- [x] Add a live, opt-in extraction evaluation runner that measures real provider output against
  fixtures (diagnostic only; never required by ordinary CI).
- [x] Add the synthetic claims evaluation set with policy, vehicle, and ground-truth files and a
  fixture-integrity test.
- [x] Add policy, policy-vehicle, page-segment, duplicate-key, and routing-decision tables
  (migration 0005), an audited idempotent reference-data loader, and a local Compose load job.
- [x] Add segment, claim-candidate, and claim tool contracts (`claim-packet-v1`), separately
  versioned claim prompts, stub and LangChain claim extractors/segmenters, and an opt-in live
  claims diagnostic scored against ground truth.
- [x] Add `CLAIM_PACKET` to the classification set together with the claim tool registry, and replace
  the placeholder classification/inspection with a keyword classifier (stub) and a model classifier
  plus real inspection (live providers).
- [x] Register and guard the five claim tools with document-type scoping and limits.
- [x] Implement versioned CR-001 through CR-014 and claim decision confidence.
- [x] Implement `claim-routing-v1` queue decisions and duplicate keys in authoritative validation;
  a rejection releases the claim's duplicate key.
- [x] Expose claim queues, the workbench read model, service levels, typed claim corrections, and
  audited reviewer re-routes (migration 0006) through `/api/v1/claims`.
- [x] Add the Angular claims screens: batch intake with per-file pipeline progress, work queues
  with service levels and queue filters, and the claim workbench (packet viewer, rule
  explanations, typed field corrections, decisions, re-route).
- [ ] Score claims against ground truth with the zero-false-fast-track gate in CI.
- [ ] Port the claims fixture generator into `scripts/` under Ruff rules.

## Campaign brief slice (ADR 0009, agency)

- [x] Record the brief decision, domain packs, brief scope, CB-001 through CB-014, and
  `brief-routing-v1`.
- [x] Add the 20-packet synthetic brief evaluation set, its in-repo generator, and integrity tests.
- [x] Refactor claims into the first domain pack with no behavior change (agent spec, tool effects,
  authoritative validation, confidence, rejection hook, API correction registry), with architecture
  tests that keep pack-specific branches out of shared modules.
- [x] Add the brief pack: contracts with a deliverables list, extraction, SOW and lead-time reference
  tables (migration 0007, audited loader, Compose job), `lookup_client_sow`, rules, routing, and
  confidence; keyword classification of briefs in stub mode.
- [x] Generalize the queue and workbench API across packs (`/api/v1/review-queues`, with
  `/api/v1/claims` kept as claims aliases), with per-type summaries, queues, service levels, typed
  brief corrections, and per-item deliverable evidence.
- [x] Make the Angular intake, queue, and workbench screens pack-driven (claims and briefs).
- [ ] Emit a work-request outbox payload with one task per deliverable.
- [ ] Draft clarifying questions for `NEEDS_CLIENT_INFO` briefs for a reviewer to edit and send.

