# Phase 2 — Canonical core: money, tenant, catalogue · Requirements

**Branch:** `phase-2/canonical-core`
**Roadmap phase:** [[roadmap]] Phase 2 · 0.5 day
**Primary output:** the `Money` value object, the largest-remainder allocation
helper, and the five catalogue tables every later phase references — `tenant`,
`seller`, `order`, `order_item`, `commission_rule`.

---

## 1. Goal

**The `Money` value object and the entities everything else references.**

This is the phase where [[mission]] P1 and P2 stop being prose and become a type
the compiler and the test suite can enforce. Every amount in this product passes
through the class written here; every table in Phase 3 references a row created
by one of the five tables written here.

Phase 2 is small in volume and disproportionate in consequence. A `Money` that
quietly permits a `float`, allocates with a residue, or compares across
currencies does not fail loudly — it produces a confident wrong number in Phase
11 and an authoritative-sounding investigation in Phase 12. The whole point of
spending half a day on four files is that the tests written here are the only
place those defects are cheap to catch.

## 2. Why this runs now

[[roadmap]] Phase 2 depends on Phase 1, which is **complete and deployed**: its
pre-merge gate passed, its post-merge deployed gate passed, and PR #8 is merged,
so `origin/main` at `6e63dbc` carries the whole spine. This branch is cut from
there.

Three things Phase 1 explicitly handed forward are consumed here:

- **`target_metadata` in `src/reckonai/migrations/env.py` is `None`**, with a
  comment naming Phase 2 as the phase that sets it to the first declarative
  `Base.metadata`. Until that happens `alembic revision --autogenerate` produces
  an empty revision, so nothing in Phase 3 can be generated either.
- **Phase 1's `validation.md` C14 asserts the money path is *absent*** — a
  deliberate placeholder so a green Phase 1 suite could not be mistaken for
  evidence that the `float` rule was enforced. Phase 2 **deletes C14** and
  replaces it with a real gate. Phase 1's own note says deleted, not inverted.
- **The baseline migration `92be96b3c343` is empty on purpose**, and says so, to
  prove the Alembic wiring end to end before there was a schema to risk. Phase
  2's revision is the first with tables in it.

Everything downstream is blocked on this. Phase 3's financial events carry
`Money` columns and foreign keys into `seller` and `order`; Phase 10's
entitlement engine is arithmetic over `Money`; Phase 4's seed data instantiates
these five tables. There is no useful work to do in parallel.

## 3. Scope

### 3.1 In scope

| Area | What is built |
|---|---|
| Currency | A curated exponent registry; an unknown code raises rather than defaulting |
| `Money` | Frozen value object: `Decimal` amount plus currency, arithmetic that refuses mixed currencies, one named rounding boundary |
| Allocation | Largest-remainder allocation of a `Money` across weights, exact to the minor unit, deterministic |
| Catalogue tables | `tenant`, `seller`, `order`, `order_item`, `commission_rule` |
| Tenant scoping | A `TenantMixin`, composite foreign keys, and a schema test that fails on a table without `tenant_id` |
| Money persistence | SQLAlchemy `composite()` over a `NUMERIC(20, 6)` + `VARCHAR(3)` column pair |
| JSON boundary | Amounts serialize as strings, proven by a decoder that refuses JSON numbers |
| Commission rules | Effective-dated, scoped rules, plus the pure resolver that picks the applicable one |
| Migration | One reversible Alembic revision creating all five tables, with `alembic check` clean |
| Gates | The `float` gate, the `NUMERIC` metadata gate, the tenant gate, the purity gate |

### 3.2 Out of scope

Named so they are not drifted into, since each is genuinely tempting here.

- **Financial event tables.** `payment`, `capture`, `refund`, `dispute`,
  `platform_fee`, `disbursement`, `fx_conversion`, `seller_ledger_entry` and the
  `raw_payload` store are **Phase 3**. Phase 2 stops at the catalogue.
- **The lifecycle state enum.** [[mission]] §4 states it and [[roadmap]] Phase 3
  owns it. Phase 2 therefore introduces **no native Postgres enum type at all**
  — see D9.
- **The entitlement engine.** Expected entitlement, proportional refund
  allocation and commission adjustment are **Phase 10**. Phase 2 supplies the
  allocation primitive those functions call and nothing above it. The
  [[mission]] worked example is Phase 10's exit criterion, not this phase's.
