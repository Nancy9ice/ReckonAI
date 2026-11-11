# ReckonAI

AI-powered financial reconciliation and investigation for marketplace operators
that pay sellers through PayPal.

**Live demo:** https://reckonai-api.onrender.com

## The problem

A marketplace takes a payment, keeps a commission, refunds part of it, converts
currency and pays the seller. PayPal holds one record of that money and the
marketplace holds another. When the two disagree, a finance analyst compares
spreadsheets and exports by hand, order by order, to find out who is owed what
and why. Nothing tells them which discrepancy to look at first.

## What ReckonAI does

1. **Connects both sides.** It maps a marketplace's own records (orders,
   commissions, seller entitlements) and PayPal's records (payments, refunds,
   disputes, platform fees, disbursements) into one canonical model.
2. **Works out what should be true.** A deterministic engine calculates what each
   seller is owed and what the marketplace's commission should be.
3. **Finds the differences.** A classifier compares that against what PayPal
   reported and raises typed exceptions: a missing refund adjustment, a
   commission that differs from the rule, a currency conversion that does not
   match, a payout mismatch, a duplicated booking.
4. **Investigates each one with AI.** An agent reads the evidence and reports
   ranked probable causes, each citing the record ids behind it, with a stated
   confidence. When the evidence is not there it says so
   (`insufficient_evidence`), instead of guessing.
5. **Puts a person in charge.** A prioritised queue shows how many exceptions
   there are and how much money is at risk, per currency. A person approves or
   rejects each one. Nothing moves money.

## Integration

Built for PayPal Complete Payments Platform for Marketplaces (multiparty), with
both `INSTANT` and `DELAYED` disbursement. The dataset is **real PayPal sandbox
orders**: 18 captured orders replayed from `fixtures/paypal/`. PayPal webhooks
are received, verified and ingested; reconciliation itself runs over date
windows.

## Principles

- **The AI never does arithmetic that reaches a financial conclusion.** Every
  amount comes from the deterministic engine, as a `Decimal`, with its currency.
- **Nothing is fabricated.** If PayPal does not supply a figure, the gap is
  recorded, not estimated. Amounts in different currencies are never added
  together.
- **Nothing moves money.** The agent is read-only; a human records every decision.
- **Absent is not zero.** Evidence the agent was not allowed to see is reported
  as unavailable, never as empty.

## Stack

Python 3.12, FastAPI, PostgreSQL 16, Alembic, a Postgres-backed job queue and
worker, React + Vite + AG Grid Enterprise, deployed on Render. The investigation
agent runs on OpenAI (`gpt-4.1`) or Anthropic, selectable by setting.

## Try it yourself

Create your own live transactions on the demo and watch ReckonAI handle each.
The PayPal side is real: each order is a real PayPal sandbox order (no real
money moves). The marketplace side is simulated: ReckonAI books the matching
marketplace records, and for some orders deliberately gets them wrong, so there
is something to find.

1. Click **Create test transactions** (top right). Keep the name offered or pick
   your own (letters and digits, up to 12), then press **Create my test
   transactions**.
2. Wait about a minute. The panel places six orders, waits for each to reach
   ReckonAI (by webhook, or fetched directly if one is slow; the table says
   which), and tells you when it is done. A different name gets a fresh set;
   the same name again creates nothing.
3. Click **Run reconciliation**, then search the queue for `JUDGE-YOURNAME`.
4. Open each order and **Investigate** the exceptions.

| Order | What it is | Expected |
|---|---|---|
| `S1-CLEAN` | Correct 10% commission | no finding |
| `S2-COMMISSION` | Marketplace books 15.00 where PayPal's fee is 10.00 | commission differs from the rule |
| `S3-HELD` | Delayed disbursement, funds held | no finding |
| `S4-LATE-REFUND` | Marketplace refund recorded, PayPal's not yet available | waiting for PayPal's record, not an exception |
| `S5-NO-LEDGER` | Refund made at PayPal, ledger never adjusted | refund not booked to the seller |
| `S6-DUPLICATE` | Sale credited twice | same payment booked twice |

**To see a finding wait and then clear (S4):** after your first reconciliation,
press **Deliver PayPal's refund** in the same panel, then click **Run
reconciliation** again. S4's waiting finding disappears. Pressing the button
before the first run is harmless, but S4 then never shows as waiting.

If Run reconciliation says nothing is new, an earlier run already covers your
orders. If it says a check just finished, try again in a few seconds.

Foreign currency and disbursement shortfalls are on the seeded orders
(ORD-0015, ORD-0008, ORD-0018).

Then explore what the queue offers:

- **Open any exception** to see the figures from each side ("PayPal settled"
  against "Marketplace recorded"), what it means, and what to do about it.
- **Evidence**, from the same drawer: the PayPal and marketplace records behind
  the finding, including the raw payload.
- **Investigate**, then read the report: ranked causes, each citing the records
  it rests on. Click a cited reference to open that record.
- **Approve or reject** a finding. The approver is a typed name, not a sign-in,
  and the decision stays in a history that is never edited.
- **Ask the queue** in plain English (for example "confirmed FX issues" or
  "group by seller"). It only changes the view; no record or amount is sent to
  the model.
- **Filter, group, save a view, or export** the queue and the decision log as CSV.
- **Coverage** shows what the checks have and have not examined, and what is
  waiting on a record that has not arrived. **Past checks** lists every run;
  open one to see the queue as that run left it.

## What was measured

- The test suite (2089 tests), `ruff`, strict `mypy` and the frontend build pass.
- `uv run python -m reckonai.eval` (free): the six seeded faults are all found
  with exact impacts, and none of the 12 clean orders is flagged.
- The AI's root-cause accuracy was scored **once, on one model, and is not a
  rate**: a reader judged the stated cause clearly right on three of the five
  faults scored, and the agent correctly declined to answer when evidence was
  removed. Details and caveats: `specs/2026-11-11-eval-harness/validation.md`.

## Limitations

No sign-in (the approver is a typed name), a simulated marketplace, no dispute
matching, and one real signed webhook delivery observed (a probe order); refund events
over the webhook are untested. The full list, with
reasons, is in section 0 of [`product_idea.md`](product_idea.md).

## Run it locally

Python 3.12 with [uv](https://docs.astral.sh/uv/), Docker, and Node for the
dashboard.

```bash
docker compose up -d --wait
uv sync --frozen
export DATABASE_URL="postgres://reckonai:devpassword@localhost:5432/reckonai"
uv run alembic upgrade head
uv run python -m reckonai.demo_data --ensure     # the seeded marketplace
(cd frontend && npm ci && npm run build)         # needs an AG Grid key, see below
uv run uvicorn reckonai.app:app                  # dashboard at http://localhost:8000
uv run python -m reckonai.worker                 # in a second shell
```

Investigations and the grid assistant need a model key set in the environment.
The dashboard depends on AG Grid Enterprise, which is commercially licensed: the
build reads your own key from `VITE_AG_GRID_LICENSE_KEY`. No key is in this
repository.

## Documents

- [`product_idea.md`](product_idea.md): the product definition, MVP scope and limitations.
- `specs/`: mission, technology decisions, the roadmap, and a requirements and
  validation record for each block of work.
- `specs/paypal-fields.md`: what PayPal actually returns, field by field, with the
  evidence.

## License

MIT, see [`LICENSE`](LICENSE).
