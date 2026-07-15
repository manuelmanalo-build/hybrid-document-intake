# First Slice Scope

## Outcome

The first slice proves a secure AP-invoice intake can cross a durable deterministic/agentic graph,
pause for application-owned human review, resume safely, and transactionally create an approved
integration event that a separate worker delivers. The graph never delivers it directly. Every
successful case requires a human decision.

## Included capabilities

- Angular PDF upload and review UI using strict standalone components and signals.
- FastAPI multipart intake with required idempotency key, correlation ID, authentication,
  authorization, and tenant enforcement.
- Bounded streamed validation, SHA-256, MIME and PDF signature checks, immutable local storage
  behind a port, PostgreSQL metadata, duplicate-hash signal, audit, and graph-run request in one
  transaction.
- A separately deployed asynchronous orchestrator with request leasing, persistent checkpoints,
  deterministic parent graph, and bounded agent subgraph.
- LangChain OpenAI or Gemini selection by environment configuration and a deterministic stub used
  by default tests.
- Typed agent tools, policy guard, structured AP extraction, independent deterministic validation,
  versioned confidence, application review tasks, interrupts and durable resume requests.
- Approval transaction that writes the authoritative decision, audit event, and Zapier outbox event;
  an integration worker with delivery ledger, bounded exponential retry with jitter, and dead letter.
- Streamlit multipage operations shell backed only by FastAPI APIs.
- Pinned Docker Compose and GitHub Actions checks.

## Deliberately deferred

Additional document types, automatic approval, parallel tools, multi-agent delegation, arbitrary
retrieval, direct SaaS administration, production object-store adapters, enterprise identity
provider integration, and production observability export are outside this slice. Visual extraction
may use bounded rendered pages but cannot put page images in checkpoints.

## AP invoice contract

`AP_INVOICE` fields are `invoice_number`, `invoice_date`, `due_date`, `supplier_name`,
`supplier_tax_id`, `purchase_order_number`, `currency`, `subtotal`, `tax`, and `total`. Money is
`Decimal`. Every extracted field preserves value, confidence in `[0,1]`, source page, bounded
evidence excerpt, extraction strategy, provider, model, schema version, and prompt version. Absence
is explicit; it is not represented by a fabricated value.

The final recommendation is validated against this logical contract:

```json
{
  "document_id": "uuid",
  "document_type": {"value": "AP_INVOICE | UNSUPPORTED | UNKNOWN", "confidence": 0.0},
  "recommended_disposition": "HUMAN_REVIEW | REQUEST_INFORMATION | REJECT_UNSUPPORTED",
  "reason_codes": [],
  "summary": "Concise explanation without hidden reasoning",
  "requested_human_actions": [],
  "tools_used": [],
  "prompt_version": "string",
  "agent_policy_version": "1.0"
}
```

`AUTO_APPROVE` is invalid. Failure to obtain a valid contract becomes `AGENT_INCOMPLETE` and does
not prevent review.

## Deterministic rules v1

| ID | Rule |
|---|---|
| BR-001 | Invoice number is required. |
| BR-002 | Invoice date cannot be in the future using the configured business date/time zone. |
| BR-003 | Due date cannot precede invoice date. |
| BR-004 | Total equals subtotal plus tax within the configured Decimal tolerance. |
| BR-005 | Currency belongs to the configured supported ISO 4217 set. |
| BR-006 | Supplier name is required. |
| BR-007 | Supplier and invoice number do not duplicate an approved invoice in the tenant. |
| BR-008 | Purchase-order number is required when total exceeds the configured Decimal threshold. |
| BR-009 | Configured critical fields below their confidence threshold require review. |
| BR-010 | Unsupported or unknown documents require review. |
| BR-011 | A matched supplier must be active. |
| BR-012 | A matched purchase order must not be exceeded. |

Each result carries rule ID/version, outcome (`PASS`, `FAIL`, `REVIEW`, `NOT_APPLICABLE`), safe reason
code, and relevant record IDs. The agent can request feedback from the service; the parent invokes
it independently after the agent and again after human corrections.

## Decision confidence v2

Maintain the provider field confidences separately from these normalized `Decimal` components:

- `K`: classification confidence
- `C`: required-field completeness (present required fields divided by seven required fields)
- `E`: evidence coverage (supported extracted fields divided by populated extracted fields)
- `F`: aggregate critical-field confidence, using the configured conservative aggregation
- `S`: supplier-match quality
- `P`: purchase-order-match quality
- `D`: deterministic-rule score (passing applicable rules divided by applicable rules)
- `R`: rule-based risk in `[0,1]`, computed from deterministic rule severities

Calculate a weighted mean with weights `K=.15`, `C=.20`, `E=.15`, `F=.20`, `S=.10`, `P=.10`,
`D=.10`.
Supplier quality is applicable when an AP candidate has a supplier reference; PO quality is
applicable when a PO is present or BR-008 requires one. Omit an inapplicable reference component
and its weight; an applicable but unresolved reference scores zero. Then:

`overall = quantize_0_0001(clamp(weighted_mean * (1 - 0.60 * R), 0, 1))`

Use `Decimal`, round-half-even, configuration snapshots, and algorithm version
`decision-confidence-v2`. The score has floor zero, cap one, a maximum risk penalty of 60%, and
round-half-even to four decimal places. It affects prioritization and explanations only; all first-slice
cases still route to review.

## Durable execution and review acceptance criteria

- Upload commits one `graph_runs.status=REQUESTED` record and one
  `graph_run_requests.status=PENDING` dispatch record with unique `graph_run_id` and `thread_id`; it
  never invokes the graph.
- The orchestrator leases requests with `FOR UPDATE SKIP LOCKED`, bounded attempts, lease expiry,
  safe errors, and visible terminal failure.
- The production graph uses `AsyncPostgresSaver`; a run and resume use the same `thread_id`.
- A review node idempotently creates one open task and audit event, then interrupts with sanitized,
  JSON-serializable identifiers and summaries.
- FastAPI claim/unclaim/correction/decision commands enforce tenant and role, use row locking and
  uniqueness-based concurrency control, and transactionally write a resume request.
- The orchestrator leases the resume request and calls `Command(resume=validated_payload)` outside
  the API transaction. Code before the interrupt is safe to run again.
- `REQUEST_CORRECTION` loops to the existing review flow; `REJECT` finalizes without an outbox;
  `APPROVE` creates exactly one outbox event transactionally.

## Audit coverage

Append events for upload received; validation passed/failed; file stored; graph run requested and
started; material node started/completed; agent started; tool requested/rejected/completed/failed;
extraction completed; recommendation submitted; authoritative validation completed; confidence
calculated; review task created; task claimed/unclaimed; field corrected; approved/rejected; graph
resumed; outbox created; webhook attempted/delivered; retry scheduled; and delivery exhausted.

Each event contains event ID, tenant/document, graph and agent identifiers where applicable, actor,
correlation ID, timestamp, safe before/after values, and sanitized metadata. Audit records exclude
document contents, secrets, private reasoning, and unrestricted evidence.

## Exit criteria

The default offline suite passes formatting, lint, strict typing, unit tests, PostgreSQL integration
tests, Angular tests/build, migrations, and Compose validation. Tests prove tenant isolation,
checkpoint/restart behavior, interrupt/resume, replay idempotency, policy limits, deterministic
rules/confidence, and exactly-once business effects over at-least-once processing.
