# Release Readiness Review

- Review date: 2026-07-15
- Reviewer stance: independent staff-level release audit
- Scope: first-slice implementation only
- Recommendation: **NO-GO until the PostgreSQL/checkpoint/E2E and container gates execute and pass**

## Executive assessment

The repository has a coherent deterministic/agentic boundary. FastAPI commits intake metadata,
audit, idempotency, and a graph-run request without running LangGraph inline. The orchestrator uses
`AsyncPostgresSaver`, persisted unique thread IDs, leased run/resume requests, a deterministic
parent graph, and an allow-listed typed-tool subgraph. Human decisions remain application-owned,
and approval creates a transactional outbox that only the integration worker can deliver.

No Camunda, Zeebe, BPMN runtime, connector, image, SDK, or environment reference remains in an
executable/configuration path. The only intentional Camunda text is historical migration/prohibition
documentation and the static guard that detects reintroduction. The obsolete Camunda-era directories
were removed.

The audit found and corrected high-impact defects in expired-lease recovery, correction-resume crash
reconciliation, permanent/transient retry classification, typed tool-output enforcement, failed-tool
audit persistence, incomplete-agent review, review authority, resume-payload integrity, tenant
predicates, restart-persistent agent deadlines, prompt registration, immutable-original cleanup, and
authenticated PDF rendering. Locally executable verification is green. A disposable pinned
PostgreSQL 17.6 container subsequently executed all seven migration, checkpoint, restart,
interrupt/resume, transactional-outbox, and end-to-end tests successfully. Gitleaks also passed the
committed history. Full Compose build/readiness and the post-fix GitHub Actions run remain required.

## Blocking findings

### B-01 — Mandatory PostgreSQL/checkpoint/E2E evidence

Severity: Blocking; status: Resolved locally, GitHub Actions rerun pending.

Evidence:

- `pytest -m integration` passed all seven tests against the pinned
  `postgres:17.6-alpine3.22` image.
- The executed set includes persistent checkpoint/restart, incomplete-agent review,
  interrupt/resume recovery, authoritative validation separation, transactional outbox, and the
  complete E2E scenario.

Required release action: require the corrected `.github/workflows/postgres-integration.yml` run to
pass on the release commit.

### B-02 — Compose and container builds were not executed

Severity: Blocking; status: Open validation gate.

Evidence: Docker is now available and the pinned PostgreSQL image passed health and integration
verification. The complete Compose config/build/up/readiness sequence has not yet passed on the
post-fix revision, so image builds, runtime users, and cross-service health remain gated.

Required release action: run `.github/workflows/containers.yml` and retain the successful Compose
config/build/up/health evidence for the release candidate.

### B-03 — Committed-history secret scanner evidence

Severity: Blocking for an independent release sign-off; status: Resolved for the initial commit;
post-fix scan pending.

Evidence: Gitleaks 8.30.1 found eight entries only when scanning ignored local runtime artifacts:
six in `.env` and two in a dependency source map under `.venv`. Both paths are excluded by
`.gitignore`. An ignore-filtered source snapshot scan covered approximately 935 KB and returned
`no leaks found`. Gitleaks 8.30.1 then scanned the committed history (one commit, approximately
1.12 MB) and returned `no leaks found`. The pinned Gitleaks job must repeat on the fix commit.

## High findings

### H-01 — Final-attempt expired leases could become permanently stranded

Status: Code corrected; unit and PostgreSQL integration verified.

Run, resume, and webhook leasing previously excluded rows whose attempt counter had reached the
maximum, even when a worker died while holding the final lease. The lease queries now always admit
expired `LEASED` rows and reuse, rather than consume, the interrupted attempt number. Correction
resume recovery also detects a later persisted review cycle and reconciles the old request instead
of replaying its stale `Command(resume=...)` into the next interrupt.

Evidence:

- `services/orchestrator/src/intake_orchestrator/repository.py`
- `services/integration-worker/src/intake_integration_worker/worker.py`
- Unit tests `test_expired_lease_recovery_does_not_consume_an_attempt` and
  `test_expired_webhook_lease_reuses_the_same_delivery_attempt` passed.
- The passing PostgreSQL E2E scenario includes a completed later review cycle and an expired old
  lease at its maximum attempt.

