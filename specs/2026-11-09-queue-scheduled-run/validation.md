# Block D — validation

> How each criterion was proven. 1,659 tests pass (1,651 before the code
> review's nine fixes added eight), `uv run mypy` clean over
> `engine`, `recon` and now `jobs`, `uv run ruff check` clean, migration clean
> up and down with no autogenerate drift.

## 1. Phase 8's exit criteria

| # | Criterion | How it is proven | State |
|---|---|---|---|
| 1 | Two workers never claim the same job | `test_jobs_queue.py` - two real sessions, the first holding its lock **uncommitted** while the second claims; disjoint id sets, union is all six jobs | **met** |
| 2 | A failing job retries and lands terminal with its error | `test_jobs_queue.py` three attempts to `failed`, plus `test_jobs_dispatch.py` through the real handler path | **met** |
| 3 | The same event id twice raises a constraint violation | `test_jobs_queue.py` on the marker, and `test_jobs_reconcile_run.py` on a real repeated window | **met** |
| 4 | The cron job fires a date-windowed run on the deployed environment | **deferred to Block F** - see §4 | deferred |
| 5 | An incomplete record and a parked exception are re-evaluated by a later run | `test_jobs_reevaluation.py`, on F5 in the real seeded dataset | **met** |

Criterion 1 was not a condition to construct: two workers already overlap for
~90 seconds on every Render rolling deploy (observed 2026-11-08).

**Criterion 2 found a real boundary.** The claim has to be **committed before
the handler runs**. Sharing one transaction means a failing handler's rollback
also undoes the claim *and* the attempt increment, so the job returns to the
queue looking untried and retries forever - the attempt count is what makes a
failure terminal, so it has to be durable before the attempt is made.
`handlers.dispatch` refuses a job whose claim is uncommitted, and
`test_dispatch_refuses_a_job_whose_claim_is_not_committed` pins it, because the
mistake is silent until a handler raises.

**Criterion 5 is proven on F5 rather than on a fixture.** ORD-0017 is the
manifest's late-refund fault: the marketplace recorded a 15.00 USD refund and
reflected it correctly, and PayPal's record has not arrived. The test asserts
the precondition (it starts parked, and `is_exception` is false), inserts the
late provider refund the way `reckonai.paypal.replay` builds one, re-runs with
an unchanged window, and asserts the `awaiting_evidence` row is gone from the
second run while the first run's row survives.

## 2. The exception table (Block C's D8)

- `test_a_finding_round_trips_without_losing_a_field` asserts field by field
  against `as_dict()`, not by spot-checking - the contract's point is that
  Block F renders every key.
- `is_exception` is stored and agrees with `exceptions_only()`. The run's
  counts come from `exceptions_only()`, and a `CHECK (exception_count <=
  finding_count)` would catch the two being filled in the wrong order - the
  trap Block C's handoff names.
- Append-only, proven on the pair of runs criterion 5 uses: the parked row
  stays, and that difference across runs **is** the evidence R10 fired.
- A composite foreign key refuses an exception naming another tenant's run.

Measured end to end against the seeded tenant: 18 orders selected, **6
findings, 5 exceptions**, with the manifest's own impact figures (10.00
commission, 27.00 missing ledger adjustment, 90.00 duplicate, 32.16 FX, 15.00
EUR disbursement). The gap between 6 and 5 is `awaiting_evidence`, which is the
whole reason the counts are taken separately.

## 3. The windowed selection (D3)

- Clause 1 in SQL, over **every** order rather than the matched ones: an order
  with no PayPal record belongs in the window so R10 can re-evaluate it, and
  reading clause 1 off the assembled cases silently dropped it. Found by
  running it - the first end-to-end run selected 9 orders where it should have
  selected 18.
- Half-open bounds, so consecutive runs cover each day exactly once.
- **Clause 2 is proven on its own**, twice: in isolation on the dispute shape
  (`test_a_late_refund_selects_an_order_the_window_has_moved_past`), and
  against the real dataset over a window deliberately containing no order at
  all, so nothing but clause 2 can select ORD-0017.
