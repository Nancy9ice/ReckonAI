# ReckonAI — Mission

> **Status:** Constitution. This document defines *what* we are building and *why*.
> It constrains every other spec. When a decision elsewhere conflicts with this
> document, this document wins or this document must be amended first.

---

## 1. One-line definition

ReckonAI is an AI-powered financial reconciliation and investigation agent that
helps marketplace operators identify discrepancies between their PayPal
transaction records and their seller financial records, trace the cause across
the transaction lifecycle, and recommend evidence-based resolutions.

## 2. Who it is for

Finance and financial-operations staff **inside a marketplace operator**:

- Finance operations teams
- Financial controllers
- Reconciliation analysts
- Marketplace operations teams

Not for: sellers, buyers, or general-purpose accountants. Seller-facing views
are explicitly out of scope (see §7).

**The integration model is PayPal multiparty** (amended 2026-11-06). The target
marketplace runs on **PayPal Complete Payments Platform for Marketplaces and
Platforms** — what PayPal calls a *connected integration*. PayPal processes the
payment, routes each seller's share, remits the marketplace's platform fee, and
executes refunds and disbursements. Both disbursement modes are in scope:
`INSTANT`, where the seller is paid at capture, and `DELAYED`, where PayPal
holds the funds until the marketplace releases them or 28 days elapse.

This is part of the definition, not an implementation detail: it fixes who holds
the money and therefore what reconciliation means. A marketplace that is its own
merchant of record — collecting centrally and paying sellers through the Payouts
API — has a different problem and is **not** the target. See
`specs/multiparty-direction.md`.

## 3. The problem we are solving

A single customer payment in a marketplace creates several simultaneous
financial obligations — seller entitlements, marketplace commission, processor
fees, refund liabilities, and the disbursement of funds to each seller. Those
obligations are recorded across systems that share no identifiers, no schema,
and no clock:

| System | Holds |
|---|---|
| Marketplace backend | Orders, sellers, order items, commission rules |
| Marketplace ledger | Seller balances, commissions, adjustments |
| PayPal | Payments, captures, refunds, disputes, platform fees, seller disbursements |
| Marketplace release records (`DELAYED` only) | Which held captures it instructed PayPal to disburse, and when |

The hard question is not *"did the payment succeed?"* It is **"were the financial
consequences of that payment recorded and allocated correctly?"**

Answering it today means manual cross-system investigation. That is the cost we
are removing.

### 3.1 Who does what

The boundary matters because it determines what we verify and what we never
touch.

| Party | Owns |
|---|---|
| **PayPal** | Moves the money: processes payments, routes seller shares, remits platform fees, executes refunds and disbursements, and reports lifecycle state |
| **Marketplace** | Orders, sellers, contractual commission rules, its internal seller ledger, and its own financial records |
| **ReckonAI** | Reconciles the two, identifies inconsistencies, investigates exceptions, recommends corrective actions |

We duplicate none of PayPal's money movement — see P6, which already said so.
What changes under multiparty is not our responsibility but our **evidence**:
PayPal's record of what each seller actually received, and of the platform fee
it actually took, becomes an independent source to check the marketplace
against. In the merchant-of-record model the commission was merely whatever the
marketplace kept, with no provider figure to compare.

One consequence worth stating here because it constrains what we may conclude:
in a connected integration **PayPal places dispute and chargeback liability on
the seller**, not on the marketplace. Whether the marketplace is exposed is
governed by its own seller terms. An exposure we report must therefore rest on a
marketplace rule, never be inferred from a PayPal record alone.

## 4. What ReckonAI does

Three capabilities, in strict dependency order:

1. **Integrate** — pull marketplace records and PayPal records into one
   currency-aware canonical financial model, preserving the original payloads.
2. **Reconcile deterministically** — independently compute what each seller is
   owed under the marketplace's own rules, compare it against the marketplace
   ledger **and against PayPal's record of what the seller actually received
   and the platform fee it actually took**, and classify every difference.
3. **Investigate with AI** — for each exception, reconstruct the lifecycle,
   rank probable causes against retrieved evidence, and recommend a resolution
   a human can approve or reject.

Reconciliation runs both **historically** (over a chosen period) and
**event-driven** (as new financial events arrive), through the same engine and
the same canonical model.

**Amended 2026-11-08 — event-driven ingestion is deferred past the MVP.**
Recorded here rather than rewritten above, because the paragraph states the
product's intent correctly and it is the *MVP's* coverage that is narrower. The
decision and its reasoning are
`specs/2026-11-08-replan/requirements.md` R8; what follows is why it does not
contradict this document.

**What the MVP delivers:** reconciliation over a chosen period, triggered on
demand or on a schedule, through one engine over one canonical model.

**What is deferred:** webhook ingestion, signature verification, and recovery of
missed deliveries. There are no webhook endpoints in the MVP.

**Why this is an amendment and not a breach.** The load-bearing claim in the
paragraph above is *"through the same engine and the same canonical model"* —
that there is no second code path for live events. That claim is **stronger** in
the MVP, not weaker: the engine is pure functions that know nothing about what
invoked them ([[tech-stack]] §6), so a trigger added later cannot create a
second path. The trigger was therefore the affordable thing to defer, and the
architecture it would attach to is already built.

**What P4 keeps.** "Unmatched is not the same as wrong" is untouched, and the
case it protects survives without webhooks: the marketplace holds a refund whose
PayPal record has not appeared yet, because the reporting APIs run roughly a day
behind. Calling that *missing* would be the exact false positive P4 forbids. So
an incomplete lifecycle is classified `awaiting_evidence` and re-evaluated on
the next scheduled run — the run is the retry. What is deferred is the webhook
parking machinery, never the classification.

