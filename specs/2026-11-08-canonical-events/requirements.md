# Phase 3 — Canonical financial events and raw payload store · Requirements

**Branch:** `phase-3/canonical-events`
**Roadmap phase:** [[roadmap]] Phase 3 · 0.5 day
**Primary output:** the event side of the canonical model — `raw_payload`,
`payment`, `capture`, `refund`, `dispute`, `platform_fee`, `disbursement`,
`fx_conversion` and `seller_ledger_entry` — plus the lifecycle state
vocabularies [[mission]] P4 requires.

---

## 1. Goal

**The event side of the model, shaped by Phase 0's and Phase 0b's findings.**

Phase 2 built the catalogue: who the tenants and sellers are, what an order
contains, which commission rule applies. This phase builds what *happened* — the
provider's record of money moving and the marketplace's record of the same
events — and the store that keeps the original payload behind each one.

Nine tables, and the value of the phase is almost entirely in what they refuse
to hold. `specs/paypal-fields.md` is 69 canonical rows of evidence about what
PayPal actually returns, and the single most expensive thing Phase 3 can do is
declare a column `NOT NULL` that PayPal leaves out, or create an
`fx_conversion` row for a transaction that was never converted. Both produce a
schema that forces Phase 7 to invent a value, which is the one thing
[[mission]] P3 forbids outright.

## 2. Why this runs now

[[roadmap]] Phase 3 depends on Phases 0, 0b and 2, and all three are complete.
`origin/main` is at `4e25c3f` and this branch is cut from it.

Four things those phases handed forward are consumed here:

- **`specs/paypal-fields.md` is regenerated from multiparty evidence only** —
  56 `present`, 6 `present-but-unreliable`, 7 `absent`, none `unanswered`. Every
  nullability decision below cites a row in it rather than a guess about PayPal.
- **Phase 2's `Base`, `TenantMixin`, `TimestampMixin`, `money_columns()` and
  `money_pair_check()`** are the structural layer, so this phase declares no new
  base machinery.
- **`target_metadata` is populated**, so `alembic revision --autogenerate`
  produces a real diff. It also means a model module that is never imported is
  absent from `Base.metadata` and autogenerate reads its table as one to
  **drop** — the trap Phase 2 named explicitly and D17 turns into a gate.
- **Phase 2's `float` gate and `NUMERIC` metadata gate already cover these nine
  tables** without a line being added to them. That was the point of writing the
  metadata gate against tables that did not exist yet.

Phases 6, 7, 9, 10 and 11 are all blocked on this. There is no useful work to do
in parallel.

## 3. Scope

### 3.1 In scope

| Area | What is built |
|---|---|
| Raw payloads | A `raw_payload` JSONB store, and a `NOT NULL` link to it from every financial record |
| Provider events | `payment`, `capture`, `refund`, `dispute` |
| Multiparty events | `platform_fee`, `disbursement` |
| Conversions | `fx_conversion`, one-to-many against its parent, created only where PayPal reported a rate |
| Marketplace ledger | `seller_ledger_entry`, append-only, unsigned with an explicit direction |
| Lifecycle | Two checked state vocabularies: reconciliation states and disbursement states |
| Sign handling | One pure helper in `engine/` that turns a direction and an amount into a signed `Money` |
| Migration | One additive Alembic revision creating all nine tables, reversible, `alembic check` clean |
| Gates | The model-import gate, the raw-payload-link gate, the conversion-shape gate, plus Phase 2's four re-run over nine new tables |

### 3.2 Out of scope

Named so they are not drifted into, since each is genuinely tempting while the
tables are open.

- **Matching, and any cross-source link.** Deciding that a marketplace refund
  and a PayPal refund are the same event is [[roadmap]] Phase 11's work and gets
  its own table there. Phase 3 adds no `matched_*_id` column — see D4.
- **Exception rows and classification.** Phase 11. `reconciliation_state`
  records where a record is in its lifecycle, not what is wrong with it.
- **The entitlement engine.** Phase 10. The only thing this phase puts in
  `engine/` is the sign helper of D8, which performs no financial reasoning.
