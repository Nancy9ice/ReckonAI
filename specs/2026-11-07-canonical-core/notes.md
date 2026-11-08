# Phase 2 — Canonical core · Notes

Findings, recorded gate results, and anything the implementation met that the
specs did not predict. Scope: `./requirements.md` · Plan: `./plan.md` · Gate:
`./validation.md`

---

## Group 0 — Prerequisite gate · CLOSED 2026-11-07

All four facts hold.

| # | Fact | Result |
|---|---|---|
| 1 | Branch cut from `origin/main`, not from `docs/phase-1-complete` | **Pass.** `origin/main` is an ancestor of `HEAD`. Phase 1's PR #8 is merged at `6e63dbc` |
| 2 | Local database up and `DATABASE_URL` exported | **Pass**, after starting Docker — see below |
| 3 | `alembic current` reports the baseline revision | **Pass.** `92be96b3c343 (head)` |
| 4 | Deployed service healthy **before** the phase changes anything | **Pass.** `/health/ready` `200`, `/health` `200` |

**On fact 1.** The branch was first cut from `docs/phase-1-complete` while that
branch was still unmerged, then reset onto `origin/main` once PR #8 landed. The
reset is why fact 1 is checked rather than assumed: a Phase 2 branch based on an
unmerged documentation branch would have carried its commit into this phase's
history and made the eventual PR diff misleading.

**On fact 2.** The Docker daemon was not running, so
`docker compose up -d --wait` failed with
`failed to connect to the docker API at unix:///Users/.../docker.sock`. Starting
Docker Desktop and waiting for the daemon resolved it in about eight seconds.
Worth noting only because the error names a socket path rather than saying the
daemon is down, which reads like a configuration fault and is not one.

**On fact 4.** This reading is the reason Group 9's identical check means
anything. `/health/ready` runs `SELECT 1`, so a `200` after the migration shows
the service came back with the new schema; without the before-reading it would
show only that the service worked at some point.

---

## Group 1 — Money and the currency registry · 2026-11-08

**75 tests pass**, `ruff` clean, and the module imports and runs with
`DATABASE_URL` unset - which is the evidence that the money path has no database
dependency rather than merely not using one today.

One implementation consequence of the float gate worth recording, because it
looks like a workaround and is not. **`value.py` cannot write
`isinstance(value, float)`**, because the gate in `tests/test_money_purity.py`
parses the file's AST and fails on any reference to the `float` builtin,
including inside a type check. So an inexact amount is rejected by *exclusion* -
it reaches the final `else` because it is not a `Decimal`, `int` or `str` - and
the error message names the offending type at runtime through
`type(value).__name__`.

That is stricter than an explicit check rather than weaker: any inexact numeric
type is refused by the same branch, including ones nobody has thought about,
such as a numpy scalar arriving through a future connector.

## Group 2 — Largest-remainder allocation · 2026-11-08

**541 tests pass.** The sum-exactly property is parameterised across 8 amounts,
all 7 registry currencies and 9 weight vectors, which is where most of that
count comes from.

**Decimal division was wrong here, and subtly.** The first implementation
computed each ideal share as `Decimal(units) * weight / weight_total`. Decimal
division is evaluated at the context precision - 28 significant digits - so both
the share and its remainder are *rounded* results, and comparing rounded
remainders can seat the spare minor unit on the wrong party. That is precisely
the residue the function exists to eliminate, reintroduced by the arithmetic
meant to remove it.

Replaced with exact integer arithmetic: the weights are scaled to a common
integer basis, and `divmod(units * weight, weight_total)` gives the floor and
the remainder's numerator. All remainders share the denominator, so their
numerators order them exactly and no division is needed to compare them.
`test_remainders_are_compared_exactly_not_at_context_precision` uses weights
that differ only at the 28th significant digit, and is what would notice a
regression to division.

### The fail-observation, run rather than asserted

`plan.md` Group 2 requires watching the property fail before trusting it. The
remainder distribution loop was disabled (`order[:0]` in place of
`order[:leftover]`) and the suite re-run:

```
311 failed, 230 passed in 0.57s
```

The failures include `test_parts_sum_exactly_to_the_total` across every
currency and every weight vector that does not divide evenly,
`test_negative_totals_also_sum_exactly` for all 7 currencies, and every named
residue case. Restored, and the full money suite is **616 passed**.

This is the check `validation.md` §5 names first among the ways this phase could
be green and wrong: a sum property written with a tolerance, or comparing
rounded sums, would have passed with the distribution removed.

## Group 3 — The JSON boundary · 2026-11-08

**641 tests pass** across the three money suites. Two findings, one of which
changed a class hierarchy.

### `MoneyError` now derives from `ValueError`

`tests/test_money_json.py::test_a_money_field_refuses_an_inexact_amount` failed
on the first run: `InvalidAmount` escaped the Pydantic validator instead of
becoming a `ValidationError`. Pydantic wraps `ValueError` and `AssertionError`
raised inside a validator, and nothing else.

Rather than translating money errors into `ValueError` at each boundary, the
family's base was changed to derive from `ValueError`. That is the accurate base
anyway - every one of these is a bad value - and `except MoneyError` is
unaffected. The practical consequence is that a JSON number arriving in a
request body is refused as an ordinary field validation failure rather than
escaping as a 500.

### Pydantic renders a `Decimal` as a JSON string, so the realistic regression is a float

Worth recording because it changed what the fail-observation had to be.

The first attempt at breaking the gate made `_serialize` return
`quantized.amount` - a bare `Decimal` - rather than `str(...)`. **The suite
stayed green: 25 passed.** Pydantic's JSON serializer renders a `Decimal` as a
string, so a `Decimal` on the wire is already safe and the gate correctly did
not flag it.

Breaking it with an actual float did fire the gate. The body became:

```
{"amount":{"amount":104.05,"currency":"USD"}, ...}
```

and **9 tests failed**, including every currency case and the JPY one. So the
gate distinguishes the harmless case from the harmful one, which is the right
behaviour and not something the first observation would have shown.

**The JPY case is why `_decode_strictly` is paired with an `isinstance` check.**
`parse_float` never fires for a JSON integer, so a broken serializer emitting
JPY's `1699` as a bare number would slip past the decoder hook alone. The
docstring on `_reject_json_number` records this, since it is the reason the
obvious `isinstance(body["amount"], str)` assertion is insufficient *and* the
reason it is still present alongside the hook.

## Group 4 — The declarative base and the five catalogue tables · 2026-11-08

**731 tests pass** with the database up, and the suite skips cleanly with
`DATABASE_URL` unset. Three findings, one of which changed the design.

### A nullable `composite()` does NOT return `None`. It constructs `Money(None, None)`

This was the open question going into the group, flagged in `requirements.md`
D4's risk, and the answer is the unhelpful one. SQLAlchemy calls the composite's
class with whatever the columns hold, so an all-NULL pair reaches
`Money(None, None)`, and `Money` - correctly - raises:

```
reckonai.money.errors.UnknownCurrency:
  a currency code must be a string, not NoneType
```

Four tests failed on it, and it would have fired on **every load of a
commission rule with no fixed component**, which is most of them.

Fixed by building the composite from a factory, `_money_or_none`, rather than
from `Money` directly. It handles three cases, and the third is the one worth
having: both NULL gives `None`; both set gives a `Money`; **exactly one set
raises**, because reaching that state means `money_pair_check()` is missing from
the table, and handing back `None` there would read as "no fixed component"
when an amount had in fact been recorded without its currency.

The factory is used for non-nullable pairs too, so there is one code path. A
`None` assigned to a non-nullable attribute then fails at flush against the
column's `NOT NULL`, which is a clearer error than one raised mid-construction.

### `uuid.UUID` must not appear in `type_annotation_map`

