# Matching and exception classification — decisions

> Block C of `specs/2026-11-08-replan/requirements.md`, which is [[roadmap]]
> Phase 11. ~4 h. Every Phase 11 exit criterion binds.
>
> Entry reading, already done: Block B's handoff
> (`specs/2026-11-09-entitlement-engine/requirements.md` §4) and the hand-check
> table (`specs/2026-11-09-paypal-capture/validation.md` §2).

## 1. Scope

Match marketplace records to PayPal records on stable identifiers, compare the
engine's expected position against the marketplace ledger and PayPal's reported
figures currency-aware, and classify every difference — including the states
that are **not** errors.

The engine answers what should be true. This block is the only place a
comparison happens, and the only place a tolerance is decided.

**Out of scope, with owners:** persisting exception rows (D8), prioritisation
scoring (Block F), the investigation agent (Block E).

## 2. Decisions

### D1. A new pure package, `reckonai/recon/`

`vocabulary.py`, `matcher.py`, `tolerance.py`, `classifier.py`. Pure functions
over `typing.Protocol` inputs, no I/O, same pattern as `reckonai/engine/`.

Not inside `engine/`: the handoff's "the engine compares nothing" is a load-
bearing boundary, and folding comparison in makes the engine's surface two
things. `tests/test_money_purity.py` is extended to watch `recon/` too, and
`[tool.mypy]` is widened to `src/reckonai/recon/`.

### D2. The exception vocabulary is a `StrEnum`, test-pinned to the manifest

Block A's `validation.md` debt 1 and this block's stated first task. The six
strings in `reckonai.sim.manifest` were written as plain strings precisely so
the authoritative vocabulary could be defined here.

A test asserts **every** manifest string is a member of the enum, so the two
cannot drift. The manifest keeps its own literals — it must not become a
restatement of what it is ground truth for.

The enum is a superset: the six, plus the codes the engine's raises become
(D5). It is deliberately **not** merged with `RECONCILIATION_STATES` in
`models/lifecycle.py`, which records where a record sits rather than what is
wrong with it. `awaiting_evidence` is the one name in both, and that is
intended — it is the state *and* the manifest's expected classification for F5.

### D3. Compare against PayPal's reported figures, never a recomputation

The first of Phase 11's two named false-positive traps.

- Expected entitlement is compared against `net_amount` directly. Measured on
  all 21 captures: `net_amount == gross_amount - platform_fees`, zero residual.
- Expected commission is compared against `platform_fee.amount`. Pinned on all
  15 clean purchase units by Block B's test.
- A converted settlement is compared against PayPal's reported
  `target_amount`, never against gross times rate. The rate is kept as a string
  parsed to `Decimal` at full precision and never used to re-derive an amount
  we already have.

### D4. Four tolerance decisions, each with its reason

Stated here as decisions, not discovered as constants while chasing noise.

| | Rule | Why |
|---|---|---|
| T1 | **Zero** tolerance on same-currency comparisons | D3's two measurements are exact on every capture. A tolerance here would only hide a real fault. |
| T2 | **One minor unit per conversion leg** when a sum of converted parts is compared against a converted total | ORD-0008: two legs convert to 119.17 while the total converts once to 119.18. PayPal rounds the settlement to the cent and leaves the rate unrounded; the residue is arithmetic, not a discrepancy. |
| T3 | Held funds are **never** an exception | `funds_held`, `release_requested` and `disbursement_pending` are healthy. PayPal auto-releases after 28 days, so an absent marketplace release instruction is not a finding either. |
| T4 | An **absent** amount is never compared as zero | No capture states a `paypal_fee` at all; a refund states an explicit `0.00`. [[mission]] P3 — absent and measured-zero are different claims. |

T2 applies only to the sum-versus-total shape. It is not a general FX slack: a
single reported settlement is compared exactly against the reported figure.

### D5. Each `EngineError` becomes a named classification, never a coerced figure

