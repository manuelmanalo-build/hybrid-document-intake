# CDP Onboarding Slice Scope

Governed by [ADR 0010](adr/0010-cdp-onboarding-document-type.md) and the domain-pack design in
[ADR 0009](adr/0009-campaign-brief-document-type-and-domain-packs.md). The constitution and earlier
scopes still apply unless this document says otherwise.

## Outcome

A client's request to onboard a data source into the CDP run by the fictional data practice
Meridian & Pike Data crosses the same durable graph as invoices, claims, and briefs: personal-data
scanning and redaction, segmentation, extraction of an onboarding request with proposed field
mappings and evidence, data-agreement and canonical-schema lookups, deterministic CD rules, a
versioned queue assignment, and application-owned human review. Every request requires a human
decision; every mapping is confirmed by a person.

## Pipeline flow

```
upload → inspect → classify (CDP_ONBOARDING)
       → scan_onboarding_packet      deterministic PII scan + redaction (no model)
       → segment_onboarding_packet   page labels
       → extract_onboarding_from_text | extract_onboarding_from_visual_content
       → lookup_data_agreement       (optional, advisory)
       → validate_candidate          CD rules, advisory
       → submit_recommendation
parent graph → authoritative CD rules → cdp-routing-v1 decision → review task
review → approve → onboarding_plan outbox event (one build task per workstream)
```

## Page segments

`ONBOARDING_FORM`, `DATA_DICTIONARY`, `SAMPLE_EXTRACT`, `PRIVACY_ADDENDUM`, `CORRESPONDENCE`,
`OTHER`; one label per page, at most 20 pages.

## Personal-data scanning and redaction

`scan_onboarding_packet` is a deterministic tool, not a model call. It reads the bounded text of
every page and detects direct identifiers with versioned patterns (`pii-scanner-v1`): email
addresses, phone numbers, US Social Security numbers, and payment card numbers (Luhn-checked). It
returns, per page, which identifier types it found and how many; it never returns the values.

- All later model calls in the run receive the redacted text (`[EMAIL]`, `[PHONE]`, `[SSN]`,
  `[CARD]`). Page images are sent only for pages without a text layer.
- Evidence excerpts are redacted again before persistence, so no identifier from the packet is
  stored in the intake database.
- Findings are stored per page (`pii_scan_findings`) and read by rules CD-007 and CD-008.
- A page later labeled `SAMPLE_EXTRACT` that has no text layer could not be scanned; CD-008 treats it
  as unverified.

## Onboarding-request contract (`cdp-onboarding-v1`)

Request-level fields, each an `ExtractedField`:

| Field | Type | Notes |
|---|---|---|
| `client_name` | str | |
| `agreement_number_stated` | str | DPA number as written, if any |
| `received_date` | date | Form date or email date |
| `source_name` | str | Human name of the feed, for example "Shopify orders (US store)" |
| `connector_type` | closed set | `SHOPIFY`, `SALESFORCE_CRM`, `SFTP_CSV`, `SNOWFLAKE_SHARE`, `GA4`, `BRAZE`, `REST_WEBHOOK` |
| `refresh_cadence` | closed set | `REALTIME`, `HOURLY`, `DAILY`, `WEEKLY`, `ONE_TIME` |
| `data_region` | closed set | `US`, `EU`, `UK`, `CA`, `APAC`; where the data subjects are |
| `lawful_basis` | closed set or null | `CONSENT`, `CONTRACT`, `LEGITIMATE_INTEREST` |
| `consent_mechanism` | str | How consent was collected, when the basis is consent |
| `declares_no_personal_data` | bool | The client's statement on the form |
| `declares_sensitive_data` | bool | |
| `retention_days` | int | |
| `estimated_records` | int | |
| `requested_go_live` | date | From the form |
| `go_live_in_correspondence` | date | Only from a later email that states a different date |
| `data_owner` | str | Client contact accountable for the source |

