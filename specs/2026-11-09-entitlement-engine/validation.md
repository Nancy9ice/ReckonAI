# Entitlement engine — validation

> How to know this block succeeded and can be merged. Filled in as each check
> runs; a check with no recorded result is not a passed check.
>
> The suite needs the compose database up and `DATABASE_URL` exported
> (`readme.md` §0). Without them the database-backed tests skip locally and
> fail hard in CI, which is deliberate.

## 1. Phase 10's exit criteria, and what proves each

[[roadmap]] Phase 10. All six bind.

| # | Criterion | Proof | Result |
|---|---|---|---|
| 1 | The [[mission]] worked example passes exactly | `test_mission_worked_example_first_half` and `test_mission_worked_example_second_half` — 160.00 across two sellers at 10% gives A 90.00, B 54.00, commission 16.00; then a 40.00 refund on A's item gives A 54.00, B 54.00, commission 12.00, with B untouched | **Pass** |
| 2 | Allocation across three or more sellers leaves no rounding residue | `test_allocation_across_three_or_more_sellers_leaves_no_residue`, parametrised over 3, 4 and 5 sellers and over `0.01`, `0.02`, `33.33`, `100.00`; plus `test_split_by_rate_parts_sum_exactly` | **Pass.** `0.01` across three equal shares is the real case |
| 3 | No database handle, HTTP client or clock read in the module | `tests/test_money_purity.py`, already watching `reckonai/engine/` | **Pass**, unchanged and not weakened |
| 4 | Hand-computed fixtures cover each refund and commission policy | The fixture table in §2 | **Pass**, 16 rows |
| 5 | Expected commission is a figure comparable to PayPal's platform fee | `test_the_commission_equals_paypals_recorded_platform_fee` — reads the captured payloads and compares the engine's commission against the `platform_fees` PayPal recorded on each purchase unit | **Pass, 15 of 15 clean purchase units.** Faulty orders excluded: F2 is a wrong commission in the marketplace's books, and including ORD-0014 would assert the engine reproduces a fault |
| 6 | A capture fee is never defaulted to zero | `test_a_capture_with_no_stated_fee_is_recorded_as_unknown` and `test_a_stated_fee_leaves_nothing_unknown`. **No fee-inclusive position is published** — D3 revised, with the measurement in §3 | **Pass** |

**Criterion 5 was nearly missed.** This table first named a test that did not
exist, and the test that did (`test_commission_is_summed_per_seller_...`)
asserts the *structural* claim only — that the order commission is built by
summing per-seller figures. It never compared anything to a provider record, so
the criterion would have been ticked on a test that could not fail it. The
check above reads `fixtures/paypal/` directly, and is the only test in this
block that touches provider data.

**Test counts:** `test_entitlement` 22, `test_refunds` 29, `test_positions` 14,
`test_order_dependence_guard` 4, with `test_sim_seed` 14 and
`test_commission_rules` 25 still passing unchanged. **Full suite 1372**,
against Phase 3's 1117 and Block A's run.

## 2. Hand-computed fixture coverage

Each row is a fixture with its expected figures written by hand, not derived
from the code under test.

