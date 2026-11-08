# Phase 3 — Canonical financial events and raw payload store · Plan

**Branch:** `phase-3/canonical-events`
Scope and decisions: `./requirements.md` · Merge gate: `./validation.md`

Each group leaves the repo in a working state and ends with one commit, so any
group can be reverted on its own. Do not start a group until the previous one's
exit condition holds.

Groups 1–9 run pre-merge. Group 10 runs **after** the merge, per
`requirements.md` D18, and the phase is not complete until it passes.

The order is deliberate. The three structural pieces — the state vocabularies,
the payload store and the parent-link helper — land before any table that uses
them, because each is spelled once and then repeated eight times. Getting the
helper wrong in Group 1 and discovering it in Group 7 means editing seven files.
The single Alembic revision comes last, when there is a whole schema to generate
it from.

---

## Group 0 — Prerequisite gate · no commit

Not implementation. Five facts to establish, because each one changes a file
below or invalidates the branch.

1. **Phase 2 is genuinely closed.** Its pre-merge gate passed, its post-merge
   deployed gate passed, and PR #9 and #10 are merged. Confirm `origin/main` is
   at `4e25c3f` or later and that this branch is cut from it.
2. **The local database is up and `DATABASE_URL` is exported.** Without it the
   database-backed tests skip locally and fail hard in CI, which is deliberate.
   `readme.md` §0 has the commands. Several checks in this phase assert that
   **Postgres** rejects an insert, and they cannot run at all without it.
3. **`uv run alembic current` reports Phase 2's revision** `b5f8485e802e`
   against the local database. If it reports the baseline `92be96b3c343`, run
   `uv run alembic upgrade head` first — Group 9 autogenerates against this
   history, and a stale local database produces a diff containing Phase 2's five
   tables as well as this phase's nine.
4. **Phase 2's four gates pass before this phase changes anything.**
   `uv run pytest tests/test_models_schema.py tests/test_money_purity.py
   tests/test_conventions.py tests/test_migrations.py` green. Recording this now
   is what makes a failure in Group 9 attributable.
   Note which file each gate actually lives in, because none is named for it:
   the `float` AST gate and the purity gate are in `tests/test_money_purity.py`,
   the pictograph gate is in `tests/test_conventions.py`, and the `NUMERIC` and
   tenant metadata gates are in `tests/test_models_schema.py`.
5. **The deployed service is healthy.**
   `GET https://reckonai-api.onrender.com/health/ready` answers `200`. A `503`
   after the merge is only evidence if it was `200` before.

**Exit:** all five recorded in `notes.md`, with the date.
**If blocked:** if Phase 2's gates are already failing, fix that first. Phase 3
adds nine tables to the metadata those gates iterate, and debugging them against
an already-red suite wastes the signal.

---

## Group 1 — State vocabularies and the lifecycle mixin · commit

No database, no table. The vocabulary every table below references.

1. Create `src/reckonai/models/lifecycle.py`:
   - `RECONCILIATION_STATES` — `("ingested", "matched", "unmatched", "delayed",
     "awaiting_evidence")`, per `requirements.md` D1, each value carrying its
     source as a comment: the last three are [[mission]] P4's non-error states
     and are the reason the tuple exists.
   - `DISBURSEMENT_STATES` — `("disbursed_at_capture", "funds_held",
     "release_requested", "disbursement_completed", "disbursement_pending",
     "disbursement_failed")`, from `specs/multiparty-direction.md` §4.
   - `LEDGER_ENTRY_TYPES` — `("sale", "commission", "commission_reversal",
     "refund", "dispute", "adjustment", "fee")`.
   - `state_check(column: str, values: tuple[str, ...], *, name: str) ->
     CheckConstraint`, generating the `IN (...)` constraint from the tuple so
     the two cannot drift. It must pass a short `name`, because Phase 2's
     `NAMING_CONVENTION` interpolates `%(constraint_name)s` and an unnamed check
     raises at table definition time.
   - `LifecycleMixin`, supplying `reconciliation_state` as a `VARCHAR(32)`,
     `NOT NULL`, defaulting to `ingested` both application-side and
     server-side, with its `CHECK`. Use `declared_attr`, for the same reason
     `TenantMixin` does.
