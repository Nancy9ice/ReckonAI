# Block F — validation

Evidence recorded 2026-11-10. `[x]` means shown by the command or observation
named; `[ ]` means **not proven**, and the reason is stated, not softened.

## 1. Phase 13 exit criteria

- [x] **Approve records decision and approver, and moves no money (P6).**
  `tests/test_api_read.py::test_approving_records_the_decision_and_moves_no_money`
  counts ledger, refund and disbursement rows before and after; and
  `test_the_api_package_cannot_name_a_money_moving_table` fails if `api/` names
  one. Observed in the browser: approving ORD-0013 wrote one row.
- [x] **Rejection has equal weight.** Same table, fields and read; asserted by
  `test_rejecting_has_equal_weight_and_the_same_shape`.
- [x] **Append-only.** A second decision adds a row and the newest is effective
  (`test_deciding_again_appends_and_keeps_the_first`). Enforced by code path
  (a scan test refuses any update or delete of the model), not by a database
  trigger - `demo_data.reset` legitimately deletes under RESTRICT.
- [x] **Confirmed vs suspected distinct**, from the stored `confirmed` column:
  `test_suspected_is_distinct_from_confirmed`, and a badge in the grid and detail.
- [x] **Score uses all four factors**, with the breakdown returned and
  own-currency impact ranking: `tests/test_priority.py` (7 tests), under the
  purity gate and strict mypy. An exception with no investigation has
  `confidence: null`, not zero.

## 2. Phase 14 exit criteria (Chrome, seeded dataset, local)

- [x] Queue shows exactly the manifest's 5 exceptions and no clean order; groups
  by type (5 groups observed), sorts by priority, filters by status, decision and
  text. **Grouping is implemented in the client as one grid per group, not AG Grid
  row grouping**, which is Enterprise-licensed; see 6.
- [x] Detail shows the finding, evidence records, and the investigation or its
  failure; approve persisted across a reload.
- [x] Every amount shows its own currency ($90.00, EUR 15.00 as a euro symbol).
  `grep -E "parseFloat|Number\(|toFixed|Math\."` over `frontend/src` matches
  only a comment.
- [x] Withheld kinds render as "Unavailable to the agent ... not the same as
  empty" - **by test of the API and code only; not exercised in the browser**,
  because no investigation with a withheld kind exists in the seeded database.
- [x] Run summary lists both runs; clicking one filters the queue to its rows.
- [ ] **A successful investigation rendered with causes, citations and the
  recommendation was never seen in the browser.** The only investigation run
  was against Anthropic, whose account had no credit: it was recorded as
  `failed` and the UI showed that correctly. The cause list, unresolved-citation
  styling and the "recommended resolution" block are therefore unobserved.

## 3. Deployed environment (settle before merge - a merge deploys)

- [ ] **Node on Render's Python runtime is unverified.** The build fails soft
  (`render.yaml`, tested), so the worst case is a deployed API whose `/` is the
  JSON index. Check after the first deploy: `GET /` must be HTML.
- [ ] **Agent credential and provider** are declared on the worker with
  `sync: false` (tested), but **someone must set them in the Render dashboard**:
  either top up Anthropic, or set `AGENT_PROVIDER=openai`, `AGENT_MODEL=gpt-4.1`
  and `OPENAI_API_KEY`. Until then every deployed investigation is a recorded
  failure with the missing variable named.
- [ ] Post-merge: `/api/exceptions` answers, and a queued investigation
  completes on the deployed worker. Not done; no merge has happened.
- [ ] The deployed database has **no reconciliation run until the Cron Job's
  first tick (01:00 UTC 2026-11-11), which covers 2 of 18 orders.** The dashboard
  will look nearly empty until a wide run is enqueued by hand, as was done
  locally with window 2026-10-12 to 2026-11-21.

## 3a. What the deploy showed (PR #24, merged 2026-11-10 23:00 UTC)

Checked from outside, with `curl` and the Render CLI, 23:04 UTC.

