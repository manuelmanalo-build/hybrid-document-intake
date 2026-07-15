# ADR 0003: PostgreSQL Checkpointing

- Status: Accepted
- Date: 2026-07-14

## Context

Production runs must survive orchestrator restarts and wait indefinitely for review. Process-local
memory cannot provide this durability. The application already requires PostgreSQL for authoritative
tenant data and request/outbox coordination.

## Decision

Use the pinned `langgraph-checkpoint-postgres` package and `AsyncPostgresSaver` in production. Use a
unique persisted `thread_id` as the checkpointer cursor for every document case. Initialize and
migrate checkpoint tables explicitly during deployment. Prefer separate application/checkpoint
schemas and least-privilege roles even when they share a PostgreSQL cluster.

Graph checkpoints contain only the bounded state defined in `docs/langgraph-state-model.md`.
Application tables remain authoritative for documents, extraction, validation, confidence, review,
audit, and delivery. Encryption, backup, retention, and restore procedures cover both schemas.
`InMemorySaver` is allowed only in narrow unit tests that do not claim restart durability.

## Consequences

Recovery and interrupts survive process loss, and local deployment remains compact. PostgreSQL load,
checkpoint retention, migrations, and connection pools need operational monitoring. Restoring only
one schema can create inconsistency, so recovery runbooks must coordinate both while continuing to
treat application records as business authority.

## Alternatives rejected

- In-memory production checkpoints: lose runs on restart and cannot support durable review.
- A custom checkpoint implementation: duplicates a maintained integration and raises correctness
  risk.
- Storing full business payloads only in checkpoints: weak query, audit, retention, and authority
  boundaries.
