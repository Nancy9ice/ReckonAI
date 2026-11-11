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
