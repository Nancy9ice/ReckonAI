# Phase 3 — Canonical financial events and raw payload store · Validation

**Branch:** `phase-3/canonical-events`
Scope: `./requirements.md` · Task groups: `./plan.md`

Merge-readiness is **demonstrated by commands that pass or fail**, not asserted.
Anything that cannot be automated is listed in §4 as an explicit manual check
with a stated reason.

Phase 3 splits its gate in two, per `requirements.md` D18. §3's checks run and
must pass **before** the merge. §4's deployed checks run **after** it, and the
phase is not complete until they do. Phase 4 may begin once §3 passes; it may
not begin while any §4 check is failing.

Every check in §3 must be run with the local database up and `DATABASE_URL`
exported. A skipped database test is not a passing one — `tests/conftest.py`
makes that a hard failure in CI deliberately, and the same standard applies to a
local gate run. This phase leans on that harder than Phase 2 did: **most of its
checks assert that Postgres rejects an insert**, and a `CHECK` constraint cannot
be proven by a unit test against the model.

---

## 1. Exit criteria mapped to checks

Every [[roadmap]] Phase 3 exit criterion, and how it is proven.

| # | Roadmap exit criterion | Check | Type | Gate |
|---|---|---|---|---|
| E1 | Every financial table's amount columns are `NUMERIC` with a currency column | C1, C2 | Auto | Pre-merge |
| E2 | Every record links to its `raw_payload` row | C3, C4 | Auto | Pre-merge |
| E3 | `fx_conversion` carries source, target, rate, timestamp, fee, provider and `is_derived`; created only where PayPal reports a rate; `fee` nullable and never zero; `timestamp` records its provenance | C5, C6, C7 | Auto | Pre-merge |
| E4 | A conversion may be more than one row per record | C8 | Auto | Pre-merge |
| E5 | Lifecycle states include the non-error states, plus the disbursement states | C9, C10 | Auto | Pre-merge |
| E6 | Nothing is required that Phase 0 could not observe; dispute fields in particular are nullable | C11, C12 | Auto | Pre-merge |
| E7 | `refund` links to its capture through a **derived** linkage, recorded as derived | C13 | Auto | Pre-merge |

Four of these carry a note, because the criterion as written is weaker than what
the phase actually has to prove.

**E1 is already enforced, by Phase 2.** Its `NUMERIC` metadata gate asserts that
no column anywhere in `Base.metadata` is `Float`, `REAL` or `DOUBLE PRECISION`,
that every `*_amount` is `Numeric(20, 6)`, every `*_currency` is `String(3)` and
every `*_rate` is `Numeric` with a scale of at least 6. That gate was written
against tables that did not exist, and C1 is it re-run over nine of them. C2
adds the one thing it cannot see: that the pair is **declared together**, via
`money_columns`, so an amount has no way to reach the ORM without its currency.

**E2's "every record" is checked over the metadata, not over a list.** C3
iterates `Base.metadata` and fails on any financial table without a `NOT NULL`
`raw_payload_id` foreign key. A hand-written list of eight tables passes while
being wrong about the ninth table someone adds in Phase 8.

**E3 is four separate claims and is checked as four.** C5 is the column
inventory; C6 is the `fee` rule, which is the one with a real failure mode — a
zero fee is how "no conversion fee exists" turns into "the conversion fee was
nothing"; C7 is the same-currency refusal, which is how "created only where a
conversion happened" is enforced rather than documented.

**E7 is checked in both directions.** C13 asserts that
`capture_linkage_is_derived` cannot be omitted *and* that a refund with an
unresolved `capture_id` still inserts. A schema that required the linkage would
force Phase 7 to invent one, and `specs/paypal-fields.md` is explicit that
PayPal supplies none.

---

## 2. Checks the specs require beyond the exit criteria

Not in the roadmap's list. These enforce standing rules from `CLAUDE.md` and the
constitution documents, and a Phase 3 that passed §1 while failing these would
be putting the violation into the schema every later phase builds on.