- Bounded: an old order with no new evidence is not selected, so a steady
  state selects nothing.
- The two clauses are disjoint sets, so an order matching both is counted once.
- An empty window is a successful run with zero findings, not an error.

**A dataset fact worth recording.** The seeded `placed_at` values are fixed in
November 2026 - `_at(day)` is `datetime(2026, 11, day, 12:00 UTC)` - and run
from the 2nd to the 19th, with all six faults late in that range (ORD-0013 on
the 14th through ORD-0018 on the 19th). So a *trailing* two-day window covers
only part of the dataset, and run on 2026-11-10 it selected nine clean orders
and no faults at all.

That is correct behaviour rather than a defect, and it needs **no reseed and no
widened window** - see §9 debt 2. The exception queue reads the latest verdict
per fault across runs rather than one run's rows, so a single wide initial run
populates every order and trailing runs keep it current.

## 4. What is deferred, and why it is not debt

**Criterion 4 is deferred to Block F's deploy** (D8). R2 budgeted one deploy
and Block A's merge spent it; a Blueprint sync also redeploys the web service
and worker, re-running migrations and the demo seed.

Proven here instead: the `cron` service is declared in `render.yaml` with its
schedule and start command, that command is a real `python -m` entry point, and
it runs against the compose database. `test_jobs_schedule.py` asserts the
Blueprint names the module this package provides, carries `--no-sync`, and
**says in the file that it is not applied** - so a reader cannot mistake a
declared service for a running one.

What is unproven is that Render invokes it on schedule, which is a statement
about Render rather than about this code.

## 5. The dispute rule (D11)

All four bookings pass on a hand-authored fixture, labelled as hand-authored:
a `dispute` debit alone, a `refund` debit alone, both for one refund, and a
`dispute` debit with no provider refund behind it. Two further tests pin the
hold-out's edges - it matches on amount, and it is not a blanket exemption.

**The fixture found that `POSITION_CATEGORIES` was only half the fix.** Adding
`dispute` to the position categories left the commission expectation
unreduced and raised a `missing_ledger_adjustment`, because
`_partition_by_ledger` read only `refund` entries when deciding whether a
provider refund had been recorded. A marketplace that books the loss as a
dispute **has** recorded the movement, so one correct booking produced two
findings. Both places had to change:

- `POSITION_CATEGORIES` decides what moves a seller's position;
- `_partition_by_ledger` decides what counts as having recorded a refund.

The double-counting hazard is handled by `_dispute_surplus`, which holds out a
dispute debit that restates a refund debit for the same order, seller and
amount - the first of the two mechanisms Block C's handoff permits. It matches
on the amount rather than through `dispute.capture_id`, which stays `NULL`
because the dispute-to-capture join is not established.

**The false-positive gate was re-run** and is unchanged: zero exceptions on the
fifteen clean purchase units, six faults detected with the manifest's figures.
That check was the point of running it - widening the categories touches every
expected-versus-ledger comparison in the product.

## 6. Gates that stayed green