A `uuid.UUID: uuid.UUID` entry makes SQLAlchemy try to instantiate
`uuid.UUID()` as a SQL type, which raises `TypeError: one of the hex, bytes,
bytes_le, fields, or int arguments must be given` at **class definition time** -
so the whole models package fails to import. SQLAlchemy 2.0 already maps
`uuid.UUID` to its own `Uuid` type, rendering as native `UUID` on Postgres.
A comment in `base.py` records this where the next person would add it back.

### A test session needs `join_transaction_mode="create_savepoint"`

Many tests in `tests/test_catalogue_constraints.py` provoke an
`IntegrityError` on purpose. Without a savepoint, the failed flush rolls back
the transaction the fixture owns, so teardown warns
`SAWarning: transaction already deassociated from connection` and anything
after it runs outside a transaction. With `create_savepoint`, a failed flush
unwinds only to the savepoint and the enclosing transaction survives to be
rolled back by the fixture.

Phase 3 will write far more constraint tests than this phase did, so the
pattern is worth inheriting rather than rediscovering.

### Two smaller notes

**A raw `session.execute(insert())` raises at the call, not at the next
flush.** Two tests that write a half-populated money pair have to bypass the
ORM - `composite()` makes the state unrepresentable through the mapped
attribute, which is D4 working - and so they need `pytest.raises` around the
`execute` itself.

**`order` is a reserved word in SQL.** SQLAlchemy quotes it automatically and
the generated DDL is correct, but any hand-written SQL in a later phase needs
`"order"` with the quotes. The table keeps the name the roadmap gives it rather
than being renamed to work around the quoting.

## Group 6 — Alembic metadata, the revision, and drift detection · 2026-11-08

**758 tests pass** from a genuinely empty database, `alembic check` is clean,
and `upgrade head` / `downgrade base` / `upgrade head` all exit 0.

Revision `b5f8485e802e`. Autogenerate produced it correctly on the first
attempt - all five `CHECK` constraints named and present, both composite
foreign keys carrying both columns, `NUMERIC` precision and scale intact, the
downgrade in valid dependency order, and no `sa.Enum`. The review in `plan.md`
Group 6 step 3 found nothing to hand-correct, which is worth recording as the
outcome rather than treated as a reason to skip the review next time.

### Two real defects, both found by running the full suite rather than a module

Neither would have been caught by running the new tests alone, and both had
been latent while the only revision was empty.

**`create_all` / `drop_all` in a test fixture fights with Alembic.**
`tests/test_catalogue_constraints.py` originally built its schema from
`Base.metadata` and dropped it at module teardown. Alphabetically it runs
before `tests/test_migrations.py`, which then tried to `downgrade base` against
tables that were already gone:

```
psycopg.errors.UndefinedObject: index "ix_order_item_tenant_id" does not exist
```

It passed throughout Group 4 because the baseline revision has no DDL to
reverse, and broke the moment this group added a revision that does.

Fixed with a session-scoped `migrated_database` fixture in `conftest.py`:
nothing but `tests/test_migrations.py` manages schema now, and that module
restores `head` on teardown. **The fix is better than the thing it replaced** -
the constraint tests now run against the DDL a migration produced rather than a
`create_all` approximation of it, so a `CHECK` lost in autogenerate fails the
constraint tests as well as the drift check.

**`fileConfig` in `env.py` was disabling every application logger.**
`logging.config.fileConfig` defaults `disable_existing_loggers` to **True**, so
running Alembic in-process silences `reckonai.web`, `reckonai.worker` and
everything else for the life of the process.

It surfaced as `tests/test_health.py::test_readiness_failure_leaks_no_credential`
failing in the full suite while passing alone: the warning it asserts on was
never emitted, because the new session fixture had migrated the database
in-process first and killed the logger.

Now `fileConfig(..., disable_existing_loggers=False)`. **This was latent from
Phase 1**, not introduced here - it simply had no in-process Alembic call to
expose it. It does not affect the deployed system, where Alembic runs as its own
process through Render's `preDeployCommand`, which is exactly why it could have
survived to Phase 8 and then looked like a worker logging bug.

### The drift gate, seen failing

A `drift_probe` column was added to `Tenant` without a migration:

```
ERROR [alembic.util.messaging] New upgrade operations detected:
  [('add_column', None, 'tenant', Column('drift_probe', String(length=8), ...))]
FAILED tests/test_migrations.py::test_the_models_and_the_migrations_agree
  alembic.util.exc.AutogenerateDiffsDetected
```

Both the command and the test fire. Reverted, and `alembic check` reports
"No new upgrade operations detected."

## Group 7 — The gates, and the deletion of Phase 1's C14 · 2026-11-08

**858 tests pass**, `ruff` clean. The two new modules add 100 checks between
them, most of them parameterised per module or per table so a failure names the
file.

### Both gates had bugs, and the self-tests found them

This is the argument for writing a gate's negative cases rather than trusting
it green, and it would have been easy to skip.

**The purity gate reported every module as forbidden.** The runtime half
collapsed `FORBIDDEN_IMPORTS` to top-level names to compare against
`sys.modules`, and `reckonai.db` collapses to `reckonai` - so importing
`reckonai.engine` "leaked" `reckonai`, `reckonai.engine` and
`reckonai.engine.rules`. Now the dotted entries are matched by full name and
the third-party ones by top level.

**The float gate flagged `x = 'float'`.** The first version walked every string
constant looking for a string annotation, which cannot distinguish an ordinary
string literal from `x: 'float' = None`. Now annotation expressions are
collected explicitly - from `AnnAssign`, `arg` and a function's `returns` - and
only string constants inside those are re-parsed and inspected.

Worth recording while fixing it: **`from __future__ import annotations` does
not turn annotations into strings in the AST.** It only changes runtime
evaluation, so `def f(x: float)` still parses as an `ast.Name` and the ordinary
walk catches it. The string branch is only for an annotation someone quoted
deliberately.

### Both gates, seen failing

A `float`-returning method was added to `money/value.py` and an unscoped table
to `models/catalogue.py`:

```
E  src/reckonai/money/value.py:277: reference to the float builtin
E  src/reckonai/money/value.py:278: reference to the float builtin
FAILED tests/test_money_purity.py::...[money/value.py]

E  AssertionError: unscoped_probe has no tenant_id. Add TenantMixin, or add
   the table to UNSCOPED_TABLES with a reason.
E  AssertionError: unscoped_probe has no UNIQUE (tenant_id, id); a child table
   cannot carry a composite foreign key to it.
FAILED tests/test_models_schema.py::test_every_table_is_tenant_scoped[unscoped_probe]
```

Both reverted. Note the tenant gate caught the new table through **three**
separate checks, including `test_the_metadata_is_not_empty`, which asserts the
table count - that one is a tripwire against a gate quietly iterating nothing.

### Phase 1's C14 is deleted, as Phase 1 specified

C14 asserted that no tracked source file mentioned `Decimal` or `NUMERIC` -
the statement that Phase 1 had no money path to test, so its green suite could
not be read as evidence the `float` rule was enforced. Confirmed that the
command now fails, which is why deleting it rather than inverting it was
right:

```
$ test -z "$(git ls-files 'src/**/*.py' | xargs grep -l 'Decimal\|NUMERIC')"
fails as expected: the money path exists
```

The heading is kept in Phase 1's `validation.md` and rewritten to record what
replaced it - C7 and C8 of this phase - so C1-C13 keep their numbering and a
reader following a reference to C14 finds an explanation rather than a gap.
Phase 1's S4 row now points here too.

### One asymmetry made explicit

`models` and `schemas` are in the **float** gate but not the **purity** gate. A
declarative model *is* the database mapping, so forbidding SQLAlchemy there
would make the gate meaningless rather than strict.
`test_the_models_package_is_deliberately_not_pure` asserts both halves of that,
so the asymmetry is a decision on the record rather than something a later
reader "fixes".

## Groups 8 and 9 — Documentation and the pre-merge gate run · 2026-11-08

Phase 2 marked complete in `specs/roadmap.md` with its handoff to Phases 3, 4,
6, 10 and 14. `readme.md` gained a section 0.1 on the money rules,
`src/reckonai/migrations/README` gained the autogenerate review checklist and
the enum trap, and `CLAUDE.md` now points Phase 3 at the handoff.

