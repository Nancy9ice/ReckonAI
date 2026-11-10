# ReckonAI — working rules

AI-powered financial reconciliation and investigation agent for marketplace
operators using PayPal.

**Start here: `specs/2026-11-08-replan/requirements.md`.** It is the session
entry point. Phases 4-15 are re-sequenced into seven blocks against a two-day
deadline, and R1-R15 record every narrowing with its reasoning. The root specs
below total ~3,500 lines and re-reading them per session is the largest
avoidable cost — read a section when a block points at it, not by default.

The specs in `specs/` are the constitution. Read them before implementing:

- `specs/mission.md` — what we are building, and principles P1–P7. §4 is
  amended: event-driven ingestion is deferred past the MVP (R8)
- `specs/tech-stack.md` — locked technology decisions. §4, §5, §6, §8 and §9.2
  carry dated MVP amendments
- `specs/roadmap.md` — exit criteria, which **all still bind**. Its sequencing
  and its cutline are superseded by the replan

Also read, and in this order:

- `specs/multiparty-direction.md` — the integration model everything assumes.
  The product targets PayPal Complete Payments Platform for Marketplaces, with
  both `INSTANT` and `DELAYED` disbursement. The amendments it called for were
  applied on 2026-11-07, so the three documents above are current; §8 lists what
  is **still unverified** and may not be designed around.
- `specs/paypal-fields.md` — what PayPal actually returns, per field, with the
  evidence. The root specs state rules and point here; they do not repeat it.

