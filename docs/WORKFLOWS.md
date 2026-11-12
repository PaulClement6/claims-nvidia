# The three claim workflows

The line of business of the policy sets the workflow. The checks, the score and the routing are shared (`docs/FRAUD.md`).

These lists are a first reading of a typical claims flow. Paul must confirm them.

## Shared fields of every claim

`policy_number`, `claimant_name`, `incident_date`, `incident_place`, `cause`, `claimed_amount`, `currency`, `documents_present`.

## A: Property / household

| | |
|---|---|
| Causes | Water, fire, theft, breakage |
| Required documents | Repair invoice **or** repair quote; at least one damage photo |
| Required for theft | Police report |
| Optional | Purchase receipts of the damaged items |
| Fields | Damaged items (description, purchase date, value), repair company, invoice number, invoice date, line items, total |
| Straight-through limit | CHF 2,000 |
| Own rules | Police report present if the cause is theft. Photo shows the damage the claim describes (VLM). |

## B: Health / accident

| | |
|---|---|
| Causes | Accident, illness |
| Required documents | Medical invoice; medical report or certificate |
| Fields | Patient name, provider, treatment dates, diagnosis in short, invoice number, line items, total |
| Straight-through limit | CHF 1,000 |
| Own rules | Patient is the insured person. Treatment dates are on or after the incident date. |
| Data | The most sensitive workflow. Medical text must never appear in logs outside the pipeline events. |

## C: Travel

| | |
|---|---|
| Causes | Trip cancellation, luggage, medical cost abroad |
| Required documents | Booking confirmation; cancellation confirmation or receipts (for the cause `luggage`, the airline's luggage report counts too) |
| Required for illness | Medical certificate |
| Fields | Booking reference, departure date, trip price, cancellation date, refund received, cause |
| Straight-through limit | CHF 2,500 |
| Own rules | Cancellation date is between the incident date and the departure date. Claimed amount = trip price − refund. |

## Build order

Travel first (simplest documents), then property, then health.