- **Ingestion of any kind.** No connector, no PayPal client, no seed data.
  Phases 4, 6 and 7. This phase writes rows only from tests.
- **A reconciliation run table.** It belongs with the engine that produces runs,
  and nothing before Phase 10 writes one.
- **Any rounding tolerance or FX comparison constant.** [[roadmap]] Phase 11
  warns against finding one while chasing noise, and this phase has no
  comparison to chase it with.

## 4. Decisions

D1–D4 and D7 are answers given on 2026-11-08 to questions put before anything
was written. The rest follow from them, from `specs/paypal-fields.md`, or from
Phase 2's handoff.

### D1 — Two checked state vocabularies, and still no native Postgres enum

[[roadmap]] Phase 3 requires the non-error states — delayed, unmatched,
awaiting evidence — **plus** the disbursement states. Those are two different
questions about a record, so they are two columns over two vocabularies:

| Vocabulary | Values | Lives on |
|---|---|---|
| `RECONCILIATION_STATES` | `ingested`, `matched`, `unmatched`, `delayed`, `awaiting_evidence` | Every financial table, via `LifecycleMixin`, defaulting to `ingested` |
| `DISBURSEMENT_STATES` | `disbursed_at_capture`, `funds_held`, `release_requested`, `disbursement_completed`, `disbursement_pending`, `disbursement_failed` | `capture.disbursement_state` |

Both are `VARCHAR(32)` with a `CHECK`, not a native Postgres enum, continuing
Phase 2's D9 rather than meeting its trap one phase later: autogenerate does not
reliably emit a native type's `CREATE`/`DROP` alongside its table's, so a
downgrade leaves an orphaned type and the next upgrade fails on a duplicate.
The values live in one Python tuple per vocabulary and the `CHECK` is generated
from it, so the two cannot drift.

Considered and rejected: **one combined vocabulary** across both questions. It
is fewer concepts, and it makes a capture legally able to carry
`awaiting_evidence` as its disbursement state and a dispute able to carry
`funds_held`. The `CHECK` would then no longer be able to say what a given table
may hold, which is the only thing it was for.

**The disbursement state lives on `capture`, not on `disbursement`**, and that
is the non-obvious half. Under `INSTANT` the seller is paid at capture and **no
disbursement record exists at all**, so a state that lived only on
`disbursement` could not answer "was this seller paid?" for half the dataset.
`disbursement.provider_status` separately retains what PayPal reported
verbatim — `SUCCESS` was the one observed value — because
`capture.disbursement_state` is our projection and the provider's own word is
the evidence ([[mission]] P7).

`disbursement_completed` is reachable **without** passing through
`release_requested`: PayPal auto-releases held funds after 28 days with no
instruction (`specs/multiparty-direction.md` §4). An engine that treats
`release_requested` as a required predecessor mis-models every auto-released
transaction, so nothing in this phase enforces an ordering between the states.

### D2 — One central `raw_payload` table, with a `NOT NULL` link from every financial record

`raw_payload` carries `source`, `source_record_type`, `source_record_id`,
`endpoint`, `retrieved_at`, a `JSONB` `body` and a `body_sha256`. Every one of
the eight financial tables carries `raw_payload_id`, `NOT NULL`, referencing it.

[[mission]] P7 requires that every record link to its payload. `NOT NULL` is
what makes that unskippable rather than aspirational: a record inserted without
its evidence fails, instead of becoming a `NULL` that a schema test has to go
looking for later. One central table also means a single payload containing a
capture, its platform fee and its conversion is stored **once** and referenced
three times, and that [[roadmap]] Phase 7's "raw payloads are retained and
diffed against the model" has one place to run.

`endpoint` is on the table for a reason specific to this API: Phase 7's exit
criteria state that refunds must be read **through the order**, because the
refund endpoint omits `platform_fees` for the same record. **Field availability
depends on the read path**, so a payload whose path is unrecorded cannot be
interpreted later.

`UNIQUE (tenant_id, source, source_record_type, body_sha256)` makes an unchanged
re-read store nothing, which is what Phases 6, 7 and 9 need for idempotency. A
*changed* re-read stores a second row, deliberately: Phase 0 observed three
undeclared fields of which two stopped being returned a day later, and that
diff is only visible if both payloads are kept.

