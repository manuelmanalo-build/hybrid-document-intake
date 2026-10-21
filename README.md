# Hybrid Agentic Document Intake Demo

This monorepo is governed by a deterministic LangGraph parent graph with a bounded LangChain agent
subgraph. The current executable increment implements secure PDF intake, immutable local storage,
tenant-scoped PostgreSQL metadata/audit/idempotency, a durable graph-run request, and a separately
deployed LangGraph orchestrator. The orchestrator runs the deterministic parent through a bounded
tool-selecting LangChain agent, independent validation and confidence nodes, then durably pauses at
an application-owned human-review interrupt. The tenant- and role-enforced review API claims tasks,
creates immutable reviewed versions, and transactionally queues graph resume requests. An approved
resume atomically creates an approved record and idempotent webhook outbox event. The integration
worker delivers that event with leasing, bounded retry, jitter, authentication/signing, a delivery
ledger, and dead-letter handling. Angular provides intake and review, while the API-only Streamlit
dashboard exposes tenant-scoped operations projections and authorized replay commands.

See [the implementation checklist](docs/implementation-checklist.md),
[architecture overview](docs/architecture-overview.md), and
[deterministic intake boundary](docs/deterministic-intake-boundary.md).

## Repository layout

- `apps/web` — strict Angular standalone/signal intake and human-review application.
- `apps/dashboard` — ten-page Streamlit operations dashboard using FastAPI only.
- `services/api` — FastAPI intake and application persistence/storage boundary.
- `services/orchestrator` — lease-based LangGraph run/resume dispatcher and durable graphs.
- `services/integration-worker` — leased transactional-outbox delivery to the server-side Zapier hook.
- `services/mock-zapier` — deterministic profile-scoped Catch Hook for local/test execution.
- `evaluation` — offline quality/safety metrics with optional environment-gated LangSmith export.
- `contracts` — versioned trust-boundary schemas.
- `infrastructure` — pinned container build assets.
- `test-data` — synthetic fixtures only.
- `scripts` — repeatable developer and CI entrypoints.
- `docs` — constitution detail, state/authority/tool/evaluation plans, and ADRs.

## Local prerequisites

- Python 3.12.13
- uv 0.11.18
- Node.js 24.14.0
- pnpm 11.7.0
- Docker Engine with Docker Compose v2

Copy `.env.example` to `.env` and replace placeholder credentials before starting containers. The
local Compose profile enables one-click role sessions, so UI users do not copy demo bearer tokens;
the tokens remain available for CLI examples. The default `AGENT_PROVIDER=stub` needs no live
model. Set `AGENT_MODEL` and the corresponding provider key to exercise OpenAI or Gemini. LangSmith
remains disabled and optional.

## Quality commands

```bash
uv sync --frozen
uv run ruff format --check services apps/dashboard evaluation scripts
uv run ruff check services apps/dashboard evaluation scripts
uv run pyright
uv run pytest -m "not integration"
TEST_DATABASE_URL=postgresql+psycopg://document_intake:integration-test-only@localhost:5432/document_intake_test \
  uv run pytest -m integration
pnpm install --frozen-lockfile
pnpm --dir apps/web format:check
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
docker compose --profile local --env-file .env config --quiet
docker compose --profile local --env-file .env up --build --wait
uv run python -m evaluation.run
```

`evaluation.run` scores recorded observations. To measure a real model, use the opt-in live
extraction diagnostic described in [the evaluation plan](docs/agent-evaluation-plan.md#live-extraction-diagnostic).

PostgreSQL integration tests require `TEST_DATABASE_URL` for a disposable database whose name
contains `test`; they apply and roll back Alembic migrations.

## Intake example

The API derives tenant/user authority from server configuration; clients cannot submit those IDs.

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $INTAKE_DEMO_SUBMITTER_TOKEN" \
  -H 'Idempotency-Key: demo-invoice-001' \
  -H 'X-Correlation-ID: demo-correlation-001' \
  -F 'file=@test-data/minimal.pdf;type=application/pdf' \
  http://localhost:8000/api/v1/documents
```

The response contains `graph_run_id` and `graph_run_status: REQUESTED`, but never an object key or
local path. Reusing a key with the same request returns the original response; different content
returns RFC 9457 Problem Details with HTTP 409. Upload does not invoke the graph.

## Review API

Authorized reviewer/approver identities use `GET /api/v1/review-tasks`, task detail, `claim`,
`unclaim`, and `complete` endpoints. Completion validates typed corrections, stores a new reviewed
business-data version, appends audit records, and inserts one durable `graph_resume_request` in the
same transaction. The API never resumes LangGraph directly; the orchestrator leases and resumes the
persisted thread.

Open Angular at `http://localhost:4200`, select Submitter, Reviewer, or Approver from the local demo
role control, and connect. FastAPI sets an opaque HttpOnly session cookie; Angular never receives a
configured token. The PDF is fetched as an authenticated FastAPI blob, and the browser never
receives its storage path. All first-slice cases stop for human review.

## Zapier delivery and replay

Keep `INTAKE_ZAPIER_HOOK_URL` and optional auth/signing values in the server `.env`; they are never
returned by an API. An approval transaction creates `approved_business_records`, the approval audit,
and one `outbox_events` row. `integration-worker` leases it and records every attempt without storing
the response body. An operations administrator can replay only a dead-letter event:

```bash
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $INTAKE_DEMO_OPERATIONS_ADMIN_TOKEN" \
  http://localhost:8000/api/v1/integrations/outbox/OUTBOX_EVENT_UUID/replay
```

If `INTAKE_ZAPIER_CALLBACK_TOKEN` is configured, a Zap may report a final status idempotently:

```bash
curl --fail-with-body -X POST \
  -H "X-Zapier-Callback-Token: $INTAKE_ZAPIER_CALLBACK_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"outbox_event_id":"OUTBOX_EVENT_UUID","idempotency_key":"APPROVAL_EVENT_KEY","callback_id":"ZAP_RUN_ID","status":"COMPLETED"}' \
  http://localhost:8000/api/v1/integrations/zapier/callback
```

## Current local stack

`docker compose --profile local` starts PostgreSQL, migration/bootstrap jobs, FastAPI, the LangGraph
orchestrator, the Zapier integration worker, Angular, and Streamlit. The orchestrator initializes
checkpoint schema, leases graph run/resume requests, uses the persisted `thread_id`, and treats a
durable review interrupt as the successful initial dispatch outcome. Streamlit is at
`http://localhost:8501`; select Operations Admin or Auditor in its sidebar. Its FastAPI session
cookie stays in Streamlit's server-side session. No UI connects to PostgreSQL or LangGraph.

Demo sessions expire after `INTAKE_DEMO_SESSION_TTL_SECONDS` and are cleared by an API restart.
Bearer-token authentication remains available for `curl` and automation. Set
`INTAKE_DEMO_ROLE_LOGIN_ENABLED=false` outside the loopback-only local profile; production
configuration rejects it.

The local profile also starts mock Zapier on `http://localhost:8090`. All services have active
health checks and depend on readiness or successful one-shot migrations rather than fixed sleeps.
See [demo guide](docs/demo-guide.md), [operations runbook](docs/operations-runbook.md),
[functional specification](docs/functional-spec.md), [technical specification](docs/technical-spec.md),
and the [independent release-readiness review](docs/release-readiness-review.md).
