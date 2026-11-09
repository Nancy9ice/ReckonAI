# Entitlement engine (Phase 10, Block B) — requirements

> Scope, decisions and context. 2026-11-09, branch `entitlement-engine`.
>
> The deterministic financial core: expected seller entitlement, refund
> allocation, commission adjustment, multi-currency position. Pure functions,
> no I/O ([[tech-stack]] §6).
>
> Read first: `specs/2026-11-09-paypal-capture/validation.md` §2 (the figures
> this engine's output is checked against) and
> `specs/2026-11-08-block-a-marketplace-connector/requirements.md` §3 (Block
> A's handoff), **noting §3's seller list is out of date** — `vendor-citrine`
> and `vendor-dunlin` no longer exist, and there is one commission rule, the
> tenant's 10%.
>
> `plan.md` exists here at the operator's request, against R1 which drops it.

## 1. What this block delivers

New modules in `src/reckonai/engine/`:

| Module | Contents |
|---|---|
| `entitlement.py` | `expected_entitlement()` and the result dataclasses |
| `refunds.py` | refund attribution, commission reversal, the proportional fallback |
| `positions.py` | per-seller, per-currency expected position across orders |

`reckonai/engine/__init__.py` re-exports the public names. `rules.py` and
`ledger.py` are unchanged and consumed as-is.

## 2. Decisions

### D1. A refund's seller comes from its capture, never from an allocation

Confirmed against the captured fixtures rather than assumed.

PayPal **cannot report a cross-seller refund**. A multi-seller order is one
`purchase_unit` per seller, each with its own `payee`, its own
`payment_instruction.platform_fees` and its own capture — `ORD-0001` carries
PU-1 at 100.00 USD with a 10.00 fee to aurora and PU-2 at 60.00 with a 6.00 fee
to bellweather. A refund is issued against **one capture**, and resolves to
that capture's single payee through `links[rel=up]`. `ORD-0011`'s refund states
20.00 gross and a 2.00 `platform_fees` reversal; `ORD-0013`'s states 30.00 and
3.00.

The marketplace side agrees independently: every `RefundSpec` in
`reckonai.sim.data` already carries a `seller_ref`.

So attribution is the primary path and it is not a policy choice — it is the
only shape either source can report. [[mission]]'s worked example follows from
it: a 40.00 refund on A's item reduces A to 54.00 and leaves B at 54.00.

**Proportional allocation is kept, for two narrower cases**, and
`reckonai.money.allocate` is what performs both:

1. **Within one seller's attribution**, splitting the refund across that
   seller's items when they resolve to *different* commission rates — the
   commission reversal is then not a single rate times the refund. The current
   dataset has one item per seller per order, so this path is covered by
   hand-computed fixtures, not by seeded rows.
2. **An order-level refund naming no capture and no seller.** Neither source
   produces one today, so rather than guess, `attribute_refund()` raises
   `UnattributableRefund` and the caller allocates deliberately by calling
   `allocate_refund_across_sellers()`. A gap in the evidence does not silently
   become a proportional split.

Phase 11 inherits one consequence: an exception resting on the capture linkage
must say the linkage was **derived**, because PayPal supplies a HATEOAS link
and not a `capture_id`.

### D2. Results are frozen dataclasses, not Money or dicts

`SellerEntitlement` (seller id, gross share, commission, entitlement, the
resolved rule id), `OrderEntitlement` (per-seller lines, order commission
total, currency, refund provenance), `SellerPosition` (seller, currency,
fee-exclusive position, optional fee-inclusive position, unknown-fee capture
ids).

Frozen and immutable. Three reasons over flat `Money` returns: Block C compares
a **per-seller breakdown** against `platform_fee.amount` and would otherwise
re-derive it; the refund and unknown-fee provenance has nowhere to live in a
bare `Money`; and Block F's JSON contract is then a serialization of a declared
shape rather than a hand-assembled dict.

No `__post_init__` arithmetic. A dataclass that recomputes its own fields is a
second implementation of the calculation.

### D3. A capture fee is never defaulted to zero, and never estimated

`SellerPosition` always carries the **fee-exclusive** position. It carries a
**fee-inclusive** position only when every contributing capture states a fee;
otherwise that field is `None` and `unknown_fee_capture_ids` lists the captures
responsible.

The payloads make the distinction concrete, which is why it cannot be collapsed:
a refund's `seller_payable_breakdown` states `paypal_fee: "0.00"` explicitly,
while a capture's `seller_receivable_breakdown` carries **no `paypal_fee` key at
all**. An absent fee and a measured fee of nothing are different claims
([[mission]] P3, [[tech-stack]] §9.6).

`None` rather than an exception: an unknown fee is a routine gap in this
dataset, and making it raise would put a `try` around every Block C call site
for a condition that is a limitation to report, not an error to handle.

### D4. Expected commission is computed as a figure comparable to a platform fee

Per seller, per order: the seller's gross share times the resolved rule's rate,
quantized once. **Not** the residue of what the marketplace kept — Block C
compares it against `platform_fee.amount`, PayPal's own record, and a residue
is not that figure.

Quantization happens once, at the commission, and the entitlement is
`share - commission`. The alternative — quantizing the entitlement
independently — lets the two disagree by a cent with neither being wrong.

### D5. `seed._verify_order`'s calculation moves here; it is not rewritten

Block A's handoff requires this. `seed.verify()` will call the engine, and
D5a's independence is preserved: the check stays hand-written literals against
a recomputation, and the recomputation becomes the real engine instead of a
stand-in.

Two copies would be worse than duplication — they could disagree, and the one
in the test fixture is the one nobody reviews.

If `verify()` still passes afterwards, the engine agrees with eighteen
hand-tabulated orders on its first day.

### D6. The debt-4 test-order guard is added here

`specs/2026-11-08-block-a-marketplace-connector/validation.md` §8 debt 4 names
Block B as the cheapest place. A session-scoped fixture refuses to run
`tests/test_migrations.py` while tenant rows exist, so the suite's
order-dependence fails loudly instead of resting on alphabetical collection.

This does **not** enable randomisation or `-n auto` in this block. It makes the
precondition explicit so a later block can.

### D7. No applicable rule still raises; a stated zero still resolves

Unchanged from `rules.py` and restated because the dataset changed. A stated
0% rule is a real rate and resolves normally; no applicable rule raises
`NoApplicableCommissionRule` rather than returning zero. The engine has to tell
those apart, and the seller that exercised it (`vendor-dunlin`) no longer
exists — so a hand-computed fixture covers it instead of a seeded row.

## 3. Out of scope

- **Matching** marketplace records to PayPal records, and exception
  classification. Block C (Phase 11). This engine computes what *should* be
  true; it compares nothing.
- **FX.** The engine takes amounts in their own currency and refuses mixed
  arithmetic. The rounding guard — compare against PayPal's reported
  settlement, never a recomputation of gross times rate — is Block C's, because
  it is a comparison.
- **Dispute effects on entitlement.** No dispute outcome is observed in the
  dataset; `ORD-0009`'s dispute is open.
- **The `awaiting_evidence` classification** (R10). A classification, so Block
  C's.

## 4. Inherited, so none of it is rediscovered

- `resolve_commission_rule()` takes `at` as a parameter and raises on a naive
  timestamp. Callers pass the **order's** `placed_at`, not the run time, which
  is what makes a historical run re-derivable ([[mission]] P7).
- `signed()` and `position()` from `ledger.py` are the one place a direction
  becomes a sign. `seller_ledger_entry.amount` is unsigned.
- `allocate()` scales weights to a common integer basis; parts sum exactly with
  no residue. Do not divide `Decimal`s to allocate — the Phase 2 finding is
  that division at context precision can seat a spare minor unit on the wrong
  party.
- The purity gate `tests/test_money_purity.py` watches this package: no
  SQLAlchemy, no HTTP client, nothing from `reckonai.db` or `reckonai.models`,
  no clock read. Inputs are `typing.Protocol` types so an ORM row and a test
  dataclass both fit.
- Amounts cross JSON as strings: `{"amount": "160.00", "currency": "USD"}`.

## Related

- [[mission]] P1, P2, P3, P7 — the LLM computes nothing; two amounts are never
  comparable because their numbers look alike; no fabricated value
- [[tech-stack]] §2 money, §6 engine purity, §9.6 never default an amount
- [[roadmap]] Phase 10 — the six exit criteria, all of which bind
- `specs/2026-11-08-replan/requirements.md` — R1, R2 (process), Block B
- `specs/2026-11-09-paypal-capture/validation.md` §2 — the hand-check table