| Case | Source | Covers |
|---|---|---|
| 160.00, two sellers, 10% | `ORD-0001` | the worked example, first half |
| 40.00 refund on A's item | fixture only | the worked example, second half; D1 attribution |
| 33.33 at 10% | `ORD-0007` | 3.3330 quantized down to 3.33 |
| 59.97 at 10% | `ORD-0009` | 5.9970 quantized up to 6.00 |
| 20.00 refund of 70.00, 2.00 reversal | `ORD-0011` | a seeded refund against a real PayPal record |
| 30.00 refund of 110.00, 3.00 reversal | `ORD-0013` | a refund absent from the ledger, computed the same way |
| 121.54 EUR, two sellers | `ORD-0008` | a non-USD multi-seller order |
| one seller, two currencies | fixture only | two `SellerPosition` rows, never a mixed sum |
| refund across one seller's items at two rates | fixture only | the proportional-within-attribution path (D1 case 1) |
| a refund naming no capture | fixture only | `UnattributableRefund` raised, not split (D1 case 2) |
| a stated 0% rule | fixture only | a stated zero resolves (D7) |
| no applicable rule | fixture only | `NoApplicableCommissionRule` raised, not a zero rate (D7) |
| a capture with no `paypal_fee` key | every capture in the dataset | the capture id is recorded as unknown (D3) |
| a refund with `paypal_fee: "0.00"` | `ORD-0011` | a stated zero leaves nothing unknown (D3) |
| a known and an unknown fee side by side | fixture only | **the position is identical either way** — the measurement that revised D3 |

### Why criterion 2 is fixture-only, and should stay that way

Raised in session as a possible gap — the dataset's largest order has two
sellers, so "allocation across three or more" is never exercised against real
provider records. **Checked, and there is nothing to close.**

`reckonai.money.allocate` has exactly two callers in all of `src/`, both in
`engine/refunds.py`: `allocate_refund_across_sellers` and `split_by_rate`. And
**nothing in `src/` calls either of those** — only tests do.

That follows from D1. Cross-seller allocation is reachable only from an
order-level refund naming no capture, and neither source produces one: PayPal
issues a refund against one capture, which has one payee, and every
`RefundSpec` carries a `seller_ref`. So `attribute_refund` raises instead, and
the proportional path is a caller's explicit choice.

A three-seller captured order would therefore exercise nothing extra — a refund
in one still attributes to a single capture. What would activate that code is
an *unattributed* refund, which no capture produces. Capturing one would cost a
sandbox order and a re-derived §2 table to cover a path real data cannot reach.

Residue does not arise elsewhere either: commission is `share * rate` per
seller, quantized independently, so no whole is being split. ORD-0008's
one-cent artefact is FX, not allocation.

**Block C should not re-raise this.** If a tenant ever has a genuine
order-level refund, the path activates and these fixtures are what covers it.

## 3. Agreement with the hand-check table

`specs/2026-11-09-paypal-capture/validation.md` §2 is eighteen orders and four
per-seller positions, hand-written and independently derived.

| Check | Proof | Result |
|---|---|---|
| Every order's commission and per-seller entitlement matches §2 | `tests/test_sim_seed.py` unchanged, now running through the engine (D5) | **Pass, 14 of 14, no literal changed** |
| Net positions match: aurora USD 349.50, EUR 73.37; bellweather USD 256.47, EUR 324.02 | `data.EXPECTED_POSITIONS` against `seed.verify()` | **Pass** |
| Commission totals match: USD 67.33, EUR 44.15 | same | **Pass** |
| The engine is the only implementation | no entitlement arithmetic remains in `seed.py` — grep for `rule.rate` outside `reckonai/engine/` | **Pass.** One hit, `data.rate_for`, a test convenience returning a rate and performing no entitlement arithmetic; its own docstring says it is "**not** the resolution the engine performs" |
| **`net_amount == gross_amount - platform_fees` on every capture, zero residual** — the measurement D3 rests on | re-run the arithmetic over `fixtures/paypal/*.json` | **Pass, 21 of 21** |

Three independent derivations survive the move: the literals in `data.py`, the
table in §2, and the engine. D5a's independence is preserved because the
literals were not regenerated from the engine.

## 4. The two non-faults are not made exceptions here

This block classifies nothing, so neither can be flagged yet. What it must not
do is compute a figure that *forces* a later flag.

| Check | Proof | Result |
|---|---|---|
| `ORD-0005` (`DELAYED`, funds held) gets an ordinary entitlement | held funds are a healthy state; the engine reads no disbursement state, and `verify()` covers ORD-0005 as a clean order | **Pass** |
| `ORD-0008`'s two correct-but-conflicting figures both survive | the engine computes in EUR and performs no conversion; `test_a_non_usd_order_is_computed_in_its_own_currency` pins B 36.02 and A 73.37 | **Pass** |

