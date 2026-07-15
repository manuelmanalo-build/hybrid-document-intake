# Project Constitution

## Purpose and precedence

This repository demonstrates enterprise document intake with a deterministic LangGraph parent
graph and a bounded LangChain agent subgraph. The deterministic application boundary is always
authoritative. The model proposes tool calls and a recommendation; it never approves, rejects, or
performs an external side effect.

These rules apply to every directory. More-local `AGENTS.md` files may add constraints but may not
weaken this constitution. Before changing code, read this file, all accepted ADRs in `docs/adr/`,
and the relevant design document. Preserve useful work and keep each increment buildable and
testable.

## Required architecture

- Angular owns browser intake and review experiences. It uses standalone components, signals,
  strict TypeScript, and strict template checks.
- FastAPI owns HTTP APIs, authentication, authorization, tenant enforcement, intake, review
  commands, graph-run requests, audit queries, dashboard APIs, and integration administration.
- PostgreSQL owns authoritative business records, append-only audit events, graph-run and resume
  requests, LangGraph checkpoints, review tasks, and transactional integration outboxes.
- A separate orchestrator process leases run/resume requests and invokes the compiled graph. A web
  request must never execute or resume the graph inside its database transaction.
- The production graph is compiled with `AsyncPostgresSaver`. `InMemorySaver` is allowed only in
  narrow, process-local unit tests.
- Streamlit uses FastAPI APIs and has no direct application-table write path.
- Integration workers deliver approved outbox events; graph replay never sends webhooks directly.

The parent graph sequence is:

`START -> initialize_case -> document_resolution_agent -> authoritative_schema_validation ->
authoritative_business_validation -> calculate_decision_confidence -> create_review_task ->
human_review_interrupt -> apply_review_response -> revalidate_reviewed_data ->
review_decision_gateway`.

The gateway loops `REQUEST_CORRECTION` to `human_review_interrupt`, routes `REJECT` through
`finalize_rejection`, and routes `APPROVE` through `create_webhook_outbox`. Every document reaches
human review in the first slice.

## Authority boundary

The Document Intake Resolution Agent is advisory. It may select only these registered typed tools:

- `inspect_document`
- `classify_document`
- `extract_invoice_from_text`
- `extract_invoice_from_visual_content`
- `lookup_supplier`
- `lookup_purchase_order`
- `validate_candidate`
- `retry_extraction`
- `submit_recommendation`

A custom policy guard must validate the requested tool name, Pydantic arguments, tenant/document
scope, caller capability, prerequisite state, counters, timeout, and idempotency key before every
execution. A model-generated function or node name is data, never executable authority. Tools
return typed, bounded results and write application audit records outside checkpoint state.

The agent may not approve or reject authoritatively, modify workflow status, bypass validation,
create or deliver integration events, execute arbitrary SQL, call arbitrary URLs, access secrets,
modify prompts/rules/schemas, delete or overwrite originals, make unrestricted filesystem calls,
invoke administrative APIs, or complete human review.

Never store private chain-of-thought. Persist only requested tool, minimized validated arguments,
concise result, timing, provider/model and available usage metadata, structured reason codes,
concise rationale, and the validated final recommendation.

## Agent limits and failure behavior

Defaults are maximum 10 tool calls, 3 extraction attempts, 2 supplier lookups, 2 purchase-order
lookups, one tool at a time, and configured model/tool/total timeouts. Enforce limits in application
state and the policy guard, not only in prompts. Provider outage, malformed output, timeout, policy
rejection, or exhausted limits produces a structured `AGENT_INCOMPLETE` result and continues to
human review.

The recommendation contract allows only `HUMAN_REVIEW`, `REQUEST_INFORMATION`, and
`REJECT_UNSUPPORTED`. `AUTO_APPROVE` is forbidden.

## Data and state rules

- Every business table and query carries `tenant_id`; use composite tenant-aware foreign keys and
  tenant predicates.
- Each case has unique `graph_run_id` and `thread_id`, plus graph version, tenant, document,
  correlation, timestamps, status, and safe last-error code.
- Graph state contains identifiers, counters, bounded evidence, status summaries, reason codes,
  and record identifiers. Binaries, secrets, provider payloads, unrestricted paths, hook URLs, and
  large extraction payloads do not belong in state or checkpoints.
- Detailed extraction, validation, confidence, review, audit, and delivery data belongs in
  application tables. Checkpoints and traces are not the authoritative business audit log.
- AP invoice money uses `Decimal`, never binary floating point.
- Originals are immutable. Reads are bounded and storage access occurs through a port using opaque,
  non-guessable object keys.
- Side effects are transactional or idempotent. Nodes may replay after failure or interrupt.

## Validation and human authority

`validate_candidate` calls the same deterministic rule service used later by the parent graph, but
its result is advisory. The parent independently runs schema and business validation after the
agent and again after corrections. Implement BR-001 through BR-012 from
`docs/first-slice-scope.md` with versioned rule results.

The application calculates the versioned decision-confidence score. Provider confidence is an
input only. Keep classification, completeness, evidence coverage, supplier match, purchase-order
match, rule risk, and overall decision confidence separate.

Review tasks are application-owned. FastAPI enforces roles, claim/unclaim rules, corrections, and
decisions transactionally, then creates a graph-resume request. The orchestrator leases that record
and resumes the same `thread_id` with validated `Command(resume=...)` data. Code before
`interrupt()` must be replay-safe and the interrupt payload must be sanitized and serializable.

Approval creates the approved business change, approval audit event, and Zapier outbox event in one
database transaction. A separate worker delivers it with a ledger, exponential backoff plus jitter,
bounded attempts, and dead-letter visibility.

## Security and audit

Roles are `SUBMITTER`, `REVIEWER`, `APPROVER`, `OPERATIONS_ADMIN`, and `AUDITOR`. Enforce them in
FastAPI/domain services, not only in Angular or Streamlit. Browsers never receive model, database,
Zapier, or LangSmith credentials; permanent public file URLs; or unrestricted local paths.

Append audit events for all events listed in `docs/first-slice-scope.md`. Include event, tenant,
document, graph/agent identifiers when applicable, actor, correlation, timestamp, safe before/after
values, and sanitized metadata. Never put document content, evidence beyond its approved bound,
credentials, tokens, URLs containing secrets, or private reasoning in audit data.

## Prohibited technology and behavior

Do not add Camunda, BPMN execution engines, connectors, its Python SDK, Zeebe, engine-owned user
tasks, process-variable contracts, related images, or related environment variables. Do not add
hardcoded secrets, floating dependency ranges, `latest` images, TODO-only behavior, placeholder
methods, broad exception swallowing, unjustified skipped tests, unbounded reads, mutable-original
operations, direct browser-to-graph or browser-to-Zapier access, direct Streamlit database writes,
live-model requirements in ordinary tests, arbitrary status changes, unrestricted tools, large
checkpoint payloads, or non-idempotent external effects.

## Change protocol

For every prompt:

1. Inspect the repository and current worktree before editing.
2. Read this constitution and all accepted ADRs; update an ADR when an architectural choice changes.
3. Implement only the requested increment and add proportionate unit/integration tests.
4. Pin dependencies and images exactly; update lock data in the same change.
5. Run formatting, linting, strict type checking, tests, builds, migration checks, and Compose
   validation that the environment supports. Correct failures attributable to the change.
6. Update the checklist and relevant docs.
7. Report files changed, decisions, commands, exact results, limitations, and manual demo steps.

The ordinary test suite uses a deterministic stub model and no live model, Zapier, or other
external service. Provider contract tests are explicit opt-in tests.
