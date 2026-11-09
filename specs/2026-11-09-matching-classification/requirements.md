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

### D4a. T2 is a guard with no caller, and that is the right outcome

*Added after building.* No classifier path reaches
`compare_converted_total`, and the reason is D3 rather than an omission.

The one-cent artefact arises only when a sum of separately-converted legs is
compared against a total converted in one step. **That total is not a figure
PayPal reports** - producing it means multiplying a net by a rate, which D3
forbids. So ORD-0008 is clean because the forbidden arithmetic is absent, not
because a tolerance absorbs it, and the FX guard is realised as "compare only
reported figures" rather than as slack.

T2 stays implemented and tested. It is the documented decision for the
sum-versus-total shape should a provider-reported total ever appear, and the
roadmap requires the rounding tolerance to be recorded as a decision rather
than discovered later. Deleting it would mean rediscovering ORD-0008's
arithmetic the first time that shape occurs.

### D4b. The held-funds guard is proven in unit tests, not by the gate

*Added after building, because the gate looks like it covers this and does
not.* Measured: deleting `funds_held` from `HEALTHY_HELD_STATES` does **not**
fail the database gate. ORD-0005's ledger credits its full 180.00 EUR
entitlement, so the residual is zero and the guard is never reached on that
order.

It is genuinely proven - removing the state fails three unit tests - and
`test_the_held_funds_order_is_clean_for_the_reason_we_think` pins the two
facts that make the gate pass, so the clean result is not mistaken for
coverage. Closing the gap needs a seeded order where funds are held *and* the
ledger is short, which would add a third non-fault and change what the gate is
measured against (D7's reasoning). Recorded as a limitation instead.

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

**A third basis was needed, not two.** Cross-source refund matching has no
shared identifier at all - the marketplace carries its own reference and
PayPal carries PayPal's - so the pairing rests on equal amount within one
order. That is materially weaker than a parsed href, and
`AMOUNT_WITHIN_ORDER` says so. Where two refunds on an order share an amount
the pairing is arbitrary among them, and each match is flagged `ambiguous` so
no individual pairing is reported as confirmed.

### D10. Three linkages the ingester leaves unmade, resolved here

*Added after building.* The schema is deliberate that a cross-source link is a
claim and not a column, and three of them were still open when this block
started. All three are resolved in `reckonai/reconcile.py`, which is the only
module holding both a `Session` and an `OrderCase`.

| Link | Evidence | Basis |
|---|---|---|
| order to payment | `purchase_units[].reference_id` in the retained payload; in no column | `provider_reported` |
| marketplace refund to order | the payload body's own `order_ref` | stated by the source |
| PayPal refund to order | `links[rel=up]` href, parsed, matched to a capture, then to its payment | `derived_linkage` |

Two things this surfaced. **Both sources write to `payment`**, 18 rows each,
so the run filters to PayPal-origin rows - including the marketplace's would
put all 18 orders in `provider_only` and reconcile nothing while passing the
false-positive gate. And **`refund.capture_id` is NULL on every row**,
including PayPal's: `reckonai.paypal.replay` retains the href and leaves the
link unmade on purpose. A refund that resolves to no order is absent from the
run rather than attached to a guess.

### D11. Two stale manifest figures, corrected against the fixtures

*Added after building.* The manifest is ground truth for the eval and is not
edited to fit the implementation. Two of its six impact figures nonetheless
described no record in the dataset:

- **F3** claimed 5.05 EUR against a 276.00 USD share at a rate of 0.9183. The
  real records are a 270.00 EUR net, a reported 294.16 USD receivable at
  1.089477173902071, and a marketplace figure of 262.00 USD - a gap of
  **32.16 USD**, in USD rather than EUR.
- **F6** claimed an 18.00 EUR shortfall against an obligation of 138.00, which
  implies a 12.00 commission on a 150.00 order and therefore a rule this
  tenant does not have. The entitlement is 135.00 and the ledger credits
  120.00, so **15.00 EUR**.

Both were Block A's synthetic provider figures, left behind when R16's capture
re-pointed the seed at real ones. `fixtures/paypal/`, `reckonai.sim.data` and
the capture block's hand-check table all agreed against the manifest in both
cases, so this is a stale document corrected against provider evidence, not
ground truth bent to fit code.

**A third instance surfaced after the code review**: four of the manifest's
*evidence references* also named retired synthetic rows - `FEE-ORD-0014`,
`CAP-ORD-0015`, `FX-ORD-0015`, `REL-ORD-0018`. Corrected to fixture-stable
ids. One consequence is permanent: **`platform_fee.external_id` cannot be
cited stably**, because `replay` builds it from a payload UUID that changes on
every seed, so a finding cites the capture that carries the fee instead.

`tests/test_manifest_agrees_with_seed.py` is the guard that was missing: it
derives each fault's impact from the seed and pins F3's reported settlement to
the fixture itself. Without it the gate would have failed against a correct
implementation, and the natural reaction would have been to change the
implementation.

## 3. Limitations to record

- No disputed-capture fixture exists; the `PENDING`-with-no-breakdown rule is
  proven on a hand-built fixture only (D7).
- Dispute matching is unavailable, and a quoted FX rate is never substituted
  for a conversion record ([[mission]] P3).
- Cross-seller refund allocation is unreachable from real records at any seller
  count (capture block `validation.md` §2).
- The held-funds guard is not exercised by the database gate (D4b).
- Cross-source refund pairing rests on amount alone, so two refunds of equal
  value on one order are ambiguous by construction (D9).

## 4. What this block hands forward

- **The exception table is Block D's** (D8). The `Finding` JSON contract is
  frozen and pinned by test; nothing persists yet, and Block F's dashboard
  depends on Block D writing rows.
- **`reckonai.reconcile.run(session, tenant_id)` is the entry point** Block
  D's date-windowed run wraps, and Block E's agent calls for a recompute. It
  is a plain function over a session: no scheduling, no windowing, no clock.
- **`exceptions_only()` is how the gate counts.** `awaiting_evidence` is a
  finding and not an exception, so counting `len(findings)` would fail the
  false-positive gate while being entirely correct.
- **Do not widen T1.** The zero tolerance rests on two measurements across all
  21 captures, and F2's whole fault is 10.00 of commission.
- **One fault must stay one finding.** Two mechanisms, and a new rule must
  use one of them: a defect's own row is **held out** of every later
  comparison (an unreflected refund, a duplicate's surplus row), or the rule
  **credits `explained`** so the residual check reports only the remainder. A
  rule that does neither will double-report an existing fault - that is what
  two of the nine code-review findings were.
- **Cite the capture, not the platform fee.** `platform_fee.external_id` is
  built as `f"pf-{payload_uuid}-{index}"` and changes on every seed, so it can
  name fee evidence but cannot be used to find it again. Block E's exit
  criterion is that every cited id resolves to a real record; the capture is
  the stable reference.
- **Both sides of the held-funds question must be read.** A capture's
  `disbursement_state` never carries `release_requested` or
  `disbursement_pending` - those exist only on the `disbursement` row. A rule
  that consults only the capture reports a release in flight as a mismatch.
- **Three cross-source linkages are claims made in `reconcile.py`, not
  columns** (D10). A fourth - the disbursement-to-capture link - was added
  after the review. Resolving a new one belongs there, with the others.
- **Read `validation.md` §9 before extending the classifier.** The nine
  code-review findings are tabulated with their fixes; two were false
  positives on correct data that the seeded dataset could not show, and the
  shapes that caused them (equal seller shares, a duplicated debit) now have
  regression tests rather than only the gate.

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