- **Query-time tenant filtering.** Deferred to Phase 6 by D7, which also
  specifies it, because Phase 2 has almost no queries to filter.
- **Any `float` tolerance or rounding constant.** [[roadmap]] Phase 11 warns
  against finding one while chasing noise. None is introduced here.

## 4. Decisions

Each of D1–D4 is an answer given on 2026-11-07 to a question put before anything
was written; D5 onward follow from them or from reading Phase 1's code.

### D1 — Currency exponents come from a curated table in code

A frozen mapping in `src/reckonai/money/currency.py`. **An unknown currency code
raises `UnknownCurrency`** rather than defaulting to two decimal places, because
a silent default is how a 3-decimal currency loses a digit.

Considered and rejected: a generated ISO 4217 table of ~180 codes, which is 180
rows of data nobody reviews and gives no signal about which codes are actually
exercised; and adding `py-moneyed` or `babel`, which [[tech-stack]] §1 locks out
without an amendment and which would put a second `Money` concept in the
codebase.

The table carries seven entries, and the reason for each is recorded beside it:

| Code | Exponent | Why it is in the table |
|---|---|---|
| `USD` | 2 | Observed in 221 PayPal fixture amounts |
| `EUR` | 2 | Observed in 59 PayPal fixture amounts, and the cross-currency capture's source currency |
| `GBP` | 2 | A plausible seller currency for the Phase 4 dataset |
| `NGN` | 2 | As above, and it exercises a non-ASCII currency symbol downstream |
| `JPY` | 0 | The zero-exponent edge. Allocation and quantization are wrong in a different way for it |
| `KWD` | 3 | The three-exponent edge the roadmap's round-trip criterion names |
| `BHD` | 3 | A second 3-decimal code, so no test can pass by special-casing one string |

Growing the table is a one-line change plus a test. That is the intended cost of
meeting a new currency.

### D2 — `commission_rule` is percentage plus optional fixed, scoped, and effective-dated

Columns: `rate` as `NUMERIC(9, 6)`; an optional fixed component as the usual
amount-plus-currency pair; `seller_id` nullable where `NULL` means a
tenant-wide default; `category` nullable where `NULL` means all categories; and
`valid_from` / `valid_to` with `NULL` meaning open-ended.

Effective dating is the part that costs something, and it is kept for a specific
reason rather than for completeness. [[mission]] P7 requires that a decision be
reconstructable after the fact, and [[tech-stack]] §9.1 already applies exactly
this reasoning to connector mappings: "editing a mapping silently rewrites the
meaning of past results". A commission rate is the same hazard with money
attached. Without dating, correcting a seller's rate today changes what last
week's reconciliation run *meant*, and no stored artifact records that.

The fixed component exists because [[roadmap]] Phase 10 requires "hand-computed
fixtures cover each refund and commission policy" — plural. A percentage-only
table gives Phase 10 exactly one policy shape to test, which is not a test of
policy handling at all.

### D3 — Mixed-currency *arithmetic and ordering* raise; *equality* returns `False`

[[tech-stack]] §2 says "Two amounts of different currencies raise an error
rather than compare." Taken literally that makes `__eq__` raise, and a value
object with a raising `__eq__` is unusable: SQLAlchemy's unit of work compares
attribute values to detect changes, `money == None` appears in ordinary code, and
the object cannot go in a `set` or a `dict` key.

So the rule is split, and the split is deliberate:

- `+`, `-`, and the ordering operators `<`, `<=`, `>`, `>=` raise
  `CurrencyMismatch` across currencies. These are the operations that produce a
  wrong *financial* answer.
- `==` and `!=` across currencies return `False` and `True`. Asking whether two
  values are the same is not a monetary operation and has a correct answer:
  10 USD is not 10 EUR.
- `==` against a non-`Money` returns `NotImplemented`, so Python falls back
  rather than claiming inequality with something it cannot compare.

**[[tech-stack]] §2 was amended for this on 2026-11-07, before any money code was
written.** A constitution document is amended ahead of the divergence, not
retrofitted after it, which is why this did not wait for Group 8's documentation
pass. The §2 comparison row now states that it governs arithmetic and ordering,
and the paragraph below the table excludes `==` with the reason: equality has a
correct answer without a conversion record, where addition and ordering do not.

