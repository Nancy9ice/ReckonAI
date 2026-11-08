# Phase 3 — Canonical financial events and raw payload store · Notes

**Branch:** `phase-3/canonical-events`
Scope: `./requirements.md` · Groups: `./plan.md` · Gate: `./validation.md`

Findings and gate results, written as they happen rather than at the end.

---

## Group 0 — Prerequisite gate · 2026-11-08

All five facts established before anything was written.

| # | Fact | Result |
|---|---|---|
| 1 | Phase 2 closed, `origin/main` at `4e25c3f`, branch cut from it | Pass |
| 2 | Compose database up, `DATABASE_URL` exported | Pass |
| 3 | `uv run alembic current` reports `b5f8485e802e (head)` | Pass |
| 4 | Phase 2's gates green before this phase changes anything | Pass — 123 passed across `test_models_schema`, `test_money_purity`, `test_conventions`, `test_migrations` |
| 5 | Deployed service healthy | Pass — `/health` `200`, `/health/ready` `200` |

**One environment finding.** `DATABASE_URL` was unset for non-interactive
shells, so the database-backed tests would have skipped rather than run. It is
now exported from `~/.zshenv`, which is the file a non-interactive zsh reads —
`~/.zshrc` is not read, so an export there would have left the same silent skip
in place.

---

## Group 1 — State vocabularies · 2026-11-08

`src/reckonai/models/lifecycle.py` plus `tests/test_lifecycle_states.py`.
28 tests, whole suite 887 passed, `ruff` clean.

Four vocabularies rather than the three the plan named: `TRANSACTION_TYPES` was
added for the ledger's debit/credit column, because the `CHECK` on it should be
generated from a tuple like every other state column rather than written out as
a literal in one table.

**One decision worth recording.** The `CHECK` constraints are **not** supplied
by `LifecycleMixin`. A `declared_attr` for `__table_args__` has to merge with
each table's own, and that merge fails silently when a table forgets to call up
the chain. Each table adds `reconciliation_state_check()` to its own
`__table_args__` instead — one visible line, and the schema gate catches an
omission. The same pattern is used for `RawPayloadMixin` in Group 2.

`test_nothing_orders_the_disbursement_states` asserts the module exports no
transition map. The absence is load-bearing — PayPal auto-releases after 28 days,
so `disbursement_completed` is reachable without `release_requested` — and a
test is the only thing that stops a later contributor adding one as a tidy-up.

---

## Group 2 — The `raw_payload` store · 2026-11-08

`src/reckonai/models/payloads.py` plus `tests/test_raw_payload.py`. 7 tests,
whole suite 904 passed, `alembic check` clean, `ruff` clean.

### Finding: the migration had to move from Group 9 to Group 2

`tests/conftest.py`'s `migrated_database` fixture runs database-backed tests
against **the schema a migration produced**, not against
`Base.metadata.create_all` — deliberately, so a `CHECK` lost in autogenerate
fails the constraint tests and not only the drift check. The consequence was not
anticipated when `plan.md` was written: **no group's database tests can pass
before a revision exists**, so a revision arriving in Group 9 would have left
Groups 2 to 7 unvalidated for the whole run.

`plan.md` Group 2 step 5 now creates the phase's single revision, and each later
group regenerates that same one file at its end. `requirements.md` D16 still
holds — the phase ships one revision — and it is now validated nine times rather
than once.

### Finding: a generated constraint name is 62 characters against a limit of 63

`UNIQUE (tenant_id, source, source_record_type, body_sha256)` generates
`uq_raw_payload_tenant_id_source_source_record_type_body_sha256`, which is
**62 characters**. Postgres truncates an identifier at 63.

One more column in that constraint, or a table name one character longer, and
the name would be silently truncated — which is the specific failure where
`downgrade()` cannot drop what `upgrade()` created, because the two computed
different identifiers. Phase 2's `NAMING_CONVENTION` interpolates
`%(column_0_N_name)s` for a unique constraint, so the length grows with every
column added to one. Worth knowing before Phase 8 or Phase 11 adds a wide unique
constraint.

### Finding: three gates each carried their own copy of the table list