Field mappings: up to 200 items, each with `source_field`, `source_type` (`STRING`, `INTEGER`,
`DECIMAL`, `BOOLEAN`, `DATE`, `TIMESTAMP`), `proposed_attribute` (a canonical attribute name or
null when the model finds no match), `client_marked_personal` (bool), source page, evidence, and
confidence. Fields that appear only in correspondence are a separate list,
`fields_added_in_correspondence`.

## Reference data

| Table | Contents |
|---|---|
| Data agreements | Agreement number, client, term, status, permitted data regions, permitted lawful bases, permitted sensitive categories, maximum retention days |
| Canonical attributes | Attribute name, entity (`PROFILE` or `EVENT`), data type, personal-data class (`NONE`, `DIRECT_IDENTIFIER`, `QUASI_IDENTIFIER`, `SENSITIVE`), identity-key flag |
| Connectors | Connector type, supported cadences, minimum build business days |

Source types are compatible with attribute types by a fixed table: `STRING` with `STRING`, `EMAIL`,
and `PHONE`; `INTEGER` and `DECIMAL` with `NUMBER`; `DATE` with `DATE`; `TIMESTAMP` with
`TIMESTAMP` and `DATE`; `BOOLEAN` with `BOOLEAN`.

## Onboarding rules v1

| ID | Rule | Outcome when violated |
|---|---|---|
| CD-001 | The client has a data agreement (the stated one if it is the client's, otherwise the one in force at go-live). | FAIL `AGREEMENT_NOT_FOUND` |
| CD-002 | The agreement is active at go-live and permits the request's data region. | FAIL `AGREEMENT_NOT_ACTIVE` or `REGION_NOT_PERMITTED` |
| CD-003 | Source name, connector, cadence, data region, go-live date, and at least one mapping are present. | REVIEW `REQUEST_INCOMPLETE` |
| CD-004 | The connector exists in the catalog and supports the requested cadence. | REVIEW `CONNECTOR_UNSUPPORTED` |
| CD-005 | At least one mapping targets an identity-key attribute. | REVIEW `NO_IDENTITY_KEY` |
| CD-006 | Every proposed attribute exists in the canonical schema and is type-compatible with its source type. | REVIEW `MAPPING_INVALID` |
| CD-007 | Every mapping to a personal-data attribute is marked personal by the client, and a client that declares no personal data maps none. | REVIEW `UNDECLARED_PERSONAL_DATA` |
| CD-008 | The scanner found no unmasked direct identifiers on sample-extract or data-dictionary pages, and every sample-extract page could be scanned. Contact details in forms and correspondence are business contact data and do not count, though they are redacted too. | REVIEW `UNMASKED_IDENTIFIERS_IN_PACKET` or `SAMPLE_NOT_SCANNABLE` |
| CD-009 | Personal data has a lawful basis the agreement permits; a consent basis names its mechanism. | REVIEW `LAWFUL_BASIS_MISSING` |
| CD-010 | Sensitive attributes, or a sensitive-data declaration, are permitted by the agreement. | REVIEW `SENSITIVE_DATA_NOT_PERMITTED` |
| CD-011 | Retention is within the agreement's maximum. | REVIEW `RETENTION_EXCEEDS_AGREEMENT` |
| CD-012 | Business days from receipt to the earliest go-live in the packet meet the connector's minimum. | REVIEW `TIMELINE_AT_RISK` |
| CD-013 | Correspondence does not change the go-live date or add fields. | REVIEW `CORRESPONDENCE_CONFLICT` |
| CD-014 | No earlier non-rejected request has the same agreement, connector, and source name. | REVIEW `DUPLICATE_REQUEST` |
| CD-015 | Critical fields (client, connector, source name, go-live) meet their confidence threshold, and every page has a supported label. | REVIEW |

Personal-data rules (CD-007 to CD-011) evaluate form mappings and correspondence-added fields
together. Business days ignore holidays in v1.

## Queue routing: `cdp-routing-v1` (first match wins)

| Priority | Condition | Queue |
|---|---|---|
| 1 | CD-014 | `DUPLICATE_REVIEW` |
| 2 | CD-001 or CD-002 failed | `AGREEMENT_REVIEW` |
| 3 | Any of CD-007 to CD-011 | `PRIVACY_REVIEW` |
| 4 | CD-003 | `NEEDS_CLIENT_INFO` |
| 5 | CD-004, CD-005, or CD-006 | `MAPPING_REVIEW` |
| 6 | CD-012 | `RUSH_REVIEW` |
| 7 | CD-013 or CD-015 | `ONBOARDING_REVIEW` |
| 8 | Otherwise | `READY_TO_BUILD` |

Privacy outranks everything except duplicates and missing agreements: a request with personal-data
findings can never reach a queue where someone might build it before privacy has looked.

## Tools added to the registry

| Tool | Input | Output | Limits |
|---|---|---|---|
| `scan_onboarding_packet` | identity; `inspection_completed=true` | per-page identifier types and counts; redaction applied | `CDP_ONBOARDING` only; once per run; deterministic |
| `segment_onboarding_packet` | identity; `scan_completed=true` | one label per page | Requires the scan; once per run |
| `extract_onboarding_from_text` | identity; attempt 1–3 | extraction-run/candidate IDs, completeness, reasons | Requires segmentation; shares the extraction counter |
| `extract_onboarding_from_visual_content` | identity; attempt 1–3 | as above, `VISUAL` | Same; for scanned packets |
| `lookup_data_agreement` | identity; bounded client name; optional agreement number | match flag, agreement reference, status | Maximum two calls; read-only |

## Persistence

New tables: `data_agreements`, `canonical_attributes`, `cdp_connectors`, `pii_scan_findings`
(page, identifier type, count; no values), and `onboarding_intake_keys`. Page segments, routing
decisions, re-routes, and review reuse the shared tables; their label and queue constraints widen to
include the onboarding values. Reference data loads with an audited, idempotent loader and a local
Compose job, like claims and briefs.

## Decision confidence: `decision-confidence-cdp-v1`

Classification 0.10, required-field completeness 0.15, critical-field confidence 0.15, evidence
coverage 0.10, agreement match 0.10, mapping validity (share of mappings passing CD-006) 0.20,
privacy clearance (CD-007 to CD-011 all passing) 0.10, rule score 0.10.

## Review

The generic review API and screens serve the pack: queue rows summarize client, source, connector,
go-live, and mapping count; the workbench shows the mapping table with each row's proposed
attribute, type check, personal-data class, and source page, and outlines undeclared personal data,
scanner findings, and changed go-live dates. Reviewers correct request-level fields and individual
mapping targets (to a canonical attribute or "do not ingest"). The client-questions panel drafts
questions for incomplete requests (missing cadence, region, lawful basis, consent mechanism).

## Approved onboarding plans

Approval emits `DOCUMENT_APPROVED` with an `onboarding_plan` (`onboarding-plan-v1`): client,
agreement, source, connector, cadence, region, lawful basis, retention, go-live, the confirmed
mapping specification (source field → canonical attribute, entity, personal-data class, identity
key), excluded fields, and build tasks: connector setup, mapping implementation, identity-resolution
configuration (listing the identity keys), consent configuration when personal data is present, and
QA against the redacted sample.

## Evaluation

`test-data/cdp`: 20 synthetic packets with agreement, attribute, and connector reference data and
ground truth, generated by `scripts/generate_cdp_fixtures.py`. A live diagnostic will report page
labels, request-field accuracy, mapping precision and recall (source field → attribute), queue
agreement, and two hard gates: zero false `READY_TO_BUILD`, and every packet with scanner findings
in `PRIVACY_REVIEW` (unless it is a duplicate or has no valid agreement).

## Deliberately deferred

Automatic CDP configuration, identity-resolution tuning, schema evolution of the canonical model,
non-US identifier patterns (national IDs, IBANs), holiday calendars, and any automatic approval or
client contact.
