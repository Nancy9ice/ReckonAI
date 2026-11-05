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

## 3. The problem we are solving

A single customer payment in a marketplace creates several simultaneous
financial obligations — seller entitlements, marketplace commission, processor
fees, refund liabilities, and an eventual payout. Those obligations are recorded
across at least four systems that share no identifiers, no schema, and no clock:

| System | Holds |
|---|---|
| Marketplace backend | Orders, sellers, order items, commission rules |
| Marketplace ledger | Seller balances, commissions, adjustments |
| PayPal | Payments, captures, refunds, disputes, payouts |
| Marketplace payout system | Payout batches and payout items |

The hard question is not *"did the payment succeed?"* It is **"were the financial
consequences of that payment recorded and allocated correctly?"**

Answering it today means manual cross-system investigation. That is the cost we
are removing.

## 4. What ReckonAI does

Three capabilities, in strict dependency order:

1. **Integrate** — pull marketplace records and PayPal records into one
   currency-aware canonical financial model, preserving the original payloads.
2. **Reconcile deterministically** — independently compute what each seller is
   owed under the marketplace's own rules, compare it against the ledger and
   payout records, and classify every difference.
3. **Investigate with AI** — for each exception, reconstruct the lifecycle,
   rank probable causes against retrieved evidence, and recommend a resolution
   a human can approve or reject.

Reconciliation runs both **historically** (over a chosen period) and
**event-driven** (as new financial events arrive), through the same engine and
the same canonical model.

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

**P5 — Evidence or silence.**
Every conclusion cites the source records it rests on. "Insufficient evidence,
escalating for human review" is a correct and expected answer. Inventing a
plausible explanation is a defect.

**P6 — Humans hold the money.**
ReckonAI never moves funds, alters a seller balance, issues a refund or
initiates a payout on its own authority. It recommends; a person approves.

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

## 8. Positioning

Payment providers and reconciliation platforms already offer transaction
reporting, matching and exception queues. ReckonAI is not another matching
dashboard.

Our differentiator is **marketplace-specific financial reasoning**: knowing what
the marketplace owes each seller, which commercial rule produced that
entitlement, how a refund or dispute changes it, whether the ledger and the
payout agree with it, and — when they do not — why, with evidence.

## 9. Related specs

- [[tech-stack]] — the technology decisions that implement this mission
- [[roadmap]] — the order in which we build it
