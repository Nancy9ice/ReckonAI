# Live ingestion — validation

> Every merge to `main` deploys, so the gate is what runs before merge. Results
> are written here as they come, with failures stated plainly. Exit criteria in
> `roadmap.md` still bind, Phase 11's zero-false-positive gate first.

## 1. Before merge (all required)

1. **Suite.** Reset the dev database first (`python -m reckonai.demo_data
   --reset`), then `pytest`, `uv run mypy`, and `python -m reckonai.eval`
   (deterministic half) all pass.
2. **Duplicate event.** The same event delivered twice produces one set of
   records.
3. **Signature.** An invalid or missing signature stores nothing as accepted.
   An unverifiable event is rejected.
4. **Edge cases.** Every row of the `requirements.md` §5 table has a test,
   including `403` never recorded as an absent field.
5. **Re-run rule.** A refund ingested after the day's run permits a same-day
   run; with no new evidence a re-run is refused as `nothing_new`; two
   concurrent requests produce one run; the first run of a new day is always
   permitted; the cooldown holds.
6. **Live sandbox, locally.** A new transaction placed through the write side
   appears without a replay and the next run examines it.
7. **Planted fault.** A fault injected on a live transaction is reported as
   the expected code.
8. **No false positive.** A clean live transaction produces zero exceptions,
   and a half-ingested one is `awaiting_evidence`. The existing clean baseline
   is unchanged.
9. **Guard.** The simulator write side is unreachable without its enabling
   variable and secret.
10. **Boundary.** No code path labels a simulated value as provider evidence;
    a test asserts the marker is present on every write-side record.
11. **Verification spike recorded** in §3 below before groups 3-6 merge.

## 2. After merge (a checklist, not a gate)

On Render, with the operator's credentials: the webhook endpoint receives a
sandbox delivery and verifies it; the recovery job runs; a live transaction
reaches a finding; the first nightly cron tick is read from the logs (still
open from `CLAUDE.md`). Each is recorded here with its result, or left marked
unproven.

## 3. Spike results

Run 2026-11-11 by `scripts/probe_webhooks.py` against the sandbox with the
platform credential (read-only; registered no webhook, simulated no event).

| Question | Result |
|---|---|
| Event types available | **Verified.** 205 in the catalogue, including `PAYMENT.CAPTURE.{COMPLETED,DECLINED,DENIED,PENDING,REFUNDED,REVERSED}`, `CUSTOMER.DISPUTE.{CREATED,UPDATED,RESOLVED}`, `CHECKOUT.ORDER.*`, `PAYMENT.PAYOUTS-ITEM.*`. **No event type names a referenced payout**, so a DELAYED release is not announced by a listed event |
| Webhooks registered for the app | **None.** Nothing has ever been delivered, so there is no sample event in the repo |
| Events-list endpoint readable | **Verified reachable** (200) with the platform credential. It returned zero events, so retention, ordering and what a connected seller's events look like are **unverified** |
| Verification endpoint reachable | **Verified.** An invalid body returns `200 {"verification_status": "FAILURE"}`. Note it answers 200 for a failure, so the handler must read the body, never the status |
| Verification succeeds for a connected seller's event | **Unverified.** Needs a real delivery, which needs a registered webhook and a deployed endpoint |
| Sandbox buyer approval can be automated | **Verified by the existing capture** (`scripts/capture_paypal.py`: card-funded, runs unattended) |

**Consequence.** The handler fails closed: anything the verification call does
not answer `SUCCESS` is rejected, so an unverified assumption can only reject
good events, never accept bad ones. The unverified rows move to the post-deploy
checklist in section 2, and the first real delivery settles them. Registering
the sandbox webhook points PayPal at the deployed app, so it is the operator's.

## 4. Stated as unproven if nothing above shows otherwise

Behaviour on Render; anything real beyond a simulated marketplace; delivery
ordering and burst volume.

## 5. Results, 2026-11-11

Branch `spec-live-ingestion`. Items 1 to 11 of section 1:

| # | Result |
|---|---|
| 1 | **Pass.** After `demo_data --reset`: `pytest` 2028 passed, `mypy` clean, `ruff` clean, `python -m reckonai.eval` GATE: PASS (false positives 0 of 12, F1-F6 all found) |
| 2 | **Pass.** `test_a_second_delivery_of_one_event_has_one_effect` |
| 3 | **Pass.** `test_a_bad_signature_stores_nothing`; verifier tests for FAILURE, missing id, missing headers |
| 4 | **Pass.** Every row of the case table has a test (`test_webhook.py`, `test_paypal_live.py`). The unrouted and unhandled rows are logged, not stored (requirements.md section 5) |
| 5 | **Pass.** `test_jobs_rerun_rule.py` (11 tests) |
| 6 | **Pass.** `scripts/e2e_live.py` against the sandbox: three real orders and one real refund through the write side; one order ingested through the webhook job's own code path with a real re-fetch as the seller |
| 7 | **Pass.** Planted duplicate -> `duplicate_ledger_entry`; unrecorded refund -> `missing_ledger_adjustment`, on live transactions |
| 8 | **Pass for the shapes tried.** A clean live order -> 0 exceptions. Not tried live: a half-ingested order |
| 9 | **Pass.** Hidden (404) unless enabled; 503 if enabled with no secret; 403 on a missing or wrong secret |
| 10 | **Pass.** Every write-side payload is `source = marketplace` with `endpoint = SIMULATED POST /sim/marketplace/live`; the seed's stay `NULL` |
| 11 | **Pass, partly.** Section 3 recorded; the unverified rows moved to section 2 |

