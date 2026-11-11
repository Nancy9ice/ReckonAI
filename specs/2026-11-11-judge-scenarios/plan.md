# Judge scenarios — plan

> Groups run straight through, each validated before the next
> (`validation.md`). Scope and decisions: `requirements.md`.

**1. Write-side levers.** `disbursement_mode` through `capture_live_order` and
`PlaceOrderIn` (document and spec follow it). `RefundIn.marketplace_only`: book
the refund and ledger, skip the PayPal call. Tests.

**2. Scenario set.** `sim/judge.py`: the six definitions, namespaced by judge
name; `ensure(name)` (advisory lock per name, create what is missing, PayPal
first, set and per-IP caps); `status(name)` (per ref: booked, PayPal side
ingested); `ingest_missing(name)`; `deliver_late_refund(name)` (idempotent).
Tests with the fake gateway.

**3. Routes and flag.** `POST /scenarios`, `GET /scenarios`,
`POST /scenarios/ingest-missing`, `POST /scenarios/late-refund` under
`/sim/judge`, each taking the name; `JUDGE_SCENARIOS_ENABLED` and
`JUDGE_MAX_SETS` in settings and `render.yaml` (`sync: false`). Hidden when off.

**4. Script.** `scripts/judge_scenarios.py`, stdlib only: `ensure`, poll,
fall back, print the judge's refs, the path taken and what to expect after a
run. `--name` required; `--late-refund` for the optional step. Base URL is an
argument.

**5. Local end to end.** Real sandbox, local stack with worker: script, Run
reconciliation, Investigate on S2, S5, S6. Confirm every expected outcome,
including the probe-order question.

**6. Judge instructions.** One short section in `readme.md`, a pointer in
`CLAUDE.md`. Wording for the unproven items stays as unproven.

**7. Deploy and prove.** Merge, operator sets the flag, run the script against
Render once, record results in `validation.md`.

**8. UI button.** `GET /sim/judge/enabled`; `api.ts` methods; a
`JudgeScenarios` panel and header button in `App.tsx`; wording in `copy.ts`;
styles. Typecheck and build.

**9. Judge instructions, again.** Readme leads with the button; the script is
the alternative.

**10. Prove in a browser.** Local stack, button clicked, panel reaches done;
then on Render once the operator has enabled the flag.
