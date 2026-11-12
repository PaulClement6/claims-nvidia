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

## Test set

Each claim file has an `expected.json`: the fields a handler would type, the signals and the outcome. The evaluation compares the pipeline output with this file. The intern grows the set: more files per workflow, scans, phone photos, and more seeded fraud cases.