The run examined the three live orders and reported the two planted faults, and
a repeat with nothing new was refused as `nothing_new`.

## 6. Still unproven

Everything in section 2, on Render. Specifically: that a real delivery verifies
for a connected seller, that capture events carry `payee.merchant_id` (routing
depends on it), the events-list's retention, and that the new cron variables
and `PAYPAL_WEBHOOK_ID` are set. The worker and cron now need PayPal credentials
the Blueprint declares but only the operator can set.

## 7. Local run through the real stack, 2026-11-11

Web service and worker started locally with the real credentials
(`scripts/with_local_env.py`), the dashboard driven in a browser. All against
the PayPal sandbox.

- **Verification against real PayPal.** A forged signature and a delivery with
  no signature headers were both refused `401`; non-JSON `400`. This exercised
  the real verify call with the real webhook id. It did **not** exercise a
  genuine signed delivery, which needs a public URL.
- **Write side guard.** `403` without the secret, `201` with it, placing a real
  sandbox order.
- **Worker.** A routed event was queued, and the worker, on its own process
  with its own credentials, re-fetched the order as the seller and ingested it.
- **Run rule.** First run permitted; a repeat with nothing new `nothing_new`;
  new evidence inside the cooldown `cooling_down`; permitted after it.
- **Dashboard.** A second live order planted with a duplicate credit appeared
  as a sixth open exception, `Same payment booked twice`, $80.00, after
  "Run reconciliation".

**Found by running it, fixed:** the seeded dataset's payloads are dated up to
Nov 19, in the future, so every same-day run looked permitted. A payload dated
after now has not arrived; `has_new_evidence` is bounded at `now`
(`test_evidence_dated_in_the_future_has_not_arrived`). No unit test could have
found it, because the tests build their own evidence.

**Not done:** a genuine signed delivery. No tunnel tool is installed, and
pointing the registered webhook at a tunnel would change the operator's PayPal
app.

## 8. Code review (medium), 2026-11-11

Ten findings. Each was checked against the code before anything was changed.

| # | Finding | Result |
|---|---|---|
| 1 | A refund was made at PayPal before checks that could still fail | **Real, fixed.** The order id is resolved before PayPal is called, and a failed read-back after the refund is reported and cannot roll the booking back (savepoint) |
| 2 | `NaN` and `Infinity` amounts gave a 500; refunds not bounded cumulatively | **NaN and Infinity: real, fixed.** Cumulative refunds: **not fixed**; PayPal itself rejects an over-refund and that refusal happens before anything is booked |
| 3 | An order whose events arrive out of order (refund before completion) is never ingested | **Real, not fixed.** An order first seen after a refund has no trustworthy capture-time fee (the known limit). Out-of-order delivery makes that permanent. A candidate fix: use the stored `COMPLETED` event's capture resource as the capture-time snapshot. Left for a decision because it relaxes "a webhook is a notification, never the record" |
| 4 | A new client and token per event, fetched inside the transaction | **Real, not changed.** Harmless at this volume; revisit if events burst |
| 5 | A stored webhook event counted as new evidence | **Real, fixed.** Webhook rows are excluded from `has_new_evidence`; a test pins the record type to the one the receiver stores |
| 6 | The cooldown looked at every run, not this window's | **Real, fixed.** Scoped to the same window. The naive-versus-aware datetime claim is not real (both sides are timezone-aware) |
| 7 | Verification used re-serialized JSON, not the received bytes | **Real, fixed.** The raw body is spliced into the verify request unparsed. Confirmed against the real sandbox: PayPal accepts the request and answers `FAILURE` for a forged event. The dedupe-by-hash point is not a defect (the event id is inside the body) |
| 8 | Missing credentials gave a 500; a client was built per request and never closed | **Real, fixed.** 503 with a logged reason; the client is closed |
| 9 | A recovery failure was swallowed at warning level | **Real, fixed.** Logged at error level, the nightly run is still enqueued, and the cron job exits non-zero so the failure shows |
| 10 | `.env.example` had a typo and angle-bracket placeholders | **Real, fixed.** Note that this edit was the operator's; placeholders now follow the file's `replace-with-` convention |
