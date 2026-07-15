# ADR 0004: Application-Owned Human Review

- Status: Accepted
- Date: 2026-07-14

## Context

All first-slice documents need review. Claiming, corrections, decisions, authorization, tenant
isolation, audit, and API/UI behavior are business concerns, while the durable graph must pause and
resume around them.

## Decision

Store review tasks, assignee/claim state, field corrections, decisions, versions, and audit in
application tables. FastAPI/domain services enforce tenant, role, task ownership, allowed transition,
payload validation, row locking, and uniqueness constraints. A review node creates one task
idempotently and then calls `interrupt()` with a sanitized payload.

A review command commits the task change and a unique graph-resume request in the same transaction.
FastAPI does not continue the graph. The orchestrator leases the committed request, validates the
current task/run version, and resumes the same `thread_id` with `Command(resume=...)`.
`REQUEST_CORRECTION` loops to review; `REJECT` and `APPROVE` are authoritative only after revalidation.

## Consequences

Review remains accessible through stable APIs and UIs independent of orchestrator uptime, and audit
is authoritative. We must build lease/retry handling, stale-decision protection, task queues, and
idempotent interrupt replay. Interrupt payloads are projections, not mutable task records.

## Alternatives rejected

- Treating an interrupt/checkpoint as the review database: insufficient authorization, querying,
  reporting, and audit ownership.
- Direct API graph resume: couples database transactions to runtime availability and risks lost or
  duplicate continuation.
- Model approval: violates the human authority boundary.
