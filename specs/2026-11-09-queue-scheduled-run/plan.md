# Block D — plan

> Task groups, run in order. Each is one pass: implementation and tests written
> together, per the replan's R2. One commit for the block.
>
> Groups 1-3 are the substrate, 4-5 persist, 6-7 schedule, 8 is the dispute
> rule, 9 closes out. Group 8 is the only one touching `recon/`.

## 1. The `jobs` table and `processed_events`

- `reckonai/models/jobs.py`: `Job` (`tenant_id`, `job_type`, `payload` JSONB,
  `status`, `attempts`, `max_attempts`, `run_after`, `claimed_at`,
  `last_error`), and `ProcessedEvent` with `UNIQUE (tenant_id, event_id)`.
- Status is a checked vocabulary, not free text — the same reasoning as
  `entry_category`.
- Register both in `reckonai/models/__init__.py`. `tests/test_model_registry.py`
  fails if either is missing.
- Alembic revision. `uv run alembic upgrade head` then `downgrade base` clean.

## 2. Claiming with `SKIP LOCKED`

- `reckonai/jobs/queue.py`: `enqueue(session, ...)`, `claim(session, limit)`
  issuing `SELECT ... FOR UPDATE SKIP LOCKED`, `complete`, `fail`.
- `fail` increments `attempts`, sets `run_after` and `last_error` (exception
  type and message, never a traceback), and reaches terminal `failed` at
  `max_attempts`.
- Test: **two real concurrent claimers never claim the same job.** Two sessions,
  overlapping claims, asserted on disjoint id sets — not inspection.
- Test: a job failing to its cap lands `failed` with its error recorded.

## 3. The worker claims instead of ticking

- Replace `worker.py`'s tick body with claim / dispatch / sleep-when-empty.
  Keep `ShutdownSignal` exactly as it is.
- A handler registry keyed on `job_type`, so Block F registers rather than edits
  the loop.
- Each job runs in its own transaction. An unhandled error fails that job, not
  the loop.

## 4. The run and exception tables

- `reckonai/models/reconciliation.py`: `ReconciliationRun` (window bounds,
  `evidence_since`, `started_at`, `finished_at`, `status`, `finding_count`,
  `exception_count`) and `ReconciliationException`, one row per `Finding` — the
  frozen contract's every field, amounts `NUMERIC` with their own currency
  column, `evidence_refs` JSONB, `is_exception` stored.
- Register. Alembic revision. Append-only: no code path updates or deletes a
  prior run's rows.
- Test: a `Finding` round-trips to a row and back with no field lost, pinned
  against the frozen JSON contract so the two cannot drift.

## 5. Persisting a run

- `reckonai/jobs/reconcile_job.py`: open a run row, call the windowed
  reconciliation, write findings, close the run with its counts.
- **Counts come from `exceptions_only()`**, never `len(findings)`.
- The `processed_events` marker and the financial rows are inserted in **one
  transaction** — tech-stack §4's whole argument.
- Test: enqueueing the same idempotency key twice writes exactly one set of
  rows, the second attempt failing on the constraint and rolling back.

## 6. The date-windowed run

- `reckonai/jobs/window.py`: select order refs by the union of D3's two
  clauses — `placed_at` in the window, plus any order holding a
  `raw_payload.retrieved_at` newer than the last completed run's `started_at`
  (`finished_at` as first written; corrected by review, `validation.md` §7b).
- Clause 2 is empty with no completed run, so the first run needs no special
  case. Both the window and the resolved `evidence_since` are stored on the run.
- Test: a narrow window over the seeded orders selects the known subset — the
  eighteen `placed_at` days make this exact.
- Test: an order outside the window, given a payload retrieved after the last
  run, is selected by clause 2 and by nothing else.
- Test: an empty window is a successful run with zero findings, not an error.

## 7. Scheduling and the Cron Job

- `reckonai/jobs/schedule.py`, a `python -m` entry point taking
  `--window-days`. It **enqueues only**; the worker executes.
- Declare the `cron` service in `render.yaml` — daily at 01:00 UTC (hourly as
  first written; corrected by review), `--no-sync`, with the
  reasoning already in that file. **Do not sync the Blueprint** (D8).
- Test: the module enqueues one job with the expected idempotency key and
  writes no financial row.

## 8. The dispute rule (D11)

- Add `dispute` to `POSITION_CATEGORIES` in `recon/classifier.py`, **and let a
  `dispute` debit count as having recorded a refund in
  `_partition_by_ledger`** — the categories decide what moves a position, that
  decides what counts as recorded, and only changing one left the commission
  expectation unreduced.
- **A refund whose only counterpart is a `dispute` debit is
  `awaiting_evidence`, not silence** (added by code review): the shape is
  undecidable from the records, so neither verdict is honest. Third group out
  of `_partition_by_ledger`, reflected for the arithmetic, `confirmed=False`.
- Hold out a `dispute` entry that duplicates a `refund` entry already counted
  for the same refund, matched on the order and the refunded amount — not
  through `dispute.capture_id`, which stays `NULL`.
- Hand-authored fixture covering D11's four rows: dispute only, refund only,
  both for one refund, and a dispute debit with no provider refund. Labelled
  hand-authored. Plus the symmetric input review found — an unrecorded refund
  beside a dispute debit of the same amount, which must not be netted to
  silence.
- **Re-run the false-positive gate.** Widening the categories touches every
  expected-versus-ledger comparison, so a clean fifteen before and after is the
  check, not an assumption.
- `uv run mypy` stays clean; `recon/` stays pure under the purity gate.

## 9. Close out

- `uv run pytest` whole suite, in collection order — no randomisation, no
  `-n auto` (Block A's debt 4). Any test committing a job or exception row
  cleans up at teardown, or `test_migrations.py`'s downgrade to `base` meets
  rows it cannot drop through `ondelete="RESTRICT"`.
- `uv run ruff check` and `format`. No emojis anywhere, no `float` on a money
  path.
- `validation.md` filled in against the five Phase 8 criteria plus D11, with
  criterion 4 recorded as deferred to Block F's deploy.
- Update the replan's "State" section and `CLAUDE.md` to point at Block E.
- One commit.