The `tech-stack.md` section 2 amendment that `requirements.md` D3 depends on was
applied before Group 1 rather than here, because a constitution document is
amended ahead of the divergence it authorises, not after it.

### The whole gate, run from an empty database

| Check | Result |
|---|---|
| C1 pure modules, no database | pass, 689 tests |
| C2 mixed-currency raises, equality does not | pass, 12 |
| C3 allocation sums exactly (property) | pass, 511 |
| C4 residue cases and tie-break order | pass, 6 |
| C5 3-decimal round-trip, exponent edges | pass, 26 |
| C6 unknown currency raises | pass, 1 |
| C7 float gate and purity gate | pass, 48 |
| C8 no float column, money column types | pass, 27 |
| C9 amounts cross JSON as strings | pass, 25 |
| C10 Money round-trips the real database | pass, 10 |
| C11 every table tenant-scoped | pass, 21 |
| C12 the database rejects what it promised | pass, 12 |
| C13 rule ambiguity and absence raise | pass, 25 |
| C14 no pictographs in tracked files | pass, 17 |
| C15 migration up / down / up, plus its tests | pass, 6 |
| C16 no model/migration drift | "No new upgrade operations detected." |
| C17 `ruff check` and the whole suite | pass, 858 |
| C18 Phase 1's C14 deleted, S4 repointed | 0 occurrences of the deleted command |

### A gate that passed while testing nothing

Found during the gate run itself, and the most useful finding of the group.

**C3's selector matched no tests and the check still exited 0.** It read
`-k "sums_exactly"` against a test named `test_parts_sum_exactly_to_the_total`.
`pytest` exits **0** when a `-k` expression deselects everything - it reported
`541 deselected` and success - so the phase's single most important
guarantee was being reported as verified by a command that ran none of it.

Three selectors were wrong this way or close to it: C3 (`sums_exactly`),
C12 (`rejects` against `..._is_rejected`), and C8 (`currency_column` against
`..._currency_columns_...`).

Fixed, and `validation.md` section 3 now states that **a check reporting
`0 passed` has failed**, with the counts recorded per check above so a future
run has something to compare against. C18's own selector was also wrong in the
opposite direction: grepping for the string `C14` matched the heading that is
deliberately kept, and grepping for `git ls-files` matched C4, which
legitimately uses it. It now greps for the deleted command itself.

This is the failure mode `validation.md` section 5 warns about - "a gate that
has only ever been seen green is not a gate" - arriving through the *runner*
rather than through the assertion.

## Group 9 — Post-merge deployed checks · 2026-11-08

PR #9 merged to `main` at `fda43d7`. Both checks pass, and M1 is confirmed from
the deploy log rather than inferred - the Render CLI was installed partway
through this group, which upgraded the evidence.

| Check | Result |
|---|---|
| M1 migration applied on Render | **Pass.** Deploy log shows the pre-deploy step running the upgrade to `b5f8485e802e`, and the deploy reached `live` |
| M2 `/health/ready` answers 200 | **Pass.** 200 on three consecutive attempts, 0.23-0.25s |

### M1 — the deploy log, quoted

`render deploys list srv-dbnp8pbrjlhs739ftnj0` shows the merge commit deployed
as `dep-dbo1c17b9u1s73d5lrt0`, **status `live`**, finished
`2026-11-08T06:27:21Z`, with the previous deploy `deactivated`. A deploy only
reaches `live` if `preDeployCommand` exits zero.

The pre-deploy step's own output:

```
06:26:37  ==> Starting pre-deploy: uv run --no-sync alembic upgrade head
06:26:49  ==> Running 'uv run --no-sync alembic upgrade head'
06:26:50  INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
06:26:50  INFO  [alembic.runtime.migration] Will assume transactional DDL.
06:26:50  INFO  [alembic.runtime.migration] Running upgrade 92be96b3c343 ->
          b5f8485e802e, canonical core: tenant, seller, order, order_item,
          commission_rule
```