Considered and rejected: **a `JSONB` column per table**. No join to read the
evidence, but the capture payload above is stored three times, and there is no
single place from which to diff retained payloads against the model.

### D3 — `source` lives on `raw_payload` only, and is never denormalised

A financial record's origin — `paypal` or `marketplace` — is reachable by
joining its payload, and is **not** repeated on the record.

The temptation is to copy it onto each table so "all PayPal captures" needs no
join. The cost is a second place for the same fact, and two places for one fact
is the shape of every reconciliation bug this product exists to find. The
reconciliation workload is join-heavy regardless ([[tech-stack]] §3 chose
SQLAlchemy for exactly that reason), so the join is not a new cost.

A derived `fx_conversion` row points at the capture payload it was derived from,
so it reports `paypal` by join, which is correct.

### D4 — One table per record type holds both sources, and Phase 3 adds no cross-source link

A marketplace refund and a PayPal refund are rows in the same `refund` table,
distinguished by their payload's `source`. Phase 6 normalises marketplace
records into these tables and Phase 7 normalises PayPal's into the same ones.

**No column in this phase links one to the other.** That link is a *claim* that
two records describe one event, and establishing it is [[roadmap]] Phase 11's
entire first sentence — "match marketplace records to PayPal records on stable
identifiers". A `matched_refund_id` added here would be a matching decision with
no run, no confidence and no evidence trail behind it, and Phase 11 would have
to migrate away from it.

The consequence is that `order_id` on a financial record is **nullable and means
only what the source itself stated**: intrinsic for a marketplace-origin record,
null for a PayPal-origin one, where the tie to a marketplace order is a match
rather than a fact.

### D5 — `platform_fee` and `fx_conversion` attach by nullable typed foreign keys, with an exactly-one `CHECK`

Both hang off whichever record they came from. A capture carries platform fees
and may carry a conversion; a refund carries the pro-rata fee reversal and
declares an **array** of conversion entries. So each table carries nullable
`capture_id` and `refund_id`, each as a composite foreign key on
`(tenant_id, parent_id)`, with `CHECK (num_nonnulls(capture_id, refund_id) = 1)`.

This keeps the two guarantees Phase 2's D7 built the composite keys to get: the
parent provably exists, and it provably belongs to the same tenant. Adding a
third parent site later — a disbursement-side conversion, which
`specs/paypal-fields.md` warns is unobserved rather than absent — costs one
column, one foreign key and a rewritten `CHECK`.

Considered and rejected: **a generic `(parent_type, parent_id)` pair.** It
extends to a new parent with no migration, and it gives up referential integrity
and tenant isolation at the database level, leaving only code between us and a
dangling or cross-tenant parent.

### D6 — A platform fee's direction is its parent, not a sign or a flag

Phase 0b observed that PayPal reverses the platform fee **automatically and pro
rata**, on an ordinary refund and on a lost dispute alike: a 30.00 refund of a
100.00 capture returned 3.00 of the 10.00 commission. The reversal arrives as
`refunds` `$.seller_payable_breakdown.platform_fees[]` — the same structure,
under a different parent.

So a reversal is a `platform_fee` row whose parent is the refund, with the
amount stored positive exactly as PayPal reported it. No `is_reversal` flag and
no sign, because both would be derivable from the parent and therefore a second
place to disagree. The amount is never negated at rest; [[roadmap]] Phase 10's
expected position applies the direction, and must assume the reversal happened
rather than instruct it.

### D7 — `seller_ledger_entry` is append-only and unsigned, with an explicit `direction`

One row per movement, never updated or deleted:

| Column | Shape | Why |
|---|---|---|
| `seller_id` | Composite FK, `NOT NULL` | An entry with no seller has no position to belong to |
| `order_id` | Composite FK, nullable | A manual adjustment, a dispute debit or a periodic fee has no order behind it |
| `entry_type` | `VARCHAR(32)`, checked | What Phase 11 asks "is there a refund entry for this refund?" against. Scenario 1 is a missing row of one type |
| `direction` | `VARCHAR(6)`, `debit` or `credit` | The sign, as its own fact |
| `amount` | `money_columns`, `NOT NULL`, `CHECK (> 0)` | Unsigned magnitude |
| `occurred_at` | `TIMESTAMPTZ`, `NOT NULL` | When the marketplace says it happened, distinct from `created_at` |
| `external_ref` | `UNIQUE (tenant_id, external_ref)` | What makes Phase 6's idempotency a database property |

`LEDGER_ENTRY_TYPES` is `sale`, `commission`, `commission_reversal`, `refund`,
`dispute`, `adjustment`, `fee`.

**The direction is a column rather than a sign on the amount**, and the `CHECK
(amount_amount > 0)` is what that buys. With a signed amount there is no way to
distinguish a wrong sign from a legitimate negative, so a zero or negative
amount cannot be rejected; with a direction column it is rejected by the
database. A seller's position is `SUM(credit) - SUM(debit)` per currency,
computed at read time.

**No stored running balance.** Considered at length and dropped: a
`balance_after` column is a derived monetary value persisted beside its inputs,
which [[tech-stack]] §9.6 forbids, and an out-of-order ingestion makes it
confidently wrong with nothing to recompute it. A variant carrying only the
balance the marketplace itself *stated*, never one we compute, was viable — it
is the same shape Phase 2's D13 chose for `order.total` — and was dropped
because it adds a comparison surface to [[roadmap]] Phase 11's zero-false-
positive gate, the hardest criterion in the project, for a figure that is
already derivable. The marketplace's stated balance is simply not evidence we
hold.

**`direction` and `entry_type` are partly redundant, and no `CHECK` ties them.**
A `sale` is always a credit and a `refund` always a debit, so a row could say
`sale` + `debit`. The marketplace supplies both, and a disagreement between them
is a finding about the marketplace's data rather than an insert to reject —
Phase 2's D13 reasoning applied unchanged. Enforcing it would convert a Phase 11
exception into an ingestion crash.

### D8 — The sign is applied in exactly one place, in `engine/`

`reckonai/engine/ledger.py` holds `signed(direction, amount) -> Money`, a pure
function over a `typing.Protocol`, and it is the only place in the codebase
where a ledger direction becomes a sign.

D7 moves the sign out of the data, which means something has to put it back, and
the failure mode is that twelve call sites each do it slightly differently — one
of them inverting a `commission_reversal`. One named function is also what keeps
Phase 2's purity gate satisfiable: `engine/` imports nothing from `sqlalchemy`,
`psycopg`, `httpx` or `reckonai.db`, so the helper takes a `Protocol` rather
than the ORM class, exactly as `resolve_commission_rule()` does.

### D9 — `fx_conversion` exists only where PayPal reported a rate, and `fee` may be `NULL` but never zero

An `fx_conversion` row is created **only** where
`$.seller_receivable_breakdown.exchange_rate.value` was present. A same-currency
transaction carries no row at all, which is why
`specs/paypal-fields.md` records all five conversion fields as `no_action`
rather than as a gap: an empty row would be indistinguishable from a conversion
whose fields went missing.

Three constraints express the rules [[tech-stack]] §2 states:

- `CHECK (source_currency <> target_currency)`. A conversion between one
  currency and itself is not a conversion.
- `CHECK (fee_amount IS NULL OR fee_amount <> 0)`. `fee` stays `NULL` — **absent,
  never zero** — because PayPal declares no conversion fee anywhere in the
  transaction APIs, and the residue of net times rate against the reported
  settlement is PayPal's rounding, not a fee. The `CHECK` permits a real fee if
  a provider ever supplies one and refuses the zero that would otherwise be
  read as "no fee was charged".
- `rate` is `NUMERIC(20, 10)`, per [[tech-stack]] §2, parsed from PayPal's
  15-decimal string into `Decimal` and stored whole. It is never recomputed, and
  `target` is PayPal's reported settlement figure rather than a product of the
  rate.