| # | Rule | Source | Check |
|---|---|---|---|
| S1 | No emoji or pictographs anywhere in the codebase | [[tech-stack]] §13 | C20 — Phase 1's gate, re-run over this phase's files |
| S2 | Every table carrying marketplace data has a `tenant_id`, and a cross-tenant link is unwritable | [[tech-stack]] §10, `CLAUDE.md` | C14, C15 |
| S3 | An amount is never separated from its currency | [[tech-stack]] §9.3 | C2, C16 |
| S4 | No `float` in the money path | [[tech-stack]] §2 | C1, C17 |
| S5 | The engine is pure functions with no I/O | [[tech-stack]] §6 | C17 (purity half), now covering `engine/ledger.py` |
| S6 | Retain raw source payloads; never discard the evidence for a decision | [[mission]] P7, `CLAUDE.md` | C3, C4, C18 |
| S7 | Never fabricate a financial value | [[mission]] P3 | C6, C19 |
| S8 | Every migration is reversible | `specs/2026-11-07-repo-deployment-skeleton` | C21 |
| S9 | Never default or invent a monetary value | [[tech-stack]] §9.6 | C6, C19, C23 |

---

## 3. Pre-merge checks

Each is a command. Record the command and its result in `notes.md`; a check that
was not run is not a passing one.

### C1 — The `float` and `NUMERIC` metadata gates pass over nine new tables

    uv run pytest tests/test_models_schema.py tests/test_money_purity.py

Phase 2's gates, unchanged, now iterating fourteen tables. **No new assertion is
added to them by this phase**, and that is the check: if a `NUMERIC(20, 6)` and
a `VARCHAR(3)` were spelled by hand instead of through `money_columns`, or an
FX rate landed on the wrong precision, these fail without anyone having
remembered to extend them.

### C2 — Every money value is declared as a pair

    uv run pytest tests/test_models_schema.py -k "money_pair or matching_currency"

For each `*_amount` column in `Base.metadata`, a `*_currency` column of the same
prefix exists on the same table, and a nullable pair carries its
`money_pair_check`. Catches the one failure `money_columns` cannot prevent: an
amount column added by hand beside it.

### C3 — Every financial table carries a `NOT NULL` link to `raw_payload`

    uv run pytest tests/test_model_registry.py -k raw_payload

Iterates `Base.metadata`, excludes `tenant`, `seller`, `order`, `order_item`,
`commission_rule` and `raw_payload` itself by name, and fails on any remaining
table without a `NOT NULL` `raw_payload_id` foreign key. [[mission]] P7.

### C4 — A financial record cannot be inserted without its payload

    uv run pytest tests/test_raw_payload.py -k without_payload

Attempting the insert raises against the `NOT NULL`. This is C3's claim proven
at the database rather than in the metadata, and it is what makes P7
unskippable instead of aspirational.

### C5 — `fx_conversion` carries all nine fields, at the right types

    uv run pytest tests/test_conversions.py -k columns

`source` and `target` as money pairs, `rate` as `NUMERIC(20, 10)`, `fee` as a
nullable money pair, `provider`, `converted_at` nullable, `is_derived`
`NOT NULL`. The `is_derived` name is asserted literally, because
[[roadmap]] Phase 3 names it and `requirements.md` D10 keeps it rather than
renaming it for precision.

### C6 — A zero conversion fee is rejected; a `NULL` one inserts

    uv run pytest tests/test_conversions.py -k fee

Three inserts: `fee` `NULL` succeeds, `fee` zero raises, `fee` non-zero
succeeds. `fee` stays **absent, never zero** — PayPal declares no conversion fee
anywhere in the transaction APIs, and the residue of net times rate against the
reported settlement is PayPal's rounding. A zero would be read downstream as a
fee that was measured and found to be nothing, which is a fabricated financial
value ([[mission]] P3).

### C7 — A same-currency conversion is rejected

    uv run pytest tests/test_conversions.py -k same_currency

The database refuses it. This is how "created **only** where PayPal reports an
`exchange_rate`" is enforced: an empty or degenerate row would be
indistinguishable from a conversion whose fields went missing.

### C8 — Two conversions against one refund insert

    uv run pytest tests/test_conversions.py -k multiple

[[roadmap]] Phase 3's array case. A refund declares an array of conversion
entries where a capture declares a single object, so nothing in the schema may
assume one conversion per record.