2. Document in the module docstring that **nothing enforces an ordering between
   disbursement states**, and why: PayPal auto-releases held funds after 28
   days, so `disbursement_completed` is reachable without
   `release_requested` ever occurring (`requirements.md` D1).
3. Tests in `tests/test_lifecycle_states.py`: each required state from
   [[roadmap]] Phase 3's exit criterion is present in the right tuple;
   `state_check` renders a constraint whose name is short enough for the naming
   convention; the tuples contain no duplicates.

**Exit:** `uv run pytest tests/test_lifecycle_states.py` green. No table exists
yet, so nothing else changes.

---

## Group 2 — The `raw_payload` store · commit

The table every financial record points at, so it is created first.

1. Create `src/reckonai/models/payloads.py` with `RawPayload`:
   - `id` `uuid4` application-side, `TenantMixin`, `TimestampMixin`.
   - `source` `VARCHAR(16)` `NOT NULL`, checked against
     `("paypal", "marketplace")`. Per `requirements.md` D3 this is the **only**
     place a record's origin is stored.
   - `source_record_type` `VARCHAR(32)` `NOT NULL` — `capture`, `refund`,
     `order`, `ledger_entry` and so on.
   - `source_record_id` `VARCHAR(128)`, nullable: a list response has no single
     record id.
   - `endpoint` `VARCHAR(256)`, nullable, with the docstring stating why it
     exists — **field availability depends on the read path**, and Phase 7 must
     read refunds through the order because the refund endpoint omits
     `platform_fees` for the same record.
   - `retrieved_at` `TIMESTAMPTZ` `NOT NULL`, passed in by the caller rather
     than server-defaulted: it is when the payload was fetched, which a
     replayed or backfilled payload does not share with its insert time.
   - `body` `JSONB` `NOT NULL`.
   - `body_sha256` `CHAR(64)` `NOT NULL`.
   - `UNIQUE (tenant_id, id)` for the composite foreign keys below, plus
     `UNIQUE (tenant_id, source, source_record_type, body_sha256)` per D2, so an
     unchanged re-read stores nothing and a changed one stores a second row.
2. Add the module to `src/reckonai/models/__init__.py`. **Every group below adds
   its module here too** — Phase 2's handoff is explicit that a module which is
   never imported is absent from `Base.metadata` and autogenerate reads its
   table as one to drop. Group 9 adds the gate that makes forgetting impossible.
3. Create `src/reckonai/models/mixins.py` with `RawPayloadMixin`, supplying
   `raw_payload_id` as a `NOT NULL` composite foreign key on
   `(tenant_id, raw_payload_id) -> raw_payload (tenant_id, id)`, via
   `declared_attr` plus a `__table_args__` contribution. Every financial table
   uses it.
4. Tests in `tests/test_raw_payload.py`, against the real database:
   - A payload round-trips with a nested `JSONB` body and a non-ASCII value
     inside it, unchanged.
   - The same `(tenant, source, type, sha)` twice raises `IntegrityError`.
   - The same body under a different `source_record_type` inserts.
   - A payload with an unknown `source` is rejected by the `CHECK`.

**Exit:** `uv run pytest tests/test_raw_payload.py` green against the compose
database. `uv run pytest tests/test_models_schema.py` still green — the tenant
and `NUMERIC` gates now iterate one new table.

---

## Group 3 — `payment` and `capture` · commit

The two tables the rest of the model hangs off.

1. Create `src/reckonai/models/events.py` with `Payment`:
   - `external_id` `VARCHAR(128)` `NOT NULL`, `UNIQUE (tenant_id, external_id)`
     per `requirements.md` D15.
   - `seller_id` and `order_id`, both nullable composite foreign keys, with the
     docstring stating D4: `order_id` is intrinsic for a marketplace-origin
     record and `NULL` for a PayPal-origin one, because that tie is a match and
     Phase 11 owns it.
   - `amount` via `money_columns("amount")`, `NOT NULL`.
   - `status` `VARCHAR(32)` `NOT NULL`, PayPal's own word retained verbatim, not
     mapped onto our vocabulary.
   - `intent` `VARCHAR(16)`, `payer_reference` `VARCHAR(64)`,
     `disbursement_mode` `VARCHAR(16)` checked against Phase 2's
     `DISBURSEMENT_MODES`, all nullable.
   - `occurred_at` `TIMESTAMPTZ` `NOT NULL` — PayPal's `create_time`.
   - `RawPayloadMixin`, `LifecycleMixin`, `TenantMixin`, `TimestampMixin`, and
     `UNIQUE (tenant_id, id)` so children can reference it compositely.
