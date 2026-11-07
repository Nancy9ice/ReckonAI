# Product direction — PayPal Multiparty, and what it changes

**Status:** direction decided 2026-11-06. Spec amendments and a verification
probe are both outstanding; **nothing here is implemented or applied.**
**Recorded, not acted on.** `2026-11-05-paypal-field-discovery/validation.md` §5
requires a canonical-model amendment to be written down on the Phase 0 branch
and carried forward, never applied there. The amendments listed in §6 belong to
a later branch.

---

## 1. The decision

ReckonAI targets marketplaces on **PayPal Complete Payments Platform for
Marketplaces and Platforms** — PayPal's multiparty model — and must support
**both** disbursement modes, `INSTANT` and `DELAYED`.

The rejected alternative was the first-party merchant-of-record model, where the
marketplace collects into its own account and disburses to sellers through the
Payouts API. That is a real marketplace architecture, and it is the one Phase 0's
fixtures exercised. But this product is built for the PayPal product
specifically, and multiparty is PayPal's marketplace offering. Where `readme.md`
describes the merchant-of-record model, the readme is what is wrong.

**This is Phase 0 succeeding, not failing.** `plan.md` Group 8 step 2 reserves a
slot for precisely this finding: "state plainly whether the canonical model
survives contact with the real API, or needs amendment. If it needs amendment,
say so here." Phase 0 ran first so that this would surface before Phases 2 and 3
built on it. It surfaced.

## 2. Responsibility boundary

The change is one of responsibility, not of scope.

| Party | Owns |
|---|---|
| **PayPal** | Processes payments, distributes funds, collects platform fees, executes refunds and disbursements, provides lifecycle information |
| **Marketplace** | Orders, sellers, contractual commission rules, the internal seller ledger, its own financial records |
| **ReckonAI** | Reconciles the two, identifies inconsistencies, investigates exceptions, recommends corrective actions |

**None of PayPal's money movement is duplicated.** That was already the rule —
[[mission]] P6, "Nothing moves money" — so nothing has to be removed. No payment
processing, payout execution or refund execution was ever built: at the time of
this decision the repository holds four Phase 0 probe scripts and the specs, and
no application code. The direction changed at the cheapest possible moment.

## 3. The two disbursement modes

| Mode | Money flow | PayPal evidence to retrieve | Reconciliation validation |
|---|---|---|---|
| `INSTANT` | Seller receives funds at capture | Capture and seller-level transaction records | Compare expected seller entitlement against the actual seller transaction amount and status |
| `DELAYED` | Payment captured; seller funds held until release | Capture, the delayed-disbursement indicator, the referenced payout and the disbursement status | First validate expected entitlement against the captured transaction. **Then** validate the release instruction and the actual disbursement |

**Entitlement validation is mode-independent.** It runs against the captured
transaction in both modes. `DELAYED` adds a second stage rather than replacing
the first, so the engine carries one entitlement path plus a conditional
disbursement stage.

`disbursement_mode` is already in the declared contract Phase 0 extracted — enum
`INSTANT` / `DELAYED`, on `CapturedPayment` and on
`purchase_units[].payment_instruction` — and is unobserved in every Phase 0
fixture. See `specs/paypal-fields.md`.

## 4. The lifecycle, and the false-positive rule

Five states, of which **2 through 5 are non-error states**:

1. Payment captured.
2. Seller funds held by PayPal.
3. Disbursement requested by the marketplace.
4. Disbursement completed.
5. Disbursement failed, or still pending.

**Do not treat a delayed disbursement as an unpaid seller obligation merely
because the seller has not received the funds yet.** Held funds are the expected
intermediate state of a `DELAYED` transaction, not a marketplace failure to pay.

This is not presentational. An engine that classifies states 2 and 3 as
discrepancies turns **every** `DELAYED` transaction into an exception, breaching
the hardest exit criterion in [[roadmap]] Phase 11 — "the clean Phase 4 baseline
produces zero exceptions" — by design rather than by bug. It is the same class of
error as recomputing `gross * rate` and reporting PayPal's cent-rounding as a
discrepancy (`2026-11-05-paypal-field-discovery/notes.md`, Group 6 blast radius
2). Two distinct ways to fail one gate, both now known in advance.

These states are the provider-backed form of the non-error lifecycle states
[[mission]] P4 requires and [[roadmap]] Phase 3 must enumerate.

## 5. What reconciliation validates

1. Marketplace orders against PayPal payments and captures.
2. Expected seller entitlements against actual seller-level financial
   transactions. **Both modes.**
3. Expected marketplace commissions against PayPal partner fee records.
4. PayPal refunds against marketplace refund and seller ledger adjustments.
5. Dispute-related financial changes against marketplace records.
6. Expected seller obligations against actual PayPal disbursement records and
   statuses. **`DELAYED` only.**
7. Original transaction currencies and available FX conversion information
   against marketplace financial records.

Pending and delayed transactions are distinguished from genuine discrepancies
per §4. All financial calculation stays deterministic; the agent investigates and
explains and never computes a financial conclusion ([[mission]] P1).

**Commission validation gets stronger under this direction.** In the
merchant-of-record model the marketplace's commission was implicit — whatever it
kept — with no provider figure to check it against. Under multiparty, PayPal's
partner fee record is independent evidence, so validation 3 becomes a real
comparison. This promotes `platform_fees` from a low-priority unobserved row to a
**core MVP dependency**.

