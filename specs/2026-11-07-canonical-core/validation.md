# Phase 2 — Canonical core: money, tenant, catalogue · Validation

**Branch:** `phase-2/canonical-core`
Scope: `./requirements.md` · Task groups: `./plan.md`

Merge-readiness is **demonstrated by commands that pass or fail**, not asserted.
Anything that cannot be automated is listed in §4 as an explicit manual check
with a stated reason.

Phase 2 splits its gate in two, per `requirements.md` D14. §3's checks run and
must pass **before** the merge. §4's deployed checks run **after** it, and the
phase is not complete until they do. Phase 3 may begin once §3 passes; it may
not begin while any §4 check is failing.

Every check in §3 must be run with the local database up and `DATABASE_URL`
exported. A skipped database test is not a passing one — `tests/conftest.py`
makes that a hard failure in CI deliberately, and the same standard applies to a
local gate run.

---

## 1. Exit criteria mapped to checks

Every [[roadmap]] Phase 2 exit criterion, and how it is proven.

| # | Roadmap exit criterion | Check | Type | Gate |
|---|---|---|---|---|
| E1 | Mixed-currency arithmetic raises | C2 | Auto | Pre-merge |
| E2 | Allocation parts sum exactly to the whole with no residue | C3, C4 | Auto | Pre-merge |
| E3 | A 3-decimal currency round-trips | C5, C10 | Auto | Pre-merge |
| E4 | No `float` anywhere in the money path, enforced by a test so it cannot regress silently | C7, C8 | Auto | Pre-merge |
| E5 | Amounts serialize to JSON as strings | C9 | Auto | Pre-merge |

Three of these carry a note, because the criterion as written is weaker than
what the phase actually has to prove.

**E1 is split by `requirements.md` D3.** Arithmetic and ordering raise across
currencies; `==` returns `False` instead. C2 therefore checks both directions —
that the dangerous operations raise *and* that equality does not — because a
`Money` whose `__eq__` raises passes a naive reading of E1 while being unusable
in the ORM, in a `set`, or against `None`.

**E2 is checked as a property, not as an example.** C3 asserts the sum over a
parameterised space of totals, currencies and weight vectors; C4 pins the
specific cases where a residue appears and the tie-break order that resolves it.
An example-only test passes against an implementation that happens to be right
for two sellers and wrong for three.

**E4's "greps the module" is implemented as an AST parse**, per
`requirements.md` D10, and paired with a metadata gate. The reason is in D10:
this repository's docstrings discuss `float` constantly, because the specs
require explaining why it is banned, so a text gate is either noisy or riddled
with exclusions. C8 is the more valuable of the two — it enforces the column
types, and it covers Phase 3's tables before they are written.

---

## 2. Checks the specs require beyond the exit criteria

Not in the roadmap's list. These enforce standing rules from CLAUDE.md and the
constitution documents, and a Phase 2 that passed §1 while failing these would
be putting the violation into the schema every later phase builds on.

| # | Rule | Source | Check |
|---|---|---|---|
| S1 | No emoji or pictographs anywhere in the codebase | [[tech-stack]] §13 | C14 — Phase 1's gate, re-run over this phase's files |
| S2 | Every table carrying marketplace data has a `tenant_id` | [[tech-stack]] §10, CLAUDE.md | C11 |
| S3 | An amount is never separated from its currency | [[tech-stack]] §9.3 | C9, C10, C12 |
| S4 | No `float` in the money path | [[tech-stack]] §2 | C7, C8. **Phase 1's C14 is deleted in Group 7**, and its S4 row updated to point here. Phase 1 recorded that as a deletion, not an inversion |
| S5 | The engine is pure functions with no I/O | [[tech-stack]] §6 | C7 (purity half) |
| S6 | A monetary value is never defaulted to zero | [[tech-stack]] §9.6 | C6, C13 |
| S7 | Models and migrations do not drift apart | [[tech-stack]] §3 (reproducibility) | C16 |

---

## 3. Automated checks · pre-merge

Run in order, from a clean checkout of the branch. Every one must exit 0.

**Every check must also report a non-zero passed count.** This is not pedantry:
`pytest -k <expression>` exits **0** when the expression deselects every test,
so a check whose keyword no longer matches anything passes while testing
nothing. That happened during this phase's own gate run — C3's selector read
`sums_exactly` against a test named `test_parts_sum_exactly_to_the_total`, and
reported `541 deselected` and success. The counts below are therefore recorded
alongside each check, and a check reporting `0 passed` has failed.

