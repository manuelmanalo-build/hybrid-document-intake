# ADR 0002: LangGraph Orchestration Runtime

- Status: Accepted
- Date: 2026-07-14

## Context

The process combines deterministic ordering, a bounded model/tool loop, durable recovery, conditional
routing, and human pauses. It must not run inside each web process, and model decisions must not
control arbitrary graph nodes.

## Decision

Use a LangGraph `StateGraph` deterministic parent with a compiled agent subgraph. Parent edges and
closed conditional routes encode authoritative process order. The subgraph may choose only typed
registered tools; a policy node controls dispatch and limits.

Run graphs asynchronously in a separate orchestrator service. FastAPI transactionally writes
graph-run and resume requests. The orchestrator leases them, invokes the graph outside the lease
transaction, and records visible status/error milestones. Production compilation uses
`AsyncPostgresSaver` and a persisted unique `thread_id` per case.

Use dynamic `interrupt()` for application-owned human review and resume the same thread with a
validated `Command(resume=...)`. Treat every node as replayable: make side effects idempotent,
separate external effects behind outboxes, and keep pre-interrupt operations safe to repeat.

## Consequences

The graph is inspectable Python and supports durable human-in-the-loop execution without granting
the model process authority. We must version graph/state schemas, keep compatible factories for
in-flight runs, implement request leases, and test crash/replay behavior. Checkpoints are operational
state, not the authoritative audit log.

## Alternatives rejected

- In-process FastAPI execution: request lifetime and deployment restart are unsafe ownership models.
- Model-selected node names: allows authority escalation and invalid transitions.
- A fully agentic outer loop: weakens deterministic validation and approval boundaries.