`test_the_metadata_is_not_empty` asserted `len(tables()) == 5`,
`test_upgrade_creates_the_catalogue_tables` asserted the six database tables by
name, and `test_every_catalogue_table_carries_a_tenant_id` asserted four. Nine
new tables meant three lists to keep in step, and `validation.md` C21 had
already predicted two of the three failures.

They now read `tests/expected_schema.py`, which lists the names once. Adding a
table is one line there, and the exactness Phase 2 wanted is kept — a table that
silently failed to register is named in the failure rather than inferred from a
count.

### What autogenerate did and did not emit

Checked against `requirements.md` §6's warning that `CHECK` handling was new
ground here. For `raw_payload` it emitted everything: the `CHECK`, both unique
constraints, the composite-FK target, the `JSONB` column as
`postgresql.JSONB(astext_type=sa.Text())`, and a `downgrade()` that drops the
indexes before the table. The `num_nonnulls` question is Group 5's.

---

## Groups 3 to 5 — the provider-side tables · 2026-11-08

`payment` and `capture` (Group 3), `refund` and `dispute` (Group 4),
`platform_fee` and `fx_conversion` plus the shared parent-link helper
(Group 5). Whole suite 1013 passed, `alembic check` clean, `ruff` clean after
each group.

### Finding: flush order between unrelated mappers is not derived from foreign keys

The first Group 3 fixture failed with
`ForeignKeyViolation: insert or update on table "seller" violates
fk_seller_tenant_id_tenant` — SQLAlchemy inserted the seller before the tenant,
although `Base.metadata.sorted_tables` orders `tenant` first and the foreign key
is declared.

The cause is that **this project declares no `relationship()` anywhere**, and
SQLAlchemy's unit of work sorts mappers by relationship dependency, not by table
foreign key. With no relationship between `Tenant` and `Seller`, the insert
order between them is simply not constrained. Phase 2 had already met this and
solved it per-fixture with `session.flush()` after each parent;
`tests/test_catalogue_constraints.py` is full of those flushes and the reason
was never written down.

It is written down now, in the `tenant` fixture in `tests/conftest.py`. **This
will bite Phase 4's seed builder**, which constructs an object graph in memory
before committing: it needs to flush in dependency order, or declare
relationships, and neither is currently obvious from the models.

### Finding: Phase 2's savepoint session pattern is now shared

`tests/test_catalogue_constraints.py` had a `session` fixture using
`join_transaction_mode="create_savepoint"`, because a test that provokes an
`IntegrityError` otherwise tears down the enclosing transaction too. Phase 3
adds five test modules with the same need, so the fixture is promoted to
`tests/conftest.py` along with `tenant`, `other_tenant`, `seller`, `payload` and
`other_payload`. The Phase 2 module keeps its own copy, which overrides.

A side benefit worth naming: nothing in these modules cleans up after itself. A
`delete()`-based teardown runs *after* a failed flush has already poisoned the
session, which is a second failure on top of the first.

### Finding: a constraint fires on the statement, not on the later flush

`test_a_half_populated_money_pair_is_rejected` wrapped `session.flush()` in
`pytest.raises` and failed, because the raw `UPDATE` is autoflushed and the
`CHECK` fires on `session.execute()` itself. Worth knowing generally: wrapping
the wrong call makes such a test pass for the wrong reason, and it would have
kept passing if the constraint were later dropped.

### Answered: `num_nonnulls` survives autogenerate

`requirements.md` section 6 flagged this as new ground and the reason to assert
the rejection from the database rather than from the model. The generated
revision carries it verbatim on both tables:

    sa.CheckConstraint('num_nonnulls(capture_id, refund_id) = 1',
                       name=op.f('ck_fx_conversion_one_parent')),
    sa.CheckConstraint('num_nonnulls(capture_id, refund_id) = 1',
                       name=op.f('ck_platform_fee_one_parent')),

So did all four `fx_conversion` checks — `fee_not_zero`, `currencies_differ`,
`rate_positive` and the `fee` money pair. No constraint has had to be written by
hand, and the revision is still a single autogenerated file.

### Finding: two more Phase 2 gates carried hard-coded Phase 2 facts