2. Add `Capture` to the same module:
   - `payment_id`, nullable composite FK. Note in the docstring that this one is
     **provider-supplied**, from
     `$.supplementary_data.related_ids.order_id` — a real field, not a link to
     parse — unlike the refund linkage of Group 4.
   - `amount` `NOT NULL`; `gross_amount`, `net_amount`, `paypal_fee`,
     `settlement_amount` all nullable money pairs, each with its
     `money_pair_check`, and `settlement_currency` `VARCHAR(3)` nullable.
   - The docstring must carry `requirements.md` D12 in full, because this is
     where a Phase 7 author meets it: **a capture loses its entire
     `seller_receivable_breakdown` while `PENDING`, and an open dispute puts it
     there.** Capture these values at ingestion; never overwrite a populated
     breakdown with `NULL` on a later read; the absence is not evidence that no
     fee was charged.
   - `disbursement_state` `VARCHAR(32)` `NOT NULL` over
     `DISBURSEMENT_STATES`, with the docstring stating why it lives here rather
     than on `disbursement`: under `INSTANT` no disbursement record exists at
     all.
   - `status` `VARCHAR(32)` `NOT NULL`, and a note that it is **mutable and does
     not mean the seller was paid** — a `DELAYED` capture reads `COMPLETED`
     while funds are held.
3. Tests in `tests/test_events_capture.py`: a capture inserts with every
   breakdown column `NULL`; a half-populated money pair is rejected by
   `money_pair_check`; a duplicate `(tenant_id, external_id)` raises; a
   `disbursement_state` outside the vocabulary is rejected by the `CHECK`; a
   capture referencing a payment in **another tenant** is rejected by the
   composite foreign key.

**Exit:** the new tests and `tests/test_models_schema.py` green.

---

## Group 4 — `refund` and `dispute` · commit

The two tables whose linkage to a capture is the weakest, each for a different
reason.

1. Add `Refund` to `src/reckonai/models/events.py`:
   - `capture_id` nullable composite FK, `capture_linkage_is_derived` `BOOLEAN
     NOT NULL`, `capture_linkage_href` `VARCHAR(512)` nullable — all three of
     `requirements.md` D11. The docstring states that PayPal supplies no
     `capture_id` on a refund and the only route back is `links[rel=up]`, whose
     href must be parsed, and that a Phase 11 exception resting on this linkage
     must say so.
   - `amount` `NOT NULL`; `gross_amount`, `net_amount`, `paypal_fee`,
     `total_refunded_amount` nullable money pairs with their pair checks.
   - A note that a refund carries `seller_payable_breakdown` where a capture
     carries `seller_receivable_breakdown`, so **mapping code cannot share a
     path**.
2. Create `src/reckonai/models/disputes.py` with `Dispute`:
   - Every column except `id`, `tenant_id`, `external_id`, `raw_payload_id` and
     the lifecycle columns is **nullable**, per `requirements.md` D14 and
     [[roadmap]] Phase 3's "nothing is required that Phase 0 could not observe".
   - `capture_id` nullable composite FK, and `buyer_transaction_id`
     `VARCHAR(128)` nullable beside it, with the docstring stating that the
     latter is **not our capture id**.
   - `status`, `lifecycle_stage`, `dispute_state`, `reason`, `channel`,
     `outcome` as nullable `VARCHAR`; `amount` as a nullable money pair with its
     pair check; `seller_response_due_date`, `occurred_at`, `updated_at` as
     nullable `TIMESTAMPTZ`.
   - `seller_response_due_date` gets its own docstring paragraph: it is present
     while a response is outstanding and **gone once the seller accepted the
     claim, because nothing is due any more**. Its absence is never a missed
     deadline, and while it lasts it is what `awaiting_evidence` keys on.
   - A module-level note that in a connected integration **PayPal places dispute
     liability on the seller**, so any marketplace exposure we report must rest
     on a marketplace rule and never be inferred from a PayPal record
     ([[mission]] §3.1).