`source_amount` is **the net, not the gross** — PayPal deducts fees in the
transaction currency and converts the remainder. On the observed fixture,
converting the gross instead is wrong by the whole fee, 14.46 USD. The column
comment says so where a mapping author will meet it.

### D10 — `is_derived` governs the timestamp, and keeps the roadmap's name

Of the nine `fx_conversion` fields, exactly one may be computed:
`specs/paypal-fields.md` decides `fx_conversion.timestamp` as `derive` and every
other field as `no_action`, `limitation` or `connector_metadata`. The rate and
both amounts are never derived.

So `is_derived` is about `converted_at` and nothing else, and the column keeps
the name [[roadmap]] Phase 3 gives it rather than being renamed to
`converted_at_is_derived` for precision. The criterion names `is_derived`, the
gate checks for `is_derived`, and the docstring states its scope. `converted_at`
is nullable and `is_derived` is `NOT NULL`, so the provenance of a timestamp can
never be unstated while the timestamp exists.

### D11 — The refund-to-capture linkage carries its provenance and the href it was parsed from

[[roadmap]] Phase 3 requires the refund-to-capture link to be recorded as
derived. PayPal supplies no `capture_id` on a refund; the only route back is
`links[rel=up]`, whose `href` must be parsed. So `refund` carries three columns
rather than one:

| Column | Shape |
|---|---|
| `capture_id` | Composite FK, **nullable** — an unresolved linkage is a recorded state, not a failed insert |
| `capture_linkage_is_derived` | `BOOLEAN NOT NULL` |
| `capture_linkage_href` | `VARCHAR(512)`, nullable — the evidence it was parsed from |

The href is kept because [[mission]] P5 requires every conclusion to cite its
source records, and a Phase 11 exception resting on this linkage must be able to
say what it rests on. `capture_id` being nullable is deliberate: a refund whose
capture cannot be resolved is a Phase 11 `unmatched`, and quarantining it
visibly beats rejecting it ([[tech-stack]] §9.6).

### D12 — A capture's breakdown is nullable, captured at ingestion, and never overwritten with `NULL`

`gross_amount`, `net_amount`, `paypal_fee`, `settlement_amount` and
`settlement_currency` are all nullable on `capture`, and that is not caution
about Phase 0's samples. It is an observed behaviour: **a capture loses its
entire `seller_receivable_breakdown` while `PENDING`, and an open dispute puts
it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the
fee, net and platform fee vanished with the breakdown, then returned once the
dispute resolved.

Two things follow, and only the first is schema:

1. The columns are nullable, because a legitimate capture can have none of them.
2. **Phase 7 must capture these values at ingestion and may never overwrite a
   populated breakdown with `NULL` on a later read.** The absence is not
   evidence that no fee was charged. Phase 3 cannot enforce this — it writes no
   ingestion code — so it is recorded in §5 as an inherited rule and in the
   model docstring where a Phase 7 author will meet it.

### D13 — `disbursement.released_at` has no default and stays `NULL`

The release record carries no usable timestamp: `time_updated` is the integer
`0`. So when a seller was actually paid is not readable from the disbursement,
and `released_at` is nullable with **no server default and no application
default**. `created_at` already records when we saw the record, and presenting
an ingestion time as a provider timestamp is precisely the substitution
[[mission]] P3 forbids.

`provider_errors` is a nullable `JSONB` column holding `items[].errors` from a
failed disbursement. [[roadmap]] Phase 7 requires that when it is absent the
answer is `insufficient_evidence`, never an assembled cause, so the column is
nullable and the absence is meaningful.

`disbursement` also carries the one piece of good news in this entity:
`capture_id` is **provider-supplied and needs no parsing**, because
`$.reference_id` is the capture id itself with `reference_type:
TRANSACTION_ID`. Unlike D11's refund linkage, `capture_linkage_is_derived` has
no equivalent here and none is added.

### D14 — Dispute columns are nullable, and the capture linkage is the buyer's transaction id

