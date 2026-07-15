# LangGraph State Model

## Design goals

State must be sufficient to route and recover a case while remaining small, serializable,
tenant-scoped, and safe to retain. Application tables contain detailed and authoritative data;
checkpoints contain working references and summaries.

## Case identity and run record

Every case receives immutable `graph_run_id`, `graph_version`, unique `thread_id`, `tenant_id`,
`document_id`, `correlation_id`, and `created_at`. The application run record also carries
`started_at`, `completed_at`, `status`, and `last_error_code`. `thread_id` is the checkpointer
cursor and is never accepted from an untrusted client. Resume always resolves it from a tenant-
scoped run/task record.

Recommended statuses are `REQUESTED`, `RUNNING`, `INTERRUPTED`, `COMPLETED`, and `FAILED`.
Transitions occur through tenant-scoped domain commands with row locks, uniqueness constraints, and audit; graph state
cannot directly change the authoritative status.

## Parent state schema

| Field | Type/bound | Purpose |
|---|---|---|
| `graph_run_id`, `graph_version`, `thread_id` | UUID/short strings | Durable run identity |
| `tenant_id`, `document_id`, `correlation_id` | UUID/short strings | Scoped case identity |
| `current_stage` | short code-controlled string | Bounded progress summary |
| `agent_run_id` | UUID or null | Detailed agent record reference |
| `recommendation_id` | UUID or null | Validated recommendation reference |
| `extraction_candidate_id` | UUID or null | Detailed extraction reference |
| `authoritative_validation_run_id` | UUID or null | Latest independent validation reference |
| `confidence_run_id` | UUID or null | Versioned score reference |
| `review_task_id` | UUID or null | Application-owned task reference |
| `review_response`, `final_decision` | bounded validated values or null | Resume and gateway data |
| `reason_codes` | unique bounded list | Routing/operations summary |
| `recoverable_errors`, `terminal_error` | bounded codes/value | Controlled and terminal errors |

Nodes return partial state updates. Reducers are defined only where accumulation is intentional;
bounded lists deduplicate and reject excess entries. Node functions do not mutate received state in
place.

## Agent subgraph state

The subgraph receives immutable identity plus record IDs, not a binary. Its working state contains
the agent-run ID, persisted start timestamp, call/lookup/extraction counters, last validated tool
request/result summary, candidate extraction ID, reason codes, and final recommendation ID. It
rebuilds a bounded model message from counters and concise tool results for each decision; private
reasoning and provider response bodies are never persisted.

Counter updates are deterministic and occur before dispatch. The policy guard is the only route from
a requested call to an adapter. Tools persist detailed results first, then return record IDs and
concise summaries.

## Persistence

Production uses `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` and asynchronous graph
execution. Checkpoint migrations/setup run as an explicit deployment step under a schema-owning
role; runtime credentials have only required DML. The graph is compiled once per orchestrator
process with the saver and invoked with:

```python
{"configurable": {"thread_id": persisted_thread_id}}
```

The local first-slice Compose deployment uses one database namespace and credential for application
and checkpoint tables. Separate schemas and least-privilege runtime/setup roles remain the
production deployment target. Backup, retention, encryption, and restore cover both table sets.
`InMemorySaver` is restricted to unit tests that expressly do not test restart durability.

Only the parent graph is compiled with `AsyncPostgresSaver`. Agent tool effects, counters, usage,
recommendations, and the persisted agent start time are durable application records, but transient
subgraph routing state is not checkpointed mid-agent. A process failure can replay model decisions
and idempotent tool operations until the parent node completes; it cannot reset the total agent
deadline or duplicate a committed tool effect.

## Interrupt and resume protocol

`create_review_task` idempotently writes the task and audit event before the interrupt node.
`human_review_interrupt` loads a sanitized snapshot, calls `interrupt()` once in a stable order, and
contains no non-idempotent pre-interrupt work. Its payload includes only run/task/document IDs,
record version, bounded field summaries, validation reason codes, and allowed decisions.

FastAPI validates and commits the human response plus a unique resume request. The orchestrator
leases it, loads the same thread, validates task/run versions again, and invokes
`Command(resume=validated_payload)`. On resume the node restarts from its beginning, so every read
and write before `interrupt()` must be deterministic or idempotent. `REQUEST_CORRECTION` increments
the bounded review round and returns to the interrupt with a refreshed task snapshot.

## Excluded state

Never checkpoint document binaries, full OCR/visual page content, unrestricted paths, credentials,
connection strings, webhook URLs, raw provider requests/responses, full supplier/PO records, private
reasoning, or complete extraction/audit payloads. Use opaque record IDs and bounded evidence.

## Evolution and recovery

Graph and state schema versions are immutable for an in-flight run. The current executor accepts
the single supported graph version and fails unknown versions visibly; it does not coerce them to
the newest graph. Multi-version graph factories and audited in-flight migration are future
compatibility work. Recovery tests restart the orchestrator around persisted checkpoints and prove
one authoritative effect per idempotency key.

Retention and coordinated application/checkpoint backup rules are defined in
`docs/checkpoint-retention-policy.md`. Active and interrupted threads are never age-purged; terminal
threads are eligible only after the configured retention window and reference checks.
