# Block A — simulated marketplace, faults, connector

> Phases 4, 5 and 6 of [[roadmap]], run as one session per
> `specs/2026-11-08-replan/requirements.md` R3 and R4. Every exit criterion of
> all three phases still binds. Three commits, one spec directory.
>
> 2026-11-08. Branch `block-a-marketplace-connector`.

## 1. Scope

Three sub-blocks, in order. Each is a commit.

**A1 — simulated marketplace API and clean seed.** Endpoints returning
deliberately non-canonical field names, over an internally consistent
multi-seller, multi-currency dataset with one `DELAYED` order. Plus the two
things Phase 2 left: `GET /money/example` deleted, and the seed builder's own
order-total consistency check.

**A2 — faults and manifest.** Six seeded faults coexisting with the clean
records in one dataset, and the manifest that becomes Block G's eval ground
truth.

**A3 — connector, mapping and normalization.** `connector_mapping` table, the
declarative mapping document, the [[tech-stack]] §9.4 validation gate, an HTTP
client against the sim, and normalization into the Phase 2-3 tables with raw
payloads retained.

Out of scope: the §9.2 pass-3 Haiku field-name guess (R11), webhook emission
(R8), any PayPal call (that is Block D), matching or classification (Block C).

## 2. Decisions

### D1. The sim API is a router in the existing app, under `/sim`

`src/reckonai/sim/` holds `router.py` and `seed.py`;
`app.include_router(sim_router, prefix="/sim")`. One process, one deploy, one
demo URL. The connector reads it over HTTP as it would any third party, and
tests drive it through `httpx.ASGITransport` so CI needs no live server.

*Rejected:* a second ASGI app and a second Render service — cleaner separation,
but a second URL for the demo and a second service to deploy for a boundary we
can enforce by test instead. Rejected: static JSON files — Phase 4's exit
criterion is endpoints, and the demo loses a live marketplace to point at.

**The risk D1 creates, named so it is guarded.** The sim lives in the same
package as the canonical model, so it could emit canonical field names by
accident — which would make A3's mapping a no-op.

*An earlier draft of this decision named the wrong guard*: "a test asserting
`reckonai.sim` imports nothing from `reckonai.models`". That is unimplementable
and was never the real risk. `seed.py` must import the models — writing those
tables is its whole job — and `router.py` must read them. Import isolation was
never what protected the mapping.

**The guard that does work** is on the outward names, which is where the risk
actually lives. Every outward JSON key comes from one map in
`src/reckonai/sim/fields.py`, and a test asserts that map's value set is
**disjoint** from the canonical column-name set drawn from `Base.metadata`.
That is stronger than an import check — it fails on the actual mistake rather
than on a proxy for it — and it is the same assertion D3 and R3's guard need,
so there is one of it rather than three.

`src/reckonai/sim/data.py`, which holds the literal dataset, does import
nothing from `reckonai.models`. That is worth keeping as a property but it is a
layering preference, not the mapping guard.

### D2. The sim serves its records from the database, not from memory

The seed builder writes the canonical Phase 2-3 tables; the sim's endpoints
read them back and *rename* the fields outward. This is why the sim can be
inconsistent-by-design for A2's faults, and it is what makes "the full seeded
dataset ingests with zero data loss" a checkable round trip: seed, serve,
ingest, compare counts and amounts.

It also means the sim is a **presentation layer over the marketplace's own
truth**, which is the honest model — a real marketplace has its own database.

*The thing to not get wrong:* the seeded rows and the ingested rows land in the
same tables.

**`raw_payload.source` cannot tell them apart, and an earlier draft of this
decision was wrong to say it could.** `PAYLOAD_SOURCES` is a `CHECK`-constrained
`("paypal", "marketplace")` — Phase 3's D3 deliberately made it the *only* place
a record's origin is stored — so `marketplace_seed` and `marketplace_api` are
values the database refuses outright. Widening that vocabulary to work around a
test-fixture problem would be the wrong trade: it is a constitutional column,
and the seed is not a third kind of system.

Both sides therefore use `source="marketplace"`, and two things separate them:

- **A separate tenant.** A3 ingests into its own tenant, which is what makes a
  row-count comparison meaningful rather than circular. This was already the
  decision and it is the load-bearing half.
- **`raw_payload.endpoint`.** `NULL` for a row the builder wrote directly, and
  the real path — `GET /sim/marketplace/orders` — for one the connector read.
  This is exactly what that column exists for: Phase 3 added it because field
  availability depends on the read path, and "there was no read path" is a
  true and useful answer to record.

### D3. Field names are deliberately different, and that is enforced

