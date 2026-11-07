# ReckonAI — Roadmap

> **Status:** Constitution. Implementation order and phase boundaries.
> **Strategy:** Foundation-first — each layer is completed before the next begins.
> **Budget:** ~1 week.
> Governed by [[mission]]; technology per [[tech-stack]].

---

## How to read this

Phases are deliberately small — most are half a day — and each one has **exit
criteria** that are checkable rather than a matter of opinion. A phase is not
done because the code exists; it is done when its exit criteria pass.

Phases run in order. The dependency note on each says what it genuinely needs,
so if you choose to resequence you can see what breaks.

**Total as written: 8.5 days against a ~1 week budget.** That is intentional, so
the overflow is visible rather than discovered on the last day. §"Cutline"
below names exactly what to drop to land in 7.

Phase 0 is **complete**, and Phase 0b was added on 2026-11-07 after Phase 0
found the product specified against the wrong PayPal integration model
(`specs/multiparty-direction.md`). So 0.5 day is spent and 0.5 added: the
remaining work is 8 days, and the overflow is unchanged.

---

## Phase 0 — PayPal sandbox field discovery · 0.5 day · **COMPLETE 2026-11-07**

**Goal:** Find out what PayPal *actually* returns before anything is modelled
around it.

> **Delivered**, and it changed the product. Output is `specs/paypal-fields.md`
> plus the artifacts and handoff in
> `specs/2026-11-05-paypal-field-discovery/`. The headline finding is that the
> product was specified for the **wrong PayPal integration model** — see
> `specs/multiparty-direction.md`. That is this phase succeeding: it surfaced
> before Phases 2 and 3 built on it.
>
> **One limit on its evidence.** Every fixture was created **first-party**, with
> the marketplace itself as `payee`. The declared column is model-independent
> and stands in full. The observed column does not cover `platform_fees`,
> `disbursement_mode` or any seller-level view, which is what **Phase 0b**
> exists to capture.

Read-only investigation. No integration code, no abstractions.

**Prerequisite:** install the APIMatic Context Plugin for PayPal
([[tech-stack]] §8.1). Without it this phase is model recall and manual doc
searching, which is the failure mode it exists to prevent.

Then establish two columns and compare them:

- **Declared** — what the PayPal API contract says exists. Corrected
  2026-11-05: this comes from the APIMatic-generated PayPal Server SDK at a
  pinned tag, which the plugin directs you to; the plugin itself carries no
  field names. Disputes and payouts are not in the SDK and fall back to PayPal's
  REST reference. See `specs/2026-11-05-paypal-field-discovery/requirements.md`
  §1 and §3.1.
- **Observed** — what the sandbox and the PayPal MCP toolkit actually return for
  our transactions.

Write down, per record type, which fields exist in each column.

- Transactions / captures: ids, amounts, currencies, fees
- Refunds: linkage back to the original capture, partial-refund shape
- Disputes: status, amount, lifecycle
- Payouts: batch and item structure
- **Settlement and FX: does a real rate, fee and settlement currency appear, or
  not?**

**Exit criteria**
- The APIMatic Context Plugin is installed and returning PayPal context.
- A field-availability table committed to `specs/paypal-fields.md`, with
  *declared* and *observed* as separate columns.
- Every field the canonical model intends to carry is marked *present*,
  *absent*, or *present but unreliable* — judged on **observed**, never on
  declared ([[mission]] P3).
- Every declared-but-not-observed field is listed explicitly. This set is the
  phase's most valuable output: it is where the canonical model would otherwise
  have been built on a field that never arrives.
- Each absent field has a decision recorded: derive it (labelled as derived) or
  declare the limitation.

**Depends on:** nothing. **Why first:** [[tech-stack]] §8 and [[mission]] P3.
Building the canonical model against guessed PayPal fields is the single most
expensive mistake available this week, because Phases 2–3 would then need
reworking after Phase 7.

---

## Phase 0b — Multiparty field discovery · 0.5 day · **COMPLETE 2026-11-07**

> **Delivered.** `specs/paypal-fields.md` is regenerated from multiparty
> evidence only: **69 canonical rows, 56 `present`, 6 `present-but-unreliable`,
> 7 `absent`, none `unanswered`.** Artifacts, fixtures and findings are in
> `specs/2026-11-07-multiparty-field-discovery/`.
>
> All six questions below are answered, plus two that were added. The headline
> results: the platform fee is taken **at capture in both disbursement modes**;
> **releasing held funds changes nothing on the capture**, so every `DELAYED`
> transaction must be joined to its disbursement record; commission is clawed
> back **automatically** on both refunds and lost disputes; and a conversion
> applies to the **net, not the gross**.
>
> **One probe is still outstanding** and does not block anything: whether the
> platform fee reaches the platform through a readable API. Both reporting
> endpoints answer `200`; the snapshot simply predated the captures. See
> `specs/2026-11-07-multiparty-field-discovery/notes.md` §5.1, which also warns
> how to read that result when it arrives.