**This check is weaker than it looks and the limit is recorded here rather than
discovered in Phase 7.** It proves our schema permits the shape; it does not
prove the shape PayPal sends, because no cross-currency refund fixture exists.
`specs/paypal-fields.md` records
`$.seller_payable_breakdown.net_amount_breakdown` as **unanswered rather than
absent**, and Phase 7 should expect the refund conversion shape to differ.

### C9 — The state vocabularies contain the states the roadmap requires

    uv run pytest tests/test_lifecycle_states.py

`RECONCILIATION_STATES` contains `unmatched`, `delayed` and
`awaiting_evidence`; `DISBURSEMENT_STATES` contains `disbursed_at_capture`,
`funds_held`, `release_requested`, `disbursement_completed`,
`disbursement_pending` and `disbursement_failed`. Asserted by name, so dropping
one is a failure rather than a smaller tuple.

### C10 — A state outside its vocabulary is rejected by the database

    uv run pytest tests/test_events_capture.py -k state_check

On `reconciliation_state` and on `capture.disbursement_state`. The `CHECK` is
generated from the Python tuple, so this also proves the two have not drifted.

### C11 — A dispute inserts with every optional column `NULL`

    uv run pytest tests/test_disputes.py -k all_null

The permissive direction of E6, and the more important one. Ten canonical
dispute fields rest entirely on a declared contract, and
`seller_response_due_date` is **observed to disappear** once a seller accepts a
claim. A `NOT NULL` anywhere here would force Phase 7 to invent a value.

### C12 — A capture inserts with its whole breakdown `NULL`

    uv run pytest tests/test_events_capture.py -k null_breakdown

`gross_amount`, `net_amount`, `paypal_fee`, `settlement_amount` and
`settlement_currency` all absent. Not defensive: a capture loses its entire
`seller_receivable_breakdown` while `PENDING`, and an open dispute puts it
there — observed, then observed returning once the dispute resolved.

### C13 — The refund linkage is nullable and its provenance is not

    uv run pytest tests/test_events_refund.py -k linkage

A refund with `capture_id` `NULL` and `capture_linkage_is_derived` set inserts;
omitting `capture_linkage_is_derived` raises against its `NOT NULL`. PayPal
supplies no `capture_id` on a refund — only `links[rel=up]`, whose href must be
parsed — so an unresolved linkage is a recorded state and an unstated provenance
is not.

### C14 — The tenant gate passes over nine new tables

    uv run pytest tests/test_models_schema.py -k tenant

Phase 2's gate, unchanged: every table except `tenant` carries a `NOT NULL`
`tenant_id`. Including `raw_payload`, which is the one most easily rationalised
as infrastructure rather than marketplace data. It holds a marketplace's
financial payloads, so it is scoped.

### C15 — A cross-tenant link is rejected by the database

    uv run pytest -k cross_tenant

On each new child relationship: capture to payment, refund to capture,
`platform_fee` and `fx_conversion` to either parent, disbursement to capture,
ledger entry to seller and to order, and every table to its `raw_payload`. The
composite foreign keys from Phase 2's D7 are what make this a database
guarantee rather than a code review.

### C16 — A half-populated money pair is rejected

    uv run pytest -k half_populated

An amount with no currency, and a currency with no amount, on each nullable
pair. [[tech-stack]] §9.3 treats an amount without a resolved currency as a hard
failure, and expressing it in DDL means it holds for a row written by a
migration or a seed script, not only one written through the ORM.

### C17 — The `float` AST gate and the purity gate pass over the new modules

    uv run pytest tests/test_money_purity.py

`reckonai.money` and `reckonai.engine` import nothing from `sqlalchemy`,
`psycopg`, `httpx` or `reckonai.db` — now including `engine/ledger.py`, which is
why `signed()` takes a `typing.Protocol` rather than the ORM class.

### C18 — A `JSONB` payload round-trips unchanged, and a re-read stores nothing

    uv run pytest tests/test_raw_payload.py

A nested body with a non-ASCII value comes back identical; the same
`(tenant, source, type, sha256)` twice raises; the same body under a different
`source_record_type` inserts. The second is what makes Phases 6, 7 and 9
idempotent; the third is what keeps a changed re-read as a second row, which is
the only way Phase 7's payload diff can see a field that stopped being
returned.

