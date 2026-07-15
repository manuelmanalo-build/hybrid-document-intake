# ADR 0006: Camunda Removal

- Status: Accepted
- Date: 2026-07-14
- Supersedes: the prior ADR set dated 2026-07-14

## Context

The initial scaffold used Camunda as a deterministic process spine. The product direction now uses
LangChain and LangGraph exclusively for model/tool integration and orchestration. Keeping both
runtimes would duplicate durable state, task ownership, retry semantics, credentials, deployment,
and operational understanding.

## Decision

Remove the Camunda runtime, BPMN model, connector runtime, Elasticsearch dependency used by that
stack, official SDK dependency, process deployer, workflow-start dispatcher, worker tests, process-
variable contract, container volumes, environment variables, and runtime configuration.

Replace the concepts as follows:

| Removed concept | Replacement |
|---|---|
| BPMN deterministic process | Versioned LangGraph parent `StateGraph` |
| Agent ad-hoc subprocess | Bounded LangGraph agent subgraph and policy guard |
| Process instance/business ID | Application `graph_run_id` plus unique checkpoint `thread_id` |
| Process variables | Bounded typed graph state with application record IDs |
| Workflow-start outbox/SDK dispatcher | Transactional `graph_run_requests` leased by orchestrator |
| Engine user task | Application review task plus `interrupt()` |
| Engine resume/completion | Durable resume request plus `Command(resume=...)` |
| Connector webhook | Application transactional outbox and integration worker |
| Engine incidents/retries | Request leases, checkpoint recovery, visible status/errors, and audit |

The existing secure intake, immutable storage, tenant metadata, idempotency, RFC 9457 handling,
correlation middleware, audit foundation, Angular upload, Streamlit shell, PostgreSQL, tests, and CI
remain useful and are preserved. Because this repository is pre-release with a single baseline
migration, that migration and response contract are rewritten from `workflow_start_requests` to
`graph_run_requests`; no production data migration is implied. A released deployment would instead
require an additive migration and an explicit in-flight-case cutover plan.

The first migration increment creates the graph-run request boundary but does not pretend the
LangGraph orchestrator is already implemented. It is delivered in a subsequent checklist increment
with pinned dependencies, checkpoints, leasing, and restart tests.

## Consequences

There is one orchestration model and one human-task authority. The local stack becomes smaller, but
the application must implement request leases, graph version compatibility, checkpoint operations,
review queues, and integration delivery. Existing installations of the superseded scaffold must
recreate the pre-release local database or receive a dedicated data migration before upgrade.

## Verification

Repository verification scans executable/configuration paths for removed SDK, engine, connector,
image, and environment references. Historical discussion is allowed only in this migration ADR and
explicit prohibition rules. Build, tests, migrations, and Compose must pass without the removed
services.
