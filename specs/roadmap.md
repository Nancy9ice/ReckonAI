# ReckonAI — Roadmap

> **Status:** Constitution. Implementation order and phase boundaries.
> **Updated 2026-11-10: Phases 12, 13 and 14 are built and merged (Blocks E and F). Phase 15 (eval, demo, limitations) was skipped by the operator on
> 2026-11-11, so every phase that will be built is built.** The rest of
> this note is the earlier handoff to Block D, kept as history. **Block D** — the `jobs` table, `FOR UPDATE SKIP LOCKED`,
> `processed_events`, the Cron Job, the date-windowed run, **and the exception
> table Phase 11 assigned to it** (that block's `requirements.md` D8). Phases
> 0, 0b, 1, 2, 3, 4, 5, 6, 10 and 11 are complete; Phase 7 is partially
> complete and its remainder is listed in its own section. Read
> `specs/2026-11-09-matching-classification/requirements.md` §4 first — Phase
> 11's handoff — and `specs/2026-11-09-paypal-capture/validation.md` §2 for the
> figures every expected amount is checked against, which supersedes Block A's.
> **Strategy:** Foundation-first — each layer is completed before the next begins.
> **Budget:** ~1 week.
> Governed by [[mission]]; technology per [[tech-stack]].

---

## How to read this

Phases are deliberately small — most are half a day — and each one has **exit
criteria** that are checkable rather than a matter of opinion. A phase is not
done because the code exists; it is done when its exit criteria pass.

Phases run in order. The dependency note on each says what it genuinely needs,
so if you choose to resequence you can see what breaks.

**Total as written: 8.5 days against a ~1 week budget.** That is intentional, so
the overflow is visible rather than discovered on the last day. §"Cutline"
below names exactly what to drop to land in 7.

Phase 0 is **complete**, and Phase 0b was added on 2026-11-07 after Phase 0
found the product specified against the wrong PayPal integration model
(`specs/multiparty-direction.md`). So 0.5 day is spent and 0.5 added: the
remaining work is 8 days, and the overflow is unchanged.

**Phases 0, 0b, 1, 2 and 3 are complete.** Phase 4 is next, and reads Phase 3's
handoff below before starting — particularly the three things that bite a seed
builder.

> **Sequencing superseded 2026-11-08.** Phases 4-15 below are re-sequenced into
> seven blocks by `specs/2026-11-08-replan/requirements.md`, against a two-day
> deadline. **Read that document first; it is the session entry point.** Every
> exit criterion below still binds for every phase the replan keeps, which is
> why they are left standing here. Where a phase is narrowed, folded or
> deferred, a note under its heading says so and names the decision.

---

## Phase 0 — PayPal sandbox field discovery · 0.5 day · **COMPLETE 2026-11-07**

**Goal:** Find out what PayPal *actually* returns before anything is modelled
around it.

> **Delivered**, and it changed the product. Output is `specs/paypal-fields.md`
> plus the artifacts and handoff in
> `specs/2026-11-05-paypal-field-discovery/`. The headline finding is that the
> product was specified for the **wrong PayPal integration model** — see
> `specs/multiparty-direction.md`. That is this phase succeeding: it surfaced
> before Phases 2 and 3 built on it.
>
> **One limit on its evidence.** Every fixture was created **first-party**, with
> the marketplace itself as `payee`. The declared column is model-independent
> and stands in full. The observed column does not cover `platform_fees`,
> `disbursement_mode` or any seller-level view, which is what **Phase 0b**
> exists to capture.

Read-only investigation. No integration code, no abstractions.

**Prerequisite:** install the APIMatic Context Plugin for PayPal
([[tech-stack]] §8.1). Without it this phase is model recall and manual doc
searching, which is the failure mode it exists to prevent.

Then establish two columns and compare them:

- **Declared** — what the PayPal API contract says exists. Corrected
  2026-11-05: this comes from the APIMatic-generated PayPal Server SDK at a
  pinned tag, which the plugin directs you to; the plugin itself carries no
  field names. Disputes and payouts are not in the SDK and fall back to PayPal's
  REST reference. See `specs/2026-11-05-paypal-field-discovery/requirements.md`
  §1 and §3.1.
- **Observed** — what the sandbox and the PayPal MCP toolkit actually return for
  our transactions.

Write down, per record type, which fields exist in each column.

- Transactions / captures: ids, amounts, currencies, fees
- Refunds: linkage back to the original capture, partial-refund shape
- Disputes: status, amount, lifecycle
- Payouts: batch and item structure
- **Settlement and FX: does a real rate, fee and settlement currency appear, or
  not?**

**Exit criteria**
- The APIMatic Context Plugin is installed and returning PayPal context.
- A field-availability table committed to `specs/paypal-fields.md`, with
  *declared* and *observed* as separate columns.
- Every field the canonical model intends to carry is marked *present*,
  *absent*, or *present but unreliable* — judged on **observed**, never on
  declared ([[mission]] P3).
- Every declared-but-not-observed field is listed explicitly. This set is the
  phase's most valuable output: it is where the canonical model would otherwise
  have been built on a field that never arrives.
- Each absent field has a decision recorded: derive it (labelled as derived) or
  declare the limitation.

**Depends on:** nothing. **Why first:** [[tech-stack]] §8 and [[mission]] P3.
Building the canonical model against guessed PayPal fields is the single most
expensive mistake available this week, because Phases 2–3 would then need
reworking after Phase 7.

---

## Phase 0b — Multiparty field discovery · 0.5 day · **COMPLETE 2026-11-07**

> **Delivered.** `specs/paypal-fields.md` is regenerated from multiparty
> evidence only: **69 canonical rows, 56 `present`, 6 `present-but-unreliable`,
> 7 `absent`, none `unanswered`.** Artifacts, fixtures and findings are in
> `specs/2026-11-07-multiparty-field-discovery/`.
>
> All six questions below are answered, plus two that were added. The headline
> results: the platform fee is taken **at capture in both disbursement modes**;
> **releasing held funds changes nothing on the capture**, so every `DELAYED`
> transaction must be joined to its disbursement record; commission is clawed
> back **automatically** on both refunds and lost disputes; and a conversion
> applies to the **net, not the gross**.
>
> **One probe is still outstanding** and does not block anything: whether the
> platform fee reaches the platform through a readable API. Both reporting
> endpoints answer `200`; the snapshot simply predated the captures. See
> `specs/2026-11-07-multiparty-field-discovery/notes.md` §5.1, which also warns
> how to read that result when it arrives.

**Added 2026-11-07.** Phase 0 answered its question against a first-party
integration. The product targets PayPal's multiparty model
(`specs/multiparty-direction.md`), so the observed column has a shaped hole in
it: the platform fee, the disbursement modes, and whatever the platform can see
of a seller's transactions.

**Goal:** Close that hole, and establish whether the multiparty reconciliation
the product now describes is implementable at all.

Same method and the same four scripts — probe, derive, extract, build — which
need new endpoints and the `PayPal-Auth-Assertion` header but no change in
approach. Fixtures for **both** disbursement modes.

**Prerequisite, and this phase cannot start without it:** a sandbox seller
account onboarded under the platform with the `PARTNER_FEE` and
`DELAY_FUNDS_DISBURSEMENT` features. These are granted **per seller at
onboarding**, not as account-level switches, and PayPal's simpler
build-your-own onboarding flow excludes Platform Fee, Delayed Disbursement,
Partner Reporting and the Disputes API outright. The same account also unblocks
the wallet-funded capture that twelve `unanswered` rows in
`specs/paypal-fields.md` are waiting on.

**The six questions**, each tied to the validation it unblocks:

| # | Question | Unblocks |
|---|---|---|
| 1 | Is `platform_fees` reachable in sandbox without partner approval? | commission validation |
| 2 | Can the platform read a seller's capture via `PayPal-Auth-Assertion`, and what returns? | entitlement validation, both modes |
| 3 | Under `DELAYED`, what records represent held funds, the release, and the statuses between? | the disbursement stage |
| 4 | Is there platform-level settlement or reporting across sellers, or only per-transaction reads? | all three above |
| 5 | How does FX behave when the payee is a seller in another currency? | currency comparison |
| 6 | Does a refund reverse the platform fee, and is the reversal automatic or instructed? | refund reconciliation |

**Question 2 carries the risk.** Under `INSTANT` the seller's funds land in the
seller's own PayPal account, and PayPal's own wording is "seller-level
transaction records **where accessible**". If the platform cannot read them,
entitlement validation is not implementable as specified and the MVP narrows.

**Known already, so do not re-derive:** release is
`POST /v1/payments/referenced-payouts-items` with `reference_id` set to the
capture id and `reference_type: TRANSACTION_ID`; held funds auto-disburse after
**28 days**; `PayPal-Auth-Assertion` is an unsigned JWT (`alg: none`) carrying
the platform client id as `iss` and the seller's `payer_id` or `email`. Both
referenced-payouts endpoints currently return **`403 NOT_AUTHORIZED`** for our
app — they need partner approval and a BN code, and that is a blocker to
confirm before planning around question 3.

**Exit criteria**
- `specs/paypal-fields.md` regenerated with multiparty fixtures, and every
  verdict still traceable to `observed.json` (`check_fields_doc.py --all`).
- Each of the six questions answered, or recorded as unreachable **with the
  reason** — an unreachable answer is a finding, not a failure, exactly as
  disputes were in Phase 0.
- Whether the platform fee is taken at capture or at release under `DELAYED`
  stated plainly, or recorded as unverifiable.
- The access-dependent amendments still outstanding in
  `specs/multiparty-direction.md` §6 applied.

**Depends on:** Phase 0, and the sandbox seller account.
**Why before Phase 3:** Phase 3 designs the canonical tables. Designing
`platform_fee` and the disbursement states without having seen one is the same
mistake Phase 0 exists to prevent, one model later.

---

## Phase 1 — Repo and deployment skeleton · 0.5 day · **COMPLETE 2026-11-07**

> **Delivered and deployed**, at **https://reckonai-api.onrender.com**. Spec,
> plan, merge gate and findings are in
> `specs/2026-11-07-repo-deployment-skeleton/`.
>
> Every exit criterion below passes. The pre-merge gate is 13 automated checks
> and the post-merge gate is the deployed ones; both results are tabled in that
> directory's `notes.md`. `/health` and `/health/ready` each answer `200` from
> the deployed URL, the baseline migration applied through the web service's
> `preDeployCommand`, and the worker is up.
>
> **What later phases inherit, so none of it is rediscovered:**
>
> - **Phase 2** sets `target_metadata` in `src/reckonai/migrations/env.py`,
>   which is `None` with a comment saying so, and **deletes** Phase 1's
>   `validation.md` C14 rather than inverting it — C14 asserts the money path is
>   *absent*, and Phase 2 is where the `float` gate replaces it.
> - **Phase 3** must migrate **expand-then-contract**. `preDeployCommand` runs
>   while the previous version is still serving traffic, so dropping or renaming
>   a column the running instance still selects breaks it for the overlap
>   window. `src/reckonai/migrations/README` carries this where a migration
>   author will meet it.
> - **Phase 7** adds PayPal variables to `render.yaml` as `sync: false`, which
>   puts the key in version control and the value only in the dashboard. None
>   are declared yet, deliberately.
> - **Phase 8** declares a real Render Cron Job — **Cron Jobs are confirmed
>   available on the plan**, so the worker-loop timer fallback in
>   [[tech-stack]] §11 is dropped. It also owes the worker real retry and
>   backoff: today an unreachable database logs
>   `reckonai.worker.database_unreachable` and the loop continues, which is
>   right for "stays up" and wrong for claiming jobs.
> - **Phase 14** deploys the frontend as a **separate Render static site**. `/`
>   is the API's own service index and is not a placeholder for the UI.
>
> **Two findings worth not relearning.** Render's `DATABASE_URL` arrives as
> `postgres://` or `postgresql://` — SQLAlchemy 2.0 rejects the first and maps
> the second to psycopg2, which this project does not install, so `config.py`
> normalises both to `postgresql+psycopg://`. And `uv run` re-syncs the
> environment before executing unless given `--no-sync`, which on Render undid
> the build's `--no-dev` and installed packages at container start.

**Goal:** A deployable spine, so deployment is never a last-day problem.

FastAPI app, `/health`, Postgres connected, Alembic initialised, pytest running,
Render web service + worker + managed Postgres provisioned, secrets in env vars.

**Exit criteria**
- `/health` returns 200 from the deployed Render URL, not just locally.
- An empty Alembic migration applies cleanly against the Render database.
- `pytest` runs green in CI with at least one real assertion.
- The worker process starts, logs, and stays up.
- `uv.lock` is committed, and every environment — local, CI, web, worker —
  installs with `uv sync --frozen` ([[tech-stack]] §3).
- Render plan constraints confirmed: Background Workers available on the current
  tier, and the Postgres plan has no expiry window inside the project's life
  ([[tech-stack]] §11).
- Connection pool sizes set explicitly per component, not left at defaults.

**Depends on:** nothing.

---

## Phase 2 — Canonical core: money, tenant, catalogue · 0.5 day · **COMPLETE 2026-11-08**

> **Delivered.** `Money`, the largest-remainder allocation helper, the five
> catalogue tables and the gates that keep them honest. Spec, plan, merge gate
> and findings are in `specs/2026-11-07-canonical-core/`.
>
> Every exit criterion below passes. The pre-merge gate is 18 automated checks
> and **858 tests**; the post-merge deployed checks are tabled in that
> directory's `notes.md`.
>
> **What later phases inherit, so none of it is rediscovered:**
>
> - **Phase 3** gets `Base`, `TenantMixin`, `TimestampMixin`,
>   `money_columns()`, a populated `target_metadata`, and both gates already
>   enforcing its eight new tables — it must add each model module to
>   `reckonai/models/__init__.py`, because a module that is never imported is
>   absent from `Base.metadata` and autogenerate reads that as a table to
>   **drop**. It also owns the lifecycle state enum, and inherits the warning
>   that **Alembic does not reliably emit a native enum type's `CREATE`/`DROP`
>   alongside its table's** — which is why Phase 2 used a checked `VARCHAR(16)`
>   for `disbursement_mode` rather than meeting that trap in the first revision
>   with a schema in it.
> - **Phase 4** instantiates these five tables. `seller` carries
>   `partner_fee_enabled` and `delay_funds_enabled`, and `order` carries
>   `disbursement_mode`, so "at least one order uses `DELAYED`" needs no schema
>   change. **Its clean baseline has no database backstop**: by decision, no
>   constraint ties an order's total to the sum of its items, so the seed
>   builder needs its own consistency check or Phase 11's false-positive gate is
>   measured against a baseline that was never clean.
> - **Phase 6** owns query-time tenant filtering, specified in that directory's
>   `requirements.md` D7. It inherits composite foreign keys that already make a
>   cross-tenant link unwritable.
> - **Phase 10** fills `src/reckonai/engine/`, calls `allocate()` for
>   proportional refund allocation, and consumes `resolve_commission_rule()`.
>   The purity gate is already watching that package, which is why its functions
>   take a `typing.Protocol` rather than an ORM class.
> - **Phase 14** formats amounts from the shape this phase fixed:
>   `{"amount": "160.00", "currency": "USD"}`.
>
> **Four findings worth not relearning.**
>
> **A nullable `composite()` does not return `None`** — it constructs
> `Money(None, None)` and raises. Nullable money pairs go through the
> `_money_or_none` factory in `models/base.py`.
>
> **Alembic's `fileConfig` was disabling every application logger.** It
> defaults `disable_existing_loggers` to `True`, so running Alembic in-process
> silences `reckonai.web` and `reckonai.worker` for the life of the process.
> Latent since Phase 1; invisible on Render, where Alembic runs as its own
> process through `preDeployCommand`.
>
> **`Base.metadata.create_all` in a test fixture fights with Alembic.**
> Database-backed tests now run against the migrated schema via the
> `migrated_database` fixture, which is also stricter — a `CHECK` lost in
> autogenerate now fails the constraint tests, not only the drift check.
>
> **Decimal division is not exact enough for allocation.** It is evaluated at
> the context precision, so comparing the remainders it produces can seat a
> spare minor unit on the wrong party. The allocator scales weights to a common
> integer basis instead.

**Goal:** The `Money` value object and the entities everything else references.

`Money` (Decimal + currency, mixed-currency arithmetic refused), largest-
remainder allocation helper, and tables for `tenant`, `seller`, `order`,
`order_item`, `commission_rule`.

**Exit criteria**
- Unit tests prove: mixed-currency arithmetic raises; allocation parts sum
  exactly to the whole with no residue; a 3-decimal currency round-trips.
- No `float` anywhere in the money path — enforced by a test that greps the
  module, so it cannot regress silently.
- Amounts serialize to JSON as strings.

**Depends on:** Phase 1. **Governs:** [[tech-stack]] §2.

---

## Phase 3 — Canonical financial events and raw payload store · 0.5 day · **COMPLETE 2026-11-08**

> **Delivered.** Nine tables — the eight financial record types below plus the
> `raw_payload` store each links to — the two lifecycle state vocabularies, and
> one reversible Alembic revision. Spec, plan, merge gate and findings are in
> `specs/2026-11-08-canonical-events/`.
>
> Every exit criterion below passes. The pre-merge gate is 24 automated checks
> and **1117 tests**; the post-merge deployed checks are tabled in that
> directory's `notes.md`.
>
> **What later phases inherit, so none of it is rediscovered:**
>
> - **Phase 4** seeds these tables. Three things bite it. The `raw_payload`
>   link is **`NOT NULL`**, so a seed builder cannot insert a financial row
>   without a payload row to point at. `seller_ledger_entry.amount` is
>   **unsigned** with `CHECK (> 0)` and needs a `transaction_type` of `debit`
>   or `credit`. And **flush order between unrelated mappers is not derived
>   from foreign keys** — this project declares no `relationship()`, so
>   SQLAlchemy can insert a child before its parent; a seed builder that
>   constructs an object graph in memory must flush in dependency order. It
>   also still owes its own order-total consistency check (Phase 2 D13) and
>   must delete `GET /money/example`.
> - **Phase 5** gets two of its non-faults as legal schema values: `funds_held`
>   and `release_requested` are ordinary `DISBURSEMENT_STATES` members, so
>   seeding them is not seeding a fault. And there is **no conversion `fee` to
>   make incorrect** — `CHECK (fee_amount IS NULL OR fee_amount <> 0)` refuses
>   a zero one — so the FX fault must be an incorrect rate or settlement value.
> - **Phase 6** normalises marketplace records into these same tables. One
>   table per record type holds **both** sources, told apart only by their
>   payload's `source`, and `UNIQUE (tenant_id, external_id)` is what makes its
>   re-run idempotent.
> - **Phase 7** inherits three rules it cannot read off the schema: **never
>   overwrite a populated capture breakdown with `NULL`** on a later read, since
>   a capture loses its whole `seller_receivable_breakdown` while `PENDING`;
>   `raw_payload.endpoint` exists because **field availability depends on the
>   read path**; and `disbursement.released_at` stays `NULL`, never an ingestion
>   time. Changed payloads are retained as additional rows, which is the
>   mechanism for its "raw payloads are retained and diffed against the model".
> - **Phase 10** consumes `signed()` and `position()` from
>   `reckonai/engine/ledger.py` — the one place a ledger direction becomes a
>   sign. The platform fee reversal is **automatic**, so the expected position
>   assumes it happened rather than instructing it, and a capture fee is never
>   defaulted to zero because `NULL` makes "unknown" representable.
> - **Phase 11** owns matching and adds the cross-source link this phase
>   deliberately omits — there is no `matched_*_id` column anywhere, pinned by
>   test. `reconciliation_state` is where its non-error outcomes land, and
>   **`disbursement_completed` needs no `release_requested` before it**, because
>   PayPal auto-releases after 28 days.
> - **Phase 12** cites records by these ids, and every citation resolves through
>   `raw_payload`. `refund.capture_linkage_href` is what lets an investigation
>   state that a refund-to-capture link was derived.
>
> **Four findings worth not relearning.**
>
> **A gate whose own setup performs the action it checks cannot fail.** The
> first model-import gate called `importlib.import_module` and then asserted
> `hasattr(package, module)` — but importing a submodule *binds* it on its
> parent, so the assertion passed because the test had just done the thing it
> was checking for, and the same import repaired `Base.metadata` for every
> later assertion. It passed with the import commented out. It now runs a
> subprocess importing only `reckonai.models`, which is what `env.py` does.
>
> **Flush order between unrelated mappers is not derived from foreign keys.**
> With no `relationship()` declared anywhere, SQLAlchemy's unit of work has no
> mapper dependency to sort on and inserted a seller before its tenant, despite
> `sorted_tables` ordering them correctly. Phase 2 had solved this per-fixture
> with flushes and never wrote down why.
>
> **A narrow checked column rejects an over-long value before its `CHECK`.**
> `transaction_type` is `VARCHAR(6)`; probing its `CHECK` with an 8-character
> value raises `DataError` from the width and **would keep passing if the
> `CHECK` were dropped**.
>
> **A generated constraint name reached 62 of Postgres's 63 characters.**
> `uq_raw_payload_tenant_id_source_source_record_type_body_sha256`. One more
> column in that unique constraint and the name truncates silently, which is
> the failure where `downgrade()` cannot drop what `upgrade()` created.

**Goal:** The event side of the model, shaped by Phase 0's findings.

Tables for `payment`, `capture`, `refund`, `dispute`, `seller_ledger_entry`,
`platform_fee`, `disbursement`, `fx_conversion`, plus a `raw_payload` JSONB
store and the lifecycle state enum from [[mission]] §4.

**Amended 2026-11-07 for multiparty.** `platform_fee` is now a first-class
table: PayPal's record of the commission it actually took is independent
evidence against the marketplace's own rules, and it is central rather than
incidental. `payout_batch` and `payout_item` are replaced by `disbursement`,
because under multiparty PayPal pays the seller — at capture under `INSTANT`,
or on a referenced-payout release under `DELAYED` — and a marketplace-run payout
batch no longer exists. A `platform_fee` row should be able to carry a
settlement reference later, since the consolidated daily settlement is deferred
rather than ruled out ([[mission]] §7).

**Exit criteria**
- Every financial table's amount columns are `NUMERIC` with a currency column.
- Every record links to its `raw_payload` row ([[mission]] P7).
- `fx_conversion` carries source, target, rate, timestamp, fee, provider, and a
  `is_derived` flag ([[mission]] P3). It is created **only** where PayPal
  reports an `exchange_rate`, so an empty row can never be mistaken for a
  conversion whose fields went missing. `fee` is nullable and stays null — never
  zero. `timestamp` records whether it came from the provider or was derived.
- A conversion may be **more than one row per record**: a refund declares an
  array of conversion entries where a capture declares a single object.
- Lifecycle states include the non-error states — delayed, unmatched, awaiting
  evidence — per [[mission]] P4, **plus the disbursement states**: disbursed at
  capture (`INSTANT`); funds held, release requested, disbursement completed,
  disbursement failed or pending (`DELAYED`). Held funds are a healthy
  intermediate state, not an unpaid obligation.
- Nothing is required that Phase 0 could not observe. Dispute fields in
  particular are nullable: their shape is known from a pinned contract and not
  one value was observed.
- `refund` links to its capture through a **derived** linkage, recorded as
  derived — PayPal supplies no `capture_id` on a refund, only a HATEOAS link.

**Depends on:** Phase 0 and **Phase 0b** (field reality for multiparty),
Phase 2 (`Money`).

---

## Phase 4 — Simulated marketplace API: clean data · 0.5 day · **COMPLETE 2026-11-08**

> **Delivered as Block A**, with Phases 5 and 6. `reckonai.sim` serves four
> sellers and twelve clean orders over `/sim/marketplace/*` under deliberately
> non-canonical field names, and `seed.verify()` recomputes every stated figure
> through the real commission resolver. All three exit criteria pass; the
> hand-check table is `specs/2026-11-08-block-a-marketplace-connector/
> validation.md` §2.

**Goal:** A marketplace to integrate against, with correct records only.

Endpoints for sellers, orders, order items, commission rules, payments, refunds,
disputes, ledger entries, and — per the multiparty amendment — expected seller
entitlements and release records rather than marketplace-run payout batches.
Seeded with an *internally consistent* multi-seller dataset, including one
multi-currency order.

**The simulated data must be multiparty-shaped**, or the connector is exercised
against a world the product does not target: each order line carries the seller
who is PayPal's `payee`, the marketplace's expected commission is what PayPal
should have taken as a platform fee, and at least one order uses `DELAYED`
disbursement so the held-funds states are reachable.

**Two things inherited from Phase 2**, both easy to miss:

- **Delete `GET /money/example` from `src/reckonai/app.py`.** It is a
  serialization probe, not product — the smallest thing that proved `Money`
  crosses JSON as strings through the real application rather than only in a
  unit test. Phase 4 is where real endpoints arrive and it should go then, along
  with its entry in the service index and `tests/test_money_json.py`'s route
  tests. Leaving it means a demo URL that returns `104.05` for no reason.
- **Nothing in the database stops an inconsistent order.** By decision
  (`specs/2026-11-07-canonical-core/requirements.md` D13) no constraint ties an
  order's total to the sum of its items, because such a disagreement is a
  finding rather than a bad write. So the "reconciles perfectly by hand"
  criterion below has **no database backstop**: the seed builder needs its own
  consistency check, or Phase 11's false-positive gate is measured against a
  baseline that was never clean.

**Exit criteria**
- All endpoints return data with deliberately *different* field names from
  canonical (`vendor_id`, `order_ref`, `item_total`) so mapping is exercised for
  real.
- The seed dataset reconciles perfectly by hand — this is the known-good
  baseline that proves the engine produces no false positives.
- `GET /money/example` is gone, and the service index no longer lists it.

**Depends on:** nothing technically; sequenced here so it is shaped by Phases 2–3.

---

## Phase 5 — Fault injection · 0.5 day · **COMPLETE 2026-11-08**

> **Delivered as Block A.** Six faults on their own orders (ORD-0013 to
> ORD-0018), so the hand-checked clean baseline stays intact, plus
> `reckonai.sim.manifest`. F5's expected classification is
> `awaiting_evidence` and **not** an exception — it is the entry that measures
> over-reporting.
>
> **Replanned 2026-11-08 (R4, R8):** folded into Block A, and the webhook
> emitter is dropped with webhook ingestion. The faults, the manifest and the
> coexistence of clean and faulty records are unchanged — the manifest is still
> Phase 15's ground truth. Clean and faulty records share one seed builder,
> which is also the only way to be confident the clean baseline is clean.

**Goal:** The faults the demo detects.

Seed the three [[mission]] §6 scenarios — refund missing from ledger, wrong
commission, FX discrepancy — plus a duplicate ledger entry, a delayed event, and
a disbursement that disagrees with its obligation.

**Two faults must not be seeded, because they are not faults.** Funds held under
`DELAYED` and a release not yet requested are healthy intermediate states, and
PayPal's cent-rounding of a converted settlement is an artefact of its
arithmetic. Both belong in the **clean** baseline precisely so Phase 11 proves
it does not flag them. The FX fault is an incorrect **rate or settlement value**,
never an incorrect conversion fee — PayPal publishes no conversion fee to
contradict one.

**Exit criteria**
- A manifest file records every seeded fault and its expected classification —
  this becomes the eval ground truth in Phase 15.
- Clean records and faulty records coexist in the same dataset.
- ~~The emitter can fire the same event twice on demand.~~ Dropped with the
  emitter (R8). The property it proved — the same event processed twice has one
  financial effect — is Phase 8's `processed_events` constraint, proven by test.

**Depends on:** Phase 4.

---

## Phase 6 — Marketplace connector and normalization · 0.5 day · **COMPLETE 2026-11-08**

> **Delivered as Block A.** `reckonai.connector`: httpx2 client, versioned
> `connector_mapping`, the closed transform set, §9.2 passes 1 and 2, all six
> §9.4 checks with a failing-direction test each, and idempotent ingestion with
> a `quarantined_record` store. Zero data loss is proven as
> `retrieved == written + quarantined`.
>
> **Replanned 2026-11-08 (R3):** runs as a sequenced sub-block of Block A rather
> than a separate phase — two commits and two spec entries, one session.
> **Nothing is removed**, including the [[tech-stack]] §9.4 validation gate and
> all three exit criteria below. We author both sides of this boundary, so the
> deliberately different field names are chosen in the same pass that writes the
> mapping consuming them.
>
> *The risk that creates, named so it is guarded rather than discovered:*
> controlling both sides makes a trivially-matching mapping easy to write, which
> would mean mapping was never exercised. Guard: field names stay deliberately
> different (`vendor_id`, `order_ref`, `item_total`), plus a check that no
> canonical field maps from an identically-named source field.
>
> The §9.2 LLM mapping pass is cut (R11); passes 1 and 2 and human review stay.

**Goal:** Marketplace records land in the canonical model.

Connector client, declarative field-mapping config, mapping validation, and
normalization into Phases 2–3 tables with raw payloads retained.

**Exit criteria**
- The full seeded dataset ingests with zero data loss.
- A missing required mapping fails loudly at config-validation time, not at
  ingestion time.
- Re-running ingestion is idempotent — no duplicate canonical rows.

**Depends on:** Phases 3, 4.

---

## Phase 7 — PayPal ingestion · 0.5 day · **PARTIALLY COMPLETE 2026-11-09**

> **The capture and replay half is delivered**, as the replan's R16 brought it
> ahead of Block B. `scripts/capture_paypal.py` captured 18 real multiparty
> orders into `fixtures/paypal/`, and `reckonai.paypal.replay` normalizes them
> into the canonical model. Spec and evidence:
> `specs/2026-11-09-paypal-capture/`.
>
> **Met:** sandbox records appear as canonical rows alongside marketplace
> ones; absent fields are recorded as limitations and nothing is invented
> (`capture.paypal_fee` NULL, `fx_conversion.fee` NULL,
> `disbursement.released_at` NULL); retrieval is idempotent; **refunds are read
> through the order**, confirmed against real responses which return
> `seller_payable_breakdown: null` from the refund endpoint; raw payloads are
> retained.
>
> **Four criteria are unmet, and three of them are R6 rather than debt.**
> R6 removed live PayPal from the reconciliation path, and these follow from
> that decision rather than from anything being skipped. They are real
> requirements for a production client, and this MVP does not have one.
>
> - **No `403` / `5xx` handling, because nothing retries.** Nothing calls
>   PayPal during a run. The only live call is the demo's opening order, which
>   already fails safe and which nothing depends on (R15).
> - **The reporting endpoints are not read at all.** Their payoff is a
>   conversion timestamp and per-event fees. [[mission]] §6 scenario 3 needs
>   the rate and the amounts, which the capture supplies, so
>   `fx_conversion.converted_at` is `NULL` and nothing in the demo wants it.
> - **`items[].errors` is unread** — it needs a failed disbursement, and the
>   dataset has none. Phase 12's `insufficient_evidence` branch does not
>   depend on this field.
>
> **The fourth is genuine, and was reduced rather than deferred.** "Raw
> payloads are retained **and diffed against the model**": retention is done;
> the diff is not. Its full form watches for live schema drift, which frozen
> fixtures cannot have — but the question it would answer is worth answering
> once, and now is.
>
> `tests/test_paypal_replay.py` asserts that no money-bearing field in the
> captures is silently ignored. Of 69 distinct fields, four look monetary and
> are unconsumed: `network` and `network_transaction_reference` are card
> routing metadata, and `exchange_rate.source_currency` / `target_currency`
> are **stated by PayPal and derived by the ingester from the amounts
> instead**. A second test asserts the two agree on all six conversions,
> because if they ever disagreed a real money figure would carry the wrong
> currency and both sides would still look internally consistent
> ([[mission]] P2).
>
> **What a live client would still owe:** the ongoing diff, the reporting
> join, retry and status rules, and `items[].errors`. None blocks the MVP.
>
> **One criterion needs reading precisely rather than ticking.** This phase
> says "nothing in this client moves money ... it never sends a referenced
> payout release". The **runtime** client, `reckonai.paypal.capture`, sends
> none: it creates one order and reads it back, nothing else.
> `scripts/capture_paypal.py` does issue two refunds and one release — in
> sandbox, run by hand, to build the test dataset that the demo then replays.
>
> That is consistent with [[mission]] P6, which forbids a code path that moves
> money **without human approval**: an operator running a dataset builder is
> the approval. But the distinction is real and worth stating, because the
> criterion as written would otherwise read as broken. **The product never
> releases funds; the fixture builder does, once, under a hand.**
>
> **Replanned 2026-11-08 (R6, R15):** narrowed to a capture script that writes
> real sandbox responses to fixtures, plus a replay ingester that normalizes
> them through this same path. Sandbox throughout; nothing production, ever.
>
> **Every field rule below still binds**, because they are rules about what a
> payload contains and a replayed payload is the same payload. [[mission]] P3 is
> intact: a replayed fixture is PayPal's own response, retained as the raw
> payload P7 requires anyway — not an estimate. What changes is when the call was
> made, not who supplied the number.
>
> The capture script is a real client subject to every rule here, and is run
> live on screen to open the demo (R15); the reconciliation run does not depend
> on it succeeding. That removes three documented live failure modes from a
> judged run: sandbox availability, the reporting APIs' roughly one-day lag, and
> the `5xx` that is not reliably transient.

**Goal:** PayPal records land in the same canonical model.

PayPal client, historical capture/refund/dispute/platform-fee/disbursement
retrieval, normalization, and FX conversion records populated from real PayPal
data where Phases 0 and 0b confirmed it exists.

**Exit criteria**
- Sandbox records appear as canonical rows alongside marketplace ones.
- Any field Phase 0 marked absent is handled as a recorded limitation — nothing
  is invented ([[mission]] P3).
- Retrieval is idempotent and re-runnable.
- **Refunds are read through the order**, not only the refund endpoint, which
  omits `platform_fees` for the same record. Field availability depends on the
  read path.
- **Raw payloads are retained and diffed against the model.** Phase 0 observed
  three fields that neither of PayPal's published contracts declares, and two of
  them stopped being returned a day later. A generated deserializer discards
  what it does not know, silently.
- The reporting endpoints are treated as **eventually consistent, roughly a day
  behind**, with the v2 resource endpoints authoritative for recent activity.
  Reporting is still read, because it is the only observed source of a
  conversion timestamp and per-event fees, joined on `transaction_id` and
  `paypal_reference_id`.
- A `403` is never recorded as an absent field, and a `5xx` is not retried
  blindly — a malformed request produced one repeatably.
- `items[].errors` is read on a failed disbursement; when it is absent the
  answer is `insufficient_evidence`, never an assembled cause.
- **Nothing in this client moves money** ([[mission]] P6). It never sends a
  referenced-payout release.

**Depends on:** Phases 0, **0b**, 3.

---

## Phase 8 — Job queue and worker · ~2 hours · **COMPLETE 2026-11-10**

> Delivered as Block D, with R9's date-windowed run and the exception table
> Block C assigned to it. Spec and evidence:
> `specs/2026-11-09-queue-scheduled-run/`.
>
> **Four of the five exit criteria are met**, each by test rather than by
> inspection - two concurrent claimers taking disjoint sets while the first
> holds an uncommitted lock; a job retrying to a terminal `failed` with its
> error recorded; the same window refused by the `processed_event` constraint;
> and a parked `awaiting_evidence` exception cleared by a later run once its
> late PayPal record arrives.
>
> **The fifth - "the cron job fires a date-windowed run on the deployed
> environment" - is still unobserved.** *Corrected 2026-11-10.* This note
> originally said the `cron` service was declared but not applied and that the
> criterion was "deferred to Block F, which owns the next deploy". Both were
> wrong. The Blueprint sync that PR #22's merge triggered created the service
> on 2026-11-10 at 14:27 UTC, and **every merge to `main` deploys** (PRs #19
> through #22 each produced a `new_commit` deploy), so there was never a
> separate deploy to wait for. What remains is observing the first scheduled
> tick, 01:00 UTC on 2026-11-11, and that is a statement about Render, not
> about this code.
>
> **R9's window needed a second clause.** `order.placed_at` alone cannot see a
> refund that settles a dispute weeks after the order, so selection is that
> clause unioned with any order holding evidence retrieved since the last
> completed run. It cannot be a SQL join: a late PayPal refund reaches its
> order only through the matcher's claim, not through a column.
>
> **The dedupe key is the enqueuer's idempotency key**, of which a provider
> event id is one kind. §4's "the provider's event id" assumed webhooks, which
> R8 cut; keying on the composed window key keeps the property §4 argues for -
> the marker and the financial write in one `COMMIT` - and makes it exercised
> by the only path the MVP has, rather than shipping a table nothing writes to.


> **Replanned 2026-11-08 (R7, R9):** minimised, **not cut.** Two reasons it
> cannot go: `render.yaml` already declares `reckonai-worker` as a *paid*
> Starter instance, in a submission that lists Render's purpose as background
> processing and scheduled reconciliation; and the concurrency note below means
> SKIP LOCKED correctness is the condition production is already in.
>
> Kept whole: `jobs`, SKIP LOCKED claiming, `processed_events`, the Cron Job.
> Narrowed: retry becomes attempt count, `run_after` and a terminal failed state
> with the error recorded, rather than a tuned backoff policy. The scheduled run
> is **date-windowed** and gains a second job — re-evaluating exceptions parked
> as `awaiting_evidence` (R10), since the reporting APIs lag roughly a day.

**Goal:** The execution substrate from [[tech-stack]] §4.

`jobs` table with `FOR UPDATE SKIP LOCKED` claiming, retry, `processed_events`
unique constraint, worker loop, Render Cron Job wired to a scheduled
date-windowed run.

**Two workers already run concurrently on every deploy** (observed 2026-11-08,
Phase 2 Group 9). Render's rolling deploy overlapped the outgoing worker and the
incoming one for about 90 seconds — the log shows the old instance at
`tick=1045..1048` interleaved with the new one at `tick=2,3,4`. So the first
exit criterion below is not a condition to construct in a test; it is the
condition production is already in, every time the worker ships. `FOR UPDATE
SKIP LOCKED` has to be right before the first real job type exists.

**Exit criteria**
- Two workers running concurrently never claim the same job — proven by test,
  not by inspection.
- A failing job retries and lands in a terminal failed state with its error
  recorded.
- Inserting the same event id twice raises a constraint violation. **This is
  where the duplicate-delivery property is proven**, now that Phase 9 is
  deferred — by test, not by a webhook.
- The cron job fires a date-windowed run on the deployed environment.
- A record incomplete when first read, and an exception parked as
  `awaiting_evidence`, are both re-evaluated by a later run (R10).

**Depends on:** Phase 1.

---

## Phase 9 — Event-driven ingestion · **DEFERRED PAST THE MVP 2026-11-08**

> **Deferred by R8**, with [[mission]] §4 amended to match rather than left
> contradicted. Kept in full below as the design for when it lands.
>
> **Why it was the affordable cut.** §4's load-bearing claim is *through the
> same engine and the same canonical model* — no second code path for live
> events. That is **stronger** in the MVP, not weaker: the engine is pure
> functions that know nothing about what invoked them ([[tech-stack]] §6), so a
> trigger added later cannot create a second path. The trigger was the
> deferrable part; the architecture it attaches to is already built. None of the
> three [[mission]] §6 demo scenarios is event-driven — all three are state
> comparisons over a seeded dataset.
>
> **Where each exit criterion went:**
> 1. Duplicate delivery → Phase 8's `processed_events` constraint, proven by
>    test rather than by a webhook.
> 2. Parked and reprocessed → **kept, narrowed** (R10). The case survives
>    without webhooks in cross-source form: the marketplace holds a refund whose
>    PayPal record has not appeared yet, because reporting lags roughly a day.
>    Calling that *missing* is the exact false positive P4 forbids, so it is
>    classified `awaiting_evidence` and re-evaluated by Phase 8's scheduled run —
>    the run is the retry. The parking **machinery** is deferred; the
>    classification is not.
> 3. Scheduled backfill of a withheld event → gone with webhooks; there is no
>    missed delivery to recover. The date-windowed run that remains does a
>    different job (R9).
>
> *Limitation to record in the readme:* no webhook ingestion, no signature
> verification; reconciliation is triggered on demand and on a schedule.

**Goal:** Live events update state through the same path as historical.

Webhook endpoints for PayPal and the simulated marketplace, signature
verification where supported, dedupe, enqueue, and reprocessing when a
previously-missing related record arrives.

**Exit criteria**
- A duplicate webhook delivery produces exactly one financial effect.
- An event arriving before its related record is parked and reprocessed later,
  not dropped and not reported as a discrepancy ([[mission]] P4).
- A scheduled backfill recovers an event deliberately withheld from the webhook.

**Depends on:** Phases 6, 7, 8.

---

## Phase 10 — Entitlement engine · 0.5 day · **COMPLETE 2026-11-09**

> **Delivered as Block B.** `reckonai/engine/{entitlement,refunds,positions}.py`
> — pure functions over `typing.Protocol` inputs. Spec, decisions, handoff and
> merge gate are in `specs/2026-11-09-entitlement-engine/`; **§4 of its
> `requirements.md` is the handoff Phase 11 should read first.**
>
> All six exit criteria below pass, each with a named test and a recorded
> result in that directory's `validation.md` §1. Suite **1372**.
>
> **What Phase 11 inherits, so none of it is rediscovered:**
>
> - **The engine compares nothing.** It answers what *should* be true; every
>   comparison, tolerance and classification is Phase 11's.
> - **The expected commission equals `platform_fee.amount`** on all 15 clean
>   purchase units, pinned by test against the captured payloads. Compare
>   against that column, never a recomputation.
> - **The expected entitlement is directly comparable to PayPal's
>   `net_amount`**: measured, `net_amount == gross_amount - platform_fees`
>   exactly on all 21 captures, zero residual.
> - **No capture states a `paypal_fee`**, so no fee-inclusive position is
>   published — `SellerPosition` carries the position plus
>   `unknown_fee_capture_ids`. An absent fee and a stated `0.00` stay different
>   claims. Block A's handoff said the opposite and is superseded.
> - **A commission reversal is cumulative, not per refund.** Do not sum
>   per-refund reversals to get a running commission; take it from the engine's
>   state. See the finding below.
> - **Nine `EngineError` subclasses mark where a figure would otherwise be
>   invented**, and Phase 11 decides what each becomes — a quarantine or an
>   escalation ([[mission]] P5), never a coercion.
> - **A duplicated refund raises** `RefundAlreadyApplied`. F4 is a double
>   credit, so this will be met; a duplicate is a finding, not an amount to
>   subtract twice.
>
> **Four findings worth not relearning.**
>
> **Per-refund commission reversals accumulate rounding, and the engine
> fabricated a figure.** Twenty 0.05 refunds of a 1.00 share at 10% each
> reverse `quantize(0.005) = 0.01`, summing to 0.20 against a commission of
> 0.10 — leaving `commission -0.10` and the engine asserting the marketplace
> owed the seller 0.10 on an order refunded in full. Phase 11 would have raised
> that as a discrepancy on a *correct* order. **Nothing already written caught
> it:** `RefundExceedsShare` held, `share == commission + entitlement` held,
> and the sequencing test happened to use amounts that divide evenly. Found by
> `/code-review medium`, which returned six findings and no false ones.
>
> **`commission_rule.fixed` had existed since Phase 2 and was missing from the
> engine's protocol**, so any rule carrying a flat per-transaction component
> would have had it silently dropped. This is exactly what
> `test_commission_rules.py`'s protocol test says it exists to catch, one phase
> late. Whether a *partial* refund reverses a flat component is deliberately
> left undefined and raises — the marketplace's rules decide, and a guess would
> become a discrepancy in a correct order.
>
> **A `Mapped[X]` attribute does not satisfy a `Protocol` member declared as
> `X`**, although instance access genuinely resolves to `X`. So the ORM classes
> do satisfy these protocols and a type checker cannot see it; declaring the
> members as read-only properties does not help. The runtime `isinstance` tests
> remain the only check, and they verify attribute **presence, not type** —
> which is how the `fixed` gap survived.
>
> **A type checker was addable all along.** `mypy` had been listed as a gate by
> Block A, found unmeetable because it was not a dependency, and dropped
> without being measured. Measured: 39 errors, of which **32 were one
> inaccurate `Money` constructor annotation**. It is now strict over
> `src/reckonai/engine/` and gated in CI.

**Goal:** The deterministic financial core. The highest-value phase here.

Pure functions, no I/O ([[tech-stack]] §6): expected seller entitlement from
commission rules, proportional refund allocation, commission adjustment,
multi-currency position.

**Exit criteria**
- The [[mission]] worked example passes exactly: $160 across two sellers at 10%
  → A $90, B $54, commission $16; after a $40 refund on A's item → A $54,
  B $54, commission $12.
- Allocation across three or more sellers leaves no rounding residue.
- No database handle, HTTP client or clock read appears in the module.
- Hand-computed fixtures cover each refund and commission policy.
- **The expected commission is computed as a figure comparable to PayPal's
  platform fee**, since that is what Phase 11 checks it against — not merely as
  the residue of what the marketplace kept.
- **A capture fee is never defaulted to zero.** Phase 0 could not establish
  whether PayPal assessed one, so a fee-inclusive position treats it as unknown
  and says so ([[tech-stack]] §9.6).

**Depends on:** Phase 2.

---

## Phase 11 — Matching and exception classification · 0.5 day · **COMPLETE 2026-11-09**

> **Delivered as Block C.** `reckonai/recon/{vocabulary,matcher,tolerance,classifier}.py`
> — pure, under the same purity and strict-mypy gates as the engine — plus
> `reckonai/reconcile.py`, the one module holding both a `Session` and an
> `OrderCase` and the entry point Block D's windowed run wraps. Spec,
> decisions, handoff and merge gate are in
> `specs/2026-11-09-matching-classification/`; **§4 of its `requirements.md`
> is the handoff Block D and Block E should read first.**
>
> All four exit criteria below pass against the seeded tenant: six faults
> detected with the manifest's own impact figures and evidence ids, and **zero
> exceptions on the fifteen clean purchase units**.
>
> Three results worth not rediscovering. **The FX guard is not a tolerance** —
> comparing only reported figures removes the shape that produces the
> one-cent artefact, so ORD-0008 is clean because the arithmetic is absent
> (D4a). **The held-funds guard is not exercised by the database gate** —
> ORD-0005's ledger is complete, so the residual never reaches it; it is
> proven in unit tests (D4b). And a `/code-review medium` returned **nine
> findings, all nine real**, two of them false positives on correct data that
> the seeded dataset could not show — `validation.md` §9 has the table.

**Goal:** Turn expected-vs-actual differences into classified exceptions.

Match marketplace records to PayPal records on stable identifiers, compare the
expected position against the marketplace ledger and PayPal's own records
currency-aware, and classify into the [[mission]] categories — including the
non-error states.

**Exit criteria**
- Every fault in the Phase 5 manifest is detected and correctly classified.
- **The clean Phase 4 baseline produces zero exceptions** — the false-positive
  gate, and the hardest criterion in this document.
- A delayed event is classified as delayed, not as a discrepancy.
- Each exception row carries financial impact and the ids of its evidence.

**Two ways to fail the false-positive gate, both known in advance and neither
permitted** (added 2026-11-07 from Phase 0):

1. **FX rounding.** PayPal rounds a converted settlement to the cent and leaves
   the rate unrounded. On our own fixture, `95.50 * 1.089477173902071` is
   `104.04507010764778050` against a reported `104.05` — a residue of
   `-0.00492989235221950`. **Compare against PayPal's reported settlement
   amount, never a recomputation of gross times rate**, and record the rounding
   tolerance as an explicit decision rather than a constant found while chasing
   noise. Keep the rate as a string parsed to `Decimal` at full precision.
2. **Held funds.** Under `DELAYED`, funds held and a release not yet requested
   are healthy states. Classifying them as discrepancies makes every delayed
   transaction an exception. PayPal also auto-releases after **28 days**, so the
   absence of a marketplace instruction does not mean the funds are stuck.

Three further matching constraints from Phase 0:

- **A reported rate may not be the capture-time rate.**
  `payment_instruction.payee_receivable_fx_rate_id` lets a caller pin an earlier
  quote, so an unexplained rate difference may be that rather than an error.
- **Refund-to-capture matching rests on a derived linkage**, parsed from a
  HATEOAS link. An exception resting on it must say so.
- **Dispute matching is unavailable** until a dispute fixture exists, and a
  quoted FX rate is never substituted for a conversion record ([[mission]] P3).

**Depends on:** Phases 6, 7, 10.

> **Read Phase 10's handoff before starting**, in
> `specs/2026-11-09-entitlement-engine/requirements.md` §4. Beyond the
> inheritance listed under Phase 10 above, three tasks are already assigned
> here: reconciling `reckonai.sim.manifest`'s six classification strings into a
> shared exception vocabulary (Block A's `validation.md` §8 debt 1, named as
> this phase's first task), taking or declining the open-dispute trap of debt 3
> deliberately, and freezing the dashboard's JSON contract at the end of the
> block.

---

## Phase 12 — AI investigation agent · 0.5 day

> **Kept entire by the replan (R11), with nothing reduced.** Stated here because
> it was conflated twice with the separate, deferred Haiku **field-name**
> mapping pass ([[tech-stack]] §9.2 pass 3), which is a connector-setup concern
> that never reads a financial record. This phase — ranked candidate causes,
> per-cause cited record ids, confidence values, the `insufficient_evidence`
> branch, persisted reasoning, model-id comparison — is the LLM that produces
> evidence-backed recommendations, and it is not in scope for any cut.

**→ Demo becomes viable at the end of this phase.**

**Goal:** Investigate an exception and produce an evidence-backed report.

Read-only retrieval tools plus the Phase 10 engine exposed as a recompute tool,
driven by the Tool Runner per [[tech-stack]] §5, returning the structured report
model.

**Exit criteria**
- Scenario 1 is investigated end-to-end and names the missing ledger adjustment
  as the cause, citing the refund and ledger record ids.
- Every cited id resolves to a real record — no fabricated references.
- An exception with evidence deliberately removed returns
  `insufficient_evidence` rather than a guess ([[mission]] P5).
- No tool can mutate financial state ([[mission]] P6).
- `stop_reason` is checked before response content is read.
- The summarized reasoning is persisted with the report, and the model id from
  the response is stored and compared against the model requested — a fallback
  produces a report flagged as downgraded ([[tech-stack]] §5.2).

**Depends on:** Phases 10, 11.

---

## Phase 13 — Investigation queue and approval gate · 0.5 day · **COMPLETE 2026-11-10 (PR #24)**

> Evidence: `specs/2026-11-10-queue-approval-dashboard/validation.md` §1.

> **Kept as its own block, 2026-11-08 (R5).** The cutline below names this as
> the first thing to drop, folding a minimal approve/reject into Phase 14. That
> was considered and **reversed** by the operator: the phase keeps prioritisation
> scoring over all four `readme.md` §9.3 factors, an append-only decision record
> carrying approver and outcome, and the visible confirmed-vs-suspected
> distinction. P6 is the point of this phase, not a constraint on it.
>
> If Day 2 runs short, the in-flight drop is the **scoring** only — approve/
> reject and the decision record stay, and P6 survives either way.

**Goal:** Prioritisation and the human decision point.

Queue ordered by financial impact, affected-seller count, age and confidence,
with a recommended resolution a user can approve or reject, and both outcomes
recorded.

**Exit criteria**
- Approving a recommendation records the decision and the approver, and moves no
  money ([[mission]] P6).
- Rejection is recorded with equal weight.
- Confirmed discrepancies and suspected issues are visibly distinct.

**Depends on:** Phase 12.

---

## Phase 14 — Operations dashboard · 1 day · **COMPLETE 2026-11-10 (PR #24)**

> Evidence: `validation.md` §2, §4a, §4b in the same directory. Unproven until a
> deploy: Node on Render, the agent credential, a successful investigation
> rendered in a browser.

> **Replanned 2026-11-08 (R13):** kept, with two changes. The **seller financial
> position view is dropped** — the least load-bearing of the four views for the
> three demo scenarios, since the investigation detail tells the underpaid-seller
> story better with evidence attached, and the easiest to add back if Day 2 runs
> ahead. The **run summary is kept but lean**: one AG Grid table over runs with
> click-through to that run's exceptions, rather than a charted view. It is kept
> because R9 adds scheduled runs and the run history is how anyone sees that one
> happened and what changed, and because [[tech-stack]] §10 requires runs to be
> append-only and timestamped, so the data exists either way.
>
> Runs with Phase 13 as Block F. AG Grid stays — `readme.md` §12 makes it a
> sponsor technology doing real grouping and drill-down work.

**Goal:** The interface the demo is given through.

React + AG Grid: reconciliation run summary, exception queue, investigation
detail with evidence trail, seller financial position view. Amounts formatted
from strings per [[tech-stack]] §7.

**Exit criteria**
- The exception queue groups, sorts and filters over the seeded dataset.
- The investigation view shows the cause, the evidence records and the
  recommendation, with approve/reject reachable.
- Every amount displays with its own currency; no client-side arithmetic.
- Deployed and reachable on Render.

**Depends on:** Phases 11, 12, 13.

---

## Phase 15 — Demo script and eval harness · 0.5 day · **SKIPPED 2026-11-11 (operator decision)**

> Not built, and no spec exists. Detection and false positives are covered by
> `tests/test_recon_gate.py`; the AI's root-cause accuracy is unmeasured. See
> `CLAUDE.md`, "Block G was skipped".

> **Kept by the replan (R12), against the cutline below**, which names the
> harness as the second thing to drop. The reasoning inverts at two days:
> hand-scoring costs hours that are not available, so the harness *replaces* a
> manual pass rather than adding one. It also gains a rehearsal against the
> deployed environment, since R15 makes a live demo the deliverable.

**Goal:** Prove the numbers in [[mission]] §6 rather than assert them.

A runner that reconciles the seeded dataset and scores detection rate, false
positives and root-cause accuracy against the Phase 5 manifest. Plus a rehearsed
demo path through all three scenarios.

**Exit criteria**
- The harness prints a metric table from a single command.
- All three scenarios run from a clean database without manual intervention.
- Detection and root-cause figures are measured output, not claims.

**Depends on:** Phases 5, 11, 12, 14.

---

## Cutline

> **Superseded 2026-11-08** by `specs/2026-11-08-replan/requirements.md`, which
> lands the remainder in **2 days** rather than 7. Both items below were
> considered and **both were reversed**, which is worth recording because the
> reversals were deliberate rather than oversights:
>
> - **Phase 13 stays its own block** (R5) — the operator's call. Prioritisation
>   scoring and the decision record are product value, not ceremony.
> - **The eval harness stays** (R12). The reasoning inverts under a two-day
>   deadline: hand-scoring costs hours that are not available, so the harness
>   *replaces* a manual pass rather than adding one. [[mission]] §6 is a table of
>   measures and the readme claims detection and root-cause figures; one command
>   printing measured output is what makes those claims true rather than
>   asserted.
>
> What was cut instead is in R6 through R13 — chiefly the event-driven trigger
> (R8), live PayPal calls at demo time (R6), the Haiku field-name pass (R11) and
> the seller position view (R13). The paragraph below still governs: Phase 11's
> zero-false-positive criterion was never on the table, and Phase 12 is kept
> entire.
>
> The replan's own in-flight drop order, for if Day 2 runs short, is: the lean
> run summary, then Phase 13's **scoring** (keeping approve/reject), then
> scenario 3 as a dashboard story. Never dropped: the engine, the
> false-positive gate, `insufficient_evidence`, approve/reject.

The plan above is 8.5 days, of which Phase 0 is already spent. To land the
remainder in 7, drop in this order:

1. **Phase 13** (0.5d) — fold a minimal approve/reject into Phase 14 and lose
   queue prioritisation. [[mission]] P6 must survive the fold.
2. **Phase 15's eval harness** (0.25d) — keep the demo script, score by hand.

Do **not** cut Phase 0b, 10, 11 or 12. 10 and 11 are the product; 12 is the
differentiator. Phase 11's zero-false-positive criterion is the last thing to
compromise — a tool that cries wolf is worse than no tool.

**Phase 0b is the one addition that is not optional**, for the same reason
Phase 0 was not: it is the last point at which a wrong assumption about PayPal
is cheap. Cutting it means Phase 3 designs `platform_fee` and the disbursement
states without having seen either. If it is genuinely blocked — the sandbox
seller account cannot be created, or the capability cannot be enabled — then
**record that as the finding and narrow the MVP accordingly**; do not proceed as
though the answers were known.

Bryntum ([[tech-stack]] §7) is below the cutline by definition.

---

## Risks

**Nothing is demoable until Phase 12, on day 6 of 7.**
This is the accepted cost of foundation-first. It is a real risk: a problem
found in Phase 12 has no schedule left behind it. Two mitigations are built in —
Phase 0 pulls the external unknown forward, and Phase 1 proves deployment on day
one. The residual exposure is the engine-to-agent seam, and the cheapest
insurance is to run Scenario 1 by hand through the engine the moment Phase 11
closes, before starting Phase 12. *If this risk is unacceptable, the
alternative is to reorder Phases 4–12 as a thin vertical slice through Scenario
1 — that was considered and not chosen.*

**PayPal may not expose settlement or FX detail.**
Phase 0 exists to learn this on day one rather than day six. **Resolved:** a
cross-currency capture does return a real rate and settlement amount, so
Scenario 3 survives — but no conversion **fee** exists anywhere, so that half of
the scenario is dropped rather than estimated ([[mission]] P3).

**The platform may not be able to read its sellers' transactions.**
The live replacement for the risk above, and Phase 0b question 2. Under
`INSTANT` the seller's funds land in the seller's own PayPal account, and
PayPal's wording is "seller-level transaction records *where accessible*". If
`PayPal-Auth-Assertion` does not grant that read, entitlement validation is not
implementable as specified and the MVP narrows to what the platform can see of
its own fees. Both referenced-payouts endpoints already return `403` for our
app, which is a related signal. This is now the largest external unknown.

**False positives are the quiet failure.**
Easy to miss because the demo still looks like it works. Phase 11's clean-
baseline criterion is the specific defence and is non-negotiable.

**Multi-currency is where correctness breaks first.**
Rounding residue and implicit same-currency assumptions are the usual causes.
Phase 2 front-loads the tests that catch both.

**Phase 14 is the only full day of work in the plan.**
It is also the most compressible — a plainer grid still demonstrates the
product. If time is short, reduce Phase 14's polish before cutting anything in
Phases 10–12.

---

## Related specs

- [[mission]] — what we are building and why
- [[tech-stack]] — the technology these phases use
- `specs/paypal-fields.md` — produced by Phase 0