### C1 — The suite runs without a database, for the pure modules

```bash
env -u DATABASE_URL uv run pytest tests/test_money.py tests/test_allocation.py \
  tests/test_commission_rules.py tests/test_money_purity.py
```

Exits 0. `Money`, allocation and rule resolution are pure Python, and a test
suite for them that needs a database would mean they are not
([[tech-stack]] §6). This check is what proves the dependency is absent rather
than merely unused.

### C2 — Mixed-currency arithmetic and ordering raise; equality does not

```bash
uv run pytest tests/test_money.py \
  -k "mismatch or ordering or equality or divided or multiplied"
```

Exits 0. Covers, as separate assertions: `+` and `-` raising
`CurrencyMismatch`; each of `<`, `<=`, `>`, `>=` raising; `==` across currencies
returning `False` without raising; `==` against a non-`Money` returning
`NotImplemented`; and a `float` operand to `*` raising.

The equality half is the one that would otherwise be missed, and
`requirements.md` D3 explains why it has to be asserted rather than assumed.

### C3 — Allocation sums exactly, as a property

```bash
uv run pytest tests/test_allocation.py -k "sum_exactly"
```

Exits 0 across every parameterised combination of total, currency and weight
vector. The assertion is `sum(parts) == total`, on `Money`, with no tolerance —
a tolerance here would be the "rounding constant found while chasing noise" that
[[roadmap]] Phase 11 names as a way to fail.

**This check must have been seen failing.** Group 2's verify step removes the
remainder distribution and confirms the property fails, then restores it. A gate
only ever seen green has not been tested, which is the lesson
`tests/test_conventions.py` records from Phase 1.

### C4 — The residue cases and the tie-break order

```bash
uv run pytest tests/test_allocation.py \
  -k "cent_case or three_case or tie or mirror or zero_weight or lopsided"
```

Exits 0. Pins the cases where largest-remainder actually does work:
`0.01 USD` across three equal weights; `100 USD` across `[1, 1, 1]` giving
`33.34, 33.33, 33.33` **in that order**; the negated total producing the exact
mirror, per `requirements.md` D6; and a zero weight receiving `0` rather than a
share.

The order matters as much as the values. A reproducible financial calculation
cannot have the spare cent land on a different seller between runs.

### C5 — A 3-decimal currency round-trips, and so do the exponent edges

```bash
uv run pytest tests/test_money.py -k "exponent or minor_units or quantize"
```

Exits 0 for `KWD` and `BHD` at exponent 3, `JPY` at exponent 0, and `USD` at 2.
Covers `quantize()` at each exponent, `to_minor_units()` /
`from_minor_units()` round-tripping, and `ROUND_HALF_UP` proven on a case where
banker's rounding would give a different answer — otherwise the stated rounding
mode is untested and Python's default would pass.

### C6 — An unknown currency raises; nothing defaults to two decimals

```bash
uv run pytest tests/test_money.py -k "unknown_currency"
```

Exits 0. `Money(Decimal("1.00"), "XYZ")` raises `UnknownCurrency`. This is the
check behind `requirements.md` D1: a two-decimal default is how a 3-decimal
currency silently loses a digit, and the registry is curated precisely so the
failure is loud and the fix is a reviewed one-line addition.

### C7 — The money path contains no `float`, and the pure packages no I/O

```bash
uv run pytest tests/test_money_purity.py
```

Exits 0. Two gates in one module, per `requirements.md` D10 and D11:

- An `ast` parse of every module under `src/reckonai/money/`,
  `src/reckonai/engine/`, `src/reckonai/models/` and `src/reckonai/schemas/`,
  failing on a reference to the `float` builtin, a `float` annotation, a call to
  `round`, or an attribute access on `math`. Strings and comments are invisible
  to an AST walk, which is the whole reason for choosing one.
- The import graph of `reckonai.money` and `reckonai.engine`, failing on
  `sqlalchemy`, `psycopg`, `httpx` or `reckonai.db`.

Both carry self-tests built from `ast.parse` on strings, so both have been
observed failing.

### C8 — No column anywhere is a float, and every money column is typed correctly

```bash
uv run pytest tests/test_models_schema.py \
  -k "float or amount or rate or currency"
```

