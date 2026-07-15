# Checkpoint Retention Policy

## Policy

- `REQUESTED`, `RUNNING`, and `INTERRUPTED` threads are retained until the case reaches a terminal
  state. Review interrupts have no automatic age-based deletion.
- Completed checkpoints are retained for 30 days after `completed_at` for operational replay and
  incident analysis.
- Failed checkpoints are retained for 90 days after the last failure unless an incident or legal
  hold extends retention.
- Application audit, approved records, extraction, validation, review, and delivery records follow
  their own business retention and are never deleted merely because a checkpoint expires.

## Safe deletion

The planned retention job must select only terminal graph runs older than policy, exclude legal holds, delete
all checkpoint rows for the exact application-owned `thread_id` in one controlled operation, record
a sanitized retention audit/operations event, and verify no active run/resume request references the
thread. It must not infer thread IDs from client input.

## Backup and restore

Application and checkpoint table sets are backed up from a consistent PostgreSQL recovery point. A
restore validates application run/thread mappings before workers start. Restoring only one table set
is an incident: stop orchestrators, reconcile from application authority, and never invent or coerce
checkpoint state. A production deployment should run quarterly restore tests that include an
interrupted thread and resume it after restart.

## Capacity controls

Monitor checkpoint row/byte growth, oldest interrupted case, terminal backlog awaiting purge, and
serialization errors. Graph state remains bounded per `docs/langgraph-state-model.md`. Automated
serialized checkpoint size/secret scanning and the retention job are not implemented in the local
first slice and remain production hardening items.
