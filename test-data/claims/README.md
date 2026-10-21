# Claims evaluation set

Twenty synthetic personal-auto FNOL packets for the fictional carrier Northfield Mutual Insurance
Co., used by the claims slice ([ADR 0008](../../docs/adr/0008-claim-packet-document-type.md),
[claims scope](../../docs/claims-slice-scope.md)). Every name, address, phone number (555
numbers), plate, VIN, shop, and police report number is invented and redistributable.

| File | Contents |
|---|---|
| `packets/C01_packet.pdf` … `C20_packet.pdf` | One PDF per claim, 2–3 pages: loss notice, repair estimate, and some with a police report abstract or customer email |
| `policies.csv` | 22 policies: term, status, cancellation date, collision/comprehensive deductibles, liability limits |
| `policy_vehicles.csv` | Vehicles per policy with VIN, plate, and actual cash value (ACV) |
| `ground_truth.csv` | Expected extracted fields, validation results, flags, expected queue, reason, and the edge case each claim tests |

`doc_types` in `ground_truth.csv` lists each packet's documents in page order (`notice`,
`estimate`, `police`, `email`), so it is also the page-segmentation answer key. Expected queues
use the lowercase names of the `claim-routing-v1` queues in the claims scope.

## Edge cases

| Claim | Tests | Expected queue |
|---|---|---|
| C01 | Hail, labor-only paintless dent repair estimate | fast_track |
| C02 | Clean collision (original of a duplicate pair) | fast_track |
| C03 | Third party and police report, estimate over $5,000 | desk_adjuster |
| C04 | Estimate exactly equals the $1,000 deductible | below_deductible_notice |
| C05 | Estimate 81% of ACV | total_loss_review |
| C06 | Loss 12 days before policy inception | coverage_review |
| C07 | Policy cancelled before the loss | coverage_review |
| C08 | Newly purchased vehicle not on the policy, disclosed in email | coverage_review |
| C09 | Transposed VIN on the notice (check digit fails); estimate shows the correct VIN | desk_adjuster |
| C10 | Form says no injury; follow-up email reports whiplash | bodily_injury_adjuster |
| C11 | Injury checkbox and an uninsured other driver | bodily_injury_adjuster |
| C12 | Reported 78 days after the loss | siu_referral |
| C13 | Loss 5 days after policy inception | siu_referral |
| C14 | Email-only resubmission of C02 | duplicate_review |
| C15 | Degraded image-only scan | fast_track |
| C16 | Clean single vehicle, estimate over $5,000 | desk_adjuster |
| C17 | Degraded scan, glass claim, $250 comprehensive deductible | fast_track |
| C18 | Claim on the second vehicle of a two-vehicle policy | fast_track |
| C19 | Email-only theft notice with police report | fast_track |
| C20 | Estimate exactly $5,000.00 | fast_track |

## Traps

- Look-alike insureds in `policies.csv` (Daniel vs. Danielle Okafor, Marisol Vega vs. Marisol
  Vega-Ortiz, Trevor Haines vs. an expired Trevor Haynes). Match on policy number, never name.
- Every VIN passes the standard check digit except the C09 typo.
- Duplicate detection depends on processing claims in `report_date` order; C14 is the later one.
- C15 and C17 have no text layer; they need the visual extraction path.
- Estimates reconcile to the cent: parts + labor (including sublet) + paint materials + 8% tax on
  parts and materials = grand total.

## Provenance

The set was produced by a standalone deterministic generator (seeded VINs and estimates, a
reference routing engine, and reportlab rendering). Porting that generator into `scripts/` under
this repository's Ruff rules is an open checklist item; until then these files are the
authoritative fixtures and `evaluation/tests/test_claims_fixtures.py` guards their integrity.
