# Live ingestion — scope, decisions, context

> Entry: replan R6 and R8, which this partly reverses. Read
> `specs/2026-11-09-queue-scheduled-run/validation.md` §7b before touching
> `jobs/`. Companions: `plan.md` (task groups), `validation.md` (merge gate).
> **Nothing here is built.** Items marked *unverified* may not be designed
> around until task 0 records them (`multiparty-direction.md` §8).

## 1. Scope

PayPal events and the marketplace's own records reach the database as
transactions happen. **Reconciliation stays a date-windowed run**; it is not
per-event. In: webhook ingestion, live retrieval, missed-event recovery, a
write side on the simulated marketplace, and one rule letting a same-day run
see evidence that arrived after the day's first run.

Out: per-event reconciliation, a real marketplace backend, production PayPal,
the referenced-payouts release (P6), approver authentication, any change to
the engine, classifier, `window.py` selection or agent.

## 2. Documents amended

`mission.md` §4 (the deferral is closed: ingestion is event-fed, reconciliation
stays windowed), `tech-stack.md` §4 (backfill reinstated) and §8 (webhook
paragraph becomes the design here), replan R6 (partly superseded) and R8
(superseded for ingestion). `multiparty-direction.md` §7's simulated-data
boundary is adopted (D4).

## 3. Decisions

- **D1. A webhook is a notification, never the record.** Store the event as a
  `raw_payload` (P7), re-fetch the resource through the order read path, and
  pass it to the existing `paypal/replay.py` normalizer. Payloads differ by
  read path, which is why the four ingester rules exist.
- **D2. Reconciliation stays windowed.** Live records are picked up by the next
  run through clause 2 of `window.py`. The webhook handler never enqueues one.
- **D3. Replay stays as an input mode.** CI, the eval and demos need fixtures.
- **D4. The simulator is the marketplace, labelled.** Simulated records carry a
  record-level marker; a simulated value never enters a result as provider
  evidence, is never cited by the agent as PayPal's, and never populates
  `fx_conversion`.
- **D5. Sandbox only.**
- **D6. Idempotency is the existing constraint.** The PayPal event id goes in
  `processed_event`, in the same transaction as the financial write.
- **D7. Same-day re-run rule** (§4).
- **D8. Cooldown: 5 minutes, configurable** (`RUN_COOLDOWN_SECONDS`). Evidence
  arriving during it is picked up by the first run after it.
- **D9. No automatic run trigger now.** The rule is a permission for run-now
  and the daily cron. A trigger is a separate later change.
- **D10. Mapping unchanged.** The write side emits the fields the read endpoints
  already serve, so `sim_mapping.py` is untouched. Any new simulator field goes
  through `validate.py`'s gate. `mapping_version` already exists on
  `raw_payload`, which every canonical record links to; no migration.

## 4. The same-day re-run rule

**Problem.** A run and its key are date-grained (`idempotency_key`), by design:
an hourly key appended 24 identical row sets (`schedule.py`). A refund arriving
after the day's run therefore waits up to a day, and run-now answers
`already_done`.

**Rule.** A run over dates that already have one is permitted only if some
`raw_payload` for the tenant has `retrieved_at` after
`last_completed_watermark` (clause 2's own test, so gate and selection cannot
disagree). Dates with no run are always permitted, which keeps the nightly
`awaiting_evidence` re-evaluation.

**Key.** `recon:{tenant}:{start}:{end}:{watermark | "none"}`. Concurrent
enqueues share a watermark, so the second is refused at the constraint. The key
alone is not enough, because each completed run moves the watermark; the gate
is what stops a chain of runs. Both are required.

**Changes.** `idempotency_key` takes the watermark. The new-evidence check is
one function in `jobs/window.py`, used by `schedule.enqueue_runs` and
`queries.request_run`. A new status `nothing_new` is distinct from
`already_done`. The "not examined until tomorrow's run" wording in
`queries.py` and `frontend/src/copy.ts` becomes false and changes.

**Known effects.** Old-format keys will not match: one extra run is possible
once on deploy day (harmless). Run-now (whole dataset) and the cron (2-day
window) have different date components and do not collide.

## 5. Webhook receiver

- **F1** public `POST` route; acknowledge `200` after durable store and
  enqueue; work happens in the queue.
- **F2** signature verification; unverifiable events are rejected, never
  accepted on a guess. *Unverified:* whether a partner credential verifies
  events for connected sellers, and whether the sandbox signs identically.
- **F3** dedupe on event id via `processed_event`.
- **F4** handler job (pattern: `investigation_job`): route, fetch, normalize,
  commit marker and records together.
- **F5** routing: merchant to seller to tenant. Unknown merchant is stored and
  recorded as unrouted, never guessed into a tenant.

| Case | Behaviour |
|---|---|
| Bad or missing signature | Rejected |
| Duplicate | Acknowledged, no second effect |
| Unknown event type | Stored, acknowledged, no records, logged |
| Unknown merchant | Stored, recorded as unrouted |
| Re-fetch `403` | Job fails, error recorded; never an absent field |
| Re-fetch `5xx` | No blind retry in the call; bounded queue retry |

Event types are *unverified*; task 0 produces the list and the registry is
closed over it.

## 6. Live retrieval and recovery

- **F6** live fetcher per seller (`PayPal-Auth-Assertion`), obeying every
  `tech-stack.md` §8 client rule. `paypal/capture.py` is the start; make it
  callable from a job.
- **F7** its output goes through `paypal/replay.py`. No second normalizer.
- **F8** lag: an incomplete live record is `awaiting_evidence` via the existing
  path (P4), never an exception.
- **F9** recovery job re-lists recent events or, failing that, re-fetches
  orders in the window; `processed_event` makes repeats no-ops. *Unverified:*
  the events-list endpoint's retention and partner access.
- `retrieved_at` of a live record is our fetch time, already documented as
  such (`window.payload_times`).

## 7. Simulator write side

The `/sim` endpoints are public and read-only today.

- **F10** place an order: real sandbox transaction plus the marketplace order,
  items, payment and ledger credit together. *Unverified:* automating sandbox
  buyer approval (`scripts/probe_card_capture.py` suggests a card path).
- **F11** refund: PayPal refund plus marketplace refund and ledger debit.
- **F12** ledger write.
- **F13** fault injection: an explicit parameter that skips, duplicates or
  alters the marketplace-side entry. Never a side effect.
- **F14** the simulated marker on every record created.
- **F15** guard: disabled unless an environment variable enables it, and a
  shared secret is required when enabled. Not optional on a public app.

## 8. Held by the operator

Webhook id and sandbox credentials on Render; the F15 secret. An agent does
not read them.

## 9. Unproven even after this lands

Behaviour on Render (the nightly cron has not been seen running either);
anything beyond a simulated marketplace; delivery ordering and burst volume;
event coverage beyond what task 0 observes.