### H-02 — Tool outputs and failed executions were not durably contract-enforced

Status: Corrected and unit verified.

Every approved tool now has a Pydantic output schema. Results are validated in both the LangChain
wrapper and the PostgreSQL backend before commit. Malformed structured output degrades to a bounded
`AGENT_INCOMPLETE` recommendation. Failed tool calls persist a stable failed operation record plus
requested/failed audit events; replay uses the same operation key. Provider rate limits and mapped
transient database/storage/provider failures receive bounded retry, while permanent failures do not.

Evidence:

- `services/orchestrator/src/intake_orchestrator/agent/tools.py`
- `services/orchestrator/src/intake_orchestrator/agent/graph.py`
- `services/orchestrator/src/intake_orchestrator/agent/backend.py`
- Agent graph tests passed, including malformed output, timeout, provider unavailable/rate-limit,
  node retry, invalid tool, malformed arguments, tenant mismatch, limits, and replay.

### H-03 — Human-review authority and incomplete-agent recovery had integrity gaps

Status: Code corrected; unit and full PostgreSQL path verified.

Operations administrators no longer receive reviewer/approver authority; only reviewers/approvers
may claim, and only approvers may approve or reject. A no-candidate `AGENT_INCOMPLETE` case can now
create a reviewed version and be authoritatively rejected instead of failing review. Resume
application verifies tenant, task, cycle, document, reviewed version, decision row, decision value,
and actor. Correction values, maps, reasons, and reason-code lists are bounded before checkpointing.

Evidence:

- `services/api/src/intake_api/application/review.py`
- `services/orchestrator/src/intake_orchestrator/workflow.py`
- `services/api/alembic/versions/0004_release_readiness_hardening.py`
- Unit role/bounds/interrupt-size tests and the no-candidate rejection/resume integration test
  passed.

### H-04 — A post-commit exception could delete an authoritative original

Status: Corrected and unit verified.

The upload exception cleanup previously deleted the finalized object for any later exception,
including one after the database transaction had committed. Cleanup now deletes only before a
successful database commit. A test forces post-commit logging failure and proves the immutable
original remains; the existing database-commit-failure test proves unreferenced compensation still
removes the object.

Evidence: `services/api/src/intake_api/application/intake.py` and
`services/api/tests/unit/test_intake_failures.py`; all three intake-failure tests passed.

### H-05 — Tenant predicates were inconsistent on primary-key reads

Status: Corrected statically and PostgreSQL tenant-isolation verified.

Production API, orchestrator, agent, and integration-worker primary-key reads were converted to
explicit tenant-scoped selects where tenant context exists. Composite tenant foreign keys and unique
constraints remain in place. The static search for `session.get(` in production paths is empty.
The authenticated optional Zapier callback is the documented exception: it correlates a globally
opaque outbox UUID plus idempotency key under a server-side callback token because the callback
contract carries no tenant claim.

### H-06 — Agent time limit and prompt identity did not survive process restart completely

Status: Corrected and unit/PostgreSQL prompt-row verified.

The total agent deadline now derives from the persisted `agent_runs.started_at`, so replay or process
restart cannot reset it; each model/tool timeout is capped by the remaining total budget. Prompt text
is centralized, versioned, content-hashed, and registered
idempotently in `prompt_versions`. Live providers must receive an explicitly configured model name;
the deterministic stub remains the default test provider.

Evidence:

- `services/orchestrator/src/intake_orchestrator/agent/prompts.py`
- `services/orchestrator/src/intake_orchestrator/agent/backend.py`
- `services/api/src/intake_api/infrastructure/config.py`
- Restart-deadline and live-provider-configuration tests passed.

### H-07 — Authenticated PDF preview used an unsafe Angular resource-URL shape

Status: Corrected and frontend verified.

The review workspace now converts only the locally created object URL from the authenticated
FastAPI Blob response into a `SafeResourceUrl`. Navigation exposes `aria-current`, while existing
skip-link, live-region, visible-focus, semantic label, and non-color status behavior remains.
Angular format, lint, five unit tests, and the production build passed.

## Medium findings

### M-01 — Local checkpoint and application privileges are not separated

The local Compose profile uses one database, namespace, and credential for application and
LangGraph checkpoint tables. PostgreSQL row-level security and separate setup/runtime roles are not
implemented. This is acceptable only for the isolated demo profile, not an enterprise deployment.

