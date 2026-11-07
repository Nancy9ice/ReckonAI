# Phase 2 — Canonical core: money, tenant, catalogue · Plan

**Branch:** `phase-2/canonical-core`
Scope and decisions: `./requirements.md` · Merge gate: `./validation.md`

Each group leaves the repo in a working state and ends with one commit, so any
group can be reverted on its own. Do not start a group until the previous one's
exit condition holds.

Groups 1–8 run pre-merge. Group 9 runs **after** the merge, per
`requirements.md` D14, and the phase is not complete until it passes.

The order is deliberate: `Money` and allocation are pure Python and need no
database, so they are finished and tested before any schema exists. If the phase
runs out of time, the groups that are hardest to get right are already done.

---

## Group 0 — Prerequisite gate · no commit

Not implementation. Four facts to establish, because each one changes a file
below or invalidates the branch.

1. **Phase 1 is genuinely closed.** Its post-merge deployed gate passed and PR #8
   is merged. Confirm `origin/main` is at `6e63dbc` or later and that this branch
   is cut from it, not from `docs/phase-1-complete`.
2. **The local database is up and `DATABASE_URL` is exported.** Without it the
   database-backed tests skip locally and fail hard in CI, which is deliberate.
   `readme.md` §0 has the commands.
3. **`uv run alembic current` reports the baseline revision** `92be96b3c343`
   against the local database. If it reports nothing, run
   `uv run alembic upgrade head` first — Group 6 generates a revision against
   this history and a stale local database produces a confusing autogenerate
   diff.
4. **The deployed service is healthy before the phase changes anything.**
   `GET https://reckonai-api.onrender.com/health/ready` answers `200`. Recording
   this now is what makes Group 9's identical check meaningful: a `503` after the
   merge is only evidence if it was `200` before.

**Exit:** all four recorded in `notes.md`, with the date.
**If blocked:** if the deployed service is already unhealthy, fix that before
starting. Phase 2 adds the first real migration, and debugging it against a
service that was broken beforehand wastes the one signal Group 9 provides.

---

## Group 1 — Currency registry and the `Money` value object · commit

The core of the phase. No database, no SQLAlchemy import.

1. Create `src/reckonai/money/currency.py`:
   - `CURRENCY_EXPONENTS`, the seven-entry frozen mapping from
     `requirements.md` D1, each entry carrying its reason as a comment.
   - `exponent(code: str) -> int`, raising `UnknownCurrency` on anything absent.
     **No two-decimal default**, which is the whole point of the function.
   - `quantum(code: str) -> Decimal` returning the currency's smallest unit as a
     `Decimal` (`Decimal("0.01")`, `Decimal("1")`, `Decimal("0.001")`), built
     with `Decimal(1).scaleb(-exponent)` rather than from a string table, so the
     two cannot disagree.
   - `normalise(code: str) -> str`: upper-cases and validates length 3. A
     currency code arriving lower-cased from a marketplace is a mapping concern,
     but normalising at the boundary here means `Money` never holds two spellings
     of one currency.
2. Create `src/reckonai/money/errors.py`: `MoneyError` base, with
   `CurrencyMismatch`, `UnknownCurrency` and `InvalidAmount`. A base class
   because Phase 6 will want to catch the family when quarantining a record.
3. Create `src/reckonai/money/value.py` with `Money` as a frozen, slotted
   dataclass over `amount: Decimal` and `currency: str`:
   - `__post_init__` validates the currency through `normalise` and `exponent`,
     and **rejects a `float` amount outright** with `InvalidAmount` rather than
     coercing it. Accept `Decimal`, `int` and `str`; a `str` is parsed with
     `Decimal(str)`, never through `float`. Reject a non-finite `Decimal` —
     `NaN` and `Infinity` are constructible and would poison every comparison
     downstream.
   - Arithmetic: `__add__`, `__sub__`, `__neg__`, `__abs__`, and `__mul__` /
     `__rmul__` by `int` or `Decimal` only. A `float` operand raises. There is
     deliberately **no `__truediv__`**: dividing money is allocation, and
     `allocate()` in Group 2 is how it is done.
   - Comparison per `requirements.md` D3: `<`, `<=`, `>`, `>=` raise
     `CurrencyMismatch` across currencies; `__eq__` returns `False` across
     currencies and `NotImplemented` against a non-`Money`; `__hash__` over the
     pair, so `Money` works in a `set` and as a `dict` key.
   - `quantize()` — the single named rounding boundary. `ROUND_HALF_UP` to the
     currency's exponent, per [[tech-stack]] §2. Nothing else in the class
     rounds.
   - `is_quantized()`, `is_zero()`, `is_negative()`.
   - Minor units: `to_minor_units() -> int` (quantizing first, so it cannot
     lose a fraction silently) and `Money.from_minor_units(units, currency)`,
     which [[tech-stack]] §9.3 needs for a source stating integer minor units.
   - `__str__` as `"160.00 USD"` and a `__repr__` that shows the unrounded
     amount, so a debugging session cannot be misled by a display rounding.
