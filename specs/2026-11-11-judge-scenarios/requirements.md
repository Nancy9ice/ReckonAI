# Judge scenarios — scope, decisions, context

## 1. Scope

Let a judge create live transactions covering the main reconciliation outcomes,
then Run reconciliation and Investigate in the UI. One script, one fixed server
endpoint, no credentials handed to judges.

Six scenarios, each a real sandbox order placed through the simulated
marketplace's write side (`sim/live.py`):

| Ref | Case | Expected outcome |
|---|---|---|
| `JUDGE-S1-CLEAN` | Correct 10% commission, `INSTANT` | no exception |
| `JUDGE-S2-COMMISSION` | Marketplace books 15.00 commission, PayPal's fee is 10.00 | `commission_mismatch` |
| `JUDGE-S3-HELD` | `DELAYED`, funds held, not released | no exception |
| `JUDGE-S4-LATE-REFUND` | Marketplace records and ledgers a refund; PayPal's has not been made | `awaiting_evidence` (not an exception) |
| `JUDGE-S5-NO-LEDGER` | Refund made at PayPal, never booked in the ledger | `missing_ledger_adjustment` |
| `JUDGE-S6-DUPLICATE` | Sale credited twice under distinct references | `duplicate_ledger_entry` |

A second, optional step delivers S4's PayPal refund, so a further run
reassesses it (new evidence, then the finding clears).

**Out of scope:** FX and disbursement mismatch (use seeded ORD-0015, 0008 and
0018; the write side has no lever for either), per-judge tenants, a reset.

## 2. Decisions

- **D1. One set per judge, idempotent per name, never cleared.** The judge
  supplies a short name (letters and digits, max 12); refs are
  `JUDGE-<NAME>-S1-CLEAN` and so on. `ensure` creates only what is missing for
  that name, so a judge repeating the script changes nothing, and a new name
  gets its own six orders. Each judge tests their own transactions end to end.
  Spend is bounded by `JUDGE_MAX_SETS` (default 25) and 2 new sets per IP per
  day; past either, the endpoint answers 429 and an existing name still works.
  Deleting is rejected: PayPal records cannot be deleted, and runs, exceptions
  and decisions are append-only.
- **D2. Fixed endpoint, no secret for judges.** `POST /sim/judge/scenarios`
  takes only the judge's name; no amounts, sellers or refs. Hidden (404) unless
  `JUDGE_SCENARIOS_ENABLED`. The existing `/sim/marketplace/live` routes keep
  their `SIM_WRITE_SECRET` guard, untouched.
- **D3. Shared tenant** (`reckonmarket`), orders prefixed `JUDGE-<NAME>-`. A
  judge filters the queue by that prefix; other judges' rows are visible, not
  hidden. Every record keeps the `SIMULATED` endpoint label.
- **D4. The webhook is the primary path; `ingest_now` is the fallback.** The
  script polls status for up to 60s per step and then asks the server to ingest
  what has not arrived. The result states which path each order took. Neither
  path is hidden. Requests stay short: the endpoint never waits.
- **D5. Concurrent calls are safe.** A Postgres advisory lock serialises
  `ensure`; the existing duplicate-ref refusal is the second line.
- **D6. Partial failure resumes.** A failed sandbox call leaves earlier
  scenarios in place; the next call completes the rest. PayPal is called
  first, so nothing is booked for an order PayPal refused.

- **D7. The judge path is a button in the deployed UI, not the script.** Judges
  test the deployed version and will not run anything locally. A header button
  ("Create test transactions") opens a panel with a name field (a random
  default, remembered in the browser). One click calls the same routes, polls
  until the set is in, falls back to `ingest-missing` after 60s, and reports
  done with the six refs and what each should show. A second button delivers
  S4's PayPal refund. The button is shown only when `GET /sim/judge/enabled`
  says the feature is on. The script stays for scripted use; the readme leads
  with the button. All wording is in `copy.ts`.

## 3. Context

- Webhook proven on Render, 2026-11-11 18:31 UTC: signed delivery verified,
  `paypal_event` job completed (a probe order, `PROBE-FE4A14`). That order's
  PayPal side is ingested with no marketplace order. Its effect on a run is
  unknown and checked in `validation.md` §1.
- `capture_live_order` hardcodes `INSTANT`; `refund_order` always refunds at
  PayPal. Both gain a lever (plan group 1).
- A new order is placed now, so it falls in the current daily window and
  carries new evidence; Run reconciliation is permitted. The 300s same-day
  cooldown is shared: a judge may get `cooling_down` after another's run, and
  that run already covers their orders once they are ingested.
- The seeded `ORD-0005` already proves the held-funds classification offline;
  S3 proves it on a live order.

## 4. Unproven until run

- `PAYMENT.CAPTURE.REFUNDED` handling by the webhook job (the fallback covers it).
- A live `DELAYED` order for a connected seller classifies as clean.
- S4 reads `awaiting_evidence` from live data, not only from the seed.
- Held by the operator: setting `JUDGE_SCENARIOS_ENABLED` on Render.

## 5. Amendment: the run cooldown (found in the browser test)

Live-ingestion D8 set a five-minute `RUN_COOLDOWN_SECONDS`. With several judges
it blocks the main flow, not only the refund step: a run by one judge held back
the next judge's click on new orders, and "run, deliver the refund, run again"
hit it every time. D9 says no automatic run trigger exists, so the cooldown only
ever guarded the person's button, and the `nothing_new` rule already refuses a
run without new evidence. **Default lowered to 10 seconds**, enough to absorb a
double-click, not enough to make anyone wait. The cron is unaffected.
