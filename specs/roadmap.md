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

**Total as written: 8 days against a ~1 week budget.** That is intentional, so
the overflow is visible rather than discovered on the last day. §"Cutline"
below names exactly what to drop to land in 7.

---

## Phase 0 — PayPal sandbox field discovery · 0.5 day

**Goal:** Find out what PayPal *actually* returns before anything is modelled
around it.

Read-only investigation. No integration code, no abstractions.

**Prerequisite:** install the APIMatic Context Plugin for PayPal
([[tech-stack]] §8.1). Without it this phase is model recall and manual doc
searching, which is the failure mode it exists to prevent.

Then establish two columns and compare them:

- **Declared** — what the PayPal API contract says exists, via the plugin.
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
`payout_batch`, `payout_item`, `fx_conversion`, plus a `raw_payload` JSONB store
and the lifecycle state enum from [[mission]] §4.

**Exit criteria**
- Every financial table's amount columns are `NUMERIC` with a currency column.
- Every record links to its `raw_payload` row ([[mission]] P7).
- `fx_conversion` carries source, target, rate, timestamp, fee, provider, and a
  `is_derived` flag ([[mission]] P3).
- Lifecycle states include the non-error states — delayed, unmatched, awaiting
  evidence — per [[mission]] P4.

**Depends on:** Phase 0 (field reality), Phase 2 (`Money`).

---

## Phase 4 — Simulated marketplace API: clean data · 0.5 day

**Goal:** A marketplace to integrate against, with correct records only.

Endpoints for sellers, orders, order items, commission rules, payments, refunds,
disputes, ledger entries, payout batches, payout items. Seeded with a
*internally consistent* multi-seller dataset, including one multi-currency order.

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
a payout that disagrees with its obligation. Add an endpoint that emits
marketplace webhooks, including deliberate duplicate deliveries.

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

PayPal client, historical transaction/refund/dispute/payout retrieval,
normalization, and FX conversion records populated from real PayPal data where
Phase 0 confirmed it exists.

**Exit criteria**
- Sandbox records appear as canonical rows alongside marketplace ones.
- Any field Phase 0 marked absent is handled as a recorded limitation — nothing
  is invented ([[mission]] P3).
- Retrieval is idempotent and re-runnable.

**Depends on:** Phases 0, 3.

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

**Depends on:** Phase 2.

---

## Phase 11 — Matching and exception classification · 0.5 day

**Goal:** Turn expected-vs-actual differences into classified exceptions.

Match marketplace records to PayPal records on stable identifiers, compare
expected position against ledger and payout records currency-aware, and classify
into the [[mission]] categories — including the non-error states.

**Exit criteria**
- Every fault in the Phase 5 manifest is detected and correctly classified.
- **The clean Phase 4 baseline produces zero exceptions** — the false-positive
  gate, and the hardest criterion in this document.
- A delayed event is classified as delayed, not as a discrepancy.
- Each exception row carries financial impact and the ids of its evidence.

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

The plan above is 8 days. To land in 7, drop in this order:

1. **Phase 13** (0.5d) — fold a minimal approve/reject into Phase 14 and lose
   queue prioritisation. [[mission]] P6 must survive the fold.
2. **Phase 15's eval harness** (0.25d) — keep the demo script, score by hand.

Do **not** cut Phase 0, 10, 11 or 12. Phase 0 prevents rework; 10 and 11 are the
product; 12 is the differentiator. Phase 11's zero-false-positive criterion is
the last thing to compromise — a tool that cries wolf is worse than no tool.

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
Phase 0 exists to learn this on day one rather than day six. If settlement data
is absent, Scenario 3 narrows to what PayPal does supply and the gap is stated
openly ([[mission]] P3) — it is not filled with an estimate.

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