`validation.md` C21 predicted two of these. The third was not predicted:
`test_the_amount_gate_actually_sees_columns` asserted exactly 4 amount columns,
5 currency columns and `rates == ["commission_rule.rate"]`.

An exact count across nine new tables is churn on every group for no signal —
the per-column type gates already check *every* amount column, so the only
property this test adds is that the lists are not empty. It now asserts Phase
2's numbers as a **floor**, which still catches the failure it was written for,
and keeps the rate columns enumerated because there are few of them and each
carries a different precision for a stated reason.

---

## Groups 6 to 8 — disbursement, ledger, registry · 2026-11-08

`disbursement` (Group 6), `seller_ledger_entry` plus `engine/ledger.py`
(Group 7), the model-import gate (Group 8). Whole suite 1117 passed,
`alembic check` clean, `ruff` clean.

### Two columns renamed before implementation

On the user's instruction, and applied to the specs before any model was
written: the ledger's `direction` became **`transaction_type`**, and
`entry_type` became **`entry_category`** so the two could not be confused. The
reasoning for the unsigned amount is unchanged and is in `requirements.md` D7.

A fourth vocabulary, `TRANSACTION_TYPES`, was added in Group 1 so the
`CHECK` on `transaction_type` is generated from a tuple like every other state
column rather than written out as a literal in one table.

### Finding: the first version of the import gate could not fail

The gate was written as: walk `src/reckonai/models/`, `importlib.import_module`
each module, then assert `hasattr(reckonai.models, module_name)`.

**It passes with the import commented out.** `importlib.import_module` binds the
submodule as an attribute of its parent package, so the `hasattr` assertion
passes *because the test itself just did the thing it was checking for*. Worse,
that same import repairs `Base.metadata` in-process, so
`test_the_metadata_holds_exactly_the_expected_tables` and
`test_there_are_financial_tables_to_check` both passed too — the only
observable difference was the parametrized test count dropping from 54 to 49,
because the parametrize list is built at collection time and the repair happened
during the run.

Verified by commenting out the `ledger` import: **49 passed, 0 failed.**

The gate now runs a subprocess that imports nothing but `reckonai.models` and
prints `sorted(Base.metadata.tables)`, which is exactly what
`src/reckonai/migrations/env.py` does before autogenerate compares metadata
against the database. With the import removed it now fails and names the table:

    At index 12 diff: 'tenant' != 'seller_ledger_entry'
    Right contains one more item: 'tenant'

The per-module `importlib` test is kept, retitled, and no longer asserts
package membership — it catches a syntax error or a circular import, which the
subprocess check would otherwise report as a missing table without saying why.

**The general lesson is worth carrying into Phase 8 and Phase 11:** a gate whose
own setup performs the action it is checking for cannot fail. Phase 2's `float`
gate avoided this by construction — it parses source text with `ast` rather than
importing it — and that now looks like a deliberate property rather than a
convenience.

### Finding: a stale `__pycache__` made the negative test look inconsistent

While confirming the above, a restored import still reported the broken test
count. `find . -name __pycache__ -path "*reckonai*" -delete` resolved it. Worth
knowing when deliberately breaking and restoring a module to prove a gate works.

### Finding: `VARCHAR(6)` rejects an over-long value before the CHECK is reached

`transaction_type` is `VARCHAR(6)`, which fits `credit` exactly. A test probing
the `CHECK` with the value `"nonsense"` fails with `DataError` from the column
width rather than `CheckViolation` — and **would keep passing if the `CHECK`
were dropped**, since it only ever asserted that *something* rejected the row.

The test now uses `"DEBIT"` and `"dr"`, both within the width, and a separate
test covers the width rejection with its own exception type. The same trap
applies to any narrow checked column, which in this phase means every
`VARCHAR(32)` state column with a long vocabulary.

### `disbursement.released_at` is asserted twice, deliberately

Once against the model and once against the reflected schema. A model with no
default and a migration that added one would both pass the model-level
assertion, and the schema is what a query runs against.

---

## Group 9 — The pre-merge gate · 2026-11-08