**Added 2026-11-07.** Phase 0 answered its question against a first-party
integration. The product targets PayPal's multiparty model
(`specs/multiparty-direction.md`), so the observed column has a shaped hole in
it: the platform fee, the disbursement modes, and whatever the platform can see
of a seller's transactions.

**Goal:** Close that hole, and establish whether the multiparty reconciliation
the product now describes is implementable at all.

Same method and the same four scripts — probe, derive, extract, build — which
need new endpoints and the `PayPal-Auth-Assertion` header but no change in
approach. Fixtures for **both** disbursement modes.

**Prerequisite, and this phase cannot start without it:** a sandbox seller
account onboarded under the platform with the `PARTNER_FEE` and
`DELAY_FUNDS_DISBURSEMENT` features. These are granted **per seller at
onboarding**, not as account-level switches, and PayPal's simpler
build-your-own onboarding flow excludes Platform Fee, Delayed Disbursement,
Partner Reporting and the Disputes API outright. The same account also unblocks
the wallet-funded capture that twelve `unanswered` rows in
`specs/paypal-fields.md` are waiting on.

**The six questions**, each tied to the validation it unblocks:

| # | Question | Unblocks |
|---|---|---|
| 1 | Is `platform_fees` reachable in sandbox without partner approval? | commission validation |
| 2 | Can the platform read a seller's capture via `PayPal-Auth-Assertion`, and what returns? | entitlement validation, both modes |
| 3 | Under `DELAYED`, what records represent held funds, the release, and the statuses between? | the disbursement stage |
| 4 | Is there platform-level settlement or reporting across sellers, or only per-transaction reads? | all three above |
| 5 | How does FX behave when the payee is a seller in another currency? | currency comparison |
| 6 | Does a refund reverse the platform fee, and is the reversal automatic or instructed? | refund reconciliation |

**Question 2 carries the risk.** Under `INSTANT` the seller's funds land in the
seller's own PayPal account, and PayPal's own wording is "seller-level
transaction records **where accessible**". If the platform cannot read them,
entitlement validation is not implementable as specified and the MVP narrows.

**Known already, so do not re-derive:** release is
`POST /v1/payments/referenced-payouts-items` with `reference_id` set to the
capture id and `reference_type: TRANSACTION_ID`; held funds auto-disburse after
**28 days**; `PayPal-Auth-Assertion` is an unsigned JWT (`alg: none`) carrying
the platform client id as `iss` and the seller's `payer_id` or `email`. Both
referenced-payouts endpoints currently return **`403 NOT_AUTHORIZED`** for our
app — they need partner approval and a BN code, and that is a blocker to
confirm before planning around question 3.

**Exit criteria**
- `specs/paypal-fields.md` regenerated with multiparty fixtures, and every
  verdict still traceable to `observed.json` (`check_fields_doc.py --all`).
- Each of the six questions answered, or recorded as unreachable **with the
  reason** — an unreachable answer is a finding, not a failure, exactly as
  disputes were in Phase 0.
- Whether the platform fee is taken at capture or at release under `DELAYED`
  stated plainly, or recorded as unverifiable.
- The access-dependent amendments still outstanding in
  `specs/multiparty-direction.md` §6 applied.

**Depends on:** Phase 0, and the sandbox seller account.
**Why before Phase 3:** Phase 3 designs the canonical tables. Designing
`platform_fee` and the disbursement states without having seen one is the same
mistake Phase 0 exists to prevent, one model later.

---

## Phase 1 — Repo and deployment skeleton · 0.5 day

**Goal:** A deployable spine, so deployment is never a last-day problem.

FastAPI app, `/health`, Postgres connected, Alembic initialised, pytest running,
Render web service + worker + managed Postgres provisioned, secrets in env vars.

**Exit criteria**
- `/health` returns 200 from the deployed Render URL, not just locally.
- An empty Alembic migration applies cleanly against the Render database.
- `pytest` runs green in CI with at least one real assertion.
- The worker process starts, logs, and stays up.
- `uv.lock` is committed, and every environment — local, CI, web, worker —
  installs with `uv sync --frozen` ([[tech-stack]] §3).
