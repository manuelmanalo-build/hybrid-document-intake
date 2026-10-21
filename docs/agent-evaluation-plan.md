# Agent Evaluation Plan

## Evaluation principles

Safety and authority are hard gates; extraction quality cannot compensate for a failed gate. The
default suite is deterministic and offline. Evaluation stores structured outcomes, not private
reasoning, and separates model quality from deterministic platform correctness.

## Test layers

### Contract and policy unit tests

- Accept/reject every Pydantic tool and recommendation boundary, including `Decimal`, enums, UUIDs,
  extra fields, oversized summaries/evidence, and `AUTO_APPROVE`.
- Prove unknown tools, arbitrary node names, multiple/parallel calls, cross-tenant/document IDs,
  missing prerequisites, and restricted capabilities never reach an adapter.
- Prove global 10-call, 3-extraction, 2-supplier, 2-PO, model/tool/total timeout limits and stable
  idempotency keys.
- Map provider unavailable, timeout, malformed output, invalid tool output, and exhausted limits to
  `AGENT_INCOMPLETE` and human review.
- Verify audit payload redaction and absence of private reasoning, credentials, raw document data,
  hook URLs, and unrestricted paths.

### Deterministic service tests

Use truth tables and boundary cases for BR-001 through BR-012: dates/time zones, Decimal tolerance,
currency configuration, tenant-scoped approved duplicates, PO threshold equality, confidence
threshold equality, inactive suppliers, currency mismatch, and PO amount equality/excess. Run the
same fixture through advisory and authoritative entry points and require identical versioned rule
results.

Test `decision-confidence-v2` with exact Decimal fixtures, applicability normalization, unresolved
reference zeros, risk extremes, clamping, and round-half-even. Assert changing provider prose cannot
change an authoritative score.

### Scripted-agent scenarios

A deterministic stub emits a sequence of structured model responses. The matrix includes clean text
invoice, visual-only invoice, unknown document, unsupported document, missing required fields,
ambiguous supplier, missing/high-value PO, invalid totals, retry-improves-candidate, retry-does-not-
improve, malformed response then recovery, provider outage, policy attack, and every limit.

For each scenario assert tool sequence/arguments, counters, persisted concise summaries, final
contract or incomplete reason, independent parent validation, and mandatory review route. Exact
natural-language wording is not a pass condition; structured fields and reason codes are.

### PostgreSQL and graph integration tests

- Apply migrations to disposable PostgreSQL and initialize `AsyncPostgresSaver` checkpoint tables.
- Create a run request, lease it concurrently with `SKIP LOCKED`, start the correct graph version,
  and persist unique run/thread identity.
- Stop after material nodes, restart the orchestrator, and prove checkpoint continuation without
  duplicate extraction, review tasks, recommendations, status transitions, or audit milestones.
- Interrupt with sanitized payload, commit a validated review/resume request, lease it, resume the
  same thread with `Command(resume=...)`, and reject wrong thread/tenant/task/version.
- Prove the interrupt node's pre-interrupt work is replay-idempotent and correction loops retain one
  authoritative task lineage.
- Corrupt or omit a checkpoint in a controlled fixture and require visible failure, safe error code,
  and no silent case loss.

### Security and API tests

Exercise each role against upload, document read, review list/claim/unclaim/correct/decide, audit,
operations, and integration administration. Prove tenant isolation in every query and foreign key.
Verify RFC 9457 errors and correlation IDs without credential or path leakage. Angular/Streamlit
tests confirm they use API boundaries and expose no server credentials or direct graph controls.

### Outbox and delivery tests

Prove approval state, audit, and unique outbox event commit atomically; rejection creates none.
Replay approval and graph nodes and require one event. Simulate 2xx, timeout, 4xx policy, 5xx,
malformed response, retry delay/jitter bounds, lease expiry, callback authentication, duplicate
callback, and terminal dead letter. No default test sends a real Zapier request.

## Dataset and metrics

