# Campaign Brief Slice Scope (agency)

Governed by [ADR 0009](adr/0009-campaign-brief-document-type-and-domain-packs.md). The constitution
and the first-slice and claims scopes still apply unless this document says otherwise.

## Outcome

A client campaign brief for the fictional agency Meridian & Pike crosses the same durable graph as
invoices and claims: segmentation, extraction of a work request with evidence, SOW and lead-time
lookups, deterministic brief rules, a versioned queue assignment, and application-owned human
review. Every work request requires a human decision.

## Page segments

`BRIEF`, `CORRESPONDENCE`, `MEDIA_PLAN`, `OTHER`; one label per page, at most 20 pages.

## Work-request contract (`campaign-brief-v1`)

Scalar fields, each an `ExtractedField`: `client_name`, `brand`, `campaign_name`,
`sow_number_stated`, `received_date`, `objective`, `primary_kpi`, `kpi_target`, `audience`,
`markets`, `budget_total` (Decimal), `launch_date`, `end_date`, `launch_date_in_correspondence`,
`approver`, `mandatories`.

Deliverables: up to 50 items, each with `asset_type` (closed set from the lead-time table),
`quantity`, `description`, `due_date`, source page, evidence, and confidence. Deliverables that only
appear in correspondence are a separate list, `deliverables_added_in_correspondence`.

Implemented in `intake_orchestrator.agent.brief_contracts` and `agent/brief_extraction.py`. Brief
prompts are versioned `brief-intake-agent-v1` with their own hash. Each deliverable is persisted with
its own evidence row (`deliverables[0]`, `deliverables[1]`, …).

## Reference data

| Table | Contents |
|---|---|
| Statements of work | SOW number, client, term, status, total and committed fees, services in scope, regulated category (`NONE`, `PHARMA`, `FINANCIAL`, `ALCOHOL`), account lead |
| SOW brands | Brands each SOW covers |
| Lead times | Minimum production business days per asset type, and the service each asset type belongs to |

## Brief rules v1

| ID | Rule | Outcome when violated |
|---|---|---|
| CB-001 | The brand is covered by a tenant SOW (the stated SOW if it covers the brand, otherwise the SOW in force at launch). | FAIL `SOW_NOT_FOUND` |
| CB-002 | That SOW is active and the launch date is within its term. | FAIL `SOW_NOT_ACTIVE_AT_LAUNCH` |
| CB-003 | Objective, audience, primary KPI, launch date, and at least one deliverable are present. | REVIEW `BRIEF_INCOMPLETE` |
| CB-004 | The KPI target contains a number. | REVIEW `KPI_NOT_MEASURABLE` |
| CB-005 | A budget is present. | REVIEW `BUDGET_MISSING` |
| CB-006 | The budget is within the SOW's remaining fees (total minus committed). | REVIEW `OVER_SOW_BUDGET` |
| CB-007 | Every deliverable's service, including deliverables added in correspondence, is in SOW scope. | REVIEW `OUT_OF_SCOPE_SERVICE` |
| CB-008 | Every deliverable has at least its minimum production business days between receipt and its due date, capped at the earliest launch date in the packet. | REVIEW `TIMELINE_AT_RISK` |
| CB-009 | Correspondence does not change the launch date or add deliverables. | REVIEW `CORRESPONDENCE_CONFLICT` |
| CB-010 | The SOW's regulated category is `NONE`. | REVIEW `REGULATED_CONTENT` |
| CB-011 | No earlier non-rejected brief has the same SOW, campaign name, and launch date. | REVIEW `DUPLICATE_BRIEF` |
| CB-012 | Received date is before launch, and launch is before the end date. | FAIL `DATES_INVALID` |
| CB-013 | Critical fields (client, brand, campaign, launch date) meet their confidence threshold. | REVIEW |
| CB-014 | Every page has a supported segment label. | REVIEW |

Business days count weekdays after the received date up to and including the due date; holidays are
ignored in v1.

## Queue routing: `brief-routing-v1` (first match wins)

| Priority | Condition | Queue |
|---|---|---|
| 1 | CB-011 | `DUPLICATE_REVIEW` |
| 2 | CB-001 or CB-002 failed | `SCOPE_REVIEW` (no active contract) |
| 3 | CB-006 or CB-007 | `SCOPE_REVIEW` (change order needed) |
| 4 | CB-010 | `LEGAL_REVIEW` |
| 5 | CB-003, CB-004, or CB-005 | `NEEDS_CLIENT_INFO` |
| 6 | CB-008 | `RUSH_REVIEW` |
| 7 | CB-009, CB-012, CB-013, or CB-014 | `TRAFFIC_REVIEW` |
| 8 | Otherwise | `READY_FOR_PLANNING` |

## Persistence (migration `0007_campaign_brief_pack`)

`statements_of_work`, `sow_brands`, `asset_lead_times`, and `brief_intake_keys`. Page segments and
routing decisions reuse `document_page_segments` and `claim_routing_decisions`, whose label and queue
constraints now cover both packs. `python -m intake_api.load_brief_reference [directory]` loads the
reference CSVs (idempotent, audited as `BRIEF_REFERENCE_DATA_LOADED`); the local Compose profile runs
it after bootstrap.

## Decision confidence

`decision-confidence-briefs-v1`: classification 0.15, required-field completeness 0.20,
critical-field confidence 0.20, evidence coverage 0.15, SOW match 0.10, scope fit 0.10, rule score
0.10.

## Review API and screens

Briefs use the generic review-queue API: `GET /api/v1/review-queues?document_type=CAMPAIGN_BRIEF`,
`GET /api/v1/review-queues/tasks/{task_id}`, and `POST /api/v1/review-queues/tasks/{task_id}/reroute`
(re-routes are limited to brief queues). Queue rows summarize client and brand, campaign, launch,
budget, and deliverable count. Service-level targets: `INTAKE_BRIEFS_SLA_RUSH_HOURS` (2),
`INTAKE_BRIEFS_SLA_NEEDS_INFO_HOURS` (4), `INTAKE_BRIEFS_SLA_READY_HOURS` (24), and
`INTAKE_BRIEFS_SLA_STANDARD_HOURS` (8). Reviewers correct scalar work-request fields; deliverable
lists are corrected through client follow-up or traffic review in v1. The Angular work queues switch
between claims and briefs; the workbench shows deliverables with per-item source pages and outlines a
launch date or deliverable list that correspondence changes.

## Approved work requests

Approving a brief emits the shared `DOCUMENT_APPROVED` outbox event with a `work_request`
(`work-request-v1`) carrying one task per deliverable, for example
`"Winter Warmers: 12 × Social static post"` with its service and due date. Delivery to Zapier (and on
to a work-management tool) uses the existing outbox, worker, and idempotency guarantees.

## Clarifying questions

`GET /api/v1/review-queues/tasks/{task_id}/client-questions` returns a rule-derived draft (one
question per missing objective, audience, KPI, launch date, or deliverables; unmeasurable KPI;
missing budget; out-of-order dates; correspondence changes; low-confidence critical fields) and the
questions already sent. After sending from their own email, a reviewer who has claimed the task
records the final text with `POST …/client-questions/sent`. The system never contacts a client.

## Evaluation

`test-data/briefs`: 20 packets, `statements_of_work.csv`, `sow_brands.csv`, `lead_times.csv`, and
`ground_truth.csv`, generated by `scripts/generate_brief_fixtures.py`. Metrics mirror the claims
set; the hard gate is zero false `READY_FOR_PLANNING` assignments.

## Deliberately deferred

Pricing and estimating, resourcing and scheduling, creative review, holiday calendars, and any
automatic approval or client contact.