Three of three §6 demo scenarios are state comparisons over a seeded dataset, so
none of them is affected.

**Amended 2026-11-11 - ingestion is event-fed; reconciliation stays windowed.**
`specs/2026-11-11-live-ingestion/requirements.md` closes the deferral above for
ingestion only: PayPal webhooks, live retrieval and missed-event recovery feed
the canonical model, and a date-windowed run examines what arrived. There is
still no second code path for live events, and reconciliation is still not
per-event.

## 5. Principles

These are non-negotiable and exist to be cited in code review.

**P1 — Money is deterministic code, never a language model.**
Every monetary amount, rate, allocation and comparison is computed by explicit
code with decimal arithmetic. The LLM never performs arithmetic that reaches a
financial conclusion. It explains and investigates; it does not calculate.

**P2 — Every amount carries its currency.**
Two amounts are never comparable because their numbers look alike. Original
amount and currency are preserved alongside any conversion, never replaced by it.

**P3 — Actual conversion evidence beats estimates.**
Where PayPal supplies a rate, fee or settlement record, we use it. We never
substitute a public FX estimate for a real conversion record, and we never
fabricate a settlement or conversion figure that the provider did not give us.

**P4 — Unmatched is not the same as wrong.**
Delayed events, incomplete lifecycles and awaited evidence are first-class
states, distinct from genuine discrepancies. A false positive costs us the
user's trust faster than a missed exception does.

This now has provider-backed form. Under `DELAYED` disbursement a transaction
passes through **seller funds held by PayPal** and **disbursement requested**
before reaching **disbursement completed**, and both intermediate states are
normal. **Held funds are not an unpaid obligation.** Classifying them as
discrepancies would make every delayed transaction an exception — the single
largest false-positive risk in the product, alongside recomputing a conversion
and reporting PayPal's cent-rounding as a difference. Both are known in advance
and neither is permitted.

**P5 — Evidence or silence.**
Every conclusion cites the source records it rests on. "Insufficient evidence,
escalating for human review" is a correct and expected answer. Inventing a
plausible explanation is a defect.

**P6 — Humans hold the money.**
ReckonAI never moves funds, alters a seller balance, issues a refund or
initiates a payout on its own authority. It recommends; a person approves.

Multiparty adds one action this explicitly covers: **releasing held funds.**
Under `DELAYED` disbursement a release is a `POST` to
`/v1/payments/referenced-payouts-items`, and it moves money. ReckonAI may
observe that a release is due, overdue, or approaching PayPal's 28-day automatic
disbursement, and may recommend one. It never sends that call.

**P7 — Nothing is destroyed.**
Source payloads, reconciliation history and investigation reasoning are
retained so any decision can be reconstructed after the fact.

## 6. What success looks like

Judged on financial correctness, not feature count:

| Measure | Target posture |
|---|---|
| Discrepancy detection | Every deliberately seeded fault in the test dataset is found |
| False positives | A valid record is not flagged; delays are not reported as errors |
| Root-cause accuracy | The stated cause matches the seeded cause |
| Evidence completeness | Every conclusion traces to retrieved source records |
| Currency correctness | Multi-currency cases compute correctly, with originals preserved |

The primary deliverable is a working demonstration of three scenarios from real
seeded records and real detection — not narrated output:

1. A partial refund never reflected in the seller ledger.
2. An incorrectly calculated seller commission.
3. A currency-conversion discrepancy between recorded and actual settlement.

## 7. Explicitly not in scope

Deferred by decision, not oversight. The architecture must allow each to be
added later without redesigning the reconciliation engine.

- Bank / settlement-account reconciliation
- Payment providers other than PayPal
- A generic any-marketplace integration builder
- Multiple production marketplace connectors
- Automated financial remediation (beyond recommend-and-approve)
- Seller-facing dashboards
- Comprehensive settlement reporting

**Where the platform fee falls across that line** (clarified 2026-11-06, because
multiparty makes it ambiguous). PayPal remits the marketplace's platform fee as
a **single next-day consolidated settlement into a bank account**, not per
transaction. So there are two distinct checks, and only one is in scope:

| Check | Scope |
|---|---|
| The platform fee PayPal recorded on a transaction against the commission the marketplace's rules specify | **In scope.** Per transaction, against a provider record, and central to the product |
| The consolidated daily settlement against the sum of fees it should cover | **Deferred.** This is settlement-account reconciliation, already excluded above |

The second is deferred on feasibility as well as scope: it needs bank-side data
we do not ingest, and PayPal's partner fee cost detail arrives as a **monthly,
SFTP-delivered** report available only to approved partners. The canonical model
should not foreclose it — a platform fee record should be able to carry a
settlement reference later — but the MVP does not attempt it.

## 8. Positioning

Payment providers and reconciliation platforms already offer transaction
reporting, matching and exception queues. ReckonAI is not another matching
dashboard.

Our differentiator is **marketplace-specific financial reasoning**: knowing what
the marketplace owes each seller, which commercial rule produced that
entitlement, how a refund or dispute changes it, whether the marketplace ledger
and PayPal's own record of what it paid and what it took agree with it, and —
when they do not — why, with evidence.

## 9. Related specs

- [[tech-stack]] — the technology decisions that implement this mission
- [[roadmap]] — the order in which we build it
- `specs/multiparty-direction.md` — the integration model this mission assumes,
  and the verification still outstanding on it
- `specs/paypal-fields.md` — what PayPal actually returns, which is what P3 is
  enforced against
