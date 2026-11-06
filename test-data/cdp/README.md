# CDP onboarding evaluation set

Twenty synthetic data-source onboarding requests for the fictional data practice Meridian & Pike
Data, used by the CDP onboarding slice ([ADR 0010](../../docs/adr/0010-cdp-onboarding-document-type.md),
[scope](../../docs/cdp-onboarding-scope.md)). Every client, person, and value is invented; the one
sample extract with unmasked identifiers uses `example.com` addresses and 555 phone numbers.

Regenerate from the repository root (needs poppler's `pdftoppm` for the scanned packet):

```bash
uv run --with reportlab==5.0.1 --with pillow==12.3.0 python -m scripts.generate_cdp_fixtures
```

| File | Contents |
|---|---|
| `packets/D01_onboarding.pdf` … `D20_onboarding.pdf` | Onboarding form, data dictionary, sample extract, and some privacy addenda and emails |
| `data_agreements.csv` | 9 data agreements: term, status, permitted regions, lawful bases, sensitive categories, maximum retention |
| `canonical_attributes.csv` | 28 canonical profile and event attributes with type, personal-data class, and identity-key flag |
| `connectors.csv` | 7 connectors with supported cadences and minimum build business days |
| `ground_truth.csv` | Expected request fields, mappings (JSON), scanner findings on sample pages, flags, expected queue, and edge case |

## Edge cases

| Request | Tests | Expected queue |
|---|---|---|
| D01 | Clean Shopify order feed with a masked sample | READY_TO_BUILD |
| D02 | Health data the agreement permits, with documented consent | READY_TO_BUILD |
| D03 | Cadence and go-live left as TBD | NEEDS_CLIENT_INFO |
| D04 | No identity key: sales cannot be tied to a customer | MAPPING_REVIEW |
| D05 | A field with no canonical attribute | MAPPING_REVIEW |
| D06 | A date mapped to a text attribute | MAPPING_REVIEW |
| D07 | Real-time cadence on a file-drop connector | MAPPING_REVIEW |
| D08 | New client with no data agreement | AGREEMENT_REVIEW |
| D09 | Agreement expires before go-live | AGREEMENT_REVIEW |
| D10 | EU data under a US-only agreement | AGREEMENT_REVIEW |
| D11 | Form says no personal data; sample has unmasked emails and phones | PRIVACY_REVIEW |
| D12 | Sensitive accessibility data the agreement does not permit | PRIVACY_REVIEW |
| D13 | 10-year retention over a 7-year maximum | PRIVACY_REVIEW |
| D14 | Consent basis with no consent mechanism | PRIVACY_REVIEW |
| D15 | Go-live in 6 business days; Snowflake shares need 10 | RUSH_REVIEW |
| D16 | Email moves go-live earlier than the CRM connector allows | RUSH_REVIEW |
| D17 | Email adds a field the dictionary does not list | ONBOARDING_REVIEW |
| D18 | Email-only resubmission of D01 | DUPLICATE_REVIEW |
| D19 | Degraded scan of the form and dictionary | READY_TO_BUILD |
| D20 | Go-live exactly at the 3-business-day SFTP minimum | READY_TO_BUILD |

## Traps

- Harbor & Vine has an expired 2025 agreement alongside the active one.
- D11's form, dictionary, and "Personal?" column all say no; only the sample proves otherwise.
- D02 shows the rules are not a blanket ban: Kestrel Health's agreement permits health data with consent.
- Contact emails in forms and correspondence are business contact details; scanner findings count
  only on sample-extract pages, though redaction applies everywhere.
