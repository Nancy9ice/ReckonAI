# Live ingestion — plan

> **All groups built; see `validation.md` sections 5 to 8.** One PR, spike first. Groups run straight through, each validated before the
> next (`validation.md`). Scope and decisions: `requirements.md`.

**0. Verification spike.** Record in `validation.md`: event types a multiparty
sandbox delivers; partner signature verification; events-list retention and
partner access; whether sandbox buyer approval can be automated. Gates 3-6.

**1. Documents and prerequisites.** Apply the amendments in `requirements.md`
§2. Adopt the simulated-data boundary. `mapping_version` already exists on
`raw_payload`; no migration for it.

**2. Same-day re-run rule.** `window.has_new_evidence`; watermark in
`idempotency_key`; `schedule.enqueue_runs` and `queries.request_run` use it;
`nothing_new` status; cooldown setting; update `copy.ts` and the API message.

**3. Live fetcher.** Make `paypal/capture.py` callable from a job; route its
output through `paypal/replay.py`. Tests against fixtures.

**4. Webhook receiver.** Route, verification, dedupe, routing, handler job
registered in `jobs/handlers.py`. Tests for every row of the case table.

**5. Recovery job.** Scheduled re-list or re-fetch; add to `render.yaml`.

**6. Simulator write side.** Place order, refund, ledger write, fault
injection, simulated marker, guard. Reads still served by the existing
endpoints.

**7. End to end.** A live sandbox transaction, locally, through to a finding
on the next run; then a planted fault; then a clean transaction with zero
exceptions. Update `CLAUDE.md` and the readme limitations.