## 6. What this changes in the specs — not yet applied

| Document | Change needed |
|---|---|
| `readme.md` §2 | State the integration model explicitly; the current list of marketplace types spans both models and forces no choice |
| `readme.md` §3.2 | "Payout amounts do not agree with recorded seller obligations" applies to `DELAYED` only; add the `INSTANT` form |
| `readme.md` §3.3 | Chargeback-after-payout becomes chargeback-after-disbursement, with the mode distinction |
| `readme.md` §6.1 | The USD 160 two-seller example stands, but the comparison target moves from the marketplace's own ledger-and-payout to PayPal's seller-level transactions and partner fee records |
| `readme.md` §6.2 | Partial refund handling must account for a platform fee reversal; see §8 question 6 |
| [[mission]] §4 | Capability 2's intent is unchanged, but "what each seller is owed" needs the mode-dependent reading in §3 |
| [[roadmap]] Phase 3 | Canonical model: `platform_fee` becomes first-class; the five states of §4 enter the lifecycle enum; `payout_batch` and `payout_item` remain but change meaning |
| [[roadmap]] Phase 11 | Add the §4 false-positive rule to the exit criteria, beside the FX rounding rule |

**All three demo scenarios survive.** `readme.md` §13 Scenario 1 is validation 4,
Scenario 2 is validation 3, Scenario 3 is validation 7 — subject to the
established constraint that Scenario 3 must inject an incorrect conversion
**value**, never a conversion **fee**, because PayPal declares no FX fee
anywhere.

## 7. Unresolved — needs a decision

**The simulated-data boundary.** The direction permits "clearly labelled
simulated fixtures only where necessary". [[mission]] P3 and `CLAUDE.md` forbid
fabricating a financial value. These coexist only with an explicit line, and the
proposed one is:

> Simulated data may populate the demo marketplace, and may stand in for a
> PayPal endpoint our credentials cannot reach, labelled as simulated at the
> record level. A simulated value may **never** enter a reconciliation result as
> provider evidence, never be cited by the agent as a PayPal record, and never
> populate an `fx_conversion` field. Where a PayPal figure is unreachable, the
> gap is a limitation, not a fixture.

Not adopted until confirmed. Without it, "where necessary" is the opening P3
exists to close.

## 8. Verification needed before the specs are amended

[[tech-stack]] §8 rule 1 — "verify fields exist before designing around them" —
applies to this decision as much as to any field. PayPal's published multiparty
pages do not cover delayed-disbursement mechanics, platform settlement
reporting, multi-seller handling, or FX with a seller payee.

| # | Question | Unblocks |
|---|---|---|
| 1 | Is `platform_fees` reachable in sandbox without partner approval? | validation 3 |
| 2 | Can the platform read a seller's capture via `PayPal-Auth-Assertion`, and what fields return? | validation 2, both modes |
| 3 | Under `DELAYED`, what records represent held funds, the release instruction, and the statuses between? | validation 6, and the §4 states |
| 4 | Is there platform-level settlement or reporting across sellers, or only per-transaction reads? | validations 2, 3, 6 |
| 5 | How does FX behave when the payee is a seller in another currency? | validation 7 |
| 6 | Does a refund reverse the platform fee, via `payment_instruction.platform_fees` on the refund endpoint? | validation 4, `readme.md` §6.2 |

Question 2 carries the highest risk. Under `INSTANT` the seller's funds land in
the seller's own PayPal account, and the direction's own wording is
"seller-level transaction records **where accessible**". If the platform cannot
read them, validation 2 is not implementable as specified.

**Blocked on:** a sandbox personal account to onboard as a seller under the
platform, and confirmation of whether the sandbox app can enable the
Marketplaces capability. That account also unblocks the wallet-funded capture
that twelve `unanswered` rows in `specs/paypal-fields.md` are waiting on.

## 9. What Phase 0 still supports

Groups 1 to 6 are committed, and most findings are architecture-independent.

**Unaffected** — the entire declared column, including the finding that no
conversion structure anywhere declares a timestamp or an FX fee; the two money
envelopes; `receivable_amount` cent-rounding; refund-to-capture linkage existing
only as `links[rel=up]`; the two fields PayPal returns but does not declare;
money as strings with no floats.

**Needs re-observation under multiparty** — the observed column, captured
first-party with `payee` set to our own merchant on every order. `platform_fees`,
`disbursement_mode` and the seller-level view are the specific gaps.

**Phase 0 still completes on its own terms.** Its remaining groups are the
validation checks and the handoff, and neither is blocked by this direction
change: the checks test internal consistency — that every verdict traces to
`observed.json`, that every absent field carries a decision — and assert nothing
about architectural coverage. The handoff's job is to state what the next phase
must change, which is this document. The field table and the handoff both record
that the observed column is first-party only.

---

## Related

- [[mission]] — P1, P3, P4 and P6 all govern this
- `readme.md` — the document this partly supersedes; see §6
- [[roadmap]] — Phases 3 and 11 consume this
- `specs/paypal-fields.md` — the field table, observed first-party only
- `specs/2026-11-05-paypal-field-discovery/notes.md` — how the direction change surfaced