### D4 — `Money` reaches the database through SQLAlchemy `composite()`

Each amount is stored as the `NUMERIC(20, 6)` + `VARCHAR(3)` column pair
[[tech-stack]] §2 mandates, and mapped to a single `Mapped[Money]` attribute with
`composite()`. The ORM accepts and returns `Money` directly.

The reason is [[tech-stack]] §9.3's: "map `item_total` to an amount and forget
the currency, and the system will compare numbers across currencies and report
confident nonsense." With `composite()` there is exactly one place in the
codebase where an amount can be separated from its currency — the mapping
declaration — rather than one place per call site. The raw columns remain
ordinary `NUMERIC`, so SQL-side aggregation in later phases is unaffected.

A small helper, `money_columns(name)`, emits the pair and the `composite()` from
one declaration, so the two cannot drift apart or be spelled inconsistently
across tables.

### D5 — `Money` does not quantize on construction

Rounding happens **once, at an explicitly named boundary** ([[tech-stack]] §2),
which means construction is not that boundary. `Money(Decimal("104.045070107"),
"USD")` is a legal intermediate — it is what a rate multiplication produces — and
`Money.quantize()` is the only thing that rounds, `ROUND_HALF_UP`, to the
currency's exponent.

The consequence is a trap worth stating because the database will not raise on
it: **`NUMERIC(20, 6)` silently rounds an over-precise amount on insert.** A
`Money` carrying ten decimal places is persisted as six. That is a caller error,
not a storage decision, and C6 in `validation.md` exists to document what the
database does with one rather than to pretend it cannot happen.

### D6 — Allocation works in integer minor units, signs are applied after

`allocate(total, weights)` converts the quantized total to integer minor units,
allocates with largest-remainder, and converts back. Integers make "the parts sum
exactly to the whole" a property of the arithmetic rather than something checked
afterwards.

Two sub-decisions, both of which change results and so are recorded:

- **Ties break by lowest index.** Sort by remainder descending, then by index
  ascending, and give the spare minor units to the first entries. Reproducibility
  is not optional for a financial calculation — the same inputs must allocate the
  same way on every run and every machine.
- **A negative total allocates by absolute value, then the sign is reapplied.**
  Refunds allocate negative amounts, and Python's floor division rounds toward
  negative infinity, so running the algorithm directly on a negative total gives
  the spare unit to a different party than the positive case would. Allocating
  `abs(total)` and negating keeps a refund's allocation the exact mirror of the
  charge's.

Weights summing to zero raise; an empty weight sequence raises. Neither has a
defensible answer, and [[tech-stack]] §9.6's rule against defaulting money
applies.

### D7 — Tenant scoping: schema enforcement now, query filtering in Phase 6

A **tenant is one marketplace operator** — ReckonAI's customer, and the
*platform* in PayPal's multiparty model. A **seller** is a merchant inside that
marketplace, PayPal's `payee`. The two are different levels and a seller belongs
to exactly one tenant.

[[tech-stack]] §10 requires both a `tenant_id` on every table carrying
marketplace data and that every query filter by it. Phase 2 delivers the first
half completely and specifies the second:

- A `TenantMixin` supplies `tenant_id`, `NOT NULL`, indexed.
- **Children carry composite foreign keys** — `(tenant_id, order_id)` references
  `order (tenant_id, id)` — so a cross-tenant link is rejected by the database
  rather than by a code review.
- A test introspects `Base.metadata` and **fails if any table other than
  `tenant` itself lacks a `NOT NULL` `tenant_id`**. This is what makes the rule
  survive Phase 3 adding eight more tables.
- Query-time filtering lands in **Phase 6**, which introduces the first real
  queries. The mechanism is specified there, not left open: a
  `do_orm_execute` event applying `with_loader_criteria` against a
  context-local tenant, which fails closed when no tenant is set.

One demo tenant is seeded. The column buys nothing visible this week; it is
carried because adding it now costs one column per table, and adding it in Phase
11 means touching every table, every query and every migration at once.

### D8 — Single-column primary keys, plus a `UNIQUE (tenant_id, id)` on each parent