- `uv run pytest`: **1,659 passed**, in collection order. Two new modules
  commit rows (`test_jobs_queue.py`, `test_jobs_dispatch.py`) and delete them
  at module teardown; both sort before `test_migrations` alphabetically, so the
  downgrade to `base` meets none of them. **Neither may be renamed to sort
  after it** (Block A's `validation.md` §8 debt 4).
- `uv run mypy` strict, clean. `src/reckonai/jobs/` was **added to the scope**:
  it is the one package deliberately outside the purity gate, which makes the
  type gate matter more there, not less.
- The purity gate: `engine/` and `recon/` still hold no session, client or
  clock. `jobs/` holds all three, by design.
- `uv run alembic upgrade head` / `downgrade base` clean both ways, `alembic
  check` reports no drift.
- `uv run ruff check` clean. (`ruff format` is not a repo gate - 61 files
  predate it - so nothing was reformatted.)

## 7. Not proven here, and who proves it

- **That Render invokes the cron job on schedule** - Block F, with the deploy.
- **That the dashboard renders these rows** - Block F. This block guarantees
  the rows exist and match the frozen contract.
- **That a real disputed capture classifies correctly.** No such fixture exists
  and none can be captured: the sandbox's one dispute is an open inquiry that
  moves no money, there is no PayPal dispute fixture, and no refund settles a
  dispute. The rule rests on a hand-authored fixture, exactly as Block C's D7
  decided for the `PENDING`-capture rule.
- **That the dispute-to-capture join works** - nothing proves it, because it is
  not established. D11's rule matches on order and amount, so nothing depends
  on it.
- **That a second worker's claim is skipped under real concurrency on Render**
  rather than in one process - the test uses two sessions against one engine,
  which is the same lock behaviour and not the same scheduler.
- **That the manifest scores** - Block G's eval harness.

## 7b. The code review, and the nine fixes

`/code-review medium` on PR #22, 2026-11-10. **Nine findings, all nine real**
on verification - the mechanical claims were each checked against the code
before being accepted, since Block C's round contained two false positives.
Every one is fixed, and each has a regression test.

| # | Severity | What was wrong | Fix |
|---|---|---|---|
| 1 | high | **A `claimed` job was unrecoverable.** Nothing moved a row from `claimed` back to `queued`, and `drain` does not wrap `dispatch`, so a process dying between the claim and the handler's own error handling left a job no worker would take again - no error recorded, window silently never reconciled. Two docstrings cited `run_after` as the recovery path, which it could not be, because the claim never returned to the status `run_after` filters on | `claim` also takes a `claimed` job whose `claimed_at` is older than `STALE_CLAIM_SECONDS` (300), capped by `attempts < max_attempts`; `reap_stale_claims` terminates a stale job already at its cap, with the reason recorded. The worker reaps before claiming |
| 2 | high | **Unmatched provider payments were dropped forever.** Clause 1 cannot reach a payment with no `placed_at`; the first run has no watermark, and every later watermark is past the payload's `retrieved_at` - so an `awaiting_evidence` finding `reconcile.run` deliberately produces was filtered out of every run | With no watermark, every unmatched payment is selected, so it is reported once rather than never; after that, only newly arrived ones |
| 3 | medium | **The hourly cron was effectively daily**, contradicting the comment written to justify hourly. Both window bounds snap to midnight UTC and the key uses only their dates, so all 24 ticks compute one key: the first works, 23 skip | Schedule is daily at 01:00 UTC, and both comments corrected. Making the key hourly instead would append 24 identical row sets a day for Block F to read through, which is worse than a day's latency on a provider that is itself a day behind |
| 4 | medium | **Evidence arriving mid-run was missed by that run and excluded by the next.** `payload_times` is read at the start, `finished_at` stamped at the end, so a payload inserted between is invisible to this run and behind the next run's watermark | The watermark is the previous completed run's `started_at`. `last_completed_finish` renamed `last_completed_watermark`. Re-covering ground is the safe direction, which that function's own docstring already argued |
| 5 | medium | **The D11 fix had a symmetric false negative.** A `dispute` debit of 30.00 vouching for a 30.00 provider refund with no `refund` row is indistinguishable from an unrecorded refund beside an unrelated chargeback loss - two losses booked once, netting to zero and reporting nothing | Neither guess. `_partition_by_ledger` returns a third group, and a dispute-backed refund is reported `awaiting_evidence` naming both records - P4's first-class non-error state, P5's escalate-rather-than-guess. Treated as reflected for the arithmetic so no residual is invented, `confirmed=False` so Block F shows it as suspected |
| 6 | low | `ReconciliationRun.last_error` was unwritable and `failed`/`running` unreachable: the run shares one transaction with its handler, so a dead run leaves no row to annotate. Three docstrings reasoned about failed runs the design cannot produce | Column dropped, migration regenerated, vocabulary kept with the reason stated - the failure is recorded on the `job` row, which is what retries |
| 7 | low | `order_count` was `len(selection)`, which is `all_refs` - including unmatched payment references | `len(selection.order_refs)` |
| 8 | low | The `IntegrityError` branch in `main` could not fire for its stated reason, and a duplicate window was retried to exhaustion into a terminal `failed` row - the noisy queue the pre-check exists to avoid | `AlreadyReconciled` wraps the constraint violation; `dispatch` completes the job instead of retrying. The dead branch is removed |
| 9 | low | The per-tenant isolation claim was false at enqueue: one commit for all tenants meant any failure discarded every tenant's enqueue | Committed per tenant |

**Two framings corrected rather than accepted.** Finding 2's reasoning said
newly arrived payments were dropped too; they are not, and the test proving it
passes - the real defect is narrower and is the permanent drop above. Finding 5
is a trade-off, not a plain defect: the old code guessed one way, this block
guessed the other, and the shape is genuinely undecidable from the records.

**The lesson is Block C's, again.** Seven of the nine are invisible to the
seeded dataset: it has no `dispute` ledger row, no unmatched provider payment,
no job that dies mid-claim, and one run per test. The gate proves the dataset,
not the logic.

**The false-positive gate was re-run after all nine**: 18 orders, 6 findings,
5 exceptions, the manifest's own figures, unchanged.

## 8. Corrections this block made to earlier documents

1. **D3 gained a second clause before implementation.** `placed_at` alone
   cannot see a dispute settled weeks later; the amendment and its reasoning
   are in `requirements.md` D3.
2. **`tests/test_render_blueprint.py::test_exactly_three_components` was
   rewritten.** It asserted two services and one database, and the count was a
   proxy for tech-stack §4's single-`COMMIT` argument. A cron job breaks none
   of that - it enqueues into the same Postgres - so the invariant is now
   asserted directly: one database, and nothing that could be a second queue.
   The components are also named rather than counted, so a failure says which
   one appeared.
3. **Phase 1's worker comment promised Phase 8 would replace the startup
   connection check.** It was kept instead: a claim that cannot reach the
   database now raises inside `drain`, the loop exits non-zero, and Render
   restarts the process - that is the retry. A second mechanism would only
   decide how long to hide the same failure.

## 9. Debts this block hands on

1. **The reconciliation is computed over every order and then filtered to the
   window.** `reconcile.run` is called whole and its findings filtered, which
   keeps one classification path shared with Block E's recompute and keeps
   `reconcile.py` free of windowing - at the cost of classifying out-of-window
   orders and discarding them. At eighteen orders this is not worth a second
   code path that could disagree with the first. It would be at eighteen
   thousand.
2. **The queue must read the latest verdict per fault, not a run's rows.**
   This entry replaces one that said Block F's dashboard "shows what the last
   run covered" and that the demo therefore needed a widened window or a
   re-dated seed. **That was wrong about Phase 14**, which requires the
   exception queue to group, sort and filter *over the seeded dataset* and
   ranks it by `readme.md` §9.3's factors - impact, affected sellers, age,
   deadlines, confidence. Only R13's lean **run summary** is run-scoped, and
   only through its click-through to that run's exceptions.

   The real consequence of D1's append-only table: one fault gets a row per
   run, so six faults over ten runs are sixty rows. A queue selecting all of
   them repeats each fault ten times; one selecting only the newest run shows
   only what that window covered. The queue therefore reads **the latest row
   per `(order_external_ref, code)` across runs**, which is also what makes
   the history useful rather than awkward - the superseded rows stay as the
   audit trail of when a verdict changed, which is how R10 is observable.

   This dissolves the windowing worry rather than needing a reseed: one wide
   initial run populates all eighteen orders, trailing two-day runs update
   whatever they cover, and the queue reads the latest verdict for each. **No
   reseed and no widened cron window are required.**

   Block D deliberately does not build that query - it is a dashboard read,
   and `reconciliation_exception` carries the index for it
   (`tenant_id, run_id, impact_amount`) plus indexes on `code`, `order_
   external_ref` and `is_exception`. A latest-per-key read may want an index
   on `(tenant_id, order_external_ref, code)`; Block F should add it when it
   writes the query rather than guessing the shape now.
3. **`job.payload` is untyped JSONB.** The handler reads two ISO dates out of
   it and `_window_from` is the only parser, so a malformed payload fails at
   dispatch with a legible error rather than silently. A per-job-type schema
   would be better and is not worth it at one job type.