- Render plan constraints confirmed: Background Workers available on the current
  tier, and the Postgres plan has no expiry window inside the project's life
  ([[tech-stack]] §11).
- Connection pool sizes set explicitly per component, not left at defaults.

**Depends on:** nothing.

---

## Phase 2 — Canonical core: money, tenant, catalogue · 0.5 day

**Goal:** The `Money` value object and the entities everything else references.

`Money` (Decimal + currency, mixed-currency arithmetic refused), largest-
remainder allocation helper, and tables for `tenant`, `seller`, `order`,
`order_item`, `commission_rule`.

**Exit criteria**
- Unit tests prove: mixed-currency arithmetic raises; allocation parts sum
  exactly to the whole with no residue; a 3-decimal currency round-trips.
- No `float` anywhere in the money path — enforced by a test that greps the
  module, so it cannot regress silently.
- Amounts serialize to JSON as strings.

**Depends on:** Phase 1. **Governs:** [[tech-stack]] §2.

---

## Phase 3 — Canonical financial events and raw payload store · 0.5 day

**Goal:** The event side of the model, shaped by Phase 0's findings.

Tables for `payment`, `capture`, `refund`, `dispute`, `seller_ledger_entry`,
`platform_fee`, `disbursement`, `fx_conversion`, plus a `raw_payload` JSONB
store and the lifecycle state enum from [[mission]] §4.

**Amended 2026-11-07 for multiparty.** `platform_fee` is now a first-class
table: PayPal's record of the commission it actually took is independent
evidence against the marketplace's own rules, and it is central rather than
incidental. `payout_batch` and `payout_item` are replaced by `disbursement`,
because under multiparty PayPal pays the seller — at capture under `INSTANT`,
or on a referenced-payout release under `DELAYED` — and a marketplace-run payout
batch no longer exists. A `platform_fee` row should be able to carry a
settlement reference later, since the consolidated daily settlement is deferred
rather than ruled out ([[mission]] §7).

**Exit criteria**
- Every financial table's amount columns are `NUMERIC` with a currency column.
- Every record links to its `raw_payload` row ([[mission]] P7).
- `fx_conversion` carries source, target, rate, timestamp, fee, provider, and a
  `is_derived` flag ([[mission]] P3). It is created **only** where PayPal
  reports an `exchange_rate`, so an empty row can never be mistaken for a
  conversion whose fields went missing. `fee` is nullable and stays null — never
  zero. `timestamp` records whether it came from the provider or was derived.
- A conversion may be **more than one row per record**: a refund declares an
  array of conversion entries where a capture declares a single object.
- Lifecycle states include the non-error states — delayed, unmatched, awaiting
  evidence — per [[mission]] P4, **plus the disbursement states**: disbursed at
  capture (`INSTANT`); funds held, release requested, disbursement completed,
  disbursement failed or pending (`DELAYED`). Held funds are a healthy
  intermediate state, not an unpaid obligation.
- Nothing is required that Phase 0 could not observe. Dispute fields in
  particular are nullable: their shape is known from a pinned contract and not
  one value was observed.
- `refund` links to its capture through a **derived** linkage, recorded as
  derived — PayPal supplies no `capture_id` on a refund, only a HATEOAS link.

**Depends on:** Phase 0 and **Phase 0b** (field reality for multiparty),
Phase 2 (`Money`).

---

## Phase 4 — Simulated marketplace API: clean data · 0.5 day

**Goal:** A marketplace to integrate against, with correct records only.

Endpoints for sellers, orders, order items, commission rules, payments, refunds,
disputes, ledger entries, and — per the multiparty amendment — expected seller
entitlements and release records rather than marketplace-run payout batches.
Seeded with an *internally consistent* multi-seller dataset, including one
multi-currency order.

**The simulated data must be multiparty-shaped**, or the connector is exercised
against a world the product does not target: each order line carries the seller
who is PayPal's `payee`, the marketplace's expected commission is what PayPal
should have taken as a platform fee, and at least one order uses `DELAYED`
disbursement so the held-funds states are reachable.

**Exit criteria**
- All endpoints return data with deliberately *different* field names from
  canonical (`vendor_id`, `order_ref`, `item_total`) so mapping is exercised for
  real.
- The seed dataset reconciles perfectly by hand — this is the known-good
  baseline that proves the engine produces no false positives.

**Depends on:** nothing technically; sequenced here so it is shaped by Phases 2–3.