## 5. Debt 4 (D6)

| Check | Proof | Result |
|---|---|---|
| `tests/test_migrations.py` fails loudly, not silently, if tenant rows exist | `no_committed_marketplace_rows`, requested by `at_base`. **Function-scoped, not session-scoped** — stronger than the debt proposed, since it runs immediately before each downgrade and so also catches a module that commits mid-session | **Pass, observed for real.** A tenant row was planted by hand, `test_migrations` failed with the intended message naming the cause, the fix and debt 4, and the row was removed; `tests/test_order_dependence_guard.py` pins both directions without committing anything |
| The suite still passes in its current collection order | full run | **Pass, 1372** |
| Randomisation is still not enabled | no `pytest-randomly`, no `-n auto` in `pyproject.toml` or CI | **Confirmed, no hits.** The guard makes the failure legible; it does not make the suite safe to randomise |

## 6. Standing gates

| Gate | Result |
|---|---|
| `tests/test_money_purity.py` — no `float`, no I/O import in the money or engine path | **Pass** |
| `tests/test_model_registry.py` — no model module missing from `reckonai/models/__init__.py` (no new models here, so this is a no-change assertion) | **Pass** |
| No emoji anywhere in the diff ([[tech-stack]] §13) | **Pass**, scanned across every file the branch touches |
| `uv run ruff check` clean | **Pass** |
| `uv run mypy` clean — strict over `src/reckonai/engine/`, the scope set in `[tool.mypy]` (D9). **This line previously read "`ruff` and the type checker", naming a checker the project did not have** — the same error Block A's §9 recorded and corrected | **Pass**, 6 source files |
| `uv.lock` re-locked and committed, since CI asserts it is unchanged | **Done** in `22b3910` |
| Full suite green, count recorded against Phase 3's 1117 | **Pass, 1372** after the review fixes |
| `/code-review medium` (R2 allows it on Blocks B and C) | **Done.** Six findings, all six fixed — §7 |

## 7. Review findings, and what each cost

`/code-review medium` over `main...HEAD`, 2026-11-09. Six findings, **all six
legitimate and all six fixed** — none was argued down. Each was reproduced with
a probe script before being accepted, and each now has a regression test.

| # | Where | Finding | Fix |
|---|---|---|---|
| 1 | `refunds.py` `apply_refund` | **Repeated partial refunds drove the commission negative and left a phantom entitlement.** Per-refund reversals each round up: twenty 0.05 refunds of a 1.00 share at 10% reverse `quantize(0.005) = 0.01` each, summing to 0.20 against a commission of 0.10. Final state was `gross_share 0.00`, `commission -0.10`, `entitlement +0.10` | The reversal is now the delta of a **cumulative** figure, `commission_for(refunded_after) - commission_for(refunded_before)`, with `refunded_gross` carried on the line. Reaches exactly zero on a full refund |
| 2 | `positions.py` | **Unknown-fee capture ids were silently dropped** when no entitlement row matched their `(seller, currency)`, and the result then reported `fee_is_known` as `True` | Those keys now get a row with a zero expected position and the ids listed |
| 3 | `refunds.py` `attribute_refund` | A refund naming a capture **not among those supplied** fell through to the weaker `refund.seller_id`, reporting `via="refund_seller_id"` as though no link existed. Also printed "capture None is not among the captures supplied" when `capture_id` was `None` | New `CaptureNotSupplied`, a subclass of `UnattributableRefund` so existing handlers still catch it. Message corrected |
| 4 | `refunds.py` `apply_refund` | **No idempotency guard**, although `refunds_applied` held exactly what was needed. Applying the same attribution twice double-reduced the line | New `RefundAlreadyApplied` |
| 5 | `refunds.py` ×2 | `allocate` raises `InvalidAmount` from the money layer when weights sum to zero, so a caller catching `EngineError` missed it — escaping the module's own contract | Caught and re-raised as `EngineError` in both call sites |
| 6 | `refunds.py` `split_by_rate` | No counterpart to `RefundExceedsShare`: a refund larger than the shares it splits returned reversals exceeding the commissions taken | The same bound now applies |

