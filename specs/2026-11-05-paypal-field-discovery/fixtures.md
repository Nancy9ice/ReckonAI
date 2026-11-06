# Phase 0 — Sandbox fixture transactions

**Branch:** `phase-0/paypal-field-discovery`
Task groups: `./plan.md` Group 2 · Findings: `./notes.md`

Fixture ids created in the PayPal sandbox so the probe is re-runnable against
known transactions (`plan.md` Group 2 step 6). Every id below was confirmed
retrievable through the API with `scripts/probe_paypal.py`.

Sandbox base URL: `https://api-m.sandbox.paypal.com`
Account: merchant `C24NAWELTTHXJ`, app `APP-9C108748UB224713D`
Seeded: 2026-11-06

---

## Status against Group 2

| # | Required fixture | Status |
|---|---|---|
| 1 | Same-currency payment, captured | **Created** |
| 2 | Cross-currency payment, captured | **Created** — and it answers Q1 and Q3 |
| 3 | Partial refund against one capture | **Created** |
| 4 | Dispute | **Not created** — needs a buyer-initiated transaction id; see below |
| 5 | Payout batch, 2+ items | **Created and succeeded** |

---

## Created fixtures

### Captures and orders

| Fixture | Order id | Capture id | Amount |
|---|---|---|---|
| Same-currency | `4U503564VJ470545D` | `8RW04003P5846784X` | USD 120.00 |
| **Cross-currency** | `476677408G744563T` | `3VG831163X0730421` | EUR 95.50 |

Both reached `COMPLETED` in a single call, card-funded, with no browser step.
This worked only because the **new** sandbox account has card processing
enabled; the previous one returned
`PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING`.

### Refund

| Fixture | Refund id | Amount | Against |
|---|---|---|---|
| Partial refund | `3435955072478500X` | USD 30.00 of 120.00 | capture `8RW04003P5846784X` |

### Payout batches

Two, deliberately kept — one successful and one denied. Having both terminal
states is what made the fee finding in `notes.md` possible.

| Batch id | Items | Status | Note |
|---|---|---|---|
| **`55LJJ7SBMXHY6`** | 2 | **`SUCCESS`** | the usable fixture |
| `PGHX46EKCCH3Q` | 2 | `DENIED` | `RECEIVER_COUNTRY_NOT_ALLOWED` |

`55LJJ7SBMXHY6` detail, USD 65.00 total, completed `2026-11-06T03:27:52Z`:

| Item id | Amount | Fee | Status | `transaction_id` | `time_processed` |
|---|---|---|---|---|---|
| `X6F34PNAC88VY` | USD 40.00 | USD 0.25 | `SUCCESS` | `66920443Y0691861P` | `2026-11-06T03:27:44Z` |
| `78AC23U6P8TNN` | USD 25.00 | USD 0.25 | `SUCCESS` | `9UY161371P094335N` | `2026-11-06T03:27:44Z` |

`batch_header.fees` is USD 0.50, equal to the sum of the item fees.

This makes `transaction_id` and `time_processed` observable: both are populated
on a successful item and `null` on a failed one, so each is
`present-but-unreliable` with the condition "only on a successfully processed
payout item".

The recipient was a sandbox personal account supplied for this purpose. Its
address is deliberately **not recorded here** — `plan.md` Group 3 step 3 keeps
buyer PII out of committed artifacts, and the probe re-runs from the batch id,
so the address is not needed for reproducibility.

The denied batch `PGHX46EKCCH3Q` used a recipient from the previous sandbox
setup and failed with a populated `errors` object: "As the recipient's country
can't receive payments, this transaction is not allowed."

### Disputes

Not created. `POST /v1/customer/disputes` is available and does accept creation,
but requires a valid **buyer** transaction id, which a merchant-side capture id
does not satisfy. See `notes.md`, "disputes CAN be created through the sandbox
API". Recorded as a stated reason rather than a blank row, per `validation.md`
M3.

---

## Void fixtures from the previous account

The first seeding run used a different sandbox account, merchant
`N8KT8ZBME5W8W` / app `APP-44H8550779412924Y`. Those credentials were replaced.
Confirmed unreachable under the current credentials, both returning `HTTP 404`:

| Void order id | Was |
|---|---|
| `4G356687JX410952M` | USD 120.00, awaiting approval |
| `25668501L1551774L` | EUR 95.50, awaiting approval |

Void payout batches from that account: `M5PZ8H8VNKYFC`, `LY9LEES5KP2WN`,
`CLVH9GE2YPHAE`, `4LCBHN39YZWZG`, `FDJ7BF2N4JNXW`, `25EXZQ4HVKVEY`,
`HJ9CQ6MTD865C`. **Do not probe these** — a `404` against them is an artefact of
the account change, not evidence about the API, and treating it as the latter is
precisely the error `notes.md` warns about for `403`.

---

## Re-running the probe against these fixtures

```bash
uv run scripts/probe_paypal.py --record-type captures --id 3VG831163X0730421 \
  --out probe.raw.json
uv run scripts/probe_paypal.py --record-type captures --id 8RW04003P5846784X \
  --out probe.raw.json
uv run scripts/probe_paypal.py --record-type refunds --id 3435955072478500X \
  --out probe.raw.json
uv run scripts/probe_paypal.py --record-type orders --id 476677408G744563T \
  --out probe.raw.json
uv run scripts/probe_paypal.py --record-type payouts --id PGHX46EKCCH3Q \
  --out probe.raw.json
```

Record types needing no id — `disputes`, `transactions`, `balances` — can be
probed without any fixture.

**Capture `3VG831163X0730421` is the most important fixture in this phase.** It
is the only record carrying real provider conversion evidence, and therefore the
only basis on which the `fx_conversion` rows of `specs/paypal-fields.md` can be
judged `present`.
