# Claims Slice Scope

Governed by [ADR 0008](adr/0008-claim-packet-document-type.md). Everything in
[first-slice-scope.md](first-slice-scope.md) and the constitution still applies unless this
document says otherwise.

## Outcome

A personal-auto FNOL packet crosses the same durable deterministic/agentic graph as an invoice:
segmentation, extraction with evidence, policy and vehicle lookup, deterministic claim rules, a
versioned review-queue assignment, and application-owned human review. Every claim requires a
human decision.

## Page segments

`segment_claim_packet` labels every page with exactly one of: `LOSS_NOTICE`, `REPAIR_ESTIMATE`,
`POLICE_REPORT`, `CORRESPONDENCE`, `OTHER`. Output is bounded to 20 pages and carries a
confidence per page.

## Claim-packet contract

`CLAIM_PACKET` fields, each an `ExtractedField` with value, confidence, source page, bounded
evidence, strategy, provider, model, schema version, and prompt version. Absence is explicit.

| Field | Type | Notes |
|---|---|---|
| `policy_number` | str | |
| `named_insured` | str | |
| `loss_date` | date | |
| `report_date` | date | Notice date reported; for correspondence-only packets, the message date |
| `coverage` | `COLLISION` \| `COMPREHENSIVE` | Comprehensive covers hail, animal, glass, theft |
| `cause_of_loss` | str | Short description |
| `vehicle_year` | int | |
| `vehicle_make` | str | |
| `vehicle_model` | str | |
| `vin_reported` | str | From the loss notice; from correspondence if there is no notice |
| `vin_on_estimate` | str | From the repair estimate |
| `injury_indicated_on_form` | bool | Loss-notice checkbox |
| `injury_mentioned_in_correspondence` | bool | Any injury described in an email or narrative |
| `third_party_involved` | bool | |
| `police_report_number` | str | |
| `estimate_number` | str | |
| `estimate_parts` | Decimal | |
| `estimate_labor` | Decimal | Including sublet |
| `estimate_materials` | Decimal | Paint materials |
| `estimate_tax` | Decimal | |
| `estimate_total` | Decimal | Grand total |

## Deterministic claim rules v1

Each result carries rule ID/version, outcome (`PASS`, `FAIL`, `REVIEW`, `NOT_APPLICABLE`), safe
reason code, and the input values it used.

| ID | Rule | Outcome when violated |
|---|---|---|
| CR-001 | Policy number is present and resolves to a tenant policy. | FAIL |
| CR-002 | Loss date is present, not in the future, and not after the report date. | FAIL |
| CR-003 | Loss date is on or after the policy effective date and before its expiration date. | FAIL |
| CR-004 | Policy was not cancelled on or before the loss date. | FAIL |
| CR-005 | `vin_reported` is on the policy. A value that fails the VIN check digit, is within two character edits of a policy VIN, and has the same year and make is `REVIEW` (`VIN_DISCREPANCY`); no match is `FAIL` (`VEHICLE_NOT_ON_POLICY`). | REVIEW or FAIL |
| CR-006 | No earlier non-rejected claim in the tenant has the same policy number, VIN, and loss date. | REVIEW (`DUPLICATE`) |
| CR-007 | Report date is no more than 30 days after the loss date. | REVIEW (`LATE_REPORTING`) |
| CR-008 | Loss date is not within 14 days after policy inception. | REVIEW (`LOSS_NEAR_INCEPTION`) |
| CR-009 | No injury is indicated on the form or mentioned in correspondence. Disagreement adds `INJURY_CONFLICT`. | REVIEW (`INJURY_INDICATED`) |
| CR-010 | Estimate parts + labor + materials + tax equals the total within the configured Decimal tolerance. | FAIL |
| CR-011 | Estimate total is below 75% of the vehicle's actual cash value. | REVIEW (`POSSIBLE_TOTAL_LOSS`) |
| CR-012 | Estimate total is above the applicable deductible (collision or comprehensive). | REVIEW (`AT_OR_BELOW_DEDUCTIBLE`) |
| CR-013 | Configured critical fields meet their confidence threshold. | REVIEW |
| CR-014 | Every page has a supported segment label. | REVIEW |

Thresholds (30 days, 14 days, 75%, fast-track limit, confidence floors) are configuration with
versioned defaults, not constants in prompts.

## Review-queue routing: `claim-routing-v1`

Computed by the application from rule results and candidate values. First match wins.

| Priority | Condition | Queue |
|---|---|---|
| 1 | CR-006 duplicate | `DUPLICATE_REVIEW` |
| 2 | CR-003 or CR-004 failed | `COVERAGE_REVIEW` |
| 3 | CR-005 `VEHICLE_NOT_ON_POLICY` | `COVERAGE_REVIEW` |
| 4 | CR-007 or CR-008 review | `SIU_REFERRAL` |
| 5 | CR-009 review | `BODILY_INJURY_ADJUSTER` |
| 6 | CR-011 review | `TOTAL_LOSS_REVIEW` |
| 7 | CR-012 review | `BELOW_DEDUCTIBLE_NOTICE` |
| 8 | CR-005 `VIN_DISCREPANCY` | `DESK_ADJUSTER` |
| 9 | Third party involved | `DESK_ADJUSTER` |
| 10 | Estimate total above the fast-track limit ($5,000.00 default) | `DESK_ADJUSTER` |
| 11 | CR-001, CR-002, CR-010, CR-013, or CR-014 not passed | `DESK_ADJUSTER` |
| 12 | Otherwise | `FAST_TRACK` |

Rule 11 guarantees that a claim with missing, inconsistent, or low-confidence data can never land
in `FAST_TRACK`.

## Tools added to the registry

| Tool | Input | Output | Limits |
|---|---|---|---|
| `segment_claim_packet` | identity; `inspection_completed=true` | page labels with confidence | Requires `CLAIM_PACKET` classification; once per run |
| `extract_claim_from_text` | identity; attempt 1–3 | extraction-run/candidate IDs, `TEXT`, completeness, reasons | Shares the three-attempt extraction limit |
| `extract_claim_from_visual_content` | identity; attempt 1–3 | as above, `VISUAL` | Shares the three-attempt extraction limit |
| `lookup_policy` | identity; bounded policy number | match flag, policy ID, in-force flag, match quality | Maximum two calls; tenant reference data only |
| `lookup_policy_vehicle` | identity; policy number; bounded VIN | match flag, vehicle ID, match kind (`EXACT`, `NEAR`, `NONE`) | Maximum two calls; tenant reference data only |

## Evaluation

The claims set lives in `test-data/claims`: 20 packets, `policies.csv`, `policy_vehicles.csv`, and
`ground_truth.csv`. Metrics: per-field accuracy, segment-label accuracy, rule agreement, queue
accuracy with a confusion matrix, flag recall, fast-track queue share, and cost/latency per claim.

**Hard gate:** zero false fast-tracks. A claim whose expected queue is not `FAST_TRACK` must never be
assigned `FAST_TRACK`. Quality cannot compensate for a failed gate.

## Deliberately deferred

Outbox payload for approved claims, claim-number issuance, coverage determination, payment,
reserve setting, subrogation, and any automatic approval or denial.
