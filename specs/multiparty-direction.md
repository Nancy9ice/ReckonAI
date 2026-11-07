# Product direction — PayPal Multiparty, and what it changes

**Status:** direction decided 2026-11-06. **Spec amendments applied 2026-11-07**
— see §6 for what landed and what is still waiting. **Verification is still
outstanding:** [[roadmap]] Phase 0b, scoped in §8, and nothing in §8.2 may be
designed around until it runs.

The direction was first recorded on the Phase 0 branch and deliberately not
acted on there, because
`2026-11-05-paypal-field-discovery/validation.md` §5 requires a canonical-model
amendment to be carried forward rather than applied inside the phase that found
it. Phase 0 is now merged and the amendments have been made on their own branch.

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
| **PayPal** | Processes payments, routes each seller's share, **remits the marketplace's platform fee to the marketplace**, executes refunds and disbursements, provides lifecycle information |
| **Marketplace** | Orders, sellers, contractual commission rules, the internal seller ledger, its own financial records |
| **ReckonAI** | Reconciles the two, identifies inconsistencies, investigates exceptions, recommends corrective actions |

PayPal calls this arrangement a **connected integration**, and it is the phrase
its documentation keys liability and capability statements to. Two of those
statements constrain what we may conclude:

- **Dispute and chargeback liability sits with the seller**, not the
  marketplace. Whether the marketplace is exposed is governed by its own seller
  terms, so any exposure we report rests on a marketplace rule and is never
  inferred from a PayPal record alone.
- **The platform fee is remitted, not merely collected.** It reaches the
  marketplace as a single next-day consolidated settlement into a bank account,
  not per transaction and not as a PayPal balance. [[mission]] §7 splits the
  resulting checks: the per-transaction fee comparison is in scope, the
  aggregate settlement reconciliation is deferred.

**None of PayPal's money movement is duplicated.** That was already the rule —
[[mission]] P6, "Nothing moves money" — so nothing has to be removed. No payment
processing, payout execution or refund execution was ever built: at the time of
this decision the repository holds four Phase 0 probe scripts and the specs, and
no application code. The direction changed at the cheapest possible moment.

## 3. The two disbursement modes

| Mode | Money flow | PayPal evidence to retrieve | Reconciliation validation |
|---|---|---|---|
| `INSTANT` | Seller receives funds at capture | Capture and seller-level transaction records | Compare expected seller entitlement against the actual seller transaction amount and status |
| `DELAYED` | Payment captured; seller funds held until release, or until PayPal releases them automatically after **28 days** | Capture, the delayed-disbursement indicator, the referenced payout and the disbursement status | First validate expected entitlement against the captured transaction. **Then** validate the release instruction and the actual disbursement |

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

**State 2 has a fuse.** PayPal disburses held funds automatically after 28 days,
with no marketplace instruction. So a transaction can move from state 2 to state
4 without ever passing through state 3, and the absence of a release instruction
does not mean the funds are stuck. An engine that treats state 3 as a required
predecessor of state 4 will mis-model every auto-released transaction, and one
that reports held funds as overdue without accounting for the fuse will raise an
exception that resolves itself.

The release itself is `POST /v1/payments/referenced-payouts-items` — see §8.1.
**ReckonAI never sends it** ([[mission]] P6); it may observe that a release is
due, overdue, or approaching the 28-day fuse, and recommend one.

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

## 6. What this changes in the specs

**Applied 2026-11-07** on branch `direction/multiparty-specs`. The
contract-derived amendments are in; the access-dependent ones wait on §8.