Primary keys stay a single `uuid` column. The composite foreign keys D7 requires
need a unique constraint on the referenced pair, so each tenant-scoped parent
carries `UNIQUE (tenant_id, id)` for that purpose alone.

Considered and rejected: a composite primary key `(tenant_id, id)` on every
table. It gets the same safety, but it makes every single-row lookup and every
relationship two-column, for no gain over the extra unique constraint.

Identifiers are `uuid4()` generated **application-side**, via a Python-level
default, so a record has its id before it is flushed. Phase 4's seed builder and
Phase 6's ingestion both construct object graphs in memory before committing, and
a server-side `gen_random_uuid()` would force a flush to learn an id — and would
add a `pgcrypto` dependency to get there.

### D9 — No native Postgres enum type in Phase 2

`order.disbursement_mode` is a `VARCHAR(16)` with a `CHECK` constraint over
`INSTANT` and `DELAYED`, nullable because the marketplace may not state it.

Native Postgres enums are a known Alembic trap: autogenerate does not reliably
emit the type's `CREATE`/`DROP` alongside the table's, so a downgrade leaves an
orphaned type and the next upgrade fails on a duplicate. Phase 2's migration is
the first with any schema in it and the wrong phase to meet that. [[roadmap]]
Phase 3 owns the lifecycle state enum and therefore owns the choice between a
native type and a checked string — made deliberately, with this noted.

### D10 — The `float` gate parses the AST; it does not grep

[[roadmap]] Phase 2 says the gate is "a test that greps the module". Grep over
source text cannot tell a `float` annotation from the word "float" in a
docstring, and this repository's docstrings discuss `float` constantly because
the specs require explaining why it is banned. A text gate is therefore either
noisy or riddled with exclusions.

So the gate parses each money-path module with `ast` and fails on a reference to
the `float` builtin, a `float` annotation, a call to `round`, or an attribute
access on `math`. Strings and comments are invisible to it by construction. This
is stricter than what the roadmap asked for, in the direction the roadmap wanted.

It is paired with a **metadata gate** that is arguably the more valuable of the
two, because it covers tables that do not exist yet: no column anywhere in
`Base.metadata` may be `Float`, `REAL` or `DOUBLE PRECISION`; every `*_amount`
column must be `Numeric(20, 6)`; every `*_currency` column must be `String(3)`;
every `*_rate` column must be `Numeric` with a scale of at least 6. Phase 3
inherits that enforcement without writing a line of it.

### D11 — `engine/` is established now, with a purity gate on it

The commission-rule resolver is a pure function and lands in
`src/reckonai/engine/rules.py`, creating the package [[roadmap]] Phase 10 fills.
It takes the candidate rules and the timestamp **as arguments** — the "no clock
read inside a calculation" rule from [[tech-stack]] §6 is what forces the
timestamp to be a parameter.

A purity gate asserts that `reckonai.money` and `reckonai.engine` import nothing
from `sqlalchemy`, `psycopg`, `httpx` or `reckonai.db`. Establishing it against
two small modules is nearly free; retrofitting it once Phase 10's engine exists
means discovering what already violates it.

The resolver takes a `typing.Protocol`, not the ORM class, so `engine/` does not
import `models/` and the gate stays satisfiable. ORM rows and test dataclasses
both fit it.

### D12 — Rule ambiguity raises; it is not resolved by picking one

Specificity order is `(seller, category)` > `(seller)` > `(category)` >
tenant default, and only rules whose date range contains the timestamp are
candidates. If **two rules of equal specificity** both apply at the same instant,
the resolver raises `CommissionRuleAmbiguity` naming both ids.

Silently taking the newest or the first would make the commission for a
transaction depend on insertion order, and [[mission]] P5 is explicit that
"insufficient evidence" is a correct answer where a guess is not.

Considered and deferred: enforcing non-overlap in DDL with
`EXCLUDE USING gist` over a `tstzrange`. It needs the `btree_gist` extension, and
the resolver has to handle the ambiguity anyway for rules that arrive through
Phase 6's ingestion. Recorded here so Phase 3 or Phase 6 can revisit it with the
reason already written down.

### D13 — `order.total` is stated, never derived, and no constraint ties it to its items

