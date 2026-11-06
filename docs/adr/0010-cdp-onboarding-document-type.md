# ADR 0010: CDP Onboarding Document Type

- Status: Accepted
- Date: 2026-11-06

## Context

Agencies and consultancies that run a client's customer data platform (CDP) receive requests to
onboard new data sources: a Shopify order feed, a CRM contact export, a loyalty file on SFTP. The
request arrives as a packet: an onboarding form, a data dictionary, a sample extract, sometimes a
privacy addendum, and an email thread. A data engineer re-keys it, maps every source field to the
CDP's canonical profile and event model, confirms the identity keys, checks the client's data
processing agreement (DPA), and decides whether privacy, legal, or a solutions engineer must look
before anything is built.

Two properties make this domain different from briefs and claims:

1. The packet itself can contain personal data: sample extracts often carry real, unmasked email
   addresses and phone numbers, even when the form says the feed has none.
2. Mapping a field to a canonical attribute is a judgment, and a wrong mapping (a birth date mapped
   to a phone number, an email field left unmarked as personal data) silently corrupts identity
   resolution or consent enforcement downstream.

## Decision

1. Add `CDP_ONBOARDING` as the fourth document type, implemented as a domain pack (ADR 0009). No
   shared module gains a branch for it; `test_domain_packs.py` enforces this.
2. Pages are segmented as `ONBOARDING_FORM`, `DATA_DICTIONARY`, `SAMPLE_EXTRACT`,
   `PRIVACY_ADDENDUM`, `CORRESPONDENCE`, or `OTHER`.
3. **Personal data never reaches the model or the database from a sample extract.** Before any
   model call, a deterministic scanner reads the bounded text of the packet, records what kinds of
   direct identifiers it found and on which pages, and replaces each match with a typed token
   (`[EMAIL]`, `[PHONE]`, `[SSN]`, `[CARD]`). The model sees only redacted text; evidence excerpts
   are redacted again before persistence. A page whose sample extract has no text layer cannot be
   scanned and is treated as unverified.
4. Whether unmasked identifiers were present is a **scanner fact**, not a model judgment. Rules read
   the scanner's findings; the model cannot set or clear them.
5. The candidate is an **onboarding request**: request-level fields with the `ExtractedField`
   evidence contract plus a bounded list of field mappings, each with the source field, source type,
   the model's proposed canonical attribute, the client's personal-data declaration, and evidence.
   Mappings are proposals. Rules validate them against the tenant's canonical schema, and a person
   confirms them in review.
6. Values that can disagree across documents stay separate: `requested_go_live` from the form and
   `go_live_in_correspondence`; form mappings and `fields_added_in_correspondence`; the client's
   `declares_no_personal_data` and the scanner's findings.
7. Tenant reference data: data agreements (term, permitted regions, lawful bases, sensitive
   categories, maximum retention), the canonical attribute schema (entity, type, personal-data
   class, identity key), and the connector catalog (supported cadences, minimum build lead time).
8. Rules CD-001 to CD-015 and the `cdp-routing-v1` policy in `docs/cdp-onboarding-scope.md` are
   deterministic and versioned. `READY_TO_BUILD` is a review priority, not an approval.
9. An approved request emits the shared `DOCUMENT_APPROVED` outbox event with an
   `onboarding_plan` (`onboarding-plan-v1`): the confirmed mapping specification and one build task
   per workstream (connector, identity resolution, consent configuration, mapping, QA).

## Consequences

- The pack adds a shared PII redactor that other packs may adopt; claims and briefs do not change.
- Mapping review becomes a first-class human step with its own queue.
- The hard evaluation gate is stricter than for briefs: zero false `READY_TO_BUILD`, and zero
  requests with personal-data findings routed anywhere but privacy review.

## Alternatives rejected

- Letting the model decide whether a sample contains personal data: non-reproducible, and it
  requires sending that personal data to the model.
- Persisting sample rows for reviewers: copies personal data into the intake database.
- Auto-applying mappings to the CDP: crosses the authority boundary; every mapping is reviewed.
