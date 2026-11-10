# Block D — queue, scheduled run, exception table

> Phase 8 ([[roadmap]]), narrowed by the replan's R7, R9 and R10, plus the
> exception table Block C assigned here (that block's `requirements.md` D8).
> Fixture replay, old Block D's other half, was delivered early by R16.
>
> 2026-11-09. Branch `block-d-queue-scheduled-run`, off `main` at `abe6295`.

## 1. Scope

**In:** the `jobs` table with `FOR UPDATE SKIP LOCKED` claiming; retry in R7's
narrowed form; `processed_events`; a worker that claims and executes rather than
ticking; the `reconciliation_run` and `reconciliation_exception` tables; a
date-windowed run wrapping `reckonai.reconcile.run`; R10's re-evaluation of
`awaiting_evidence`; the Render Cron Job declared in `render.yaml`.

**Out, with owners:** prioritisation scoring and approve/reject (Block F); the
dashboard that reads these rows (Block F); the investigation agent (Block E);
webhook ingestion (cut by R8); any live PayPal call during a run (R6).

**Not re-derived here.** Block C's handoff (`specs/2026-11-09-matching-
classification/requirements.md` §4) is taken as given: `run(session, tenant_id)`
is a plain function over a session with no clock and no windowing, exceptions are
counted through `exceptions_only()`, and the `Finding` JSON contract is frozen.
This block wraps that function and persists its output; it changes no rule.

## 2. Decisions

### D1. Runs and exceptions are two tables, append-only

`reconciliation_run` carries the window, `started_at`, `finished_at`, status and
the two counts. `reconciliation_exception` carries one row per `Finding`, FK'd to
its run. A later run writes new rows and never updates or deletes an earlier
run's.

Append-only is [[tech-stack]] §10, and it is also what makes R10 visible: an
order parked as `awaiting_evidence` on Monday and cleared on Tuesday leaves two
rows that differ, which is the evidence the re-evaluation happened. An upserted
current-state table would overwrite exactly the row that proves it.

Both tables carry `tenant_id` and both are indexed on it. The exception row
stores every field of the frozen contract, amounts as `NUMERIC` with their own
currency column, `evidence_refs` as JSONB. `is_exception` is stored rather than
derived in SQL, so Block F's queue filters on a column and cannot disagree with
`exceptions_only()` about what counts.

### D2. Findings are written, never recomputed on read

The run executes the engine once and persists what it concluded. Block F renders
stored rows. Nothing reconstructs a verdict from stored inputs, because two
code paths producing a verdict is the thing [[mission]] §4 forbids.

### D3. The window filters on `order.placed_at`

**A correction to how this was chosen.** The decision was taken as "order
creation time", and `Order` has two candidate columns: `placed_at`, the
marketplace's own order date, and `created_at`, a server-defaulted ingestion
clock from `TimestampMixin`. The intent — "orders placed in this period", stable
across re-runs — is `placed_at`. `created_at` is our clock, and windowing on it
would make the window mean "orders we happened to ingest then", which for a
single-seed dataset is one instant.

This is also what makes the window testable: the eighteen seeded orders span
eighteen distinct `placed_at` days, so a narrow window selects a known subset.

R10 survives this choice for a *reporting* lag. A refund whose PayPal record
arrives a day late does not move its order's `placed_at`, so the order is still
inside a two-day window and the next run re-reads its evidence.

Both bounds are dates, half-open (`>= start`, `< end`), stored on the run row so
a reader knows what a run covered rather than inferring it.

**Amended before implementation: `placed_at` alone is not sufficient.** The
question that produced this is a dispute settled by a refund. A dispute opens
days after the order, PayPal decides it weeks later, and the refund that settles
it is ingested 30+ days after `placed_at`. By then every scheduled window has
moved past that order, so it is never re-selected, the late refund is never
read, and D1's append-only history never gets the second row that would show the
verdict changing. The whole R10 mechanism silently does not fire.

So selection is the union of two clauses:

1. orders with `placed_at` in the window — the primary clause, unchanged; and
2. orders, **of any age**, holding a `raw_payload` whose `retrieved_at` is
   newer than the `finished_at` of the last completed run for this tenant.

Clause 2 is bounded by construction: an order enters it only when evidence
actually arrived, so a steady state selects nothing. With no completed run it is
empty and clause 1 alone applies, which is why the first run needs no special
case. The run row stores both the window and the evidence-since timestamp, so a
reader can tell which clause pulled an order in.

This was offered as a third option when the window basis was chosen and not
taken. It is taken now because the dispute case makes it load-bearing rather
than defensive: without clause 2 there is no lag long enough to matter that the
run can see.

### D4. `run_window` loads a subset; `reconcile.run` is untouched

`reconcile.run(session, tenant_id)` stays as Block C delivered it. The windowed
entry point is a new function that selects the order refs in the window and
reconciles those, so the window is a filter on input rather than a parameter
threaded through the comparison logic. An empty window is a successful run with
zero findings, not an error.

### D5. `processed_events` is keyed on a job idempotency key

`UNIQUE (tenant_id, event_id)`, where `event_id` is supplied by whoever
enqueues. A scheduled run's key is its tenant and window —
`recon:<tenant>:<start>:<end>` — so the same window cannot be reconciled twice.

[[tech-stack]] §4 says "the provider's event id", and R8 removed the thing that
would supply one. Keying on the enqueuer's idempotency key keeps the property
§4 actually argues for — the dedupe marker and the financial write share one
`COMMIT` — and makes it exercised by the only path the MVP has, rather than
shipping a table nothing writes to. When webhooks land, a provider event id is
an idempotency key like any other and needs no schema change.

The marker is inserted **in the same transaction** as the run and exception
rows. A second enqueue of the same key fails on the constraint and rolls back,
so the rows are written exactly once.

### D6. Retry is attempts, `run_after`, and a terminal failure

R7's narrowing, stated as columns: `attempts`, `max_attempts`, `run_after`,
`last_error`, and a status reaching `failed` when attempts are exhausted. A
fixed delay, not a tuned backoff. A job that fails records the exception type
and message in `last_error` — never a traceback, which can carry row contents.

A claimed job whose process dies is handled by `run_after` and the claim
timestamp, not by a heartbeat: the simplest correct form.

### D7. The worker claims; it no longer only ticks

`reckonai/worker.py`'s Phase 1 loop logs a tick and sleeps. It becomes: claim up
to *n* jobs with `SKIP LOCKED`, execute each in its own transaction, sleep when
the queue is empty. `ShutdownSignal` is kept exactly as it is — a `SIGTERM`
mid-tick is already handled, and that is more important once a tick does work.

A job's handler is looked up from a registry keyed by job type, so Block F's
later job types are a registration rather than a change to the loop.

### D8. The Cron Job is declared, not applied

`render.yaml` gains a `cron` service invoking the scheduled run hourly. The
Blueprint is **not** synced in this block: R2 budgeted one deploy, Block A's
merge spent it, and a sync redeploys the web service and worker too, re-running
migrations and the demo seed. Phase 8's fourth exit criterion — the cron job
fires on the deployed environment — is therefore **deferred to Block F's
deploy** and recorded as deferred in `validation.md`, not claimed here.

What is proven here instead: the start command is a real module entry point,
runnable locally against the compose database, with `--no-sync` for the reason
`render.yaml` already documents.

### D9. The scheduling entry point is a CLI module

`python -m reckonai.jobs.schedule --window-days N` enqueues a run for the
window ending today. It enqueues; it does not execute. The worker executes.
That split is what makes the cron job cheap and the concurrency safe — a cron
invocation that did the work itself would be a third process writing financial
rows.

### D10. `awaiting_evidence` is re-evaluated by re-running, not by a state machine

R10 says the run is the retry. There is no parked-event table and no transition
logic: the next run over a window reconciles those orders again from current
evidence and records whatever it now concludes. The append-only history is how
anyone sees the verdict changed.

### D11. `dispute` becomes position-moving, proven on a hand fixture

Block C's D7 precedent: **encode the rule, seed nothing.** The clean baseline
stays at 15 purchase units and two non-faults, so the false-positive gate keeps
measuring the same thing.

`dispute` joins `POSITION_CATEGORIES` as a position-moving debit. Without it the
classifier's ledger sum omits a dispute loss the expected entitlement has
already absorbed through the refund, so the ledger appears to overstate the
seller and a correct marketplace is reported as not having reflected its refund.
That is a false positive on correct data.

**The hazard this creates is double-counting, and it is the same discipline
Block C's handoff names.** A marketplace may book the loss once or twice:

| Marketplace books | Expected behaviour |
|---|---|
| a `dispute` debit only | position moves once; no finding |
| a `refund` debit only | unchanged from today; no finding |
| both, for the same refund | **one** debit counted, no finding |
| a `dispute` debit with no provider refund | a finding — the loss is unevidenced |

The third row is where a naive inclusion breaks: summing both halves debits the
seller twice and reports a shortfall that does not exist. So a `dispute` entry
that duplicates a `refund` entry already counted for the same refund is **held
out**, which is the first of the two mechanisms Block C's handoff permits — a
defect's own row held out of every later comparison — rather than crediting
`explained`.

The dispute-to-capture join stays unestablished, and the rule does not need it:
the entries are matched on the order and the refunded amount, not through
`dispute.capture_id`. That column stays `NULL` and `buyer_transaction_id` stays
the buyer's side, both as the model documents.

**Fixture, not seed.** A hand-authored dispute-with-refund case covering all
four rows above, in `tests/`, labelled as hand-authored the way the replan's
R16 requires of any record the sandbox could not produce. The real dataset has
one open dispute that writes no ledger entry on purpose, and no PayPal dispute
fixture at all, so nothing here can be proven against it.

**The false-positive gate is re-run after this change**, because widening the
position categories touches every expected-versus-ledger comparison in the
product. A clean fifteen before and after is the check.

## 3. Risks named rather than discovered

**The purity gate must not widen.** `reckonai/recon/` and `reckonai/engine/`
stay pure. The new code holds sessions and reads a clock, so it lives in
`reckonai/jobs/` and is excluded from the purity gate but included in strict
mypy, which is a deliberate asymmetry rather than an oversight.

**Two workers already run concurrently on every deploy** (observed 2026-11-08,
~90s of overlap on Render's rolling deploy). SKIP LOCKED correctness is the
condition production is already in, so it is proven by a test with two real
concurrent claimers, not by inspection.

**The test suite is order-dependent** (Block A's `validation.md` §8 debt 4).
These tables are new, and `tests/test_migrations.py` downgrades to `base`, so
any test committing a job or exception row must clean up at teardown or the
downgrade meets rows it cannot drop through `ondelete="RESTRICT"`.

**A new model module must be registered** in `reckonai/models/__init__.py`, or
autogenerate reads its table as one to drop.

**A dispute-settling refund is selected by D3 and then mis-expected, and that
is not this block's to fix.** Two reasons, both outside `jobs/`:

- `classifier.py`'s `POSITION_CATEGORIES` deliberately excludes the `dispute`
  and `fee` ledger categories, because neither appears in the dataset and
  treating an unseen category as position-moving would change every expected
  figure on no evidence. That reasoning was right for Block C. But a dispute
  loss booked under `entry_category='dispute'` is then ignored by the expected
  position while PayPal's reported figure reflects it — a mismatch on correct
  data, which is a false positive of exactly the kind Phase 11 gates against.
  `dispute` is already in the checked `LEDGER_ENTRY_CATEGORIES` vocabulary, so
  the category is reachable; only the classifier's treatment of it is absent.
- `dispute.capture_id` is `NULL` throughout. The model records why:
  `disputed_transactions[].buyer_transaction_id` is the buyer's side of the
  transaction and the dispute-to-capture join is not established. So nothing
  currently ties a dispute to the capture whose refund settles it, and a
  dispute-driven refund is indistinguishable from an ordinary one.

**Nothing in the dataset can prove either.** There is one seeded dispute, open,
`WAITING_FOR_SELLER_RESPONSE`, which writes no ledger entry on purpose — an
open dispute moves no money, and a speculative debit would make the clean
baseline wrong (P4). There is no PayPal dispute fixture at all, and no refund
anywhere that settles a dispute. So this is hand-fixture territory, and the
right call is Block C's D7 precedent: encode the rule, seed nothing.

**Taken in this block, on a hand-authored fixture — see D11.**

## 4. What this block hands forward

- **Block F reads `reconciliation_exception` and `reconciliation_run`.** The
  columns are the frozen `Finding` contract plus `run_id`; the queue filters on
  stored `is_exception`.
- **Block F owns the deploy** that makes the cron job real (D8), and Block A's
  debt 5 — seeding the deployed database — which `reckonai.demo_data --ensure`
  in `render.yaml`'s `preDeployCommand` appears to have already settled; Block F
  should verify rather than rebuild.
- **Block E's agent may enqueue nothing.** It calls `reconcile.run` for a
  recompute, as Block C's handoff states. The queue is not an agent tool, since
  P6 keeps the agent read-only.
- **Job types are a registry entry** (D7), so Block F adds one without touching
  the loop.

## Related

- [[roadmap]] Phase 8 — the five exit criteria, four of which are met here
- `specs/2026-11-08-replan/requirements.md` — R7, R9, R10, R16, and R2's deploy
  budget
- [[tech-stack]] §4 — the queue, and the `COMMIT` argument D5 preserves
- `specs/2026-11-09-matching-classification/requirements.md` §4 and D8 — the
  handoff and the table this block owes
- `specs/2026-11-08-block-a-marketplace-connector/validation.md` §8 — debts 4
  and 5
