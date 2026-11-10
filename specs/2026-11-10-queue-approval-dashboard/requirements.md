# Block F — queue, approval, dashboard (Phases 13 + 14)

> Entry: `specs/2026-11-08-replan/requirements.md` (R5, R13). Prior block:
> `specs/2026-11-10-investigation-agent/validation.md` §7b and §8 — read both.
> Exit criteria in [[roadmap]] Phases 13 and 14 still bind.

## 1. Scope

Built, in this order of dependence:

1. **Read API** — there is none today. Exceptions, runs, investigations, decisions.
2. **Prioritisation** — one score over the four `readme.md` §9.3 factors:
   financial impact, affected sellers, age, confidence.
3. **Decision record** — append-only approve/reject with approver and outcome.
4. **Queued investigation** — `investigate` as a job type (Block E debt 1).
5. **Dashboard** — React + TypeScript + Vite + AG Grid: exception queue,
   investigation detail with evidence trail, lean run summary, approve/reject.

Out, by R13/R14: seller financial position view, charts, Bryntum, auth.

## 2. Decisions

**D1. Frontend is built by Vite and served by the existing FastAPI web service.**
One URL, one deploy, no CORS. (Operator, 2026-11-10.) The build runs in
`buildCommand`; whether Render's Python runtime ships Node is unverified and is
a validation gate, not an assumption.

**D2. The agent runs as a queued job.** A new `investigate` job type registered
in `jobs/handlers.py`; the API enqueues and returns `202`, the UI polls. A
request-scoped call is the wrong shape at 6-8 s (§7b item 4). The idempotency
key is `(tenant, order_external_ref, code, run_id)` so a double click is one job.

**D3. Latest-per-exception read** (Block E debt 2) is one query over
`ix_investigation_latest_per_exception`, shared by the list and detail routes.
Tables stay append-only; "latest" is a read, never an update.

**D4. A decision never changes a finding or moves money (P6).** The decision
table is append-only; the effective decision is the newest row. Approving
records intent only — no refund, ledger or payout code is imported by the
decision path, and a test asserts it.

**D5. Approver is an unauthenticated free-text name** required on every
decision. There is no auth in the MVP; this is a **limitation to record**, not a
claim of identity.

**D6. Score is deterministic, `Decimal`, and lives in a pure module** under the
purity gate and strict mypy. The LLM contributes only the stored cause
confidence (P1). Factors normalise to 0-1 and combine by fixed documented
weights. **Impact is ranked within its own currency, never converted** — a
cross-currency comparison would need an FX rate, which P3 forbids us to
substitute. The factor breakdown is returned beside the score so the ranking is
explainable.

**D7. Confirmed vs suspected** reads `reconciliation_exception.confirmed`
(already stored) and is a visible column and badge, not derived in the client.
An exception with no investigation shows no confidence, not zero (P3).

**D8. Withheld stays withheld.** The API surfaces `investigation.withheld` and
`unresolved_refs` as-is; the UI renders "unavailable", never an empty list
(Block E's sharpest lesson).

**D9. Amounts are strings with their own currency end to end.** Formatted with
`Intl.NumberFormat` on the record's currency; no arithmetic in TypeScript.

**D10. Tenant** resolves server-side to the seeded demo tenant; every query
still filters `tenant_id`.

**D11. The queue has a natural-language assistant** (AG Grid's AI Toolkit).
The grid describes itself (schema and state); `POST /api/grid-assistant` asks
the configured provider for a new grid *state*; the grid applies it. It changes
the display only: no record or amount is sent or returned, and the `aggregation`
and `pivot` features are excluded from the schema so it cannot be asked to total
anything. The key stays server-side. It runs on the **web** service, so the
model credential is declared there as well as on the worker. No failover.

**D12. AG Grid Enterprise, and a redesign.** The licence key is the build-time
variable `VITE_AG_GRID_LICENSE_KEY` (local environment file, and a `sync: false`
variable on the web service); it is embedded in the shipped JavaScript, as AG
Grid licensing requires. Native row grouping replaces the earlier workaround
(group headers show counts only), with a sidebar, a status bar and browser-local
saved views. **Master/detail is not used**: AG Grid does not allow it together
with row grouping, so detail is a slide-in drawer. Visual language is a
PayPal-adjacent palette (navy, blue, cyan); no PayPal mark is used.

**D13. Cited references open as records.** `GET /api/evidence/{ref}` resolves
through the agent's own `CitationResolver` and returns the record's fields with
amounts in their own currency; an absent value is "not stated", never zero (P3).
The provider's retained response is a second, on-demand call. A reference that
resolves but is unstable (a platform fee id) says so. The module only reads, and
a test pins that.

**D14. Run now.** `POST /api/runs` queues a reconciliation over the whole
dataset and the UI follows the job. **It is idempotent by design:** the window
and its dedupe key are date-grained, so a second request the same day is
answered ("already reconciled today"), not queued. It needs the worker running.

**D15. Coverage.** `GET /api/coverage` accounts for every order: with an
exception, parked as `awaiting_evidence`, or clean. **"Clean" means "no newest
finding"**, and a run records how many orders it covered, not which, so an
order no run has reached reads as clean; the response and the page say so.

**D16. Export.** Server-side CSV of the queue (optionally one run) and of the
whole decision log, amounts as strings beside their currency. Every cell is
neutralised against formula injection. There is no export of the on-screen
filtered view: client-side CSV would skip that neutralisation.

## 3. Deployed environment (settle before merge — a merge deploys)

- `render.yaml` gains the agent credential (`sync: false`) and `AGENT_PROVIDER`
  / `AGENT_MODEL` on the **worker**, which runs investigations (§7b items 1-2).
- Provider choice is the operator's; default is `anthropic` /
  `claude-haiku-5-5`, unexercised, and the account had no credit on 2026-11-10.
  Fallback is `openai` / `gpt-4.1`. No failover between them.
- `demo_data --ensure` already seeds on deploy; verify, do not rebuild.
- The Cron Job's first tick is 2026-11-11 01:00 UTC, covering 2 of 18 orders;
  the dashboard must not need that run, so a wide run is enqueued by hand.

## 4. Limitations to record

No auth (D5); no seller position view (R13); impact not comparable across
currencies (D6); investigations run only on request, not automatically.