| Document | Change | State |
|---|---|---|
| `readme.md` | Integration model in §2 and the status block; both modes in §1; mode-specific comparison targets in §3.2; chargeback liability in §3.3; conversion fee removed from §3.5, §5.3 and §13; two-source comparison in §6.1; fee reversal in §6.2; five new exception categories in §6.3; disbursement states in §6.4; §11.1 and §11.2 rewritten | **Applied** |
| [[mission]] | Integration model in §2; the four-system table and the new §3.1 responsibility boundary; capability 2 in §4; disbursement states under P4; the release call under P6; platform fee scope split in §7 | **Applied** |
| [[roadmap]] | Phase 0 marked complete with its first-party limit; **Phase 0b added**; Phase 3 gains `platform_fee` and `disbursement`; Phases 4 and 5 require multiparty-shaped fixtures; Phase 7 gains the client rules; Phases 10 and 11 gain the fee and false-positive criteria; budget and risks updated | **Applied** |
| [[tech-stack]] | FX rules in §2; multiparty access mechanics and client rules in §8; the SDK condition in §8.1; money envelopes in §9.3; seller attribution and derived refund linkage in §9.5 | **Applied** |
| All four | Phase *findings* removed from the root specs, which now state rules and point at `specs/paypal-fields.md` and the phase directory | **Applied** |
| `readme.md` §6.1, [[roadmap]] Phase 7 | Whether entitlement validation is implementable at all depends on §8 question 2 | **Waiting on Phase 0b** |
| [[roadmap]] Phase 3 | The exact records representing held funds and release depend on §8 question 3 | **Waiting on Phase 0b** |

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

## 8. Verification still needed — Phase 0b

[[tech-stack]] §8 rule 1 — "verify fields exist before designing around them" —
applies to this decision as much as to any field. The questions below are
[[roadmap]] Phase 0b's scope.

### 8.1 Settled from documentation, 2026-11-07 — do not re-derive

Found while amending the specs. Each is documented by PayPal but **none is
observed**, so each still needs a fixture before anything depends on it.

| Mechanic | What PayPal states |
|---|---|
| `DELAYED` release | `POST /v1/payments/referenced-payouts-items`, `reference_id` set to the capture id, `reference_type: TRANSACTION_ID`. This is the disbursement record under `DELAYED` |
| Automatic release | Held funds "are automatically disbursed to the seller after 28 days" — a hard fuse that fires with no marketplace instruction |
| Fee incidence | PayPal "deducts fees from the seller's funds" |
| Platform fee settlement | Partner fees settle **once daily, consolidated**, into a bank account, not a PayPal balance. A fee-receiving API-caller account cannot hold a PayPal balance |
| Capability grant | Per seller **at onboarding**: `PARTNER_FEE` for platform fees, `DELAY_FUNDS_DISBURSEMENT` for delayed disbursement. The simpler onboarding flow excludes Platform Fee, Delayed Disbursement, Partner Reporting and the Disputes API outright |
| `PayPal-Auth-Assertion` | An unsigned JWT (`alg: none`), platform client id as `iss`, the seller's `payer_id` or `email` in the payload, trailing period |
| Dispute liability | "In connected integration, merchants assume financial liability and handle disputes and chargebacks" — the **seller** bears it, not the marketplace |
| Partner fee cost detail | Pricing Transaction report: **monthly**, SFTP, approved partners on gross-billing or revenue-share only. Carries `PRICING_FEE_TYPE: CURRENCY_CONVERSION` and FX markup columns, amounts in **integer cents** |

**One correction this produced.** An earlier reading of this direction held that
the Payouts API is not used for seller settlement under multiparty. That is
wrong: referenced payouts **are** the `DELAYED` release mechanism.

**And one narrowing.** "No FX fee exists anywhere" should read "none in the
transaction APIs". A partner-level FX markup does exist, in the monthly report.
It is PayPal's cost to the partner rather than a per-transaction conversion fee
in a seller's settlement, so the `fx_conversion.fee` verdict is unchanged — but
the claim needed bounding.

### 8.2 Open questions