- [x] **Both services went live** in about three minutes from the merge (the
  `blueprint_sync` deploys of `69a3a0c`; the plain commit deploys were superseded
  by them). The pre-deploy step ran, so **the migration and the demo seed
  succeeded**: the deployed database holds the 18 seeded orders.
- [x] **Node is available in Render's Python runtime.** `GET /` returns the
  dashboard (`text/html`), its 3 MB script loads (200), and `/service` returns the
  JSON index. This was the largest unverified risk; it is closed.
- [x] `/health`, `/health/ready` and `/api/coverage` answer 200.
- [x] **The model credential is set on the web service**: one tiny request to
  `/api/grid-assistant` returned a valid grid state (HTTP 200). One cheap model
  call was spent. Which provider it used was not read.
- [x] **Coverage is honest on an unreconciled database**: 18 orders, 0 clean, 18
  "not examined", and `/api/runs` is empty. This is the post-review wording doing
  its job: no run has looked, so nothing is called clean.
- [ ] **The worker's model key is not verified.** It is a separate service with
  its own variables. The only check is an investigation completing there, which
  has not been run.
- [ ] **No reconciliation has run on the deployed database.** The Cron Job
  redeployed and is live but has not fired; its first tick is 01:00 UTC
  2026-11-11 and covers 2 of 18 orders. Until a wider run is queued (the
  dashboard's "Run reconciliation" does it), the deployed queue is empty.
- [ ] **The nightly investigation path has not run on Render.**
- [ ] Whether the AG Grid watermark is gone, and the wording on the deployed
  pages, were not seen in a browser.

## 4. Regression gates

- [x] 1872 tests pass in order; `ruff`, strict `mypy` (now including `api/`),
  purity gate green. The local database was reset afterwards
  (`demo_data --reset`), so the suite runs clean.
- [x] Frontend `npm run build` (runs `tsc --noEmit`) passes; added to CI as its
  own job.
- [x] The clean-baseline gate is untouched: no classifier or engine file changed.
- [x] **`/code-review medium`: 10 findings, 9 fixed, 1 deferred.**

| Finding | Outcome |
|---|---|
| Decision keyed to one run's exception row vanished at the next run | Fixed: keyed by (order, code) like investigations; regression test |
| Fail-soft build only soft for a missing npm | Fixed: any dashboard failure is soft; test updated |
| Validation error replaced the whole detail panel | Fixed: separate load and action errors |
| Impact column sorted and filtered as text | Fixed: column is display-only |
| Check-then-enqueue not atomic | Fixed: `pg_advisory_xact_lock` per exception |
| Stale queue response could overwrite a newer one | Fixed: request ticket; selection cleared |
| Missing order row would 500 the queue | Fixed: nullable date, zero age; regression test |
| Edge-detector hook over-built | Fixed: a ref |
| Server error body discarded by the client | Fixed: FastAPI `detail` is shown |
| `exception_detail` recomputes the whole queue to score one row | **Deferred**: harmless at 18 rows; debt below |

The concurrency fix is covered only by the sequential double-click test; **no
test races two real connections.**

## 4a. The redesign and the assistant (added after the first pass)

- [x] **Assistant, end to end in Chrome**: "only Bellweather Books, highest
  priority first" filtered the queue to 3 of 5 rows and sorted it, with the
  model's explanation shown. Run on `openai` / `gpt-4.1`; **not observed on
  Anthropic**, whose account has no credit. Backend covered by
  `tests/test_grid_assistant.py` (15 tests, stub clients, no live call): both
  providers, no sampling parameters, refusal read before content, a reply with
  an unknown property is an error, the model is sent only the sentence, state
  and schema.
- [x] Native grouping by seller (counts in headers), sidebar, status bar,
  priority meter, KPI strip, slide-in drawer, saved views (inline name field, no
  browser dialog) - seen in Chrome. **Saved views and the sidebar were not
  clicked through**; the Runs tab was not re-checked after the redesign.
- [x] Impact column is now right-aligned and orders as decimal strings
  (`localeCompare` numeric); the numeric-coercion grep is still clean.
- [ ] **Impact's number filter was not exercised.** AG Grid's number filter is
  given a decimal string; whether it parses it correctly was not checked.
- [ ] Bundle is about 3 MB (850 kB gzipped) because `AllEnterpriseModule` is
  registered; registering only the modules used would shrink it.
- [ ] Licence: the key is read by Vite from the local environment file, which
  this session was not permitted to read, so **whether the watermark is gone
  was not confirmed** - open the page and look.
- [ ] Render needs `VITE_AG_GRID_LICENSE_KEY` (build) and the four agent
  variables on the **web** service (declared, tested, values set by a person).

## 4b. Run now, evidence, coverage, export

- [x] **Run reconciliation, end to end in Chrome** with a real worker: queued,
  then complete, then the queue and the clean count refreshed. Backend:
  no orders, queued, in flight, already done today, job polling and tenant
  isolation (`tests/test_api_extras.py`).
- [x] **Evidence opens**: an order and a seller ledger entry seen in Chrome,
  with the provider-record section. By test on the seeded dataset: a capture's
  absent PayPal fee is "not stated" and not zero, the retained payload is
  reachable, a platform fee id resolves but is marked unstable, another tenant's
  record is a 404.
- [x] **Coverage**: 18 orders, 5 with an exception, 1 parked (ORD-0017, the
  late-refund case), 12 clean, seen in Chrome; the page states what "clean"
  rests on.
- [x] **Export**: both CSVs return; amounts are strings beside a currency;
  superseded decisions are kept in the log; `=`, `+`, `-`, `@` and tab-led cells
  are neutralised (tested, including a note of `=HYPERLINK(1)`).
- [ ] **Not clicked in the browser:** the "Show the raw response" button, the
  CSV downloads from the buttons (the endpoints were fetched with curl), and the
  platform-fee warning text.
- [ ] A run is date-grained, so "Run reconciliation" is a no-op the second time
  in a day; that is by design (D14) but may surprise a demo.
- [ ] The export buttons wrap onto a second toolbar line at laptop width.
- The P6 guard on `api/` now allows one module to name money tables:
  `evidence.py`, which a second test holds to read-only.

## 4c. Second code review (the work after the first)

`/code-review medium` over everything added after the first pass: the assistant,
evidence, run-now, coverage, export and the redesigned frontend. **10 findings;
9 real and fixed, 1 a cleanup, also done.** No finding was invisible to the
tests that existed, which is the point: the worst two were wrong answers, not
crashes.

| Finding | Outcome |
|---|---|
| Coverage could count an order as both flagged and parked | Fixed: one precedence (flagged, parked, not examined, clean); a test pins that the four sum to the order count |
| Orders no run had looked at were called clean | Fixed: clean now needs a completed run whose window contained the order **after it was ingested**; otherwise "not yet examined", its own count and list. The KPI shows a dash, not 0, until loaded |
| CSV guard turned negative amounts into text | Fixed: plain decimals are left alone; `=`, `@`, tab and `-SUM(...)` are still neutralised |
| Assistant route: unauthenticated and unbounded paid calls | Fixed: body size capped (200 KB), 20 calls a minute per process. **Still unauthenticated**, so this bounds the cost, it does not remove the exposure |
| Server did not enforce the features the client excluded | Fixed: `aggregation`, `pivot` and `columnSizing` are stripped from any reply and always listed as ignored |
| Raw payload route coerced a non-object body | Fixed: returned exactly as retained |
| Group-by select went stale when the assistant or a saved view regrouped | Fixed: the select follows the grid's own grouping, with a "Custom grouping" entry |
| "Already reconciled today" shown as green success | Fixed: a neutral notice that says new data waits for tomorrow, and Coverage lists it as not yet examined |
| Run-now poll gave up on one failed request, and ran after unmount | Fixed: three consecutive misses, and a mounted check |
| Redundant imports in `request_run` | Cleaned |

Still true after the fixes: the rate limit is per process and in memory, so two
workers allow twice the rate; and "examined" cannot recognise an old order a run
re-examined only because new evidence arrived, so it errs toward "not yet
examined".

## 4d. Bulk and automatic investigation

- [x] **In Chrome, with the real worker and OpenAI**: ticked one row, "Investigate
  selected (1)", the inline confirm appeared, and after confirming the banner
  followed the job ("1 queued. Investigating: 0 of 1 done" then "All finished")
  and the row changed to "Insufficient evidence"; the "Investigated" card went
  from 1 to 2 and "Investigate all" dropped from 4 to 3. It also showed "Investigate
  all" skipping an exception someone had already investigated. One model call
  was spent.
- [x] Backend (`tests/test_api_extras.py`, `tests/test_auto_investigate.py`):
  queued, in flight, already concluded, unknown and foreign ids; the 100-id bound;
  duplicate ids; and for the scheduled run: one job per exception and none for the
  parked finding, nothing for a run nobody scheduled, the schedule sets the flag,
  a repeat does not queue twice, a concluded exception is skipped, a `failed` one
  is retried, the cap, 0 disables, and no credential queues nothing.
- [ ] **"Investigate all" itself was not clicked**: it would have spent three more
  calls. It shares the code path of "selected"; only the id list and the skip flag
  differ, and both are tested at the API.
- [ ] **The nightly path has not run on Render**, and its first tick is
  2026-11-11 01:00 UTC. Locally it is proven by test, not by a cron firing.
- [ ] The cap picks by order reference, not by priority.
- [ ] A successful `reported` verdict was seen in the grid ("Cause reported" on
  ORD-0016, run on the operator's own server) but its drawer was not opened in
  this session.

## 4e. Wording for a finance reader

- [x] **A real defect fixed:** the drawer showed "Expected $294.16 / Reported
  $262.00" under a sentence saying the marketplace recorded 262.00 and PayPal
  settled 294.16. The figures were right and the labels read backwards. Now
  "PayPal settled" and "Marketplace recorded", per type (FX, commission,
  payout).
- [x] Seen in Chrome: queue titles, the drawer's "What this means" and "What to
  do", the plain priority line, the coverage and run wording. The 11 types'
  meaning and action text is in `copy.ts`.
- [ ] **Not tested with a finance user.** The copy is my reading of what the
  engine does, written for the audience named; a reader may still find a phrase
  unclear. The commission and payout labels were derived from the classifier but
  not seen on screen, and the other eight types' text was not seen in a browser.
- [ ] The evidence viewer's field names are mapped for the common ones; an
  uncommon field falls back to its raw name.
- [ ] "Order date" is hidden by default to make room; it is in the Columns panel.

## 5. Corrections and debts handed on

- **Age reads 0% on every seeded row.** The seed's orders are dated 2026-11-13 to
  11-19 and today is 11-10, so age is clamped at zero. Real in the data, not a
  bug in the factor; Block G's demo should account for it.
- **The queue cannot retract an exception a later run stops emitting**, because a
  run records how many orders it covered, not which.
- **The recommendation shown is the top-ranked cause**; the agent produces no
  separate recommendation field, and nothing in the product acts on it.
- Debt: scoring one row recomputes the queue (finding above).
- Debt: `ExceptionOut.seller_name` and `seller_count` are derived per request.

- **Approver is free text** (D5): confirmed in conversation, accepted for the
  MVP. A named-user login (about an hour) or SSO (about a day) is the fix.
- **Block G's handoff:** seeded orders are dated 2026-11-13 to 11-19, so age is
  0%; the Anthropic provider and a successful investigation rendered in a browser
  are unproven; the deploy values (licence key, provider, API keys on web and
  worker) need a person; the worker's 30-second poll makes a deployed
  "Run reconciliation" feel slow.

## 6. One decision for the operator

**Resolved 2026-11-10.** The operator has an AG Grid Enterprise licence, so
grouping is native (D12). Whether the watermark is gone was not confirmed in
this session.
