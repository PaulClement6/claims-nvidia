# Mock data

All data is invented. No real person, no real policy. The documents must look real: letterheads, tables, stamps, and some as scans or phone photos.

## Customers and policies

| Customer | Policy | Line | Period | Limit |
|---|---|---|---|---|
| C-1001 Anna Keller | TRV-2026-0417 | Travel | 2026-01-01 to 2026-12-31 | CHF 5,000 |
| C-1001 Anna Keller | HH-2025-1182 | Household | 2025-03-01 to 2027-02-28 | CHF 20,000 |
| C-1002 Marco Bianchi | HH-2024-0933 | Household | 2024-06-01 to 2027-05-31 | CHF 15,000 |
| C-1002 Marco Bianchi | TRV-2026-0721 | Travel | 2026-02-01 to 2027-01-31 | CHF 5,000 |
| C-1003 Lukas Meier | ACC-2026-2210 | Accident | 2026-10-20 to 2027-10-19 | CHF 4,500 |
| C-1003 Lukas Meier | HH-2026-0102 | Household | 2026-01-15 to 2027-01-14 | CHF 10,000 |

## The three demo claim files

### 1. Travel, clean → `straight_through`

Anna Keller, policy TRV-2026-0417. Trip cancelled because of illness. Incident 2026-08-03. Claimed CHF 1,500.

- Booking confirmation: reference, departure 2026-08-10, trip price CHF 1,840.
- Cancellation confirmation: cancelled 2026-08-04, refund CHF 340.
- Medical certificate: Anna Keller, unfit to travel from 2026-08-03.

Expected: all fields right, no signal, score 0.

### 2. Property, review → `handler_review`

Marco Bianchi, policy HH-2024-0933. Water damage in the kitchen. Incident 2026-09-14. Claimed CHF 3,200.

- Repair invoice: 4 line items, total CHF 3,200, dated 2026-09-22.
- Two damage photos.

Expected: no fraud signal. The amount is above the limit of CHF 2,000, so a handler reviews.

### 3. Health, fraud → `investigation`

Lukas Meier, policy ACC-2026-2210. Sprained ankle. Incident 2026-10-25. Claimed CHF 4,450.

- Medical invoice: patient "Jonas Meier", dated 2026-10-18, line items add up to CHF 1,450, total shows CHF 4,450. PDF last written by an editing tool, modified on 2026-11-02.
- Medical report: Lukas Meier, sprained ankle on 2026-10-25. Clean.

Expected signals: R003, R004, R005, T001, T002, T003, C001. Score above 60. Fraud report with every signal.

## The document kit (D14, issue #23)

The three claim files above and six more scenarios: 24 documents in 9 folders of `data/mock/`, and 9 hard versions in `data/mock/hard/`. `data/mock/index.json` is the source of this table (the generator writes it). A visitor picks an account, types the story of a row, uploads its documents and types "done". `backend/tests/test_document_kit.py` plays every row in `local` mode and checks the exact signals and outcome.

| Folder | Account, policy | Story to type | Docs | Signals | Outcome |
|---|---|---|---|---|---|
| `claim-1-travel` | Anna, TRV-2026-0417 | My trip was cancelled because I was ill. It began on 3 August 2026 and I claim CHF 1,500. | 3 | none | `straight_through` |
| `claim-2-property` | Marco, HH-2024-0933 | Water damage in the kitchen on 14 September 2026. I claim CHF 3,200. | 3 | none | `handler_review` |
| `claim-3-health` | Lukas, ACC-2026-2210 | I sprained my ankle in an accident on 25 October 2026. I claim CHF 4,450. | 2 | C001, R003, R004, R005, T001, T002, T003 | `investigation` |
| `kit-anna-luggage` | Anna, TRV-2026-0417 | My luggage was lost on arrival in Porto on 14 May 2026. I claim CHF 787. | 4 | none | `straight_through` |
| `kit-anna-theft` | Anna, HH-2025-1182 | Burglary at my flat on 9 October 2026: the front door was forced. I claim CHF 2,640. | 5 | none | `handler_review` |
| `kit-marco-travel` | Marco, TRV-2026-0721 | I had to cancel my trip to Sicily on 12 July 2026 because of a family emergency. I claim CHF 2,300. | 2 | none | `straight_through` |
| `kit-marco-travel` | same | The same story, but I claim CHF 3,200 (the full trip price, not price minus refund of CHF 900). | | C003 | `handler_review` |
| `kit-marco-bathroom` | Marco, HH-2024-0933 | A leaking pipe flooded my bathroom on 3 November 2026. I claim CHF 4,740. | 2 | T001, T002, T003 | `investigation` |
| `kit-lukas-clean` | Lukas, ACC-2026-2210 | I fell off my bike on 6 November 2026 and hurt my wrist. I claim CHF 840. | 2 | none | `straight_through` |
| `kit-lukas-household` | Lukas, HH-2026-0102 | A football broke the living-room window on 14 September 2026. I claim CHF 1,260. | 1 | R001 (no photo) | `handler_review` |

The luggage report is a new document type, `luggage_report`: the airline report of a lost bag. It counts for the "cancellation confirmation or receipt" requirement of a travel claim with the cause `luggage`, and only then. `kit-marco-bathroom` is the invoice of claim 2 made again with a printed total (CHF 4,740) above its lines (CHF 1,740), saved by an editing tool two days after its creation: 60 points, the lowest score of an `investigation`.

**Hard versions** (`hard/`, for the reader, not for a claim story): the same content in other bytes. Six scans (150 DPI, grey, turned 1 to 2 degrees, noise and blur, a one-page PDF that holds only the image: no text layer) of `claim-2-property/repair-invoice`, `kit-lukas-clean/medical-invoice`, `claim-1-travel/booking-confirmation`, `claim-1-travel/medical-certificate`, `kit-anna-theft/police-report`, `kit-anna-luggage/receipt-jacket-shoes`. Three phone photos (a page on a wooden table, tilted, unevenly lit, JPEG) of the repair invoice, the receipt and the luggage report. Each has an `expected.json` with the fields of its source plus `variant` (`scan` or `photo`) and `source_document`. A scan loses the PDF metadata, so T001 and T002 cannot fire on it: that is the case for the reader, not for the checks.

The damage photos stay drawings (D35): a kitchen floor (two), a forced door, a bathroom floor.

## Test set

Each document has an `expected.json`: the fields a handler would type. `index.json` has the signals and the outcome of each story. The evaluation compares the pipeline output with these files. To add a scenario, read the header of `scripts/mockdocs/kit.py`.