`order` carries a `total` `Money`, and `order_item` carries both `unit_price` and
a stated `line_total`. **No `CHECK` or trigger asserts that the items sum to the
order total**, and that absence is the decision.

A marketplace whose order total disagrees with its own line items is exactly the
sort of thing this product exists to find. Enforcing it in the schema converts a
Phase 11 finding into an ingestion crash, and [[tech-stack]] §9.6 is clear that a
record failing validation is quarantined and visible, never silently rejected.
The same reasoning is why `line_total` is stored as the marketplace stated it
rather than computed from `unit_price * quantity`.

### D14 — The merge gate splits, as Phase 1's did

Automated checks run and must pass **before** the merge. After merging, the
migration's application to the Render database and the service's continued
readiness are confirmed and recorded. Phase 2 is not complete until both pass;
Phase 3 may not begin while either is failing.

Phase 2's revision is purely additive, so it cannot break the running instance
the way a dropped column could — Phase 1's handoff warns Phase 3 about exactly
that, since `preDeployCommand` runs while the previous version still serves
traffic. Additive is not the same as verified, though: this is the first
migration with tables in it, the first use of `composite()`, and the first
`CHECK` constraints, and `NUMERIC(20, 6)` behaving on managed Postgres as it does
on compose is an assumption worth one command.

## 5. What later phases inherit

Recorded here so none of it is rediscovered.

- **Phase 3** gets `Base`, `TenantMixin`, `money_columns()`, a populated
  `target_metadata`, and both the `float` and `NUMERIC` metadata gates already
  enforcing its eight new tables. It owns the lifecycle enum, and D9 hands it the
  Alembic enum trap in writing. It must migrate expand-then-contract, per Phase
  1's handoff and `src/reckonai/migrations/README`.
- **Phase 4** instantiates these five tables for its seed dataset. `seller`
  carries the multiparty capability flags and `order` carries
  `disbursement_mode`, so the roadmap's requirement that at least one order use
  `DELAYED` is representable without a schema change.
- **Phase 6** owns query-time tenant filtering, specified in D7, and inherits the
  composite foreign keys that make a cross-tenant link unwritable.
- **Phase 10** fills `src/reckonai/engine/`, calls `allocate()` for proportional
  refund allocation, and consumes `resolve_commission_rule()`. The purity gate
  from D11 is already watching that package.
- **Phase 14** formats amounts from the JSON string shape this phase fixes:
  `{"amount": "160.00", "currency": "USD"}`.

## 6. Risks

**The `float` gate passes while a `float` enters through a dependency.**
The AST gate reads our source, not `psycopg`'s type handling. The metadata gate
is the real defence — it asserts the column types the driver round-trips through
— and it is why D10 pairs the two rather than shipping the AST check alone.

**`composite()` is the one piece of ORM machinery in this phase.**
If it misbehaves against `NUMERIC` + `VARCHAR` in a way the tests do not catch,
it is load-bearing for every table in Phase 3. Mitigated by C10: a real
round-trip through the compose database, not a unit test against a mock, for
every currency in the registry including both exponent edges.

**Effective-dated rules are the complexity D2 accepted.**
The resolver is the only consumer and D12 makes its failure mode loud. If it
proves expensive in Phase 10, the cheap retreat is to stop writing new
`valid_to` values rather than to drop the columns, which keeps past runs
re-derivable.

**Half a day is optimistic for ten task groups.**
The groups are small and independently committable precisely so the phase can be
stopped after Group 7 with the roadmap's exit criteria met; Groups 8 and 9 are
documentation and the deployed check. Nothing in Groups 1–7 is optional.

## 7. Related

- [[mission]] — P1, P2, P3, P5, P7 and the worked example this phase's primitives
  must be able to produce in Phase 10
- [[tech-stack]] — §2 (money, and the D3 clarification), §6 (purity), §9.3
  (money as a pair), §9.6 (never default an amount), §10 (tenant), §13 (no
  pictographs)
- [[roadmap]] — Phase 2, and the Phase 1 handoff that names `target_metadata` and
  C14
- `specs/paypal-fields.md` — the observed currencies behind D1, and the
  15-decimal rate string behind D5
- `specs/2026-11-07-repo-deployment-skeleton/` — the spine this builds on
- `./plan.md` — the task groups · `./validation.md` — the merge gate