3. Tests in `tests/test_disputes.py` and additions to the refund tests: a
   dispute inserts with every optional column `NULL`; a refund inserts with
   `capture_id` `NULL` and `capture_linkage_is_derived` set; omitting
   `capture_linkage_is_derived` fails against its `NOT NULL`.

**Exit:** the new tests green, and `tests/test_models_schema.py` still green
across four new tables.

---

## Group 5 — The parent-link helper, `platform_fee` and `fx_conversion` · commit

The group with the most constraint logic in it, which is why the two tables that
share a shape are written together.

1. Add `capture_or_refund_parent()` to `src/reckonai/models/mixins.py`: the two
   nullable composite foreign keys plus
   `CHECK (num_nonnulls(capture_id, refund_id) = 1)`, emitted from one call so
   the two tables cannot spell it differently (`requirements.md` D5).
2. Create `src/reckonai/models/fees.py` with `PlatformFee`:
   - The parent link from step 1; `seller_id` nullable composite FK;
     `payee_reference` `VARCHAR(64)` nullable — where PayPal routed the
     commission.
   - `amount` `NOT NULL`, stored **positive as PayPal reported it**, with the
     docstring carrying `requirements.md` D6: the reversal is a row whose parent
     is the refund, there is no `is_reversal` flag and no sign, and the reversal
     is **automatic and pro rata** on an ordinary refund and a lost dispute
     alike.
   - `settlement_reference` `VARCHAR(128)` nullable, which **stays `NULL`** —
     partner fees settle as a next-day consolidated bank payment and no
     per-transaction reference is exposed. It exists because [[mission]] §7
     requires that a platform fee row be able to carry one later without a
     redesign.
3. Create `src/reckonai/models/conversions.py` with `FxConversion`:
   - The parent link from step 1.
   - `source` and `target` money pairs, both `NOT NULL`; `rate`
     `NUMERIC(20, 10)` `NOT NULL`; `fee` a nullable money pair; `provider`
     `VARCHAR(32)` `NOT NULL`; `converted_at` `TIMESTAMPTZ` nullable;
     `is_derived` `BOOLEAN NOT NULL`.
   - Three checks, per `requirements.md` D9:
     `CHECK (source_currency <> target_currency)`,
     `CHECK (fee_amount IS NULL OR fee_amount <> 0)`, and the money pair check
     on `fee`.
   - The docstring must state all four of: a row exists **only** where PayPal
     reported an `exchange_rate`, so a same-currency transaction carries none;
     `source_amount` is **the net, not the gross**, and converting the gross is
     wrong by the whole fee (14.46 USD on the observed fixture); `target` is
     PayPal's reported settlement figure and is **never** recomputed as
     `source * rate`; and `is_derived` governs `converted_at` alone, because the
     rate and both amounts are never derived (D10).
4. Tests in `tests/test_conversions.py` and `tests/test_platform_fee.py`:
   - Both parents set → rejected. Neither set → rejected. Exactly one →
     accepted. On both tables.
   - A `fee` of zero is rejected; a `NULL` `fee` inserts; a real non-zero `fee`
     inserts.
   - A same-currency conversion is rejected.
   - **Two conversion rows against one refund insert successfully** — the array
     case [[roadmap]] Phase 3 requires, since a refund declares an array of
     conversion entries where a capture declares a single object.
   - A 15-decimal rate string round-trips through `NUMERIC(20, 10)` without
     passing through `float`, and the stored value is compared as `Decimal`.

**Exit:** the new tests green. This is the group to slow down in; if anything in
the phase is wrong in a way the later groups hide, it is here.

---

## Group 6 — `disbursement` · commit

The entity `specs/paypal-fields.md` calls the most dangerous in the model.