### M-02 — The agent subgraph is not checkpointed mid-loop

Only the parent graph is compiled with `AsyncPostgresSaver`. Agent effects, counters, usage,
recommendations, and its start time are application records, so replay is bounded and side effects
are idempotent; transient model-routing state can still be recomputed after a crash. Mid-agent
checkpoint continuity is not proven.

### M-03 — Checkpoint retention and serialized-state scanning are policy, not automation

The state and interrupt contracts are bounded, and the interrupt payload has a byte-size assertion.
There is no automated PostgreSQL checkpoint byte/secret scan and no retention/purge job. The
implementation checklist and retention policy now state this accurately.

### M-04 — Audit coverage uses domain events rather than generic per-node events

Material business actions are audited, and audit/stored-object/version/history tables are protected
by append-only database triggers. The enum contains generic graph-node start/completion values, but
the graph does not emit a row for every node boundary. Before/after field values live in the
append-only correction record rather than the audit event. This is adequate for first-slice business
reconstruction but falls short of generic node-level observability.

### M-05 — Zapier is at-least-once, not intrinsically exactly once

Approval, approved record, audit, and outbox creation are atomic and idempotent. Delivery attempts
use stable idempotency headers/ledger keys and expired-lease recovery. Exactly-once downstream effect
still depends on the Zap/receiver honoring the idempotency key. The optional callback is authenticated
and idempotent but has no timestamp freshness window.

### M-06 — Version evolution supports one active graph factory

Unknown persisted graph versions fail visibly, and graph/prompt/policy/rules/confidence/schema
versions are stored. The process does not retain multiple compatible graph factories or implement
audited in-flight state migration.

### M-07 — Accessibility automation is shallow

The Angular shell has useful semantic and keyboard controls, but the five component/service tests do
not run axe or an equivalent automated WCAG scanner, nor is there browser-level keyboard/screen-reader
coverage.

### M-08 — An unused legacy application outbox table remains

`webhook_outbox_events` from the earlier workflow schema is unused; the active boundary is
`outbox_events` plus `webhook_deliveries` and `dead_letter_events`. Removing the unused table requires
a separate, verified migration and was deferred to avoid an untested destructive schema change.

## Low findings

- FastAPI tests emit one Starlette deprecation warning about the TestClient/httpx compatibility
  path; it does not fail current tests but should be removed before the dependency forces migration.
- GitHub Actions are version-tag pinned rather than immutable commit-SHA pinned.
- The pinned upstream nginx/database images retain upstream runtime-user behavior; custom Python
  images run as non-root. Production deployment policy should enforce container restrictions.
- Local immutable storage is create-only and path-safe but is neither encrypted nor malware-scanned;
  those controls remain outside the first-slice demo boundary.

## Evidence reviewed

- All project instructions and ADRs.
- Graph state, executor/checkpointer configuration, node retries, run/resume repositories, and
  checkpoint setup.
- Agent model factories, typed input/output contracts, policy guard, counters/timeouts, persistence,
  extraction, validation, confidence, and recommendation authority.
- Intake/storage transactions, review commands, tenant/role queries, append-only migrations, audit,
  outbox, integration delivery, callback, Angular, Streamlit, Compose, CI, fixtures, and evaluation.
- Static searches for unfinished markers, placeholder exceptions, empty/broad swallowing, disabled
  tests, secret patterns, floating-point money, unbounded reads, mutable originals, arbitrary SQL/URL
  tools, policy bypass, large checkpoint content, agent approval, direct graph-to-Zapier calls,
  non-idempotent effects, tenantless primary-key reads, direct Streamlit persistence, and stale
  Camunda artifacts.

No TODO/FIXME/XXX product marker, placeholder exception, empty exception block, model-dependent
ordinary test, floating-point monetary calculation, unbounded production document read, arbitrary
SQL/URL tool, direct Streamlit database write, direct graph Zapier call, `latest` image, or executable
Camunda artifact was found. Broad exception handlers either compensate and re-raise, translate to a
bounded failure record, or operate at a leased-worker boundary with visible retry/exhaustion.

## Fixes applied

Changed implementation areas:

- API: intake cleanup, review authorization/bounds/no-candidate handling, tenant-scoped queries,
  production model configuration, reviewed-version nullability, and Alembic revision 0004.
- Orchestrator: lease recovery, retry classification, resume reconciliation, parent retries, typed
  tool outputs/failures, provider mapping, persisted deadlines, prompt hash registry, tenant-scoped
  persistence, authoritative resume validation, explicit tenant-FK parent/child flush ordering,
  phase-scoped audit idempotency, and crash-safe interrupted-status publication.
- Integration worker: expired final-lease recovery and tenant-scoped ledger/dead-letter reads.
- Angular: role actions, authenticated PDF resource URL, and navigation accessibility.
- JavaScript supply-chain policy: replaced pnpm 11's generated build-script placeholders with
  explicit approvals for the four pinned native dependencies required by the Angular build, and
  removed the obsolete `onlyBuiltDependencies` setting.
- Dependencies/tests: pinned `greenlet` directly for async SQLAlchemy on Apple Silicon, isolated
  configuration tests from developer `.env` values, and corrected the mock Zapier catch-hook path.
- Quality: static release architecture checks in `scripts/check.sh` and Python CI; new unit and
  PostgreSQL integration scenarios.
- Documentation: architecture, state/checkpoint boundaries, tool contracts, audit behavior,
  operations, threat model, scope, checklist, and Zapier callback guarantees corrected to match code.
- Removed: obsolete `infrastructure/camunda`, `process-models`, and `services/workers` directories.

The full multi-container build/readiness path remains explicitly open under B-02. B-01 and B-03
require confirmation from the post-fix GitHub Actions revision before release sign-off.

## Deferred issues

The medium and low findings above are intentionally deferred because they require production
deployment architecture, destructive schema migration, new observability scope, or broader
accessibility tooling. None grants the agent approval/integration authority or bypasses deterministic
validation. The remaining B-02 and post-fix CI confirmations are validation gates, not accepted
deferrals.

## Exact final test results

| Check | Result |
|---|---|
| `uv sync --frozen` | Passed: `Checked 111 packages` |
| `sh scripts/release_static_checks.sh` | Passed |
| Ruff format | Passed: 86 files already formatted |
| Ruff lint | Passed |
| strict Pyright | Passed: 0 errors, 0 warnings, 0 informations |
| `pytest -m "not integration"` | Passed: 100 passed, 7 deselected, 1 deprecation warning |
| graph tests | Passed: 33 passed, 25 deselected |
| Streamlit tests | Passed: 2 passed |
| local evaluation | Passed: 9 cases; classification 1.0000; extraction 1.0000; completeness 1.0000; evidence 0.9825; format validity 1.0000; unauthorized-tool rate 0.0000; average tools 5.2222; retry rate 0.1111; rule agreement 1.0000; correction rate 0.0351 |
| Alembic static head | Passed: one head, `0004_release_readiness_hardening` |
| workflow/Compose YAML parse | Passed: five workflow files and `docker-compose.yml` parsed |
| Angular format | Passed |
| Angular lint | Passed |
| Angular unit tests | Passed: 2 files, 5 tests |
| Angular production build | Passed: 177.74 kB initial bundle, 51.15 kB estimated transfer |
| Clean forced pnpm install | Passed: 656 packages; all four explicitly approved native build scripts completed |
| Python production dependency audit | Passed: no known vulnerabilities (`pip-audit==2.9.0`) |
| pnpm dependency audit | Passed: no known vulnerabilities at high threshold |
| PostgreSQL integration/checkpoint/E2E | Passed: 7 passed, 100 deselected, 1 deprecation warning |
| Docker Compose/container build/health | **Post-fix full stack not yet run** |
| Gitleaks source snapshot | Passed: no leaks in approximately 935 KB after excluding `.gitignore` runtime artifacts |
| Gitleaks committed history | Passed: one commit, approximately 1.12 MB, no leaks |

## Go/no-go recommendation

**NO-GO.** Do not label or deploy this release candidate until the exact revision passes the
PostgreSQL migration/integration/checkpoint/E2E workflow, the Compose container build/readiness
workflow, and Gitleaks. If those gates pass without changes, the first-slice demo can proceed with
the medium/low limitations explicitly accepted. Any code change made to satisfy those gates requires
this review and the complete suite to be rerun.