`vendor_id`, `order_ref`, `item_total`, `gross_amount`, `commission_pct`,
`posted_on`, `txn_kind`, `ccy`. Per R3's named guard, a test asserts **no
canonical field in the active mapping draws from an identically-named source
field**. The mapping is exercised because it has to be.

Money crosses as `{"gross_amount": "160.00", "ccy": "USD"}` — a separate
currency key, not an envelope object, so A3's money mapping must resolve two
paths and §9.3's hard failure is reachable.

### D4. Six faults, and two deliberate non-faults

Faults, each with a manifest entry:

| # | Fault | Expected classification |
|---|---|---|
| F1 | Partial refund with no matching ledger entry | missing ledger adjustment ([[mission]] §6 scenario 1) |
| F2 | Commission charged at the wrong rate | commission mismatch (scenario 2) |
| F3 | FX settlement value disagreeing with the reported rate | fx discrepancy (scenario 3) |
| F4 | Duplicate ledger entry for one sale | duplicate ledger entry |
| F5 | Marketplace refund whose PayPal counterpart is absent | `awaiting_evidence`, **not** an exception (R10) |
| F6 | Disbursement amount disagreeing with the seller's entitlement | disbursement mismatch |

F3 is an incorrect **rate or settlement value**, never a conversion fee:
`CHECK (fee_amount IS NULL OR fee_amount <> 0)` refuses a zero fee, and P3
forbids inventing one to be wrong.

F5 is in the fault table but its expected classification is a non-error state.
It is the fault that proves the system does **not** over-report, and Block C
reads it from the manifest the same way as the others.

**Two non-faults live in the clean baseline on purpose**, so Block C proves
they are not flagged:

- An order under `DELAYED` disbursement sitting at `funds_held` with no release
  requested. A legal `DISBURSEMENT_STATES` value, not a seeded fault.
- A converted settlement where PayPal's cent-rounding leaves the product of
  gross and rate off by one cent from the recorded settlement.

### D5. Scale: small and hand-checkable

4 sellers, **12 clean orders plus 6 faulty ones**, ~25 order items, USD and EUR,
one FX order, one `DELAYED` order. Small enough that "reconciles perfectly by
hand" is literally true — `validation.md` §2 tabulates every expected
entitlement, and that table is the hand-check.

*Amended while building:* the faults get their **own orders**, `ORD-0013`
through `ORD-0018`, rather than perturbing the 12 clean ones. Two reasons. The
clean baseline stays exactly as hand-tabulated, so a fault can be added later
without re-deriving §2's table; and `verify()` excludes faulty rows by
manifest id, which is far simpler to keep honest when the boundary is a whole
order than when it is a column inside a clean one.

The cost is that the faults are trivially separable by order reference, which
is slightly less realistic. Accepted: the manifest names the ids anyway, so
nothing downstream was ever discovering them by inference.

### D5a. The expected figures are literals, so `verify()` is not circular

`data.py` states each order's expected commission and per-seller entitlement as
**hand-written literals**. `seed.py` writes the rows from those literals, and
`verify()` **recomputes** them from the items and the commission rules and
compares.

That ordering is the point. If `verify()` checked figures that the builder had
itself derived by the same arithmetic, it would be asserting that a function
agrees with itself — the shape of Phase 3's gate that passed with its own
subject commented out. Hand-written literals on one side and a recomputation on
the other is a real check, and a disagreement means one of the two is wrong,
which is worth knowing either way.

*Rejected:* a generated medium dataset. It fills the AG Grid demo better, but
"reconciles by hand" degrades to "reconciles by a second script", and a second
script that agrees with the first proves only that they share a bug. The
false-positive gate is the thing most worth protecting (replan, "Not cut").

### D6. The seed builder consistency check is its own gate, and runs in CI

Phase 2 D13 ties no constraint between an order's total and the sum of its
items, deliberately — such a disagreement is a finding, not a bad write. So
the clean baseline has **no database backstop**, and a silent inconsistency
would be measured by Block C as a false positive forever.

`seed.verify()` asserts, over the clean records only: every order total equals
the sum of its item line totals in the order's currency; every line total
equals unit price times quantity; every seller's ledger position equals their
entitlement; every commission equals the applicable rule's rate applied to the
seller's share. It raises on any failure and it is a test, not a script flag.

Faulty records are **excluded by manifest id**, not by a heuristic — the
builder knows which rows it broke.

### D7. One mapping document, versioned, per [[tech-stack]] §9.1

`connector_mapping`: per-tenant, JSONB, Pydantic-validated, `status`
`draft`/`active`, `activated_by`, `activated_at`, and a `version`. Every
canonical record stores the `mapping_version` that produced it, so a historical
run is re-derivable (P7).