1. Create `src/reckonai/models/disbursements.py` with `Disbursement`:
   - `external_id` from `$.item_id`, `UNIQUE (tenant_id, external_id)`.
   - `capture_id` nullable composite FK, with the docstring noting this linkage
     is **provider-supplied and needs no parsing** — `$.reference_id` is the
     capture id itself, with `reference_type: TRANSACTION_ID` — unlike Group 4's
     refund linkage.
   - `amount` `NOT NULL` — the capture's **net**, because fees were already
     taken at capture.
   - `recipient_reference` `VARCHAR(64)` nullable, `payout_transaction_id`
     `VARCHAR(128)` nullable, `provider_status` `VARCHAR(32)` nullable holding
     `$.processing_state.status` verbatim.
   - `released_at` `TIMESTAMPTZ` nullable with **no server default and no
     application default** (`requirements.md` D13), and the docstring stating
     why: `time_updated` is the integer `0`, so when a seller was paid is not
     readable from the record, and presenting an ingestion time as a provider
     timestamp is the substitution [[mission]] P3 forbids.
   - `provider_errors` `JSONB` nullable, for `items[].errors`, with the note
     that when it is absent the answer is `insufficient_evidence`, never an
     assembled cause.
   - A module-level note that **PayPal publishes no contract at all** for
     referenced payouts — neither the SDK nor its OpenAPI documents — so every
     field here is known from observation alone and a change would arrive
     without warning. And that the release is idempotent per reference: a second
     attempt answers `422 PAYOUT_ALREADY_COMPLETED_FOR_REFERENCE`.
   - A note that **ReckonAI never sends the release** ([[mission]] P6); it may
     observe that one is due or approaching the 28-day fuse, and recommend one.
2. Tests in `tests/test_disbursements.py`: a disbursement inserts with
   `released_at` `NULL`; the column has **no server default**, asserted against
   the reflected schema rather than by inspection; `provider_errors` round-trips
   as `JSONB`; a duplicate `external_id` within a tenant raises.

**Exit:** the new tests green.

---

## Group 7 — `seller_ledger_entry` and the sign helper · commit

The marketplace side of the model, and the only new thing in `engine/`.

