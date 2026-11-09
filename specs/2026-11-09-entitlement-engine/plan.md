# Entitlement engine — plan

> Task groups, in order. Each group is one pass: implementation and tests
> written together, per R2. The operator runs the suite between groups and
> pastes failures only.
>
> Decisions are in `requirements.md`; merge gate in `validation.md`.

## 1. Result shapes

`src/reckonai/engine/entitlement.py`: the frozen dataclasses from D2 —
`SellerEntitlement`, `OrderEntitlement` — plus the `OrderLike`, `OrderItemLike`
and `CaptureLike` protocols the engine reads its inputs through.

No arithmetic in this group. No `__post_init__` that recomputes a field.

Tests: the dataclasses are frozen; an ORM row and a plain test dataclass both
satisfy each protocol.

## 2. Expected entitlement

`expected_entitlement(order, items, rules, sellers)` in the same module.

Per seller: sum their line totals into a gross share, resolve the rule at the
order's `placed_at`, commission is `(share * rate).quantize()`, entitlement is
`share - commission`. Order commission is the sum of the per-seller figures,
never a separate calculation over the order total.

Tests, hand-computed: `ORD-0001` (160.00 across two sellers at 10% gives A
90.00, B 54.00, commission 16.00); `ORD-0007` (33.33 at 10% gives 3.3330
quantized to 3.33); `ORD-0009` (59.97 gives 5.9970 quantized to 6.00); a stated
0% rule resolving normally; no applicable rule raising
`NoApplicableCommissionRule` rather than returning zero (D7); a naive `at`
raising.

## 3. Refunds and commission reversal

`src/reckonai/engine/refunds.py`.

`attribute_refund()` resolves the refund to one seller through its capture, per
D1, and raises `UnattributableRefund` when it cannot.
`apply_refund(order_entitlement, refund)` returns a new `OrderEntitlement`
with that seller's share, commission and entitlement reduced, and the refund
recorded in the provenance. `allocate_refund_across_sellers()` is the explicit
proportional fallback, over `reckonai.money.allocate`.

Tests, hand-computed: [[mission]]'s worked example second half — a 40.00 refund
on A's item leaves A 54.00, B 54.00, commission 12.00; `ORD-0011` (20.00 refund,
2.00 reversal); `ORD-0013` (30.00, 3.00); a refund split across one seller's
items at two different rates; an unattributable refund raising rather than
splitting; `allocate` across three and four sellers leaving no residue;
a refund exceeding its capture raising.

## 4. Multi-currency position

`src/reckonai/engine/positions.py`: `expected_positions()` returning one
`SellerPosition` per (seller, currency), fee-exclusive always and fee-inclusive
only when every contributing capture states a fee, per D3.

Tests, hand-computed: a seller trading in two currencies yields two rows and
never a mixed-currency sum; one capture with an absent fee makes the
fee-inclusive position `None` and lists that capture id; a refund's explicit
`paypal_fee` of `0.00` is a stated zero and does **not** make the position
unknown; a capture carrying no `paypal_fee` key does.

## 5. Public surface

Re-export from `src/reckonai/engine/__init__.py` and extend its module
docstring — it currently says Phase 10 "adds" these, which becomes past tense.
Update `__all__`.

Test: everything the later blocks import resolves from `reckonai.engine`.

## 6. Move `seed._verify_order`'s calculation (D5)

Delete the entitlement arithmetic from `src/reckonai/sim/seed.py` and have
`_verify_order` call `expected_entitlement()`, comparing its result against the
hand-written literals in `data.py` exactly as now. The item-sum and order-total
invariants stay in `seed.py` — they are consistency checks on the seed, not
engine calculations.

Test: `tests/test_sim_seed.py` still passes unchanged. That is the point of the
group — eighteen hand-tabulated orders agreeing with the engine on day one.

## 7. The debt-4 guard (D6)

A session-scoped fixture in `tests/conftest.py` that fails
`tests/test_migrations.py` loudly if tenant rows exist when it runs, rather
than depending on alphabetical collection order.

Does not enable randomisation or `-n auto`; makes the precondition explicit so
a later block can.

## 8. Gate and commit

Run the full suite with the compose database up and `DATABASE_URL` exported —
without it the database-backed tests skip locally and fail hard in CI.
Work `validation.md`'s table and record what each check actually returned.
`/code-review medium` per R2. One commit for the block.

No deploy. R2's single deploy was spent on Block A's merge; this block needs
none.