**Phases 0, 0b, 1, 2, 3, Block A (4 + 5 + 6), the PayPal capture (R16), Block
B (Phase 10), Block C (Phase 11), Block D (Phase 8 + R9 + the exception table)
and Block E (Phase 12, the investigation agent, kept entire) are complete.
Block F is next** — Phases 13 and 14. **Every merge to `main` deploys the
API and the worker** (PRs #19-#22 each did), so there is no "one remaining
deploy" to budget: Block E's merge deploys too, migration included.

**Block F has no spec yet.** Every prior block wrote `requirements.md`,
`plan.md` and `validation.md` into a dated `specs/` directory before
implementing, and that is the first task rather than an optional preamble.
**Read `specs/2026-11-10-investigation-agent/validation.md` §7b first** — five
things to settle before a merge deploys, including that `render.yaml` declares
**no agent credential at all**, so a deploy as it stands yields an agent that
fails on every investigation.

**Two things Block F builds that may not be obvious.** There is **no read API**
— the deployed service exposes only `/`, `/health`, `/health/ready` and
`/sim/marketplace/*`, the last being the simulated marketplace feeding data
*in* rather than this product's own API — and there is **no frontend at all**,
no `package.json` anywhere in the repo. Everything built so far is reachable
only from Python and the CLI, so Block F builds the API and the UI, not a UI
over an existing API. Budget accordingly: the replan already admits ~27.5 hours
of work against ~20, and this is where that gap lands.

**Read `specs/2026-11-10-investigation-agent/validation.md` before touching
`reckonai/agent/`.** §6 lists five corrections this block made to earlier
documents — two of them to rules stated in `CLAUDE.md` itself, corrected below
— **§7 lists five things found by running it, three of which only a live call
could find**, §7a four more from building it, **§4a the code review (14
findings, all real, all worked)**, and §8 the six debts.

**The two model-dependent criteria are met on `gpt-4.1`, not on the Anthropic
default**, whose account had no credit on the day. `claude-haiku-5-5` is
unexercised and §5 records it as unproven rather than assumed. Whoever tops up
re-runs §3's two invocations without `--provider`.

**A withheld record is `unavailable`, never empty, and this is the block's
sharpest lesson.** Returning `[]` for a withheld kind made "you may not see
the ledger" identical to "the ledger has no entries", and the agent reported
the stronger claim at confidence 1.00. That is [[mission]] P3's distinction —
an absent value and a measured zero are different claims — applied to
evidence. A withheld kind raises `Withheld` and reaches the model as an
explicit marker. **Any new tool must preserve that distinction**; returning an
empty collection for something you are declining to show is how the agent
fabricates a finding about the marketplace's books. **Review found it three
times by three different routes** - a withheld kind, a raw-payload lookup that
bypassed the withhold, and an order with no assembled case - so the rule is
about every path to an empty result, not about `Withheld` alone.

The agent lives in `reckonai/agent/`: `tools.py` (the read-only surface),
`citations.py`, `registry.py` (one tool description, adapted to both
providers), `report.py` (the shared Pydantic report), `prompt.py`,
`providers/` (`anthropic.py` and `openai.py`), `store.py`, `investigate.py`
(the `python -m` entry point). **It is the second package outside the purity
gate and inside strict mypy**, after `jobs/`, and `tests/test_money_purity.py`
records why.

**Two providers, one report.** `settings.agent_provider` selects `anthropic`
(default, `claude-haiku-5-5`) or `openai`; the structured report is the seam. **There is no failover between them on any failure, outages included, by the
operator's decision** (`validation.md` §8 item 7): the user names the provider
(`AGENT_PROVIDER`, `AGENT_MODEL`, or `--provider` / `--model`), and an outage is
a recorded failed investigation, not a reroute. Do not add failover without
asking; it was scoped and declined.
`tech-stack.md` §1 and §5 carry the dated 2026-11-10 amendment, and **§5.1's
request table is Anthropic-specific** — every row was derived from real Claude
API behaviour and several are 400s there. The OpenAI path's configuration is
recorded in that `validation.md` §3a, including that it returns **no reasoning
summary**. Load the `claude-api` skill before changing the Anthropic provider.

**Nothing downgrades, and the columns are what prove it.** Neither provider has
a refusal fallback and there is none between them, so a `stop_reason:
"refusal"` ends the investigation and is reported as **failed** — never as a
report — and `investigation.downgraded` can never be set in the MVP. That is
§5.2's rule honoured rather than exempted. `stop_reason` is read before any
content on both providers.

**Two `CLAUDE.md` rules this block corrected.** "Cite the capture, never
`platform_fee.external_id`" is **incomplete**: a fee PayPal *reverses* has no
`capture_id` — its parent is the refund — and Scenario 1's own order is that
shape. The stable reference is the fee's parent, whichever it is, and
`fetch_platform_fees` returns `fee_kind` so a capture fee and a reversal are
never added together. And a fee id **does resolve** to a real record; it simply
will not find it again after a reseed, so `Citation` answers `resolved` and
`stable` separately — treating a fee id as fabricated would be false and would
fail on the classifier's own findings.

Three things Block E inherits rather than builds. `reconcile.run(session,
tenant_id)` is its recompute tool and returns **every** finding regardless of
any window. The exception to investigate is selected through
`exceptions_only()`, so a parked `awaiting_evidence` finding is never handed to
the agent as a discrepancy. And the agent is **read-only**: it enqueues nothing
and moves nothing (P6), which
`tests/test_agent_tools.py::test_no_tool_writes_to_the_session` proves by
calling every tool and asserting the session holds nothing pending.

**Running the agent locally needs seed, run, reset, in that order.**
`python -m reckonai.demo_data --ensure` commits, and the suite requires a
database with no committed marketplace rows — skipping the `--reset` afterwards
fails six tests and errors 43 more, none of them about the agent. **The
`insufficient_evidence` branch is still the criterion most at risk on a cheaper
model, so Block G scores that branch specifically**, on both models and now
both providers.

**Read `specs/2026-11-09-queue-scheduled-run/validation.md` §9 and §8 first**
(three debts handed forward, three corrections to earlier documents), then
`specs/2026-11-09-matching-classification/requirements.md` §4, which is Block
C's handoff. In that directory's `validation.md`, **§9 tabulates the nine
code-review findings and their fixes** — read it before extending the
classifier — §10 lists its corrections, and §11 what it did not prove.

The engine lives in `reckonai/engine/`: `rules.py`, `ledger.py`,
`entitlement.py`, `refunds.py`, `positions.py`. **It compares nothing** — it
answers only what should be true. Comparison lives next door in
`reckonai/recon/`: `vocabulary.py` (the `ExceptionCode` enum, pinned to the
sim manifest), `matcher.py`, `tolerance.py` (**every tolerance decision in the
product, and nowhere else**), `classifier.py`. Both packages are pure, under
the purity gate and strict mypy.

`reckonai/reconcile.py` is the seam: the only module holding both a `Session`
and an `OrderCase`. Block D's windowed run wraps it and Block E calls it for a
recompute — one classification path, no windowing or clock inside it —
`run(session, tenant_id)` and `exceptions(session, tenant_id)`. **Count
exceptions through `exceptions_only()`, never `len(findings)`** —
`awaiting_evidence` is a finding and not an exception, so counting raw
findings fails the false-positive gate while being correct.

**Findings persist, and Block F's dashboard reads them.** Block D added
`reconciliation_run` and `reconciliation_exception` — append-only, one row per
`Finding`, the frozen contract column for column. Two things to know: the
column is **`order_external_ref`**, not `order_ref`, because `order_ref` is one
of the sim's deliberately non-canonical outward names and a guard fails if a
canonical column claims one; and **`is_exception` is stored**, so a queue
filters on a column rather than on a `WHERE code NOT IN (...)` that could
disagree with `exceptions_only()`.

The job side lives in `reckonai/jobs/`: `queue.py` (`SKIP LOCKED`),
`window.py` (the two selection clauses), `reconcile_job.py` (the handler),
`handlers.py` (the registry), `schedule.py` (the `python -m` entry point the
Cron Job invokes). **It is the one package deliberately outside the purity
gate** — it holds the sessions, clocks and transaction boundaries the engine
and classifier refuse — and it is inside strict mypy for that reason.

Three Block D results not to re-derive. **The claim is committed before the
handler runs**: sharing one transaction means a failing handler's rollback
undoes the attempt increment too, and the job retries forever looking untried.
**The windowed run selects on `order.placed_at` unioned with new evidence since
the last completed run** — the second clause is what makes a dispute settled
weeks later visible at all, and it cannot be a SQL join because a late PayPal
refund reaches its order only through the matcher's claim. And **`dispute` is
now a position-moving ledger category**, which took changing both
`POSITION_CATEGORIES` and `_partition_by_ledger`: the latter decides what
counts as having recorded a refund, and reading only `refund` entries there
made one correct booking produce two findings.

**The Cron Job is deployed and has not yet fired.** The Blueprint sync that
PR #22's merge triggered created it on 2026-11-10 14:27 UTC (`reckonai-recon`,
`crn-dbpijkmi0phs73aa406g`). Render does not run a cron job at creation, so the
first tick is 01:00 UTC on 2026-11-11, and Phase 8's fourth criterion stays
unproven until its logs show a run (`render logs -r crn-dbpijkmi0phs73aa406g`).
It runs **daily**, not hourly: the window bounds and the idempotency key are
both date-grained, so every tick in a UTC day is the same piece of work. **The
first tick covers 2 of the 18 seeded orders** - the window is `[11-10, 11-12)`
and the seed is fixed-date (Nov 2-19) - so the full set of faults appears only
as the calendar advances, unless a wide initial run is enqueued by hand.

