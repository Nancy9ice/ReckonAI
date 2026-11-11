# Judge scenarios — validation

> Every merge to `main` deploys. Sections 1 and 2 gate the merge; section 3 is
> read after. Results are written here as they come.

## 1. Before merge (all required)

1. **Suite.** `python -m reckonai.demo_data --reset`, then `pytest`,
   `uv run mypy`, `ruff check`, and `python -m reckonai.eval` all pass. No
   `ruff format`.
2. **Outcomes.** Local, real sandbox, worker running: after the script and a
   run, exactly the table in `requirements.md` §1. S1 and S3 give no exception;
   S4 gives `awaiting_evidence`; the rest give one named code each.
3. **Idempotent per name.** A second and a tenth `ensure` for one name create
   no order, no sandbox call and no row; two concurrent calls for it create
   each order once. A different name creates its own six and touches none of
   the first name's rows.
3a. **Caps.** Past `JUDGE_MAX_SETS` or 2 new sets per IP per day a new name
   gets 429 and creates nothing; an existing name still works. A name outside
   letters and digits is refused.
4. **Resume.** With the gateway failing on the third order, the next call
   creates the remaining ones and none twice.
5. **Late refund.** After `--late-refund` and a run, S4's finding clears; a
   second `--late-refund` does nothing.
6. **Guard.** Flag off gives 404 on every judge route. The existing write
   routes still answer 403 without the secret. The judge routes accept no
   amounts, sellers or refs.
7. **Labels.** Every record written carries `endpoint = SIMULATED ...` and none
   is `source = paypal` except the ingested provider documents.
8. **Probe order.** Ingest a PayPal-only order locally, run, record what a run
   does with it. If it raises an exception, say so in section 3 and decide
   before judges see production.
9. **Fallback.** With the webhook disabled locally, `ingest_missing` produces
   the same results, and the script reports the path used.

## 2. Not required to merge

The script's output formatting, and the readme wording beyond being accurate.

## 3. After merge (read from Render)

- Flag set by the operator; script run once against the deployed app.
- Per order: webhook or fallback; refund events included.
- Run, then Investigate on S2, S5 and S6: completes and cites the records.
- Two names run back to back on Render: each gets six orders, and a run
  covers both.
- Stays unproven if not seen: `PAYMENT.CAPTURE.REFUNDED` over the webhook;
  several judges truly at once.

## 4. Results, 2026-11-11 (local stack, real sandbox)

Web and worker local with the real credentials; webhooks cannot reach a local
server, so every order took the fallback path. The webhook path was seen on
Render separately (probe order, 18:31 UTC).

| # | Result |
|---|---|
| 2 | **Pass.** After the script and a run: S2 `commission_mismatch`, S5 `missing_ledger_adjustment`, S6 `duplicate_ledger_entry`, S4 `awaiting_evidence` (`is_exception` false), S1 and S3 no finding. |
| 3 | **Pass** (unit tests, and a second script run created nothing). Concurrent calls tested only by the advisory lock, not by two real clients. |
| 4 | **Pass, and found a bug.** A DNS failure mid-run left the earlier orders in place and the next run resumed. It also showed `ingest_missing` was all-or-nothing and that a raw HTTP error became a 500; both fixed (per-order savepoints, `httpx` errors become 502) with a test. |
| 5 | **Pass.** `--late-refund` delivered PayPal's refund; the next run no longer reported S4, and raised no exception. |
| 8 | **Pass.** A PayPal order with no marketplace order (the production probe) reads `awaiting_evidence`, not an exception: it does not change the exception count. It will show as a non-exception finding keyed by its PayPal order id. |
| 9 | **Pass** locally (fallback path reported per order). |
| - | Investigations on S2, S5, S6 (OpenAI `gpt-4.1`) all `reported`, ranked causes naming the right fault, every cited ref resolved. Anthropic failed with a 400 (no credit) and is unexercised, as before. |

Run cooldown observed: a run inside five minutes of the last answers
`cooling_down`; the script's wording covers it.

## 5. The UI button (D7)

Before merge: `GET /sim/judge/enabled` answers 200 whether on or off and never
404; the frontend typechecks and builds; in a browser the button is absent when
the feature is off, and when on one click reaches "done" with six rows and no
console errors; a second click on the same name creates nothing; a missing
worker or an error shows a message and a retry, not a frozen panel.
After merge: the same on Render, which needs the flag and is the operator's.

### 5a. Results, the UI button (local stack, real sandbox, Chrome)

| Check | Result |
|---|---|
| Absent when off | **Pass.** With the flag unset `/sim/judge/enabled` answered `false` and the button was not on the page. |
| One click to done | **Pass.** The panel showed "Placing your orders...", then "fetched directly" (no webhook locally), then "Done. All six are in", with the six rows in plain wording. The first request takes about 40s, so the panel shows busy for that long. |
| Findings in the queue | **Pass.** After Run reconciliation: `JUDGE-...-S2`, `S5`, `S6` appeared; `S1`, `S3` did not; `S4` is not an exception so is not in the queue. |
| Console | Only AG Grid's licence notice, which is this local build having no licence key; unrelated. |
| Not exercised in the browser | A second click on the same name (covered by tests); the late-refund button (covered through the API earlier); an error state; a phone-width layout. |