| # | Question | Unblocks | State |
|---|---|---|---|
| 1 | Is `platform_fees` reachable in sandbox, with a seller onboarded for `PARTNER_FEE`? | validation 3 | open |
| 2 | Can the platform read a seller's capture via `PayPal-Auth-Assertion`, and what fields return? | validation 2, both modes | open — **highest risk** |
| 3 | What do the held-funds and release records actually contain, and what statuses do they carry? | validation 6, the §4 states | mechanism known (8.1), payload unobserved |
| 4 | Is the consolidated daily settlement visible through any API, or only the monthly report? | the deferred settlement check ([[mission]] §7) | open |
| 5 | How does FX behave when the payee is a seller in another currency? | validation 7 | open |
| 6 | Does a refund reverse the platform fee, and is the reversal automatic or instructed? | validation 4, `readme.md` §6.2 | partly answered: it is **instructed** on the refund call and PayPal must enable it. Whether it can be omitted entirely is unobserved |
| 7 | Is the platform fee taken at **capture** or at **release** under `DELAYED`? | Phase 3's fee timing, Phase 11's matching | **open, and not answerable from documentation.** PayPal states neither |
| 8 | What happens to the platform fee when a dispute or chargeback occurs? | dispute reconciliation | open; undocumented |

Question 2 carries the highest risk. Under `INSTANT` the seller's funds land in
the seller's own PayPal account, and PayPal's own wording is "seller-level
transaction records **where accessible**". If the platform cannot read them,
validation 2 is not implementable as specified and the MVP narrows to what the
platform can see of its own fees.

Questions 7 and 8 were added on 2026-11-07. Both are undocumented, and an
attempt at 7 was blocked: **both referenced-payouts endpoints return
`403 NOT_AUTHORIZED`** for our sandbox app. They require partner approval and a
BN code. By the rule this phase already established, a `403` is evidence about
our permissions and not about the API — but it is a blocker to clear before
planning around question 3.

**Blocked on:** a sandbox personal account onboarded as a seller under the
platform with `PARTNER_FEE` and `DELAY_FUNDS_DISBURSEMENT`, and confirmation of
whether partner approval and a BN code are obtainable for the sandbox. That
account also unblocks the wallet-funded capture that twelve `unanswered` rows in
`specs/paypal-fields.md` are waiting on.

## 9. What Phase 0 still supports

**Phase 0 is complete and merged** (PR #1, 2026-11-07). All eight groups landed,
including the validation gate and the handoff. Most of its findings are
architecture-independent.

**Unaffected** — the entire declared column, including the finding that no
conversion structure anywhere declares a timestamp or an FX fee; the two money
envelopes; `receivable_amount` cent-rounding; refund-to-capture linkage existing
only as `links[rel=up]`; the two fields PayPal returns but does not declare;
money as strings with no floats.

**Needs re-observation under multiparty** — the observed column, captured
first-party with `payee` set to our own merchant on every order. `platform_fees`,
`disbursement_mode` and the seller-level view are the specific gaps.

**Phase 0 completed on its own terms**, and the direction change did not block
it. Its validation gate tests internal consistency — that every verdict traces
to `observed.json`, that every absent field carries a decision — and asserts
nothing about architectural coverage. Its handoff's job was to state what the
next phase must change, which is this document. Both the field table and the
handoff record that the observed column is first-party only.

Its tooling carries straight over to Phase 0b: `probe_paypal.py`,
`derive_observed.py`, `extract_declared.py`, `build_fields_doc.py` and
`check_fields_doc.py` need new endpoints and the `Auth-Assertion` header, but no
change of approach, and the declared column needs nothing at all.

---

## Related

- [[mission]] — P1, P3, P4 and P6 all govern this; §3.1 carries the boundary
- `readme.md` — the document this partly superseded; amendments applied, see §6
- [[roadmap]] — Phase 0b verifies this; Phases 3, 7 and 11 consume it
- [[tech-stack]] — §8 holds the access mechanics and client rules
- `specs/paypal-fields.md` — the field table, observed first-party only
- `specs/2026-11-05-paypal-field-discovery/notes.md` — how the direction change surfaced