Exits 0 over `Base.metadata.sorted_tables`: no `Float`, `REAL` or
`DOUBLE PRECISION` column exists; every `*_amount` column is `Numeric(20, 6)`;
every `*_currency` column is `String(3)`; every `*_rate` column is `Numeric`
with a scale of at least 6.

This is the half of S4 that matters most, because it is the half that covers
tables nobody has written yet. Phase 3 declares eight more and inherits this
enforcement without adding a line.

### C9 — Amounts cross JSON as strings, proven by refusing numbers

```bash
uv run pytest tests/test_money_json.py
```

Exits 0. The load-bearing assertion decodes the response body with
`json.loads(body, parse_float=_fail)` where `_fail` raises, and requires that it
does **not** raise — so no amount arrived as a JSON number.

Asserting `isinstance(value, str)` on a parsed field does not prove this: by the
time the assertion runs, `json` has already turned a bare `160.00` into a
`float`, and the test would be checking the wrong object. Also covers a
3-decimal amount keeping all three digits across the boundary.

### C10 — `Money` survives a real database round-trip, in every currency

```bash
uv run pytest tests/test_catalogue_constraints.py -k "round_trip or reads_back or columns"
```

Exits 0 against the compose database for every currency in the registry. This is
the evidence for `requirements.md` D4: `composite()` is the one piece of ORM
machinery in the phase and it is load-bearing for every table in Phase 3, so it
is checked against real `NUMERIC` and `VARCHAR` columns rather than in a unit
test.

Includes the `requirements.md` D5 trap, asserted rather than avoided: an
over-precise `Money` comes back rounded to six places, because
`NUMERIC(20, 6)` rounds on insert and does not raise.

### C11 — Every table is tenant-scoped

```bash
uv run pytest tests/test_models_schema.py -k "tenant"
```

Exits 0. Every table in `Base.metadata` except `tenant` itself carries a
`NOT NULL` `tenant_id`. Iterating the metadata rather than listing the tables is
what makes this survive Phase 3 and Phase 4 — a new table without a `tenant_id`
fails the suite the moment it is declared, which is the only version of this
rule that holds under time pressure.

### C12 — The database rejects what the schema promised

```bash
uv run pytest tests/test_catalogue_constraints.py -k "rejected"
```

Exits 0. Each assertion is a write the database must refuse:

- an `order_item` whose `order` belongs to a different tenant — the composite
  foreign key from `requirements.md` D7
- a `commission_rule` with a fixed amount and no currency, and the reverse —
  [[tech-stack]] §9.3's hard failure, enforced in DDL rather than only in code
- `quantity = 0`
- `rate` outside 0 to 1
- `valid_to` equal to or earlier than `valid_from`

A constraint that has never rejected anything is a constraint that may not be
there. `CHECK` constraints are easy to lose in an autogenerated migration, which
Group 6 step 3 reviews for and this check proves.

### C13 — No applicable commission rule raises; ambiguity raises

```bash
uv run pytest tests/test_commission_rules.py
```

Exits 0. Covers each specificity level winning in turn; date-range filtering
including the inclusive `valid_from` and exclusive `valid_to` boundaries; an
open-ended `valid_to`; `CommissionRuleAmbiguity` raised on a tie, with both ids
in the message; and `NoApplicableCommissionRule` raised when none applies.

The last two are `requirements.md` D12. A resolver that silently picks the newest
rule makes a transaction's commission depend on insertion order, and a resolver
that returns a zero rate for a missing rule defaults money, which
[[tech-stack]] §9.6 forbids.

### C14 — No pictographs in any tracked file

```bash
uv run pytest tests/test_conventions.py
```

Exits 0. Phase 1's gate, unchanged, re-run over this phase's source, tests and
three specification documents. [[tech-stack]] §13 bans pictographs and permits
typographic punctuation and box drawing, and this phase's documents use the
latter heavily.

### C15 — The migration applies, reverses, and re-applies

```bash
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run pytest tests/test_migrations.py
```

All exit 0. Reversibility was established by Phase 1's empty baseline so the
property would exist from the first revision rather than be retrofitted; this is
the first revision with a schema in it, and the first chance to break it.

The `downgrade` step is the one that catches a missed dependency order and an
orphaned constraint, which is why it runs between two upgrades rather than last.

### C16 — Models and migrations have not drifted

```bash
uv run alembic check
```