### C19 — `disbursement.released_at` has no default

    uv run pytest tests/test_disbursements.py -k no_default

Asserted against the reflected schema, not by reading the model. The release
record carries no usable timestamp — `time_updated` is the integer `0` — so an
ingestion time written here would be an ingestion time presented as a provider
timestamp ([[mission]] P3).

### C20 — No pictographs in any file this phase touches

    uv run pytest tests/test_conventions.py

Phase 1's gate, over every tracked file. [[tech-stack]] §13 bans pictographs,
not all non-ASCII: typographic punctuation, box-drawing characters in docs and
non-ASCII characters in real data are fine, and
`test_permitted_characters_are_not_flagged` pins that.

### C21 — The migration is clean, applies, and reverses

    uv run pytest tests/test_migrations.py
    uv run alembic check

`tests/test_migrations.py` already holds all four properties and this phase
**extends** it rather than adding a parallel check:
`test_the_models_and_the_migrations_agree` is `alembic check` as a test,
`test_downgrade_reverses_every_revision` and `test_the_cycle_is_repeatable`
prove the round-trip, and `test_every_catalogue_table_carries_a_tenant_id`
asserts the tenant rule against the schema a migration actually produced rather
than against `Base.metadata`.

**One of its tests fails by design until it is updated**, and finding that out
here rather than in CI is the point of saying so:
`test_upgrade_creates_the_catalogue_tables` asserts the table list is *exactly*
`alembic_version`, `commission_rule`, `order`, `order_item`, `seller`, `tenant`.
Nine new tables break it. The useful property is still "exactly these tables",
so the list is extended to fifteen rather than loosened to "some tables exist"
— which is the note Phase 2 left in that docstring.

The downgrade is the half autogenerate gets wrong, and nine tables with foreign
keys between them have to drop in the right order. `alembic check` is run on the
command line as well because Phase 2's `migrated_database` fixture makes the
constraint tests run against the migrated schema, so a `CHECK` lost in
autogenerate fails the constraint tests and not only the drift check — and
seeing which of the two went red first tells you whether the model or the
revision is wrong.

### C22 — Every model module is registered

    uv run pytest tests/test_model_registry.py

No module under `src/reckonai/models/` is unreachable from
`reckonai.models.__init__`, and all fourteen expected table names are in
`Base.metadata.tables`. Phase 2's handoff names this trap: an unimported module
is absent from the metadata, and autogenerate reads its table as one to
**drop**.

### C23 — The ledger is unsigned, positive, and deliberately unconstrained across its two type columns

    uv run pytest tests/test_ledger.py

Four assertions, and the fourth is the unusual one:

- A zero `amount` is rejected by `CHECK (amount_amount > 0)`, and so is a
  negative one. This is what `requirements.md` D7 buys by moving the sign out of
  the amount: a signed column cannot distinguish a wrong sign from a legitimate
  negative, so neither could be refused.
- `signed()` inverts a `debit` and leaves a `credit` alone.
- An unknown `transaction_type` raises rather than defaulting to `credit`.
- **A `sale` row with `transaction_type='debit'` inserts successfully.** The
  deliberate absence of a cross-column `CHECK`, pinned by a test so a later
  contributor does not add one as a tidy-up. The marketplace supplies both
  columns and a disagreement between them is a Phase 11 finding about the
  marketplace's data, not an insert to reject — Phase 2's D13 reasoning
  unchanged.

### C24 — The whole suite is green

    uv run pytest

Including Phase 2's 858 tests. A Phase 3 that broke a Phase 2 test has changed
something the money rules depend on.

---

## 4. Post-merge deployed checks

These cannot run before the merge, because `preDeployCommand` runs on deploy.
Phase 3 is not complete until all four pass, and Phase 4 may not begin while any
is failing (`requirements.md` D18).