Every dispute column except the identifiers is nullable, per [[roadmap]] Phase
3's "nothing is required that Phase 0 could not observe". The specific one that
matters is `seller_response_due_date`: `specs/paypal-fields.md` decides it
`no_action` because it is present while a seller response is outstanding and
**gone once the seller accepted the claim, because nothing is due any more**.
Its absence must never be read as a missed deadline, and while it lasts it is
what [[mission]] P4's `awaiting_evidence` state keys on.

`capture_id` is nullable, and `buyer_transaction_id` sits beside it, because the
dispute's `disputed_transactions[].buyer_transaction_id` is **not our capture
id** — the buyer's side of the transaction has its own identifier. Filtering the
disputes endpoint by `disputed_transaction_id` does accept the capture id, which
is a Phase 7 retrieval route rather than a column.

### D15 — Every ingested record carries the external id it arrived under, unique per tenant

Each of the eight financial tables carries `external_id`, `NOT NULL`, with
`UNIQUE (tenant_id, external_id)`. This is what makes "re-running ingestion is
idempotent" ([[roadmap]] Phase 6) and "retrieval is idempotent and re-runnable"
([[roadmap]] Phase 7) properties of the database rather than promises made by
two different connectors.

It is scoped per tenant rather than globally because two marketplaces may
legitimately use the same internal reference, and it is a plain `VARCHAR(128)`
rather than a typed id because the same column holds a PayPal capture id and a
marketplace ledger reference.

Phase 8's `processed_events` unique constraint is a different mechanism for a
different layer — it dedupes *deliveries*, where this dedupes *records* — and
neither replaces the other.

### D16 — The migration is additive, one revision, and still expand-then-contract

All nine tables are new, so nothing is dropped or renamed and the revision
cannot break the instance still serving traffic while `preDeployCommand` runs.
That is the hazard Phase 1's handoff and `src/reckonai/migrations/README` warn
Phase 3 about specifically, and this phase avoids it by construction rather than
by care.

Recorded anyway, because the warning was addressed to this phase and a later
reader needs to know it was read rather than missed: the rule stands for any
revision Phase 3 adds in a follow-up, and the first column this phase would want
to rename is a reason to add a new one and backfill instead.

One revision rather than nine, because `alembic check` and the
downgrade-and-back round-trip are the things being proven, and nine revisions
prove the same thing eight more times at the cost of nine files to keep in
order.

### D17 — A gate asserts every model module is imported in `models/__init__.py`

Phase 2's handoff names this trap in writing: a model module that is never
imported is absent from `Base.metadata`, and autogenerate reads its table as
one to **drop**. With nine tables arriving across several modules, the chance of
meeting it is no longer theoretical.

So a test walks `src/reckonai/models/` and fails if any module there is not
reachable from `reckonai.models.__init__`. It is three lines, it cannot be
satisfied by remembering, and it protects every later phase that adds a model.

### D18 — The merge gate splits, as Phases 1 and 2 did

Automated checks run and must pass **before** the merge. After merging, the
migration's application to the Render database and the service's continued
readiness are confirmed and recorded. Phase 3 is not complete until both pass;
Phase 4 may not begin while either is failing.

The deployed half is not ceremony here. This is the first migration with
`JSONB`, the first with `num_nonnulls` in a `CHECK`, and the first whose
revision creates nine tables and must drop all nine in order on downgrade.
Postgres 16 on Render behaving as Postgres 16 on compose is an assumption worth
one command, and the database is **not reachable externally**
(`ipAllowList: []`), so the schema check runs from inside Render — the mechanics
are in `specs/2026-11-07-canonical-core/notes.md` §"Render CLI setup".

## 5. What later phases inherit

Recorded here so none of it is rediscovered.

- **Phase 4** seeds these tables for its clean baseline. It must honour D7's
  `CHECK (amount_amount > 0)` and supply a `direction`, and it needs a
  `raw_payload` row per record because D2's link is `NOT NULL` — a seed builder
  that was going to insert financial rows directly cannot. It also still owes
  its own order-total consistency check (Phase 2 D13) and must delete
  `GET /money/example`.
- **Phase 5** seeds faults into these tables. Two of its constraints are now
  schema-visible: funds held under `DELAYED` is `funds_held`, a legitimate
  `DISBURSEMENT_STATES` value and not a fault, and an FX fault is an incorrect
  **rate or settlement value** — there is no `fee` to make incorrect, because
  D9's `CHECK` refuses a zero one.