---

## Phase 5 — Fault injection and webhook emitter · 0.5 day

**Goal:** The faults the demo detects, and a way to replay events.

Seed the three [[mission]] §6 scenarios — refund missing from ledger, wrong
commission, FX discrepancy — plus a duplicate ledger entry, a delayed event, and
a disbursement that disagrees with its obligation. Add an endpoint that emits
marketplace webhooks, including deliberate duplicate deliveries.

**Two faults must not be seeded, because they are not faults.** Funds held under
`DELAYED` and a release not yet requested are healthy intermediate states, and
PayPal's cent-rounding of a converted settlement is an artefact of its
arithmetic. Both belong in the **clean** baseline precisely so Phase 11 proves
it does not flag them. The FX fault is an incorrect **rate or settlement value**,
never an incorrect conversion fee — PayPal publishes no conversion fee to
contradict one.

**Exit criteria**
- A manifest file records every seeded fault and its expected classification —
  this becomes the eval ground truth in Phase 15.
- Clean records and faulty records coexist in the same dataset.
- The emitter can fire the same event twice on demand.

**Depends on:** Phase 4.

---

## Phase 6 — Marketplace connector and normalization · 0.5 day

**Goal:** Marketplace records land in the canonical model.

Connector client, declarative field-mapping config, mapping validation, and
normalization into Phases 2–3 tables with raw payloads retained.

**Exit criteria**
- The full seeded dataset ingests with zero data loss.
- A missing required mapping fails loudly at config-validation time, not at
  ingestion time.
- Re-running ingestion is idempotent — no duplicate canonical rows.

**Depends on:** Phases 3, 4.

---

## Phase 7 — PayPal ingestion · 0.5 day

**Goal:** PayPal records land in the same canonical model.

PayPal client, historical capture/refund/dispute/platform-fee/disbursement
retrieval, normalization, and FX conversion records populated from real PayPal
data where Phases 0 and 0b confirmed it exists.

**Exit criteria**
- Sandbox records appear as canonical rows alongside marketplace ones.
- Any field Phase 0 marked absent is handled as a recorded limitation — nothing
  is invented ([[mission]] P3).
- Retrieval is idempotent and re-runnable.
- **Refunds are read through the order**, not only the refund endpoint, which
  omits `platform_fees` for the same record. Field availability depends on the
  read path.
- **Raw payloads are retained and diffed against the model.** Phase 0 observed
  three fields that neither of PayPal's published contracts declares, and two of
  them stopped being returned a day later. A generated deserializer discards
  what it does not know, silently.
- The reporting endpoints are treated as **eventually consistent, roughly a day
  behind**, with the v2 resource endpoints authoritative for recent activity.
  Reporting is still read, because it is the only observed source of a
  conversion timestamp and per-event fees, joined on `transaction_id` and
  `paypal_reference_id`.
- A `403` is never recorded as an absent field, and a `5xx` is not retried
  blindly — a malformed request produced one repeatably.
- `items[].errors` is read on a failed disbursement; when it is absent the
  answer is `insufficient_evidence`, never an assembled cause.
- **Nothing in this client moves money** ([[mission]] P6). It never sends a
  referenced-payout release.

**Depends on:** Phases 0, **0b**, 3.

---

## Phase 8 — Job queue and worker · 0.5 day

**Goal:** The execution substrate from [[tech-stack]] §4.

`jobs` table with `FOR UPDATE SKIP LOCKED` claiming, retry with backoff,
`processed_events` unique constraint, worker loop, Render Cron Job wired to a
scheduled run.

**Exit criteria**
- Two workers running concurrently never claim the same job — proven by test,
  not by inspection.
- A failing job retries with backoff and lands in a terminal failed state with
  its error recorded.
- Inserting the same event id twice raises a constraint violation.
- The cron job fires a run on the deployed environment.

**Depends on:** Phase 1.

---

## Phase 9 — Event-driven ingestion · 0.5 day

**Goal:** Live events update state through the same path as historical.

Webhook endpoints for PayPal and the simulated marketplace, signature
verification where supported, dedupe, enqueue, and reprocessing when a
previously-missing related record arrives.

**Exit criteria**
- A duplicate webhook delivery produces exactly one financial effect.
- An event arriving before its related record is parked and reprocessed later,
  not dropped and not reported as a discrepancy ([[mission]] P4).
- A scheduled backfill recovers an event deliberately withheld from the webhook.

**Depends on:** Phases 6, 7, 8.

---

## Phase 10 — Entitlement engine · 0.5 day

