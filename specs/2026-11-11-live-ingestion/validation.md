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
