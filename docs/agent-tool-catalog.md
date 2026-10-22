# Agent Tool Catalog

## Common contract

All tools are LangChain typed tools backed by Pydantic input/output models with `extra="forbid"`.
Inputs include `tenant_id`, `document_id`, and `agent_run_id`; the guard requires them to equal
persisted graph context. The orchestrator derives a stable SHA-256 operation key from the run, call
ordinal, tool, and canonical validated arguments—the model cannot choose that key. Every output is
also validated against a Pydantic output contract before commit. Detailed fields/evidence remain in
application tables.

The immutable registry contains exactly the tools below. Invoice tools (`extract_invoice_*`,
`lookup_supplier`, `lookup_purchase_order`) require an `AP_INVOICE` classification and claim tools
require a `CLAIM_PACKET` classification; the policy guard enforces this. Default execution is serial. Policy checks,
`TOOL_REQUESTED`, completion/failure/rejection audit, timing, provider metadata when applicable, and
safe argument/result summaries apply to every tool. The initial slice uses the configured global
per-tool timeout (30 seconds by default), serial execution, and a three-attempt LangGraph node retry
policy only for mapped transient provider, database, and storage failures. Replays reuse the same
operation key and the persisted agent start time enforces the total deadline across process restarts.

| Tool | Typed input | Typed output | Authorization and prerequisites | Timeout/retry/idempotency |
|---|---|---|---|---|
| `inspect_document` | identity | document ID, bounded page count, text/visual flags, PDF media type | Immutable object belongs to tenant/document and bounded PDF inspection succeeds | Stable run/call/argument operation key |
| `classify_document` | identity; `inspection_completed=true` | closed document type, confidence, bounded reasons | Inspection must have completed in this run | Stable run/call/argument operation key |
| `extract_invoice_from_text` | identity; attempt number 1–3 | extraction-run/candidate IDs, `TEXT`, completeness, reasons | Inspection/classification complete; text available; extraction counter allows attempt | Unique extraction operation key; candidate/evidence persisted atomically |
| `extract_invoice_from_visual_content` | identity; attempt number 1–3 | extraction-run/candidate IDs, `VISUAL`, completeness, reasons | Inspection/classification complete; visual content available; extraction counter allows attempt | Same protections as text extraction |
| `lookup_supplier` | identity; bounded supplier name and optional tax ID | match flag, optional supplier ID/active flag, match quality | Current tenant reference data only; maximum two calls | Stable normalized argument key; read-only lookup |
| `lookup_purchase_order` | identity; bounded PO number | match flag, optional PO ID/active flag, match quality | Current tenant reference data only; maximum two calls | Stable normalized argument key; read-only lookup |
| `validate_candidate` | identity; extraction candidate ID | validity, retry recommendation, bounded rule reasons | Candidate belongs to tenant/document/run | Advisory run uses the deterministic rule engine; stable run/candidate key |
| `retry_extraction` | identity; candidate ID; next strategy; bounded reasons | authorization, next attempt 2–3, next strategy | Existing candidate; prior validation; extraction counter below limit | No external effect; stable run/attempt key |
| `submit_recommendation` | identity; optional candidate; closed document type/disposition; bounded reasons/actions/summary/tools | recommendation ID | Inspection and classification required; the document type must equal the classification; an AP invoice or claim packet requires a validated candidate; no `AUTO_APPROVE` value exists | One stable recommendation operation per validated call |
| `segment_claim_packet` | identity; `inspection_completed=true` | page count, one closed segment label per page, bounded reasons | Requires a `CLAIM_PACKET` classification; once per run; labels persisted per page | Stable run/call/argument operation key |
| `extract_claim_from_text` | identity; attempt number 1–3 | extraction-run/candidate IDs, `TEXT`, completeness, reasons | `CLAIM_PACKET` classification and segmentation complete; shares the three-attempt extraction counter | Candidate/evidence persisted with schema `claim-packet-v1` |
| `extract_claim_from_visual_content` | identity; attempt number 1–3 | extraction-run/candidate IDs, `VISUAL`, completeness, reasons | Same as text claim extraction; used for scanned packets | Same protections as text claim extraction |
| `lookup_policy` | identity; bounded policy number | match flag, optional policy ID and status | `CLAIM_PACKET` only; current tenant reference data; maximum two calls | Read-only lookup |
| `lookup_policy_vehicle` | identity; policy number; bounded VIN | `EXACT`, `NEAR` (mistyped VIN on a policy vehicle), or `NONE`, with optional vehicle ID | `CLAIM_PACKET` only; current tenant reference data; maximum two calls | Read-only lookup |

## Output restrictions

`inspect_document` does not return document bytes or unrestricted paths. Extraction tools persist
field value, `Decimal` money, confidence, source page, bounded excerpt, strategy, provider, model,
schema, and prompt versions, then return only the attempt ID and summary. Lookup tools return no
unnecessary supplier/PO attributes. Validation feedback is advisory inside the subgraph; the parent
runs the same service independently.

## Policy reason codes

At minimum, the guard uses `TOOL_NOT_REGISTERED`, `TOOL_ARGUMENT_INVALID`, `TENANT_SCOPE_MISMATCH`,
`DOCUMENT_SCOPE_MISMATCH`, `TOOL_NOT_AUTHORIZED`, `TOOL_PREREQUISITE_MISSING`,
`TOOL_CALL_LIMIT_REACHED`, `EXTRACTION_LIMIT_REACHED`, `SUPPLIER_LOOKUP_LIMIT_REACHED`,
`PURCHASE_ORDER_LOOKUP_LIMIT_REACHED`, `PARALLEL_TOOL_CALL_REJECTED`, `AGENT_TOOL_TIMEOUT`, and
`AGENT_TOTAL_TIMEOUT`. Reasons are safe for audit and human review.

## Tool implementation test contract

Each tool requires unit tests for Pydantic boundaries, authorization, limit/prerequisite checks,
timeout mapping, idempotent replay, bounded output, audit success/failure, and tenant mismatch. A
PostgreSQL integration test proves detailed persistence and unique idempotency constraints. Model-
using tools run against deterministic scripted responses in the default suite.