**Goal:** The deterministic financial core. The highest-value phase here.

Pure functions, no I/O ([[tech-stack]] §6): expected seller entitlement from
commission rules, proportional refund allocation, commission adjustment,
multi-currency position.

**Exit criteria**
- The [[mission]] worked example passes exactly: $160 across two sellers at 10%
  → A $90, B $54, commission $16; after a $40 refund on A's item → A $54,
  B $54, commission $12.
- Allocation across three or more sellers leaves no rounding residue.
- No database handle, HTTP client or clock read appears in the module.
- Hand-computed fixtures cover each refund and commission policy.
- **The expected commission is computed as a figure comparable to PayPal's
  platform fee**, since that is what Phase 11 checks it against — not merely as
  the residue of what the marketplace kept.
- **A capture fee is never defaulted to zero.** Phase 0 could not establish
  whether PayPal assessed one, so a fee-inclusive position treats it as unknown
  and says so ([[tech-stack]] §9.6).

**Depends on:** Phase 2.

---

## Phase 11 — Matching and exception classification · 0.5 day

**Goal:** Turn expected-vs-actual differences into classified exceptions.

Match marketplace records to PayPal records on stable identifiers, compare the
expected position against the marketplace ledger and PayPal's own records
currency-aware, and classify into the [[mission]] categories — including the
non-error states.

**Exit criteria**
- Every fault in the Phase 5 manifest is detected and correctly classified.
- **The clean Phase 4 baseline produces zero exceptions** — the false-positive
  gate, and the hardest criterion in this document.
- A delayed event is classified as delayed, not as a discrepancy.
- Each exception row carries financial impact and the ids of its evidence.

**Two ways to fail the false-positive gate, both known in advance and neither
permitted** (added 2026-11-07 from Phase 0):

1. **FX rounding.** PayPal rounds a converted settlement to the cent and leaves
   the rate unrounded. On our own fixture, `95.50 * 1.089477173902071` is
   `104.04507010764778050` against a reported `104.05` — a residue of
   `-0.00492989235221950`. **Compare against PayPal's reported settlement
   amount, never a recomputation of gross times rate**, and record the rounding
   tolerance as an explicit decision rather than a constant found while chasing
   noise. Keep the rate as a string parsed to `Decimal` at full precision.
2. **Held funds.** Under `DELAYED`, funds held and a release not yet requested
   are healthy states. Classifying them as discrepancies makes every delayed
   transaction an exception. PayPal also auto-releases after **28 days**, so the
   absence of a marketplace instruction does not mean the funds are stuck.

Three further matching constraints from Phase 0:

- **A reported rate may not be the capture-time rate.**
  `payment_instruction.payee_receivable_fx_rate_id` lets a caller pin an earlier
  quote, so an unexplained rate difference may be that rather than an error.
- **Refund-to-capture matching rests on a derived linkage**, parsed from a
  HATEOAS link. An exception resting on it must say so.
- **Dispute matching is unavailable** until a dispute fixture exists, and a
  quoted FX rate is never substituted for a conversion record ([[mission]] P3).

**Depends on:** Phases 6, 7, 10.

---

## Phase 12 — AI investigation agent · 0.5 day

**→ Demo becomes viable at the end of this phase.**

**Goal:** Investigate an exception and produce an evidence-backed report.

Read-only retrieval tools plus the Phase 10 engine exposed as a recompute tool,
driven by the Tool Runner per [[tech-stack]] §5, returning the structured report
model.

**Exit criteria**
- Scenario 1 is investigated end-to-end and names the missing ledger adjustment
  as the cause, citing the refund and ledger record ids.
- Every cited id resolves to a real record — no fabricated references.
- An exception with evidence deliberately removed returns
  `insufficient_evidence` rather than a guess ([[mission]] P5).
- No tool can mutate financial state ([[mission]] P6).
- `stop_reason` is checked before response content is read.
- The summarized reasoning is persisted with the report, and the model id from
  the response is stored and compared against the model requested — a fallback
  produces a report flagged as downgraded ([[tech-stack]] §5.2).

**Depends on:** Phases 10, 11.

---

## Phase 13 — Investigation queue and approval gate · 0.5 day

**Goal:** Prioritisation and the human decision point.

Queue ordered by financial impact, affected-seller count, age and confidence,
with a recommended resolution a user can approve or reject, and both outcomes
recorded.

**Exit criteria**
- Approving a recommendation records the decision and the approver, and moves no
  money ([[mission]] P6).