4. Create `src/reckonai/money/__init__.py` re-exporting `Money`, the errors,
   and the currency helpers. This is the import path every later phase uses, so
   it is fixed now.
5. Write `tests/test_money.py`. It must cover, at minimum: mixed-currency `+`,
   `-` and each ordering operator raising; mixed-currency `==` returning `False`
   and *not* raising; a `float` amount and a `float` operand both raising; `NaN`
   rejected; construction preserving an over-precise amount unrounded;
   `quantize()` at exponents 0, 2 and 3; `ROUND_HALF_UP` proven on a `.005` case
   where banker's rounding would differ; minor-unit round-trips for `JPY`, `USD`
   and `KWD`; an unknown currency raising; `hash` equality for equal values.

**Verify:** `uv run pytest tests/test_money.py` green, `uv run ruff check` clean.
No database needed, and the suite must pass with `DATABASE_URL` unset.
**Commit:** `feat(phase-2): add the Money value object and the currency registry`

---

## Group 2 — Largest-remainder allocation · commit

1. Create `src/reckonai/money/allocation.py`:
   - `allocate(total: Money, weights: Sequence[Decimal | int]) -> list[Money]`,
     implementing `requirements.md` D6: quantize, convert to integer minor units,
     compute each ideal share, floor it, then distribute the remaining units one
     each by remainder descending and index ascending.
   - A negative total allocates `abs(total)` and negates each part, so a refund
     allocation is the exact mirror of the charge's.
   - Raise on an empty weight sequence, on weights summing to zero, and on a
     negative weight. None has a defensible answer, and [[tech-stack]] §9.6
     forbids defaulting money.
   - `allocate_evenly(total: Money, parts: int) -> list[Money]` delegating to
     `allocate` with equal weights, since even division has the same residue
     problem and should not be re-implemented at a call site.
2. Re-export both from `src/reckonai/money/__init__.py`.
3. Write `tests/test_allocation.py`. The non-negotiable test is a **property**:
   over a parameterised set of totals, currencies and weight vectors, the parts
   always sum exactly to the input total. Plus, explicitly:
   - `0.01 USD` across three equal weights, the classic residue case
   - `100 USD` across weights `[1, 1, 1]` — parts `33.34, 33.33, 33.33`, and the
     tie-break order asserted, not just the sum
   - the same total negated, asserting the parts are the exact mirror
   - `JPY` (exponent 0) and `KWD` (exponent 3)
   - a zero weight receiving exactly `0`, not a share
   - a single weight receiving the whole
   - an unquantized total being quantized first, with the stated result
   - determinism: the same inputs allocated twice are equal element-wise

**Verify:** `uv run pytest tests/test_money.py tests/test_allocation.py` green.
The sum-exactly property must fail if the remainder distribution is removed —
check that by deleting it locally before committing. A gate never seen failing
has not been tested, which is the lesson Phase 1's `test_conventions.py` records.
**Commit:** `feat(phase-2): allocate money by largest remainder with no residue`

---

## Group 3 — The JSON boundary · commit

Amounts leave this system as strings. [[tech-stack]] §2 is explicit about why:
a JSON number is an IEEE-754 double and loses precision in the browser.

1. Create `src/reckonai/schemas/money.py`:
   - `MoneyOut`, a Pydantic v2 model with `amount: str` and `currency: str`,
     plus `from_money()`.
   - An annotated type that lets a `Money` appear directly in a FastAPI response
     model and serialize to `{"amount": "...", "currency": "..."}`, via a
     `PlainSerializer` and a validator that accepts both a `Money` and the dict
     form. The amount is serialized from `quantize()`, because the boundary that
     presents a number to a human is a rounding boundary and `requirements.md`
     D5 says it must be a named one.