Every check in `validation.md` section 3, run with the compose database up and
`DATABASE_URL` exported. A check that was not run is not a passing one.

| # | Check | Command | Result |
|---|---|---|---|
| C1 | `float` and `NUMERIC` metadata gates over nine new tables | `pytest tests/test_models_schema.py tests/test_money_purity.py` | **Pass** — 190 |
| C2 | Every money value declared as a pair | `pytest tests/test_models_schema.py -k "money_pair or matching_currency"` | **Pass** — 28 |
| C3 | Every financial table links to `raw_payload`, NOT NULL | `pytest tests/test_model_registry.py -k raw_payload` | **Pass** — 8 |
| C4 | A record cannot be inserted without its payload | `pytest tests/test_raw_payload.py` | **Pass** — 7 |
| C5 | `fx_conversion` carries all nine fields at the right types | `pytest tests/test_conversions.py -k columns` | **Pass** |
| C6 | A zero conversion fee rejected; NULL inserts | `pytest tests/test_conversions.py -k fee` | **Pass** |
| C7 | Same-currency conversion rejected; exactly-one parent | `pytest tests/test_conversions.py` | **Pass** — 12 |
| C8 | Two conversions against one refund insert | `pytest tests/test_conversions.py -k multiple` | **Pass** |
| C9 | State vocabularies contain what the roadmap requires | `pytest tests/test_lifecycle_states.py` | **Pass** — 28 |
| C10 | A state outside its vocabulary rejected by the database | `pytest tests/test_events_capture.py -k state` | **Pass** |
| C11 | A dispute inserts with every optional column NULL | `pytest tests/test_events_refund.py -k all_null` | **Pass** |
| C12 | A capture inserts with its whole breakdown NULL | `pytest tests/test_events_capture.py` | **Pass** — 20 |
| C13 | Refund linkage nullable, provenance not | `pytest tests/test_events_refund.py -k linkage` | **Pass** — 11 total |
| C14 | Tenant gate over nine new tables | `pytest tests/test_models_schema.py -k tenant` | **Pass** — 48 |
| C15 | A cross-tenant link rejected by the database | `pytest -k cross_tenant` | **Pass** — 3 |
| C16 | A half-populated money pair rejected | `pytest -k half_populated` | **Pass** — 2 |
| C17 | `float` AST gate and purity gate over the new modules | `pytest tests/test_money_purity.py` | **Pass** — 57 |
| C18 | JSONB round-trip; re-read stores nothing | `pytest tests/test_raw_payload.py` | **Pass** — 7 |
| C19 | `disbursement.released_at` has no default | `pytest tests/test_disbursements.py` | **Pass** — 10 |
| C20 | No pictographs in any tracked file | `pytest tests/test_conventions.py` | **Pass** — 17 |
| C21 | Migration clean, applies, reverses | `pytest tests/test_migrations.py` + `alembic check` | **Pass** — 6, no diff |
| C22 | Every model module registered | `pytest tests/test_model_registry.py` | **Pass** — 55 |
| C23 | Ledger unsigned, positive, unconstrained across its two type columns | `pytest tests/test_ledger.py tests/test_platform_fee.py` | **Pass** — 26 |
| C24 | The whole suite is green | `uv run pytest` | **Pass** — **1117** |
| — | Lint, as CI runs it | `uv run ruff check` | **Pass** |

Phase 2 finished at 858 tests; this phase adds 259.

**The migration round-trip, run explicitly as well as by test:**

    alembic downgrade b5f8485e802e   ->  33b2f40099eb -> b5f8485e802e
    alembic upgrade head             ->  b5f8485e802e -> 33b2f40099eb
    alembic current                  ->  33b2f40099eb (head)
    alembic check                    ->  No new upgrade operations detected.

**One thing deliberately not fixed.** `uv run ruff check scripts` reports six
E501s in Phase 0's probe scripts. `pyproject.toml` excludes `scripts` from ruff
with the reason written beside it — they produced the committed evidence in
`specs/paypal-fields.md` and Phase 0's validation re-runs them, so reformatting
them now would edit evidence-producing code for no benefit. CI runs bare
`uv run ruff check`, which honours the exclusion, and that passes.