- Rejection is recorded with equal weight.
- Confirmed discrepancies and suspected issues are visibly distinct.

**Depends on:** Phase 12.

---

## Phase 14 — Operations dashboard · 1 day

**Goal:** The interface the demo is given through.

React + AG Grid: reconciliation run summary, exception queue, investigation
detail with evidence trail, seller financial position view. Amounts formatted
from strings per [[tech-stack]] §7.

**Exit criteria**
- The exception queue groups, sorts and filters over the seeded dataset.
- The investigation view shows the cause, the evidence records and the
  recommendation, with approve/reject reachable.
- Every amount displays with its own currency; no client-side arithmetic.
- Deployed and reachable on Render.

**Depends on:** Phases 11, 12, 13.

---

## Phase 15 — Demo script and eval harness · 0.5 day

**Goal:** Prove the numbers in [[mission]] §6 rather than assert them.

A runner that reconciles the seeded dataset and scores detection rate, false
positives and root-cause accuracy against the Phase 5 manifest. Plus a rehearsed
demo path through all three scenarios.

**Exit criteria**
- The harness prints a metric table from a single command.
- All three scenarios run from a clean database without manual intervention.
- Detection and root-cause figures are measured output, not claims.

**Depends on:** Phases 5, 11, 12, 14.

---

## Cutline

The plan above is 8.5 days, of which Phase 0 is already spent. To land the
remainder in 7, drop in this order:

1. **Phase 13** (0.5d) — fold a minimal approve/reject into Phase 14 and lose
   queue prioritisation. [[mission]] P6 must survive the fold.
2. **Phase 15's eval harness** (0.25d) — keep the demo script, score by hand.

Do **not** cut Phase 0b, 10, 11 or 12. 10 and 11 are the product; 12 is the
differentiator. Phase 11's zero-false-positive criterion is the last thing to
compromise — a tool that cries wolf is worse than no tool.

**Phase 0b is the one addition that is not optional**, for the same reason
Phase 0 was not: it is the last point at which a wrong assumption about PayPal
is cheap. Cutting it means Phase 3 designs `platform_fee` and the disbursement
states without having seen either. If it is genuinely blocked — the sandbox
seller account cannot be created, or the capability cannot be enabled — then
**record that as the finding and narrow the MVP accordingly**; do not proceed as
though the answers were known.

Bryntum ([[tech-stack]] §7) is below the cutline by definition.

---

## Risks

**Nothing is demoable until Phase 12, on day 6 of 7.**
This is the accepted cost of foundation-first. It is a real risk: a problem
found in Phase 12 has no schedule left behind it. Two mitigations are built in —
Phase 0 pulls the external unknown forward, and Phase 1 proves deployment on day
one. The residual exposure is the engine-to-agent seam, and the cheapest
insurance is to run Scenario 1 by hand through the engine the moment Phase 11
closes, before starting Phase 12. *If this risk is unacceptable, the
alternative is to reorder Phases 4–12 as a thin vertical slice through Scenario
1 — that was considered and not chosen.*

**PayPal may not expose settlement or FX detail.**
Phase 0 exists to learn this on day one rather than day six. **Resolved:** a
cross-currency capture does return a real rate and settlement amount, so
Scenario 3 survives — but no conversion **fee** exists anywhere, so that half of
the scenario is dropped rather than estimated ([[mission]] P3).

**The platform may not be able to read its sellers' transactions.**
The live replacement for the risk above, and Phase 0b question 2. Under
`INSTANT` the seller's funds land in the seller's own PayPal account, and
PayPal's wording is "seller-level transaction records *where accessible*". If
`PayPal-Auth-Assertion` does not grant that read, entitlement validation is not
implementable as specified and the MVP narrows to what the platform can see of
its own fees. Both referenced-payouts endpoints already return `403` for our
app, which is a related signal. This is now the largest external unknown.

**False positives are the quiet failure.**
Easy to miss because the demo still looks like it works. Phase 11's clean-
baseline criterion is the specific defence and is non-negotiable.

**Multi-currency is where correctness breaks first.**
Rounding residue and implicit same-currency assumptions are the usual causes.
Phase 2 front-loads the tests that catch both.

**Phase 14 is the only full day of work in the plan.**
It is also the most compressible — a plainer grid still demonstrates the
product. If time is short, reduce Phase 14's polish before cutting anything in
Phases 10–12.

---

## Related specs

- [[mission]] — what we are building and why
- [[tech-stack]] — the technology these phases use
- `specs/paypal-fields.md` — produced by Phase 0