| # | Check | How |
|---|---|---|
| D1 | The migration applied on Render | Render CLI deploy logs — `preDeployCommand` ran `alembic upgrade head` and reported the new revision as head |
| D2 | The service is still ready | `GET /health` and `/health/ready` both answer `200` from `https://reckonai-api.onrender.com` |
| D3 | The nine tables exist with their constraints, and `alembic current` matches head | From **inside Render**, via a one-off job — see below. The database is not reachable externally, which was re-confirmed by test on 2026-11-08 rather than assumed. Ids in `specs/2026-11-07-canonical-core/notes.md` §"Render CLI setup" |
| D4 | The worker is up and logging | Render logs. Two workers overlapping for about 90 seconds during a rolling deploy is expected and is Phase 8's problem, not a failure here |

**How D3 reaches the database.** `ipAllowList: []` on `reckonai-db` means
internal Render traffic only, and it governs **external** connections only —
which is why the migration itself connects without trouble, since a service
inside Render uses the internal URL and bypasses the list. Re-confirmed by test
on 2026-11-08, with the Render API key present and the workspace set:

    $ render psql dpg-dbnp86rrjlhs739fsjdg-a --command "SELECT ..."
    Error: IP address (...) not in allow list for reckonai-db

The key authenticated fine and the CLI resolved the instance; what refused the
connection was a packet filter, not an authorization check. The two are
orthogonal and no credential changes the second.

So D3 runs **inside** Render. Phase 2 named two paths — the dashboard Shell,
which is manual, and `render ssh`, which needs an SSH public key registered on
the account — and ran neither. There is a third, which is non-interactive and
needs no key:

    render jobs create srv-dbnp8pbrjlhs739ftnj0 \
      --start-command "uv run --no-sync alembic current"

A one-off job runs in the service's own environment, so `DATABASE_URL` is the
internal URL and the allowlist does not apply. Output comes back through
`render logs`. Two things to know: Render **bills a one-off job per second** of
runtime, and `--no-sync` is required — without it `uv run` re-syncs at container
start and undoes the build's `--no-dev` (`CLAUDE.md`).

**This command is documented but not yet verified**, because running it before
the merge would exercise the pre-merge deploy. Group 10 is where it is proven;
if it does not work, the dashboard Shell is the fallback and that is a finding
for `notes.md`, not a reason to skip the check.

**Manual, with a stated reason.** D1 and D4 read deploy logs. Automating them
means giving CI the Render credentials, which [[tech-stack]] §10 does not
provide for and which is not worth it for a check run once per phase.

---

## 5. What a failure means

Not every red result blocks the merge, and the distinction is worth stating in
advance so it is not negotiated under pressure.

**Blocks the merge.** C1–C4, C6, C7, C10, C14–C17, C21, C22, C23, C24. Each is a rule
from a constitution document, and a violation is written into the schema that
every later phase builds on. C21 in particular: a migration that does not
reverse is not reversible later.

**Blocks, but by amending a spec first.** C9. If a state the roadmap requires
turns out to be the wrong vocabulary — a sixth disbursement state, or a
reconciliation state that cannot be reached — the correct move is to amend
[[roadmap]] Phase 3 and `specs/multiparty-direction.md` §4 **before** changing
the tuple, as Phase 2 amended [[tech-stack]] §2 ahead of its D3 divergence. A
constitution document is amended ahead of the divergence, not retrofitted after
it.

**Does not block.** C8's recorded weakness, which is a statement about our
fixtures rather than our schema. Nothing in this phase can strengthen it; a
cross-currency refund fixture can, and that belongs to whoever next has PayPal
credentials in hand.

**And one thing that is a finding rather than a failure.** If a column this
phase made nullable turns out in Phase 7 to be reliably populated, that is new
evidence and `specs/paypal-fields.md` should record it. The reverse — a
`NOT NULL` that PayPal cannot satisfy — is the expensive error, which is why
`requirements.md` §6 names it as the phase's whole risk surface and why C11 and
C12 prove the permissive direction works.

---

## 6. Related

- `./requirements.md` — the decisions these checks enforce
- `./plan.md` — the groups that produce them
- [[roadmap]] — Phase 3's exit criteria, mapped in §1
- `specs/paypal-fields.md` — the evidence behind C6, C7, C11, C12, C13 and C19
- `specs/2026-11-07-canonical-core/validation.md` — the gates C1, C2, C14, C16,
  C17 and C20 re-run unchanged