2. Add a FastAPI route that returns a `Money` — the smallest thing that proves
   the serializer is wired into the app rather than only into a unit test. Put it
   under the existing service index rather than inventing a new surface; a single
   `GET /money/example` is enough and is removed in Phase 4 when real endpoints
   exist. Note that intent in its docstring so it is not mistaken for product.
3. Write `tests/test_money_json.py`:
   - `MoneyOut` round-trips every currency in the registry.
   - The route's response body, decoded with
     `json.loads(body, parse_float=_fail)` where `_fail` raises, **does not
     raise** — which proves no amount arrived as a JSON number. This is the
     check that actually enforces the criterion; asserting `isinstance(str)` on a
     parsed field does not, because `json` would already have made a `float` of
     it.
   - A 3-decimal amount survives the boundary with all three digits.

**Verify:** `uv run pytest tests/test_money_json.py` green.
**Commit:** `feat(phase-2): serialize monetary amounts as JSON strings`

---

## Group 4 — Declarative base, tenant mixin, and the five tables · commit

1. Create `src/reckonai/models/base.py`:
   - `Base(DeclarativeBase)` with a `type_annotation_map` binding `Decimal` to
     `Numeric(20, 6)` and `datetime` to `DateTime(timezone=True)`, so a column
     declared from a type annotation cannot land on the wrong SQL type.
   - A naming convention on `Base.metadata` for indexes, unique, check and
     foreign-key constraints. Alembic needs it to name constraints
     deterministically, and without it a downgrade cannot drop what an upgrade
     created on a different machine.
   - `TenantMixin`, supplying `tenant_id` as a `NOT NULL`, indexed `uuid`.
   - `money_columns(name)` per `requirements.md` D4, emitting the
     `NUMERIC(20, 6)` + `VARCHAR(3)` pair and the `composite()` from one
     declaration. Include a `CHECK` that the two are either both `NULL` or both
     set, for the nullable case — [[tech-stack]] §9.3 calls an amount without a
     currency a hard failure, so the database should say so too.
   - A `TimestampMixin` with `created_at`, server-defaulted.
