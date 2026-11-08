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
