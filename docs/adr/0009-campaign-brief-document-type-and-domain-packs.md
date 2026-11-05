# ADR 0009: Campaign-Brief Document Type and Domain Packs

- Status: Accepted
- Date: 2026-11-04

## Context

The platform now handles two governed document types: AP invoices and personal-auto claim packets
(ADR 0008). The next increment demonstrates a marketing-technology use case for an **agency**: client
campaign briefs arrive as forms, memos, slide decks, and email threads, and a traffic or project
manager re-keys them into a work request, checks them against the client's statement of work (SOW),
chases missing information, and assigns the work. The claims slice added its document type by
writing claim-specific code alongside the invoice code. Doing the same again would leave three
parallel silos.

## Decision

1. Add `CAMPAIGN_BRIEF` to the closed document-type set. Invoice and claim behavior is unchanged.
2. Before adding briefs, refactor document types into **domain packs**. A pack declares its candidate
   contract, closed segment labels, prompts and schema versions, deterministic rules, routing policy
   and queues, confidence weights, reference-data lookups, typed correction fields, and UI labels.
   The graph, policy guard, persistence, review API, outbox, and Angular screens dispatch on the pack
   registered for the classified document type. The claim slice becomes the first pack, with no
   behavior change.
3. Brief packets segment pages as `BRIEF`, `CORRESPONDENCE`, `MEDIA_PLAN`, or `OTHER`.
4. A brief candidate is a **work request**: scalar fields with the `ExtractedField` evidence contract
   plus a bounded list of deliverables, each with its own evidence. Values that can disagree across
   documents stay separate fields (`launch_date` from the brief and
   `launch_date_in_correspondence`; brief deliverables and `deliverables_added_in_correspondence`).
5. Reference data is tenant-scoped: statements of work, the brands each SOW covers, and production
   lead times per asset type.
6. Brief rules CB-001 to CB-014 and the `brief-routing-v1` policy in `docs/briefs-slice-scope.md`
   are deterministic and versioned, shared by the advisory tool and authoritative validation.
7. Queues are assigned by the application, never the model. `READY_FOR_PLANNING` is a review
   priority, not an approval: a person still approves every work request.
8. For briefs routed to `NEEDS_CLIENT_INFO`, the agent may **draft** clarifying questions for the
   client. A reviewer edits and sends them; the model never contacts a client.
9. An approved work request emits an outbox event whose payload creates one task per deliverable in
   the client's work-management tool. Delivery stays behind the existing outbox and worker.

## Consequences

- The domain-pack refactor is a separate increment that must keep every invoice and claim test
  green before any brief code lands.
- Adding a fourth document type becomes a configuration, rules, and fixtures exercise.
- Brief money (budgets, SOW fees) uses `Decimal`; dates use ISO calendar dates and business-day
  arithmetic that ignores holidays in v1.

## Alternatives rejected

- Copying the claims code for briefs: three silos, triple maintenance.
- Letting the model judge scope or legal risk: makes routing non-reproducible and crosses the
  authority boundary.
- Sending clarifying questions automatically: the model must not contact clients.

## Amendment (2026-11-05): drafts and the work-request payload

- Clarifying questions are drafted **deterministically** from the brief's failed rules and
  extracted values (no model call in v1), so every question traces to a rule and the same brief
  always yields the same draft. The reviewer edits the draft, sends it from their own email, and
  records the final text; the record and a `CLIENT_QUESTIONS_SENT` audit event (without the body)
  are kept. A model-written draft can replace the template later behind the same review step.
- An approved brief's `DOCUMENT_APPROVED` outbox event adds `document_type` and a `work_request`
  object (`work-request-v1`): client, brand, campaign, resolved SOW, effective launch, end, budget,
  KPI, and one task per deliverable (title, asset type, service, quantity, description, due date,
  and whether it was requested in the brief or in correspondence). Claim events add only
  `document_type`. Invoice events are unchanged.