**A `/code-review medium` on Block D returned nine findings, all nine real** -
two of them high, and seven invisible to the seeded dataset. That directory's
`validation.md` §7b tabulates them with their fixes; read it before touching
`jobs/` or the dispute rule. The two worth carrying: **a `claimed` job is
recoverable only because `claim` reclaims a stale one** - nothing else moves a
row out of `claimed`, and `drain` does not wrap `dispatch` - and **the
dispute-versus-refund shape is undecidable from the records**, so a
dispute-backed refund is `awaiting_evidence` rather than a guess in either
direction.

Three Block C results not to re-derive. **Do not widen the zero same-currency
tolerance** — it rests on two measurements across all 21 captures, and F2's
whole fault is 10.00 of commission. **One fault must stay one finding**:
specific rules account for an amount and a residual check reports only the
remainder, so a new rule that does not credit `explained` will double-report
an existing fault. And **the sim manifest was stale in two ways** — F3's and F6's
impact figures, and four evidence references naming records that exist
nowhere, all of them Block A synthetic rows retired by R16. Corrected against
the fixtures and now pinned by `tests/test_manifest_agrees_with_seed.py` and
`test_every_reference_the_manifest_names_is_a_real_record`, the two guards
that were missing. **Cite the fee's parent, never `platform_fee.external_id`**
— the latter is built from a payload UUID and changes on every seed. Block C
stated this as "cite the capture"; Block E found that a *reversal* fee has no
capture and corrected it, as above.

