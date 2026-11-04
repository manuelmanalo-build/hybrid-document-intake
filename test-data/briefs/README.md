# Campaign brief evaluation set

Twenty synthetic client briefs for the fictional agency Meridian & Pike, used by the brief slice
([ADR 0009](../../docs/adr/0009-campaign-brief-document-type-and-domain-packs.md),
[brief scope](../../docs/briefs-slice-scope.md)). Every agency, client, brand, person, email
address, and number is invented.

Regenerate from the repository root (needs poppler's `pdftoppm` for the two scans):

```bash
uv run --with reportlab==5.0.1 --with pillow==12.3.0 python -m scripts.generate_brief_fixtures
```

| File | Contents |
|---|---|
| `packets/B01_brief.pdf` … `B20_brief.pdf` | One packet per brief: agency form, client memo, slide deck, or email, plus some follow-up emails and media plans |
| `statements_of_work.csv` | 11 SOWs: term, status, fees, services in scope, regulated category, account lead |
| `sow_brands.csv` | Brands each SOW covers |
| `lead_times.csv` | Minimum production business days per asset type |
| `ground_truth.csv` | Expected fields, deliverables (JSON), flags, expected queue, and the edge case each brief tests |

## Edge cases

| Brief | Tests | Expected queue |
|---|---|---|
| B01 | Clean agency-form brief | READY_FOR_PLANNING |
| B02 | Clean client memo | READY_FOR_PLANNING |
| B03 | Success metric has no number | NEEDS_CLIENT_INFO |
| B04 | Budget "TBD" | NEEDS_CLIENT_INFO |
| B05 | Budget exceeds remaining SOW fees | SCOPE_REVIEW |
| B06 | Out-of-home is not in the SOW | SCOPE_REVIEW |
| B07 | SOW ends before launch | SCOPE_REVIEW |
| B08 | New brand not on any SOW | SCOPE_REVIEW |
| B09 | Pharma client with label claims | LEGAL_REVIEW |
| B10 | Credit union with an APY claim | LEGAL_REVIEW |
| B11 | :30 video due in 8 business days (needs 25) | RUSH_REVIEW |
| B12 | Follow-up email moves launch from Jan 5 to Nov 30 | RUSH_REVIEW |
| B13 | Follow-up email adds three TikTok cut-downs | TRAFFIC_REVIEW |
| B14 | Email-only resubmission of B01 | DUPLICATE_REVIEW |
| B15 | Degraded scan of an agency form | READY_FOR_PLANNING |
| B16 | Degraded scan of a client memo | READY_FOR_PLANNING |
| B17 | End date before launch | TRAFFIC_REVIEW |
| B18 | Two-slide deck with a media plan | READY_FOR_PLANNING |
| B19 | Email-only brief, no form | READY_FOR_PLANNING |
| B20 | Landing page due exactly at its 15-business-day minimum | READY_FOR_PLANNING |

## Traps

- Harbor & Vine has an expired 2025 SOW and an active 2026 SOW; pick by term, not by client name.
- B12's brief alone looks routine; only the email reveals the timeline risk.
- B14 also flags a timeline risk because it arrived a week later; the duplicate route wins.
- B15 and B16 have no text layer.