1. Create `src/reckonai/models/ledger.py` with `SellerLedgerEntry`, exactly the
   shape of `requirements.md` D7:
   - `seller_id` composite FK `NOT NULL`; `order_id` composite FK nullable.
   - `entry_type` `VARCHAR(32)` `NOT NULL` over `LEDGER_ENTRY_TYPES`.
   - `direction` `VARCHAR(6)` `NOT NULL`, checked against
     `("debit", "credit")`.
   - `amount` `NOT NULL`, plus `CHECK (amount_amount > 0)`.
   - `occurred_at` `TIMESTAMPTZ` `NOT NULL`; `external_ref` `VARCHAR(128)`
     `NOT NULL` with `UNIQUE (tenant_id, external_ref)`.
   - The docstring states three things: the table is **append-only** — never
     updated, never deleted; the amount is unsigned and the `CHECK (> 0)` is
     what that buys, since a signed column cannot distinguish a wrong sign from
     a legitimate negative; and **no `CHECK` ties `direction` to `entry_type`**,
     because the marketplace supplies both and a disagreement is a Phase 11
     finding rather than an insert to reject (Phase 2 D13's reasoning).
   - It also states that there is **no stored running balance**, and why: a
     derived monetary value persisted beside its inputs is what
     [[tech-stack]] §9.6 forbids, and a seller's position is
     `SUM(credit) - SUM(debit)` per currency, computed at read time.
2. Create `src/reckonai/engine/ledger.py` with `signed(direction, amount) ->
   Money`, taking a `typing.Protocol` rather than the ORM class so the purity
   gate stays satisfiable, and `position(entries, currency) -> Money` if it
   falls out cleanly — otherwise leave the sum to Phase 10 and ship only
   `signed`. An unknown direction raises; it is not defaulted to `credit`.
3. Tests in `tests/test_ledger.py`: a zero amount is rejected by the database; a
   negative amount is rejected; `signed` inverts a debit and leaves a credit
   alone; an unknown direction raises; a `sale` row with `direction='debit'`
   **inserts successfully**, pinning the deliberate absence of a cross-column
   check so a later contributor does not add one as a tidy-up.

**Exit:** the new tests green, and `uv run pytest tests/test_money_purity.py`
green — the purity half of that gate now covers `engine/ledger.py`.

---

## Group 8 — The model-import gate · commit

Small, and it protects every later phase.

1. Confirm all seven new modules are in `src/reckonai/models/__init__.py`.
2. Add `tests/test_model_registry.py`: walk `src/reckonai/models/`, and fail if
   any module there is not reachable from `reckonai.models.__init__`.
   `requirements.md` D17 — a module that is never imported is absent from
   `Base.metadata`, and autogenerate reads its table as one to **drop**.
3. Assert in the same file that `Base.metadata.tables` holds all fourteen
   expected table names — Phase 2's five plus this phase's nine — by name, so a
   table that silently failed to register is named in the failure rather than
   inferred from a count.

**Exit:** `uv run pytest tests/test_model_registry.py` green, and deliberately
commenting out one import in `models/__init__.py` makes it fail. Verify that by
hand once; it is the only proof the gate works.

---

## Group 9 — The migration, the full gate, and the documentation · commit

1. `uv run alembic revision --autogenerate -m "canonical financial events and
   raw payload store"`. Then **read the generated revision line by line**
   before running it. Specifically check: all nine `create_table` calls are
   present; every `CHECK` named by the naming convention appears; the
   `num_nonnulls` checks survived autogenerate; `downgrade()` drops the nine
   tables in an order that respects the foreign keys; and no Phase 2 table
   appears, which would mean the local database was stale.
2. `uv run alembic upgrade head`, then `uv run alembic check` — clean. Then
   `uv run alembic downgrade b5f8485e802e` and `uv run alembic upgrade head`
   again, proving the round-trip. The downgrade is the half autogenerate gets
   wrong.
3. **Extend `tests/test_migrations.py`, which fails by design until you do.**
   `test_upgrade_creates_the_catalogue_tables` asserts the table list is
   *exactly* `alembic_version`, `commission_rule`, `order`, `order_item`,
   `seller`, `tenant`. Add this phase's nine, keeping the list exact rather than
   loosening it to "some tables exist" — Phase 2's docstring there says why the
   exactness is the useful property. Extend
   `test_every_catalogue_table_carries_a_tenant_id` the same way, so the tenant
   rule is checked against the schema the migration produced and not only
   against `Base.metadata`.
4. Run the whole pre-merge gate from `validation.md` §3 and record each result
   in `notes.md` with the command and its output. A check that was not run is
   not a passing one.
5. Documentation, in this order:
   - `notes.md` — the Group 0 facts, the gate results table, and any finding
     worth not relearning. Phases 1 and 2 each produced several; assume this one
     does too and write them as they happen rather than at the end.
   - [[roadmap]] Phase 3 — marked complete, with a handoff block listing what
     Phases 4, 5, 6, 7, 10, 11 and 12 inherit, taken from `requirements.md` §5.
   - `CLAUDE.md` — the phase status line, and the Phase 3 handoff pointer.
   - `readme.md` §0.1 if the money rules summary needs the new tables named.
6. Open the PR. Its description carries the gate table, not a claim that the
   gate passed.

**Exit:** `validation.md` §3 passes in full, the PR is open, and `notes.md`
records every result.

---

## Group 10 — Post-merge deployed checks · commit

Runs **after** the merge, per `requirements.md` D18. The phase is not complete
until this passes, and Phase 4 may not begin while it is failing.

1. Watch the deploy with the Render CLI — the workspace and resource ids and the
   three mechanics are in `specs/2026-11-07-canonical-core/notes.md`
   §"Render CLI setup". Confirm `preDeployCommand` ran the migration and that it
   reported the new revision as head.
2. `GET https://reckonai-api.onrender.com/health` and `/health/ready` both
   answer `200`.
3. From **inside Render** — the database is not reachable externally,
   `ipAllowList: []` — confirm the nine tables exist with their constraints, and
   that `alembic current` matches the new head.
4. Confirm the worker is up and still logging. Phase 2's Group 9 observed two
   workers overlapping for about 90 seconds during a rolling deploy; that is
   expected and is Phase 8's problem, not a failure here.
5. Record all four in `notes.md` with timestamps, and mark the phase complete.

**Exit:** all four recorded and passing.

---

## If the phase runs short

Groups 1–8 are the schema and are not optional — [[roadmap]] Phase 3's exit
criteria are met at the end of Group 8 only once Group 9's migration exists, so
the earliest honest stopping point is **the end of Group 9**. What can be
trimmed inside it is the documentation breadth in step 5: the [[roadmap]]
handoff and `notes.md` are required, `readme.md` §0.1 is not.

Nothing in Groups 1–7 can be deferred to Phase 4. A missing table means Phase 4
has nothing to seed, and a column added later lands in a second migration
against a deployed schema, which is the expand-then-contract problem
`requirements.md` D16 exists to avoid.