2. Create `src/reckonai/models/catalogue.py` with the five tables, per
   `requirements.md` D2, D8, D9 and D13:
   - `tenant` — the only table without a `tenant_id`. `id`, `name`, `slug`
     unique, `created_at`.
   - `seller` — `external_ref` (the marketplace's own id),
     `paypal_merchant_id` and `paypal_email` both nullable, `name`,
     `default_currency` nullable, and the two multiparty capability flags
     `partner_fee_enabled` and `delay_funds_enabled`, which are granted per
     seller at onboarding ([[tech-stack]] §8). `UNIQUE (tenant_id,
     external_ref)` and `UNIQUE (tenant_id, id)`.
   - `order` — `external_ref`, `total` as a `Money` composite, `placed_at`,
     `disbursement_mode` as a checked `VARCHAR(16)`, nullable.
     `UNIQUE (tenant_id, external_ref)` and `UNIQUE (tenant_id, id)`.
   - `order_item` — `order_id`, `seller_id` (the `payee`, and the join the whole
     reconciliation rests on per [[tech-stack]] §9.5), `external_ref`,
     `category` nullable, `quantity` with a `CHECK > 0`, `unit_price` and
     `line_total` as `Money` composites. **Composite foreign keys**
     `(tenant_id, order_id)` and `(tenant_id, seller_id)`.
   - `commission_rule` — `seller_id` nullable, `category` nullable, `rate`
     `NUMERIC(9, 6)` with a `CHECK` between 0 and 1, the optional fixed amount
     pair, `valid_from`, `valid_to` nullable, with `CHECK (valid_to IS NULL OR
     valid_to > valid_from)`. Composite FK on `seller_id` where set.
   - **No constraint relating `order.total` to its items** (D13). Add a comment
     saying so, because its absence looks like an omission.
3. Create `src/reckonai/models/__init__.py` re-exporting `Base` and the five
   classes, so `Base.metadata` is complete from one import — which is what
   Group 6's `target_metadata` and Group 7's gates both depend on.
4. Write `tests/test_catalogue_constraints.py`, database-backed, creating the
   schema from `Base.metadata` in a transaction that rolls back:
   - A `Money` composite round-trips through the database for **every** currency
     in the registry, including both exponent edges. This is the C10 evidence
     that `composite()` behaves against real `NUMERIC` + `VARCHAR`.
   - A cross-tenant `order_item` — one whose `order` belongs to another tenant —
     is rejected by the composite foreign key.
   - A `commission_rule` with a fixed amount and no currency is rejected.
   - `quantity = 0` is rejected; `rate = 1.5` is rejected;
     `valid_to <= valid_from` is rejected.
   - An over-precise `Money` is stored rounded to six places — the D5 trap,
     asserted so it is documented rather than discovered.

**Verify:** `uv run pytest` green with the database up. Confirm it **skips**
rather than errors with `DATABASE_URL` unset, which is the behaviour
`tests/conftest.py` already establishes.
**Commit:** `feat(phase-2): add the tenant, seller, order and commission tables`

---

## Group 5 — Commission rule resolution · commit

1. Create `src/reckonai/engine/__init__.py` — the package [[roadmap]] Phase 10
   fills.
2. Create `src/reckonai/engine/rules.py`:
   - A `CommissionRuleLike` `Protocol` carrying only the fields the resolver
     reads, so `engine/` does not import `models/` and the purity gate in Group 7
     stays satisfiable. The ORM class and a test dataclass both satisfy it.
   - `resolve_commission_rule(rules, *, seller_id, category, at) -> rule`
     implementing `requirements.md` D12: filter to rules whose date range
     contains `at`, score by specificity `(seller, category)` > `(seller)` >
     `(category)` > default, and return the single most specific.
   - Raise `CommissionRuleAmbiguity`, naming both ids, when two rules tie on
     specificity. Raise `NoApplicableCommissionRule` when none applies — a
     missing rule is a quarantine, not a zero rate ([[tech-stack]] §9.6).
   - `at` is a parameter, never a clock read ([[tech-stack]] §6).
3. Write `tests/test_commission_rules.py` over plain dataclasses, with no
   database: each specificity level winning in turn; a rule outside its date
   range being ignored; an open-ended `valid_to` applying; the ambiguity raising
   and its message naming both ids; no applicable rule raising; and a boundary
   case at exactly `valid_from` (inclusive) and exactly `valid_to` (exclusive),
   with the half-open interval stated in the docstring so the next reader does
   not have to infer it from the test.

**Verify:** `uv run pytest tests/test_commission_rules.py` green with
`DATABASE_URL` unset.
**Commit:** `feat(phase-2): resolve the applicable commission rule by specificity and date`

---

## Group 6 — Alembic: metadata, the revision, and drift detection · commit

1. In `src/reckonai/migrations/env.py`, set `target_metadata` to
   `reckonai.models.Base.metadata` and **replace** the comment that currently
   explains why it is `None`. Phase 1 left that comment naming this phase; leave
   a short note on what autogenerate now compares against instead.
2. Generate the revision: `uv run alembic revision --autogenerate -m "canonical
   core: tenant, seller, order, order_item, commission_rule"`.
3. **Read the generated revision line by line before trusting it.** Autogenerate
   is a starting point. Confirm specifically: every `CHECK` constraint is
   present and named; the composite foreign keys are emitted with both columns;
   `NUMERIC(20, 6)` and `NUMERIC(9, 6)` have their precision and scale, not a
   bare `NUMERIC`; the `downgrade()` drops in dependency order; and no
   `sa.Enum` appears anywhere (D9). Hand-correct what is wrong rather than
   regenerating and hoping.
4. Prove it reverses: `uv run alembic upgrade head`, then
   `uv run alembic downgrade base`, then `upgrade head` again. All three exit 0.
5. Add the drift gate: `uv run alembic check` must exit 0 against a database at
   `head`. This is what makes a model change without a migration a failing
   check rather than a surprise on the next deploy.
6. Extend `tests/test_migrations.py` with the upgrade/downgrade/upgrade
   round-trip, and an assertion that the five tables and the `alembic_version`
   table exist after `upgrade head`.

**Verify:** the three Alembic commands and `alembic check` all exit 0, and
`uv run pytest` is green.
**Commit:** `feat(phase-2): add the canonical core migration and wire autogenerate`

---

## Group 7 — The gates, and the deletion of Phase 1's C14 · commit

The group that makes the rest of the phase non-regressable. It is last among the
implementation groups because a gate written before the code it guards tends to
be shaped around what the code happens to do.

1. Create `tests/test_money_purity.py`:
   - The AST `float` gate per `requirements.md` D10, over
     `src/reckonai/money/`, `src/reckonai/engine/`, `src/reckonai/models/` and
     `src/reckonai/schemas/`: fail on a reference to the `float` builtin, a
     `float` annotation, a call to `round`, or an attribute access on `math`.
     Report `path:line` for each offender, the way `test_conventions.py` does.
   - The purity gate per D11: walk the import graph of `reckonai.money` and
     `reckonai.engine` and fail on `sqlalchemy`, `psycopg`, `httpx` or
     `reckonai.db`.
   - **Self-tests for both**, built from `ast.parse` on strings rather than from
     fixture files, so the gates are seen failing. `test_conventions.py` records
     the cost of skipping this: its own fixtures tripped its own check.
2. Create `tests/test_models_schema.py`:
   - The tenant gate per D7: every table in `Base.metadata` except `tenant`
     carries a `NOT NULL` `tenant_id`.
   - The `NUMERIC` metadata gate per D10: no `Float`, `REAL` or
     `DOUBLE PRECISION` column anywhere; every `*_amount` is `Numeric(20, 6)`;
     every `*_currency` is `String(3)`; every `*_rate` is `Numeric` with scale
     `>= 6`.
   - Both iterate `Base.metadata.sorted_tables`, so Phase 3's tables are covered
     the moment they are declared.
3. **Delete C14 from
   `specs/2026-11-07-repo-deployment-skeleton/validation.md`**, and update that
   document's S4 row, which currently reads "Not applicable — no money path
   exists until Phase 2". Point it at this phase's gate. Phase 1's own note says
   the check is deleted here, not inverted, and leaving it would now fail:
   `src/` is full of `Decimal` and `NUMERIC` by design.

**Verify:** `uv run pytest` green and `uv run ruff check` clean. Then confirm
each new gate fails when it should, by temporarily introducing a `float`
annotation in `money/value.py` and a table without `tenant_id`, and reverting
both.
**Commit:** `test(phase-2): gate the money path against float and every table on tenant_id`

---

## Group 8 — Documentation and the two spec amendments · commit

1. **[[tech-stack]] §2 — already applied, 2026-11-07, before Group 1.** The
   `requirements.md` D3 clarification is the one place Phase 2 diverges from a
   literal reading of a constitution document, and the amendment is what
   authorises the divergence, so it landed before the code rather than in this
   documentation pass. Nothing to write here; confirm the comparison row and the
   paragraph below the table are both still present and still agree with what
   `Money` actually does.
2. **[[roadmap]]** — mark Phase 2 complete in the same form Phases 0, 0b and 1
   use: what was delivered, where the artifacts are, and what later phases
   inherit, drawn from `requirements.md` §5. Keep it to what a later reader needs
   and does not already have.
3. **`readme.md`** — a short note on the money rules a contributor meets first:
   `Money` is the only monetary type, `quantize()` is the only rounding, amounts
   cross JSON as strings.
4. **`src/reckonai/migrations/README`** — one line that `alembic check` is now
   part of the gate, beside the existing expand-then-contract warning.
5. **`notes.md`** in this directory — the Group 0 facts, anything the
   implementation found that the specs did not predict, and the Group 9 results
   once they exist. Phases 0, 0b and 1 all carry one; it is where a finding goes
   so it is not relearned.

**Verify:** `uv run pytest` green — this includes the no-pictograph gate over
the files just edited.
**Commit:** `docs(phase-2): record the canonical core and clarify the money comparison rule`

---

## Group 9 — Post-merge deployed check · no commit

Runs after the merge to `main`, per `requirements.md` D14. The phase is not
complete until both checks pass and both results are recorded in `notes.md` with
the date.

1. Watch the Render deploy. The web service's `preDeployCommand` runs
   `alembic upgrade head`, so **the migration applies here, not from a start
   command**. A failure shows as a failed deploy with the previous version still
   serving, which is the behaviour Phase 1 chose deliberately.
2. `GET /health/ready` answers `200`. Group 0 recorded it answering `200`
   beforehand, which is what makes this check evidence rather than a coincidence.
3. Confirm the five tables exist on the Render database and that
   `alembic current` reports the new revision.
4. Record all three in `notes.md`. If the migration failed, record what failed
   and why before fixing it — the failure is the more useful note.

**Exit:** `validation.md` §4 M1 and M2 both pass and are recorded.
**If blocked:** a failed migration on Render does not take the service down, so
there is no emergency. Diagnose against the deployed database rather than
re-running locally, where it already passed.

---

## Related

- `./requirements.md` — scope and the fourteen decisions
- `./validation.md` — the merge gate
- [[mission]] · [[tech-stack]] · [[roadmap]]
- `specs/2026-11-07-repo-deployment-skeleton/` — the spine, and the C14 this
  phase deletes
