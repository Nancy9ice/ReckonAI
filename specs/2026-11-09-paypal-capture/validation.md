# PayPal capture — validation

> How R16's work was proven. 2026-11-09, branch `paypal-capture`.
>
> **This supersedes Block A's `validation.md` §2**, which describes four
> sellers and invented FX figures. That document is left as the record of what
> Block A did.

## 1. What R16 required, and what proves it

| Requirement | Proof | Result |
|---|---|---|
| The PayPal side is real, not invented | 18 fixtures in `fixtures/paypal/`, each a captured sandbox response | **Pass.** 21 captures, 2 refunds, 1 release |
| D12's synthetic rows are gone, not managed | No `_synthetic` marker anywhere; `seed.py` writes no provider table | **Pass.** Asserted in `test_every_replayed_row_links_to_a_paypal_payload` |
| The seed is re-pointed at captured figures | Every gross in `data.py` matches a capture | **Pass.** `test_every_order_has_a_captured_fixture` compares the two sets both ways |
| Hand-checkability survives | §2's table, recomputed by `seed.verify()` through the real resolver | **Pass.** |
| Replay is idempotent | Ingest twice; row counts unchanged | **Pass.** Second run inserts 0 |
| The demo loads on deploy without calling PayPal | `preDeployCommand` runs `--ensure` | **Pass.** `test_the_predeploy_command_seeds_but_never_calls_paypal` |
| The live call cannot break the demo | `try_live_order` swallows everything | **Pass, observed.** No credentials gives a warning and exit 0 |

## 2. The hand-check — every order, from the captured dataset

Two sellers, both settling USD. One rule: tenant default 10%. **A** = aurora
(`C24NAWELTTHXJ`), **B** = bellweather (`E4FUU2CX86ALC`). Every gross below
matches a real capture.

| Order | Seller(s) | Ccy | Gross | Commission | Entitlement | Notes |
|---|---|---|---|---|---|---|
| ORD-0001 | A, B | USD | 160.00 | 16.00 | A 90.00; B 54.00 | **mission worked example** |
| ORD-0002 | A | USD | 50.00 | 5.00 | A 45.00 | |
| ORD-0003 | B | USD | 120.00 | 12.00 | B 108.00 | |
| ORD-0004 | A | USD | 80.00 | 8.00 | A 72.00 | |
| ORD-0005 | B | EUR | 200.00 | 20.00 | B 180.00 | **non-fault**: `DELAYED`, held |
| ORD-0006 | A, B | USD | 75.00 | 7.50 | A 27.00; B 40.50 | |
| ORD-0007 | A | USD | 33.33 | 3.33 | A 30.00 | 3.3330 → 3.33 |
| ORD-0008 | A, B | EUR | 121.54 | 12.15 | B 36.02; A 73.37 | **non-fault**: the rounding artefact |
| ORD-0009 | B | USD | 59.97 | 6.00 | B 53.97 | 5.9970 → 6.00; open dispute |
| ORD-0010 | A | USD | 45.00 | 4.50 | A 40.50 | |
| ORD-0011 | A | USD | 70.00 | 7.00 | A 63.00 | refund 20.00, reversal 2.00 |
| ORD-0012 | B | EUR | 120.00 | 12.00 | B 108.00 | |
| ORD-0013 | B | USD | 110.00 | 11.00 | B 99.00 | **F1** refund 30.00, not in ledger |
| ORD-0014 | A | USD | 200.00 | **30.00** | A 170.00 | **F2** PayPal took 20.00 |
| ORD-0015 | B | EUR | 300.00 | 30.00 | B 270.00 | **F3** recorded 262.00 USD |
| ORD-0016 | A | USD | 90.00 | 9.00 | A 81.00 | **F4** credited twice |
| ORD-0017 | A | USD | 60.00 | 6.00 | A 54.00 | **F5** no PayPal refund |
| ORD-0018 | B | EUR | 150.00 | 15.00 | B 135.00 | **F6** ledger credits 120.00 |

Net clean positions, refunds and reversals included:

| Seller | Ccy | Position | Derivation |
|---|---|---|---|
| aurora | USD | **349.50** | 90 + 45 + 72 + 27 + 30 + 40.50 + (63 − 20 + 2) |
| aurora | EUR | **73.37** | ORD-0008 |
| bellweather | USD | **256.47** | 54 + 108 + 40.50 + 53.97 |
| bellweather | EUR | **324.02** | 180 + 36.02 + 108 |

Total expected commission: **USD 67.33**, **EUR 44.15**.

Hand-written in `data.EXPECTED_POSITIONS`, recomputed from the rows by
`seed.verify()`, asserted against this table by `tests/test_sim_seed.py`.
Three independent derivations.

### The two non-faults, from PayPal's own arithmetic