"Will assume transactional DDL" is the line that makes the rest conclusive:
the revision is all-or-nothing on Postgres, so a completed upgrade to
`b5f8485e802e` means all five tables and every constraint exist, or the deploy
would have failed and the previous version would still be serving.

**353 log lines in the deploy window contain no `error`, `traceback`,
`exception`, `failed` or `503`.**

### M2, and why the Group 0 reading mattered

`/health/ready` executes `SELECT 1` and answers `503` when the database is
unreachable, so a `200` after the migration shows the service came back up with
the new schema and its connection pool intact. Group 0 recorded it at `200`
**before** the phase changed anything, which is what makes this a comparison
rather than an isolated observation.

### `ipAllowList: []` demonstrably works

Attempting the direct database read through the external path:

```
$ render psql dpg-dbnp86rrjlhs739fsjdg-a -c "SELECT version_num FROM alembic_version;"
Error: IP address (105.113.98.236) not in allow list for reckonai-db
```

That is Phase 1's security decision working, not an obstacle to report. An
absent `ipAllowList` means *allow every IP* on a new Render database, and this
repository becomes public at submission, so external access is off. The
allowlist governs **only** external connections; a service inside Render uses
the internal URL and bypasses it, which is why the migration itself connected
without trouble.

So `SELECT version_num FROM alembic_version` is reachable only from inside
Render - the dashboard Shell, or `render ssh`, which needs an SSH public key
registered on the account. It was not run, because the deploy log above
establishes the same fact more directly: it shows the upgrade *executing*,
where the version row would only show a stamp.

### Two things worth carrying to Phase 8

**Two workers ran concurrently for about 90 seconds**, which the worker log
shows plainly - the outgoing instance at `tick=1045..1048` overlapping the
incoming one at `tick=2,3,4`:

```
06:27:10  reckonai.worker.tick tick=2
06:27:28  reckonai.worker.tick tick=1047
06:27:40  reckonai.worker.tick tick=3
06:27:58  reckonai.worker.tick tick=1048
```

This is ordinary rolling-deploy behaviour and it is **exactly** the condition
Phase 8's exit criterion names: "two workers running concurrently never claim
the same job". It is not a hypothetical to be constructed in a test - it happens
on every deploy, so `FOR UPDATE SKIP LOCKED` has to be right before the first
job type exists.

**The Postgres instance reports `version=16`**, matching compose and CI. The
parity that `tests/conftest.py` asserts locally holds against the deployed
instance too.

### Render CLI setup, for the next phase that needs it

Installed with `brew install render` (homebrew-core, no tap). Authentication is
`RENDER_API_KEY` in `~/.zshrc`, and two mechanics are worth recording because
both cost a round trip here:

- **A non-interactive shell does not read `~/.zshrc`.** zsh sources it for
  interactive shells only, so every CLI invocation needs
  `source ~/.zshrc` first, or the key moved to `~/.zshenv`. Without it the CLI
  reports "run `render login` to authenticate", which looks like a bad key.
- **`render workspace set` needs an argument in non-interactive mode**, and
  there is no `workspace list` subcommand. The workspace id came from
  `GET https://api.render.com/v1/owners`. Ids for this project:

  | Resource | Id |
  |---|---|
  | Workspace | `tea-csppp356l47c73f6r3b0` |
  | `reckonai-api` | `srv-dbnp8pbrjlhs739ftnj0` |
  | `reckonai-worker` | `srv-dbnp8pbrjlhs739ftnig` |
  | `reckonai-db` | `dpg-dbnp86rrjlhs739fsjdg-a` |

- `render logs` emits a **stream of concatenated JSON objects**, not a JSON
  array, so it needs `json.JSONDecoder().raw_decode` in a loop rather than
  `json.load`.

### Phase 2 is complete

All five roadmap exit criteria pass, the 18-check pre-merge gate is green at
858 tests, and both post-merge checks pass. Phase 3 is next and should start
from the handoff under Phase 2 in `specs/roadmap.md`.
