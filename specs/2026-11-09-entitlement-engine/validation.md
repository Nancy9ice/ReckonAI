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
| 1 | The [[mission]] worked example passes exactly | `test_mission_worked_example` — 160.00 across two sellers at 10% gives A 90.00, B 54.00, commission 16.00; then a 40.00 refund on A's item gives A 54.00, B 54.00, commission 12.00 | |
| 2 | Allocation across three or more sellers leaves no rounding residue | `test_refund_allocation_leaves_no_residue`, three and four sellers, parts summed against the whole | |
| 3 | No database handle, HTTP client or clock read in the module | `tests/test_money_purity.py`, already watching `reckonai/engine/` | |
| 4 | Hand-computed fixtures cover each refund and commission policy | The fixture table in §2 | |
| 5 | Expected commission is a figure comparable to PayPal's platform fee | `test_commission_is_per_seller_not_a_residue` — the per-seller commission equals that purchase unit's `platform_fees` value for every captured order | |
| 6 | A capture fee is never defaulted to zero | `test_a_capture_with_no_stated_fee_is_recorded_as_unknown` and `test_a_stated_fee_leaves_nothing_unknown`. **No fee-inclusive position is published** — D3 revised, with the measurement in §3 | |

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

## 3. Agreement with the hand-check table

`specs/2026-11-09-paypal-capture/validation.md` §2 is eighteen orders and four
per-seller positions, hand-written and independently derived.

| Check | Proof | Result |
|---|---|---|
| Every order's commission and per-seller entitlement matches §2 | `tests/test_sim_seed.py` unchanged, now running through the engine (D5) | |
| Net positions match: aurora USD 349.50, EUR 73.37; bellweather USD 256.47, EUR 324.02 | `data.EXPECTED_POSITIONS` against `seed.verify()` | |
| Commission totals match: USD 67.33, EUR 44.15 | same | |
| The engine is the only implementation | no entitlement arithmetic remains in `seed.py` — grep for `rule.rate` outside `reckonai/engine/` | |
| **`net_amount == gross_amount - platform_fees` on every capture, zero residual** — the measurement D3 rests on | re-run the arithmetic over `fixtures/paypal/*.json` | **Pass, 21 of 21** |

Three independent derivations survive the move: the literals in `data.py`, the
table in §2, and the engine. D5a's independence is preserved because the
literals were not regenerated from the engine.

## 4. The two non-faults are not made exceptions here

This block classifies nothing, so neither can be flagged yet. What it must not
do is compute a figure that *forces* a later flag.

| Check | Proof | Result |
|---|---|---|
| `ORD-0005` (`DELAYED`, funds held) gets an ordinary entitlement | held funds are a healthy state; the engine reads no disbursement state | |
| `ORD-0008`'s two correct-but-conflicting figures both survive | the engine computes in EUR and performs no conversion | |

## 5. Debt 4 (D6)

| Check | Proof | Result |
|---|---|---|
| `tests/test_migrations.py` fails loudly, not silently, if tenant rows exist | the session-scoped guard, exercised by leaving a row in place deliberately | |
| The suite still passes in its current collection order | full run | |
| Randomisation is still not enabled | no `pytest-randomly`, no `-n auto` in `pyproject.toml` or CI | |

## 6. Standing gates

| Gate | Result |
|---|---|
| `tests/test_money_purity.py` — no `float`, no I/O import in the money or engine path | |
| `tests/test_model_registry.py` — no model module missing from `reckonai/models/__init__.py` (no new models here, so this is a no-change assertion) | |
| No emoji anywhere in the diff ([[tech-stack]] §13) | |
| `uv run ruff check` clean | |
| `uv run mypy` clean — strict over `src/reckonai/engine/`, the scope set in `[tool.mypy]` (D9). **This line previously read "`ruff` and the type checker", naming a checker the project did not have** — the same error Block A's §9 recorded and corrected | |
| `uv.lock` re-locked and committed, since CI asserts it is unchanged | |
| Full suite green, count recorded against Phase 3's 1117 | |
| `/code-review medium` (R2 allows it on Blocks B and C) | |

## 7. Debts handed on

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

## 8. Not proven here, and who proves it

- That the faults are **detected** and the clean baseline produces **zero
  exceptions** — Block C. This block supplies the expected figures the
  comparison needs; it makes no comparison.
- That the FX rounding guard holds — Block C, because it is a comparison
  against PayPal's reported settlement.
- That `awaiting_evidence` is reached for F5 — Block C.
- That the figures score against the manifest — Block G.

## 9. Merge condition

Every result cell in §1 through §6 filled and passing, the full suite green in
CI, §7's debts carried forward with their owners named, and §8 left to its
owners. No deploy — R2's single deploy was spent on Block A's merge and this
block needs none.