**ORD-0005** — `DELAYED`, funds held, never released. The capture reads
`COMPLETED` while the seller is unpaid, which is the trap; `disbursement_state`
is `funds_held`.

**ORD-0008** — the rounding artefact, no longer constructed:

```
PayPal, per capture : 36.02 EUR -> 39.24 USD
                      73.37 EUR -> 79.93 USD   = 119.17 USD
the 109.39 total, converted once               = 119.18 USD
```

One cent, and **neither figure is wrong**. A single-seller order cannot
produce this, which is why ORD-0008 has two — the original single-line version
could never have exercised the guard it existed for.

## 3. The six faults, with re-derived impacts

| # | Order | Fault | Expected | Impact |
|---|---|---|---|---|
| F1 | ORD-0013 | refund real, ledger silent | `missing_ledger_adjustment` | USD 27.00 |
| F2 | ORD-0014 | retained 30.00, PayPal took 20.00 | `commission_mismatch` | USD 10.00 |
| F3 | ORD-0015 | recorded 262.00 USD, settled 294.16 | `fx_discrepancy` | USD 32.16 |
| F4 | ORD-0016 | one sale credited twice | `duplicate_ledger_entry` | USD 90.00 |
| F5 | ORD-0017 | PayPal's refund has not arrived | `awaiting_evidence` — **not an exception** | USD 0.00 |
| F6 | ORD-0018 | ledger credits 120.00 of 135.00 | `disbursement_mismatch` | EUR 15.00 |

F3's gap is 32.16 against ORD-0008's one cent — three orders of magnitude, so
no threshold can confuse them, and Phase 11 forbids introducing one.

## 4. The four ingester rules, each tested in the failing direction

| Rule | Passes on | Must fail on | Result |
|---|---|---|---|
| Fee from the capture-time snapshot | the fixtures | `order_at_capture` removed — records 3.00 where PayPal took 11.00 | **Both** |
| Payee from the readback | the fixtures | asserted absent from the snapshot directly | **Both** |
| Refunds through the order | the fixtures | the refund endpoint's own breakdown is `null` | **Both** |
| Conversion only where reported, source is the net | the fixtures | checked arithmetically against PayPal's rate, so a switch to gross fails | **Both** |

## 5. Principle checks

| Check | Result |
|---|---|
| `fx_conversion.fee` is `NULL`, never `0` (P3) | **Pass**, all 6 |
| `capture.paypal_fee` `NULL`; `refund.paypal_fee` a measured `0.00` | **Pass** — both claims in one dataset |
| `disbursement.released_at` carries no timestamp (P3) | **Pass** |
| Every replayed row links to a `paypal`-source payload (P7) | **Pass**, all 5 tables |
| Every payload records its read path | **Pass** |
| No `_synthetic` marker survives anywhere | **Pass** |
| Every capture resolves to a seller | **Pass**, 21 of 21 |
| No credential in `render.yaml` | **Pass** — all three `PAYPAL_*` are `sync: false` |

## 6. Mechanical gates

| Gate | Result |
|---|---|
| `uv run pytest` | **1258 passed** |
| `uv run ruff check` | **Pass** |
| Migration reverses, autogenerate empty | Unchanged — this block adds no table |
| CI on the branch | Pending push |

## 7. Found by running it, not by reading

Each cost something, and each is encoded so it is not rediscovered.

**`PayPal-Request-Id` is an idempotency key.** Hardcoded, a second order
asking for 100.00 came back as the earlier 10.00. A capture built that way
produces eighteen copies of its first order, and every count agrees with
itself.

**A capture's `platform_fees` mutates after a partial refund, and differs by
seller.** See D5.

**Verifying a seller's grant from the tracking-id lookup reports a false
negative.** It returns a summary with no `oauth_integrations`, scored as six
refusals on sellers that had all six — twice, because the first fix guessed
the wrong response shape.

**Onboarding a second seller needs a clean browser session.** Signed in as the
first, it dead-ends at `Cannot GET /unifiedonboarding/after-login` and creates
nothing. Different cause from §8.4's `/entry` dead-end; both cost a single-use
token.

**`--ensure` commits**, so running it against a developer's database breaks
the next test run — observed immediately. Debt 4 of Block A in a new form.

## 8. Not proven here

- That the faults are **detected** — Block C, against the manifest.
- That the two non-faults are **not flagged** — Block C.
- **The live order's success path.** `--live` is proven to fail safely; that a
  real order ingests cleanly through the same code as a fixture is unverified,
  because `Settings` reads the process environment and the credentials are in
  a file this session may not read. One run before the demo depends on it.
- That the deployed `--ensure` works on Render. It works locally; the deploy
  has not run.

## Related

- `requirements.md` — the decisions these checks enforce
- `specs/2026-11-08-replan/requirements.md` — R6, R15, R16
- `specs/2026-11-08-block-a-marketplace-connector/validation.md` — superseded §2
