# ADR 0008: Claim-Packet Document Type

- Status: Accepted
- Date: 2026-10-21

## Context

The first slice proves governed AP-invoice intake. The next increment demonstrates the same
platform on personal-auto property and casualty (P&C) first notice of loss (FNOL). A claim
arrives as one PDF that mixes several documents: a loss notice, a repair estimate, and sometimes
a police report abstract or customer correspondence. Some packets have no loss notice, and some
pages are image-only scans. The first-slice scope deferred additional document types, and the
constitution lists a closed nine-tool registry, so this change needs a recorded decision.

Insurers expect claims to be sorted into handling queues (fast track, desk adjuster, coverage
review, special investigations, bodily injury, total loss). That sorting must stay explainable
and must not let a model approve or deny anything.

## Decision

1. Add `CLAIM_PACKET` to the closed document-type set. `AP_INVOICE` behavior is unchanged.
2. A claim packet is one immutable upload. Its pages are labeled by a typed segmentation tool with
   a closed set: `LOSS_NOTICE`, `REPAIR_ESTIMATE`, `POLICE_REPORT`, `CORRESPONDENCE`, `OTHER`.
3. Extraction produces one `ClaimPacketCandidate` whose fields keep the existing `ExtractedField`
   evidence contract, including source page. Values that can disagree across documents are kept
   as separate fields (for example `vin_reported` and `vin_on_estimate`;
   `injury_indicated_on_form` and `injury_mentioned_in_correspondence`). The model never merges
   or resolves a conflict; deterministic rules see both values.
4. Claim business rules CR-001 to CR-014 in `docs/claims-slice-scope.md` are versioned,
   deterministic, and shared by the advisory `validate_candidate` tool and the authoritative
   parent-graph validation, exactly as BR-001 to BR-012 are.
5. The application computes a **review queue** with the versioned `claim-routing-v1` policy. Each
   decision is an immutable record tied to one authoritative validation run and read by the
   application-owned review task. The model cannot select, suggest,
   or change it. The recommendation contract is unchanged, and `AUTO_APPROVE` remains invalid.
6. **Every claim still reaches human review.** `FAST_TRACK` is a priority queue with a short
   review form, not a touchless path. Measured "touchless rate" is replaced by fast-track queue
   share and false-fast-track count.
7. The registry gains claim tools: `segment_claim_packet`, `extract_claim_from_text`,
   `extract_claim_from_visual_content`, `lookup_policy`, and `lookup_policy_vehicle`. The policy
   guard makes a tool executable only for its document type: invoice tools require
   `AP_INVOICE`, claim tools require `CLAIM_PACKET`. Shared tools stay shared.
8. Policy and vehicle reference data are tenant-scoped application tables, like suppliers and
   purchase orders. Duplicate-claim detection is a deterministic rule, not a tool.
9. Claim money (estimate amounts, deductibles, actual cash value) uses `Decimal`.

## Consequences

- The constitution, tool catalog, evaluation plan, and checklist change in the same increment
  that introduces each tool or rule; nothing becomes executable before it is registered,
  guarded, and tested.
- Claims add new tables, an Angular claim review form, and a claims evaluation set. The
  integration outbox payload for approved claims is deferred to a later increment.
- Queue assignment is reproducible from stored candidate values, rule results, and the routing
  version, so any routing decision can be explained and replayed for audit.

## Alternatives rejected

- A separate claims repository: duplicates the audited intake, review, and outbox machinery that
  is the main value of this platform.
- Letting the model choose the queue: makes routing non-reproducible and moves an operational
  decision across the authority boundary.
- One upload per document inside a claim: does not match how FNOL packets actually arrive.
- Touchless fast-track approval: violates the constitution and is not needed to show value.
