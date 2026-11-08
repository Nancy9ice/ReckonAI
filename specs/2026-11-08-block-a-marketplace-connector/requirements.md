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
package as the canonical model, so it could import `reckonai.models` and emit
canonical shapes by accident — which would make A3's mapping a no-op. Guard:
a test asserting `reckonai.sim` imports nothing from `reckonai.models` or
`reckonai.engine`. The sim builds its payloads from its own literals.

### D2. The sim serves its records from the database, not from memory

The seed builder writes the canonical Phase 2-3 tables; the sim's endpoints
read them back and *rename* the fields outward. This is why the sim can be
inconsistent-by-design for A2's faults, and it is what makes "the full seeded
dataset ingests with zero data loss" a checkable round trip: seed, serve,
ingest, compare counts and amounts.

It also means the sim is a **presentation layer over the marketplace's own
truth**, which is the honest model — a real marketplace has its own database.

*The thing to not get wrong:* the seeded rows and the ingested rows land in the
same tables. They are told apart by `raw_payload.source` — `marketplace_seed`
for what the builder wrote, `marketplace_api` for what the connector ingested
— exactly as Phase 3's handoff describes one table holding both sources.
A3 therefore ingests into a **separate tenant** from the one the seed wrote,
so a count comparison is meaningful rather than circular.

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

4 sellers, 12 orders, ~25 order items, USD and EUR, one FX order, one `DELAYED`
order. Small enough that "reconciles perfectly by hand" is literally true —
`validation.md` §2 tabulates every expected entitlement, and that table is the
hand-check. Faults sit on top of those 12 orders.

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