Two things from Block B that change how other code is written. A **type checker
is now wired in**: `uv run mypy`, strict, scoped to `src/reckonai/engine/` by
`[tool.mypy]`, and gated in CI. And `src/reckonai/py.typed` now exists, so the
package advertises its annotations.

The capture is done: 18 real sandbox orders in `fixtures/paypal/`, replayed by
`reckonai.paypal.replay`, with the marketplace seed re-pointed at the captured
figures. **Read `specs/2026-11-09-paypal-capture/` before anything that
touches the dataset** — its `validation.md` §2 is the current hand-check, and
Block A's §2 is superseded. §7 there lists five things found by running it
that are not obvious from any payload.

`specs/2026-11-09-paypal-capture/validation.md` §2 holds the figures every
expected amount is checked against. Block A's handoff in
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` §3 still has
useful detail, but **its seller list is out of date**: `vendor-citrine` and
`vendor-dunlin` no longer exist, and there is one commission rule, the tenant's
10%. Its claim that "every seeded capture states a fee" is also now false — see
below.

**Two measurements from Block B, now relied on by Block C's comparisons.** Across all
21 captured captures, `net_amount` equals `gross_amount - platform_fees`
**exactly, zero residual** — so the expected entitlement is directly comparable
to PayPal's `net_amount`, with nothing between them. And **no capture states a
`paypal_fee` at all** (a refund states an explicit `0.00`; a capture omits the
key), which is why no fee-inclusive position is published and why an absent fee
and a measured zero must stay different claims.

The expected commission equals `platform_fee.amount` on all 15 clean purchase
units, pinned by test. **Compare against that column, never a recomputation.**

Block A delivered the simulated marketplace (`reckonai.sim`), the six seeded
faults with their manifest, and the connector (`reckonai.connector`). That
directory's `validation.md` §9 records seven corrections to its own
`requirements.md` and two bugs the checks caught; §8 records three debts
handed forward. Of its five debts, 1 was Block C's first task, 2 was retired
by the capture, 4 was settled by Block B, and 5 is Block F's; **debt 4's
warning still binds** — the suite is order-dependent and must not be
randomised.

**What the MVP does not include**, so it is not half-built by accident: webhook
ingestion (R8), live PayPal calls during the reconciliation run — records are
replayed from captured sandbox responses (R6, whose capture now runs early per
R16), LLM-suggested field mapping (R11), and the dashboard's seller financial
position view (R13). The AI
investigation agent is kept **entire** (Phase 12) and the zero-false-positive
gate (Phase 11) is the last thing to compromise.

Phase 0's observed evidence was captured against a **first-party** integration,
so it does not cover platform fees, disbursement modes or any seller-level view.
Phase 0b closed that gap, and `specs/paypal-fields.md` is now regenerated from
multiparty evidence only.

Phase 1 delivered the deployable spine — FastAPI, Postgres, Alembic, pytest,
GitHub Actions CI and a Render Blueprint — running at
**https://reckonai-api.onrender.com**. What it hands to Phases 2, 3, 7, 8 and 14
is listed under Phase 1 in `specs/roadmap.md`; read that before starting any of
them.

Phase 2 delivered the canonical core — `Money`, largest-remainder allocation,
and the `tenant`, `seller`, `order`, `order_item` and `commission_rule` tables,
with the `float` and tenant gates enforcing the rules below. `readme.md` §0.1 is
the short version of the money rules.

Phase 3 delivered the event side — `raw_payload`, `payment`, `capture`,
`refund`, `dispute`, `platform_fee`, `disbursement`, `fx_conversion` and
`seller_ledger_entry`, plus the two lifecycle state vocabularies in
`reckonai/models/lifecycle.py`. Its handoff to Phases 4, 5, 6, 7, 10, 11 and 12
is under Phase 3 in `specs/roadmap.md`, and the decisions are in
`specs/2026-11-08-canonical-events/requirements.md`. **Phase 4 should read that
handoff before writing a seed builder** — the `raw_payload` link is `NOT NULL`,
the ledger amount is unsigned, and SQLAlchemy's flush order between unrelated
mappers is not derived from foreign keys.

## Hard rules

**No emojis in the codebase.** Source, comments, docstrings, log messages, test
names, commit messages, CLI output, UI strings. Typographic punctuation,
box-drawing characters in docs, and non-ASCII characters in real data are fine —
this bans pictographs, not all non-ASCII. See `specs/tech-stack.md` §13.

**Never use `float` for a monetary value.** `decimal.Decimal` in Python,
`NUMERIC` in Postgres, strings over JSON. Every amount carries its currency.
See `specs/tech-stack.md` §2.

**The LLM never does arithmetic that reaches a financial conclusion.** All
monetary calculation lives in the deterministic engine. The agent explains and
investigates; it does not compute. (`mission.md` P1)

**Never fabricate a financial value.** If PayPal does not supply a settlement,
rate or fee, record the gap as a limitation. Do not estimate, infer, or
substitute a public FX rate for a provider conversion record. (`mission.md` P3)

In the schema this is why `fx_conversion.fee` is `NULL` and never zero — **an
absent fee and a measured fee of nothing are different claims**, and a `CHECK`
refuses the zero. The same reasoning keeps `disbursement.released_at` free of
any default: PayPal's release record carries no usable timestamp, so writing an
ingestion time there would present our clock as a provider timestamp.

**Nothing moves money.** No tool and no code path alters a seller balance,
issues a refund or initiates a payout without human approval. (`mission.md` P6)

**The agent loop is append-only.** No compaction, no context editing, no
trimming of earlier tool results. See `specs/tech-stack.md` §5.2.

## Conventions

- Package manager is `uv`. Install with `uv sync --frozen`, run with `uv run`.
  No hand-maintained `requirements.txt`.
- **The test suite is order-dependent.** Two modules commit rows and clean up
  at module teardown; `tests/test_migrations.py` downgrades to `base`. It
  passes because pytest collects alphabetically. **Do not add test
  randomisation or `-n auto`** without first adding a session-scoped guard —
  `specs/2026-11-08-block-a-marketplace-connector/validation.md` §8 debt 4 has
  the detail and the fix.
- **The deployed database carries the schema and no rows**, and is not
  reachable externally, so nothing can currently seed it. The live demo needs
  that. It is §8 debt 5, owned by Block F — though `render.yaml`'s
  `preDeployCommand` already runs `reckonai.demo_data --ensure`, so Block F
  should verify rather than rebuild.
- **Local work needs the compose database and `DATABASE_URL` exported.**
  Without them the database-backed tests skip locally and fail hard in CI, which
  is deliberate — a silent skip would be a green run that tested nothing.
  `readme.md` §0 has the commands.
- In a deployed command, `uv run` takes `--no-sync`. Without it `uv run` syncs
  before executing, undoing the build's `--no-dev` and installing packages at
  container start.
- The reconciliation engine is pure functions with no I/O — no database handle,
  HTTP client or clock read inside a calculation.
- Retain raw source payloads; never discard the evidence for a decision.
- Every table carrying marketplace data has a `tenant_id`, and every query
  filters by it.
- **Every financial record links to its `raw_payload` row, `NOT NULL`.** One
  central JSONB store, referenced compositely with `tenant_id`; a record cannot
  exist without the evidence behind it.
- **A new model module must be added to `reckonai/models/__init__.py`.** A
  module that is never imported is absent from `Base.metadata`, and
  autogenerate reads its table as one to **drop**.
  `tests/test_model_registry.py` fails if one is missing.
- **The Render CLI is available** (`brew install render`) for reading deploy
  logs, deploy status and service state — which is how Phase 2's post-merge gate
  was verified. The workspace and resource ids, and three mechanics that each
  cost a round trip to discover, are in
  `specs/2026-11-07-canonical-core/notes.md` §"Render CLI setup". The database
  is **not** reachable externally (`ipAllowList: []`), so a schema check runs
  from inside Render, not from a developer machine.
