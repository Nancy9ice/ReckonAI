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

## What was measured

- The test suite (2056 tests), `ruff`, strict `mypy` and the frontend build pass.
- `uv run python -m reckonai.eval` (free): the six seeded faults are all found
  with exact impacts, and none of the 12 clean orders is flagged.
- The AI's root-cause accuracy was scored **once, on one model, and is not a
  rate**: a reader judged the stated cause clearly right on three of the five
  faults scored, and the agent correctly declined to answer when evidence was
  removed. Details and caveats: `specs/2026-11-11-eval-harness/validation.md`.

## Limitations

No sign-in (the approver is a typed name), a simulated marketplace, no dispute
matching, and no real signed webhook delivery observed yet. The full list, with
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