- **Phase 6** normalises marketplace records into these same tables. D4 means
  its output and Phase 7's coexist, distinguished only by their payload's
  `source`, and D15's unique constraint is what makes its re-run idempotent.
- **Phase 7** inherits three rules it cannot discover from the schema alone:
  D12's "never overwrite a populated breakdown with `NULL`", D2's `endpoint`
  column and the reason it exists (refunds read through the order, because the
  refund endpoint omits `platform_fees`), and D13's `released_at` staying
  `NULL`. It also inherits D2's changed-payload retention as the mechanism for
  "raw payloads are retained and diffed against the model".
- **Phase 10** consumes `signed()` from D8 and must apply D6's direction rule:
  the platform fee reversal is automatic, so the expected position assumes it
  happened. A capture fee is never defaulted to zero — D12 makes "unknown" a
  representable state, which is what lets Phase 10 say so.
- **Phase 11** owns matching and adds the cross-source link D4 deliberately
  omits. `reconciliation_state` is where its non-error outcomes land, and
  `DISBURSEMENT_STATES` is what keeps held funds out of its exception count.
  D1's note that `disbursement_completed` needs no `release_requested`
  predecessor is the auto-release fuse it must model.
- **Phase 12** cites records by the ids here, and every citation resolves
  through `raw_payload`. D11's `capture_linkage_href` is what lets an
  investigation state that a refund-to-capture link was derived.

## 6. Risks

**A nullable column that should have been `NOT NULL`, and the reverse.** This is
the phase's whole risk surface. The defence is that every nullability decision
cites a row in `specs/paypal-fields.md` rather than an intuition, and that
`validation.md` C10 inserts a dispute with every optional column `NULL` to prove
the permissive direction actually works. The opposite error — a `NOT NULL` that
PayPal cannot satisfy — surfaces in Phase 7 as a column that forces a fabricated
value, and is the expensive one.

**`num_nonnulls` in a `CHECK` is new ground here.** It is standard Postgres and
is used by D5 on two tables. If it misbehaves under autogenerate, the
`exactly-one-parent` guarantee quietly disappears, which is why C7 asserts the
rejection from the database rather than from the model.

**Nine tables is roughly twice Phase 2's schema volume in the same half day.**
The groups in `plan.md` are ordered so the structural decisions land first and
each table is independently committable. The phase can stop after Group 8 with
[[roadmap]] Phase 3's exit criteria met; Groups 9 and 10 are the gate run,
documentation and the deployed check.

**`fx_conversion` is the table most likely to be wrong in a way tests do not
catch**, because the only cross-currency fixture in existence is a single
capture. The one-to-many refund case of C6 is proven against our own inserts, not
against a PayPal payload, and `specs/paypal-fields.md` is explicit that
`$.seller_payable_breakdown.net_amount_breakdown` is **unanswered rather than
absent**. Phase 7 should expect to find the refund conversion shape differs.

## 7. Related

- [[mission]] — P3 (never fabricate), P4 (non-error states), P5 (evidence), P7
  (nothing destroyed), and §7's note that a platform fee row should be able to
  carry a settlement reference later
- [[tech-stack]] — §2 (money, FX rules), §3 (`JSONB` for retained payloads), §6
  (purity), §9.3 (money as a pair), §9.5 (seller attribution, derived refund
  linkage), §9.6 (never default an amount), §10 (tenant), §13 (no pictographs)
- [[roadmap]] — Phase 3, and the Phase 1 and Phase 2 handoffs that name the
  Alembic enum trap, `models/__init__.py` and expand-then-contract
- `specs/paypal-fields.md` — the evidence behind every nullability decision
  above, and the decision table behind D9 and D10
- `specs/multiparty-direction.md` — §4's five lifecycle states and the 28-day
  fuse behind D1
- `specs/2026-11-07-canonical-core/` — the structural layer this builds on
- `./plan.md` — the task groups · `./validation.md` — the merge gate