Mapping production runs §9.2 passes 1 and 2 only — exact match, then the
curated synonym table. Whatever remains goes to human review. The `llm`
provenance value and the "a model cannot activate a mapping" rule stay in the
schema so pass 3 can be added without a migration (R11).

Transforms are a closed named set: decimal parsing, currency extraction,
timestamp with timezone, enum translation. Not expressions — a mapping is
configuration and must not become an execution surface.

### D8. Validation gate failures block activation and name the field

All six §9.4 checks. The money check is the one to get right: an amount
resolving without a currency is a **hard failure**, not a warning, because
§9.3 names it as the single most likely place for a mapping to be quietly
wrong. A mapping never half-activates.

### D9. Idempotence rests on `UNIQUE (tenant_id, external_id)`

Re-running ingestion inserts nothing new. The connector upserts on that
constraint. Per Phase 3's Phase 7 handoff, applied here too: **never overwrite
a populated value with `NULL`** on a re-read.

### D10. The connector is an httpx client against a configurable base URL

`MARKETPLACE_BASE_URL` in `Settings`, optional with no default. Tests point the
client at the in-process app via `ASGITransport`; the demo points it at the
deployed URL. This is the real retrieval path, so JSON serialization — where
the non-canonical names and money-as-strings actually live — is exercised, and
Block D's replay ingester reuses the shape.

### D11. Quarantine, never default

A record missing a required mapped field is quarantined: visible, countable,
not skipped and not defaulted. A missing monetary value is a quarantine, never
a `0` ([[tech-stack]] §9.6). Unmapped source fields survive in the retained
raw payload, so a mapping gap is recoverable.

### D12. The seed writes a PayPal side too, labelled synthetic, superseded by Block D

**A sequencing gap in the replan, found while building.** Block C — matching and
classification — is Day 1, and it compares the marketplace's records against
**PayPal's reported** figures. Block D, which captures the real sandbox
fixtures, is Day 2. So Block C as sequenced would have had nothing to match
against.

Resolution: this block's seed writes both sides. The marketplace side is the
sim's own truth. The PayPal side — `capture`, `platform_fee`, `disbursement`,
`fx_conversion`, with `raw_payload.source = "paypal"` — is **synthetic test
data**, written so Block B and Block C can be built and tested on Day 1.
Block D then replaces those rows with real captured sandbox responses through
the same tables, and the manifest's expected figures do not change when it
does, because they were derived from the marketplace side.

**Why this does not breach P3.** P3 forbids fabricating or estimating a
financial value *in reconciliation output* — presenting our own arithmetic as a
provider's figure. These rows are a test fixture for a simulated marketplace,
and every one of them is marked as such: `raw_payload.body` carries
`{"_synthetic": true, "replaced_by": "block-d"}`, so a row that survives into a
demo is identifiable rather than indistinguishable from a captured response.
The distinction P3 protects is between a measured provider figure and an
invented one, and a fixture that announces itself is neither a measurement nor
a claim to be one.

**What would breach it** is leaving these rows in place at demo time and
letting the dashboard present them as PayPal's evidence. So Block D's
validation owes one check: no `_synthetic` payload remains in the demo tenant
after the replay ingester has run. That is recorded here because it is this
block's debt, not Block D's discovery.

## 3. What this block hands forward

- **Block B** gets the clean dataset its worked example runs against: $160
  across two sellers at 10% → A $90, B $54, commission $16. Order `ORD-0001`
  is that order, seeded exactly.
- **Block C** gets both the clean baseline its false-positive gate is measured
  on and the faults it must classify, with D4's two non-faults planted in the
  clean set.
- **Block D** reuses `MarketplaceClient`'s shape and the `raw_payload` retain
  path for PayPal fixtures.
- **Block G** reads the manifest as eval ground truth.

## 4. Deviations from the replan, recorded

`plan.md` is written for this block although R1 dropped it, at the operator's
instruction, 2026-11-08. R1's reasoning stands for the documents it was about;
this is a four-group block across three phases and the sequencing is worth
writing down. Kept tight.

## 5. Limitations to record in the readme

Nothing new from this block. The marketplace being simulated is the premise of
the MVP rather than a limitation, and is already stated.

## Related

- [[roadmap]] Phases 4, 5, 6 — the exit criteria; Phase 3's handoff
- `specs/2026-11-08-replan/requirements.md` — R3, R4, R10, R11, Block A
- [[tech-stack]] §9 — mapping, the validation gate, runtime behaviour
- [[mission]] P1, P3, P4, P7 — and §6's three scenarios, which are F1-F3
- `specs/2026-11-07-canonical-core/requirements.md` D13 — why D6 exists
