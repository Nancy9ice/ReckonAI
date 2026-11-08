# Block A — plan

> Task groups, in order. Each group is written whole — implementation and tests
> in one pass, per the replan's R2 — and the groups run straight through
> without pausing between them. Commits at the ends of groups 2, 3 and 5.
>
> Decisions are in `requirements.md`; merge conditions are in `validation.md`.

## Group 1 — clear the deck · ~15 min

1. Delete `GET /money/example` from `src/reckonai/app.py`, its entry in the
   service index response, and its route tests in `tests/test_money_json.py`.
   Keep that file's non-route serialization tests — they still pin that `Money`
   crosses JSON as strings.
2. Add `marketplace_base_url: str | None = None` to `Settings`, with a comment
   saying why it has no default (a default would let a deployed connector
   silently read a developer's host).
3. Confirm `uv sync --frozen` and the suite are green before any new code, so a
   later failure is attributable.

## Group 2 — sim API and clean seed (Phase 4) · ~2 h

4. `src/reckonai/sim/seed.py` — the builder. Writes the Phase 2-3 tables for
   one tenant from literal data: 4 sellers, 12 orders, ~25 items, commission
   rules, payments, captures, refunds, platform fees, ledger entries,
   disbursements, one `fx_conversion`.
   - **Flush in dependency order.** No `relationship()` is declared anywhere in
     this project, so SQLAlchemy's unit of work has no mapper dependency to
     sort on and will insert a child before its parent (Phase 3's finding).
     Tenant, sellers, orders, items, then events.
   - **Every financial row needs its `raw_payload` row first**, `NOT NULL`,
     referenced compositely with `tenant_id`.
   - `seller_ledger_entry.amount` is **unsigned**, `CHECK (> 0)`, and needs a
     `transaction_type` of `debit` or `credit`.
   - `fx_conversion.fee` stays `NULL`. `disbursement.released_at` gets no
     default (P3).
   - `ORD-0001` is the [[mission]] worked example, seeded exactly: $160, two
     sellers, 10% → A $90, B $54, commission $16.
   - The `DELAYED` order sits at `funds_held`, no release requested.
   - The FX order's recorded settlement is PayPal's cent-rounded figure, off by
     one cent from gross times rate. Both of these are **clean** (D4).
5. `seed.verify()` — D6's consistency check. Order totals against item sums,
   line totals against unit price times quantity, ledger positions against
   entitlements, commissions against the applicable rule. Raises, naming the
   row.
6. `src/reckonai/sim/router.py` — the endpoints, reading those tables back and
   renaming fields outward per D3: sellers, orders, order items, commission
   rules, payments, refunds, disputes, ledger entries, expected entitlements,
   release records. Money as `{"gross_amount": "...", "ccy": "..."}`.
7. Mount under `/sim` in `app.py` and list it in the service index.
8. Tests: every endpoint's shape and field names; `seed.verify()` passing on
   the clean dataset and *failing* when a total is perturbed (a check whose
   own setup cannot make it fail is not a check — Phase 3's first finding);
   the D1 import-isolation test; `/money/example` returning 404.

**Commit:** `feat(sim): simulated marketplace API over a consistent seed`

## Group 3 — faults and manifest (Phase 5) · ~1 h

9. Extend the builder with D4's six faults, each behind an explicit id, each
   excluded from `verify()` by that id rather than by a heuristic.
10. `src/reckonai/sim/manifest.py` plus a checked-in manifest document: one
    entry per fault carrying its id, the record ids it touches, the fault
    description, the expected classification, and the expected financial
    impact as a string amount with its currency.
11. Tests: clean and faulty records coexist in one dataset; `verify()` still
    passes over the clean subset with the faults present; the manifest's record
    ids all resolve; every manifest expected classification is a member of the
    Block C vocabulary that exists so far.

**Commit:** `feat(sim): six seeded faults and the eval manifest`

## Group 4 — connector and mapping (Phase 6) · ~2 h

12. `connector_mapping` model — D7's columns — added to
    `reckonai/models/__init__.py` (a module that is never imported is absent
    from `Base.metadata`, and autogenerate reads its table as one to **drop**).
    One reversible Alembic revision.
13. `mapping_version` on the canonical records the connector writes.
14. `src/reckonai/connector/mapping.py` — the Pydantic mapping schema, the
    closed transform set, and §9.2 passes 1 and 2 with the curated synonym
    table checked in.
15. `src/reckonai/connector/validate.py` — §9.4's six checks. Money without a
    resolved currency is a hard failure. Failure blocks activation and names
    the field; no half-activation.
16. `src/reckonai/connector/client.py` — `MarketplaceClient`, httpx against
    `MARKETPLACE_BASE_URL`.
17. `src/reckonai/connector/ingest.py` — normalization into the Phase 2-3
    tables. Raw payload written first, `source="marketplace_api"`. Upsert on
    `UNIQUE (tenant_id, external_id)`; never overwrite a populated value with
    `NULL`. Quarantine on a missing required field; never default money to
    zero.
18. The checked-in active mapping document for the sim's field names.

**Commit:** `feat(connector): field mapping, validation gate and ingestion`

## Group 5 — prove it and record it · ~45 min

19. Tests: the full dataset ingests with zero loss, into a second tenant (D2)
    so the count comparison is not circular; a missing required mapping fails
    at config-validation time, not at ingestion; a second ingestion run adds no
    rows; the R3 guard — no canonical field maps from an identically-named
    source field; a money mapping missing its currency path is rejected; a
    quarantined record is visible and countable.
20. Run the whole suite against the compose database with `DATABASE_URL`
    exported. Operator runs it and pastes failures only.
21. `alembic upgrade head` then `downgrade` then `upgrade` on a clean database,
    to prove the revision reverses.
22. Fill in `validation.md` with the measured results, including §2's
    hand-check table as actually produced.
23. `/code-review medium` is **not** run on this block — the replan reserves it
    for Blocks B and C.

**Commit:** folded into group 4's, or `test(block-a): ...` if group 4 is
already pushed.

## Deliberately not in this plan

PayPal anything (Block D). Matching, classification, `awaiting_evidence`
*transitions* — F5 is seeded here but classified in Block C. The Haiku mapping
pass (R11). Webhook emission (R8). A deploy — the replan allows one, in Block F.