`test-data` contains synthetic, redistributable PDFs and expected structured truth with no real
personal or supplier data. Partition fixtures into visible regression and held-out evaluation sets.
Record per-field exact/normalized accuracy, amount/date accuracy, classification accuracy,
completeness, evidence coverage, unsupported detection, average tool/model calls, timeout/incomplete
rate, policy rejection rate, review correction rate, and deterministic rule precision/recall.

## Release gates

Release requires zero authority escapes, cross-tenant reads/writes, unvalidated operational calls,
direct integration calls from graph nodes, duplicate authoritative effects, secret/content leakage,
or cases missing review. All rules and scoring vectors must pass exactly. Restart/interrupt/outbox
integration suites must pass. Quality thresholds are versioned after a baseline dataset exists and
cannot weaken the hard gates.

Optional live-provider tests run in an isolated job only when explicit credentials and cost approval
are supplied. They are diagnostic and cannot be required for ordinary CI.

## Executable local harness

Run `python -m evaluation.run` against `test-data/evaluation/cases.json`. The sanitized nine-case
regression set covers valid, arithmetic mismatch, missing PO, inactive supplier, PO overage,
unsupported, low confidence, malformed PDF, and duplicate invoice scenarios. It reports document
classification accuracy, field extraction accuracy, required-field completeness, evidence coverage,
recommendation-format validity, unauthorized-tool execution rate, average tool count, extraction
retry rate, rule agreement, and human correction rate.

The local evaluator has no network requirement. `EVALUATION_LANGSMITH_ENABLED=true` optionally
exports sanitized case inputs and observations when `LANGSMITH_API_KEY` and
`EVALUATION_LANGSMITH_DATASET` are explicitly set. `LANGSMITH_TRACING` separately controls normal
LangChain tracing. Both default false, and missing LangSmith never affects ordinary tests.

## Live extraction diagnostic

`evaluation.run` scores recorded observations; it does not call a model. To measure a real
provider, run the opt-in live extraction diagnostic. It reads each `AP_INVOICE` fixture with the
same bounded PDF reader the graph backend uses, calls the production structured extractor, and
scores every field against `expected.fields` after normalizing numbers, dates, case, and
whitespace.

```bash
PYTHONPATH=.:services/api/src:services/orchestrator/src \
AGENT_PROVIDER=openai AGENT_MODEL=<model> OPENAI_API_KEY=<key> \
EVALUATION_LIVE_ENABLED=true EVALUATION_LIVE_STRATEGY=TEXT \
uv run python -m evaluation.live_extraction
```

A live provider refuses to run without `EVALUATION_LIVE_ENABLED=true` (explicit cost approval).
With the default stub provider the report is labeled `STUB_NOT_A_MEASUREMENT`. The full per-case,
per-field report is written to `EVALUATION_LIVE_REPORT` (default `evaluation-live-report.json`,
git-ignored). Per-case failures are recorded by error type, counted, and make the command exit
non-zero. The diagnostic is never part of ordinary CI; its first live result becomes the versioned
quality baseline referred to under release gates.

### Live claims diagnostic

`evaluation.live_claims_extraction` runs the claim segmenter and extractor over the 20 packets in
`test-data/claims` and scores them against `ground_truth.csv`. It reports page-segment accuracy,
per-field accuracy, and field accuracy split between digital and scanned packets. It defaults to the
`VISUAL` strategy so the two image-only scans are readable, uses the same
`EVALUATION_LIVE_ENABLED=true` cost gate, labels stub runs `STUB_NOT_A_MEASUREMENT`, and writes
`evaluation-live-claims-report.json` (git-ignored).

```bash
PYTHONPATH=.:services/api/src:services/orchestrator/src \
AGENT_PROVIDER=openai AGENT_MODEL=<model> OPENAI_API_KEY=<key> \
EVALUATION_LIVE_ENABLED=true uv run python -m evaluation.live_claims_extraction
```

A live run makes two model calls per packet (40 total).