**Finding 1 is the one that mattered.** It is a [[mission]] P3 violation produced
by the engine itself — a fabricated figure asserting the marketplace owed a
seller 0.01 on an order refunded in full — and Phase 11 would have raised it as
a discrepancy on a correct order, which is the exact false positive its hardest
criterion forbids. **Nothing already written would have caught it:**
`RefundExceedsShare` still held, the `share == commission + entitlement`
invariant still held, and `test_two_refunds_apply_in_sequence` happened to use
amounts that divide evenly.

Worth recording that the per-refund reversal was not a careless choice — D4
argues for it explicitly, because PayPal reports a per-refund reversal and the
figure has to be comparable to it. The cumulative form preserves that: for a
single refund it is identical, which
`test_a_single_refund_still_reverses_exactly_what_paypal_reports` pins.

**Three non-findings were checked and cleared**, recorded so they are not
re-examined: the `TYPE_CHECKING` `__init__` on `Money` is runtime-inert under
`slots=True`; `_gross_share`'s `categories.pop()`-then-`if categories` logic is
correct for 0, 1 and n categories; and the seed's move to the engine is safe
for the current dataset, since no order has one seller spanning two categories.

**+15 regression tests**, suite 1357 → **1372**.

## 8. Debts handed on

**Five mypy errors outside `engine/`, left reported rather than suppressed.**
D9 scopes the gate to `engine/`, so these do not fail CI. Counted here so the
next block inherits a number rather than a surprise.

| Where | Error | Reading |
|---|---|---|
| `sim/seed.py:506` | `RuleT` cannot be `CommissionRule` `[type-var]` | **Not a defect.** `Mapped[X]` against a Protocol member `X`; see D9 |
| `sim/seed.py:574` | `list[SellerLedgerEntry]` is not `Iterable[LedgerEntry]` | Same limitation |
| `models/base.py:192` | `composite()` expects `Callable[..., Money]`, got one returning `Money \| None` | Real, and the `_money_or_none` factory Phase 2 added for nullable pairs. Worth a look by whoever next touches `models/base.py` |
| `connector/ingest.py:341,437` | `"type" has no attribute "tenant_id"` (x2), plus a missing annotation | Real, and probably a `type[Model]` variable that wants a narrower annotation. Block C or D |
| `money/allocation.py:126` | unary `-` on `Literal['n','N','F'] \| int` | Real but benign: `Decimal.as_tuple().exponent` is typed as that union, and the value is an `int` for any finite Decimal, which the code has already guaranteed |

**The protocol gap D8 found can recur.** The runtime `isinstance` tests check
attribute **presence, not type**, and D9 records why the static check cannot
cover the ORM boundary. A third consumer of these protocols would find the next
missing field the way Phase 10 found `fixed` — by being wrong first.

## 9. Not proven here, and who proves it

- That the faults are **detected** and the clean baseline produces **zero
  exceptions** — Block C. This block supplies the expected figures the
  comparison needs; it makes no comparison.
- That the FX rounding guard holds — Block C, because it is a comparison
  against PayPal's reported settlement.
- That `awaiting_evidence` is reached for F5 — Block C.
- That the figures score against the manifest — Block G.

## 10. Merge condition

Every result cell in §1 through §6 filled and passing, the full suite green in
CI, §7's review findings all fixed, §8's debts carried forward with their
owners named, and §9 left to its owners. No deploy — R2's single deploy was spent on Block A's merge and this
block needs none.