Exits 0 against a database at `head`. A model change without a migration is a
failing check here rather than a surprise on the next Render deploy, where
`preDeployCommand` would apply a revision that no longer matches the code being
deployed.

### C17 — Lint, and the whole suite together

```bash
uv run ruff check
uv run pytest
```

Both exit 0. The suite is run in full as well as in the slices above, because a
check that passes alone and fails beside its neighbours is a check with hidden
state — and the database-backed tests in this phase share a schema.

### C18 — Phase 1's C14 is gone, and S4 points here

```bash
# The deleted command, not the string "C14" - the heading is kept on purpose.
grep -c "Decimal..NUMERIC" specs/2026-11-07-repo-deployment-skeleton/validation.md
```

Returns **0 occurrences** of the deleted command, and Phase 1's S4 row names
this phase's C7 and C8 instead of "Not applicable".

Grep for the deleted *command*, not for the string `C14`: the heading is kept so
C1-C13 keep their numbering and a reader following a reference finds out what
became of it. Grepping for `C14` matches that heading and reports a false
failure, and grepping for `git ls-files` matches C4, which legitimately uses it.

Verified by reading, not only by grep: Phase 1's C14 asserted that the money path
was *absent*, as an explicit statement that its green suite was not evidence of
the `float` rule being enforced. Left in place it would now fail — `src/` is full
of `Decimal` by design — and inverting it would lose the distinction Phase 1 was
careful to draw. `requirements.md` §2 records that Phase 1 specified a deletion.

---

## 4. Manual checks · post-merge

Each is manual for a stated reason. Record the result of every one in `notes.md`,
with the date.

### M1 — The migration applied to the Render database

Manual because it needs the Render deploy log and the deployed database, neither
reachable from CI.

The web service's `preDeployCommand` runs `alembic upgrade head` while the
previous version is still serving traffic. Confirm from the deploy log that it
ran and succeeded, then confirm against the database that the five tables exist
and `alembic current` reports the new revision rather than `92be96b3c343`.

This is the first migration with a schema in it. C15 proves it applies to
compose and to CI's service container; only this proves it applies to managed
Postgres, where `NUMERIC(20, 6)` and the `CHECK` constraints are running against
a different instance configuration than either.

### M2 — `/health/ready` still answers 200

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://reckonai-api.onrender.com/health/ready
```

Returns `200`. Manual because it needs the deployed URL.

`/health/ready` runs `SELECT 1` and answers `503` when the database is
unreachable, so a `200` after the migration confirms the service came back up
with the new schema and its connection pool intact.

**Group 0 recorded this answering `200` before the phase changed anything.**
That is what makes this check evidence rather than a coincidence: without the
before-reading, a `200` here proves only that the service was working at some
point.

---

## 5. What would make this phase not mergeable

Stated plainly, because "the tests pass" is not the same as "this is correct",
and these are the failures that would still look green.

- **An allocation property that cannot fail.** If C3's assertion is written so
  that a broken remainder distribution still passes — comparing rounded sums, or
  comparing with a tolerance — then the phase's single most important guarantee
  is untested. Group 2's verify step exists for this and is not optional.
- **A gate written around the implementation.** C7, C8 and C11 were written
  after the code deliberately, and each has a self-test or a manual
  fail-observation. A gate that has only ever been seen green is not a gate.
- **A `CHECK` constraint lost in autogenerate.** The models would still say the
  right thing and every ORM-level test would pass, while the database accepted a
  `quantity` of 0. C12 is the only thing standing between that and Phase 4's
  seed data.
- **`Money` quantizing on construction.** It would make C3 and C5 pass more
  easily and would silently round a 15-decimal PayPal rate multiplication at
  every intermediate step, which is precisely the error
  `specs/paypal-fields.md` warns about and `requirements.md` D5 forbids.
- **A tenant gate that lists tables instead of iterating metadata.** It would
  pass today and would not notice Phase 3's eight new tables, which is the only
  case it exists for.

---

## 6. Related

- `./requirements.md` — scope and the fourteen decisions
- `./plan.md` — the task groups, each ending in one commit
- `./notes.md` — Group 0's recorded facts, findings, and the §4 results
- [[mission]] · [[tech-stack]] · [[roadmap]]
- `specs/2026-11-07-repo-deployment-skeleton/validation.md` — the gate this one
  follows, and the C14 that C18 confirms is gone