The engine raises where a figure would be invented, and this block decides what
each raise means. `NoApplicableCommissionRule` and `CommissionRuleAmbiguity` →
`unresolvable_commission_rule`. `MixedCurrencyOrder` → `mixed_currency_order`.
`RefundExceedsShare` → `refund_exceeds_share`. `UnattributableRefund` →
`refund_unattributable`. `RefundAlreadyApplied` → `duplicate_provider_record` —
a finding, **not** an amount to subtract, which is what F4 will exercise.
`FlatComponentReversalUndefined` and `FixedCommissionCurrencyMismatch` →
`unresolvable_commission_rule`.

**`CaptureNotSupplied` is not caught.** It subclasses `UnattributableRefund`,
so a general handler would swallow it, and it means the call was scoped wrongly
— reporting a caller bug as a finding about the data. It is re-raised and fails
the run.

### D6. The running commission comes from engine state, not from summing reversals

`apply_refund` computes the reversal as the delta of a cumulative figure
because per-refund quantization accumulates and drove a commission negative
(Block B `validation.md` §7 finding 1). Where a per-refund figure is needed to
compare against PayPal's own reversal record, that is `commission_reversal()`.
The two are never substituted for each other.

### D7. The dispute trap: encode the rule, seed nothing

Block A's `validation.md` debt 3 left this for Block C to take or decline
deliberately. **Declined as a seeded order; taken as a rule.**

The protection already exists and is historical, in two layers: capture-time
snapshots (`order_at_capture`, capture block D5, because `platform_fees`
mutates after a partial refund) and retained raw payloads ([[mission]] P7,
`raw_payload` link `NOT NULL`). Block C reads retained capture-time figures and
**never re-reads PayPal**, so a dispute reverting a capture to `PENDING` and
blanking its `seller_receivable_breakdown` cannot blind the classifier.

What is encoded: a capture with no breakdown classifies `awaiting_evidence`,
never a discrepancy and never "no fee charged" — `paypal-fields.md` line 189's
rule, which until now had no test. Proven on a hand-built fixture.

Not seeded, because the clean baseline is fixed at 15 purchase units and two
non-faults, and adding a third silently changes what the false-positive gate is
measured against.

*Correction this produced.* `specs/2026-11-09-paypal-capture/validation.md` §2
annotates ORD-0009 "open dispute". That is wrong: the fixture's note is
`rounding`, status `COMPLETED`, breakdown intact, `HIGH_RISK` risk indicator
only. Checked across all 18 fixtures — **no capture is `PENDING` and none is
missing its breakdown**. The note is fixed in this block.

### D8. Exception rows are a frozen contract here, a table in Block D

Phase 11's fourth criterion — financial impact and the ids of its evidence on
every exception row — is met by a frozen dataclass and its JSON shape, asserted
by test. No table, no migration.

The table belongs to Block D, which owns the date-windowed run that would
write it. Keeping it out holds `recon/` pure and keeps the purity gate honest.
**Block F depends on this**: its dashboard renders rows that Block D must
persist, and nobody owned the table before this decision.

### D9. Evidence basis is carried, because one linkage is derived

Refund-to-capture matching rests on a linkage parsed from a HATEOAS `up` link.
[[roadmap]] Phase 11 requires an exception resting on it to say so, so every
finding carries `evidence_basis`: `provider_reported` or `derived_linkage`.

This is also what Block F's confirmed-versus-suspected distinction reads, and
what keeps a rate difference from being reported as an error when it may be a
pinned earlier quote (`payment_instruction.payee_receivable_fx_rate_id`).

## 3. Limitations to record

- No disputed-capture fixture exists; the `PENDING`-with-no-breakdown rule is
  proven on a hand-built fixture only (D7).
- Dispute matching is unavailable, and a quoted FX rate is never substituted
  for a conversion record ([[mission]] P3).
- Cross-seller refund allocation is unreachable from real records at any seller
  count (capture block `validation.md` §2).

## Related

- [[mission]] P2, P3, P4, P5 — comparability, no fabricated value, unmatched is
  not wrong, escalate rather than guess
- [[roadmap]] Phase 11 — the four exit criteria and both false-positive traps
- `specs/2026-11-08-replan/requirements.md` — Block C scope, R10
  `awaiting_evidence`
- `specs/2026-11-09-entitlement-engine/requirements.md` §4 — the six things not
  to get wrong
- `specs/2026-11-09-paypal-capture/validation.md` §2 — the figures every
  expected amount is checked against
