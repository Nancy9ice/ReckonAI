# Block A — validation

> How each exit criterion of Phases 4, 5 and 6 is proven, and what must be true
> before this branch merges. Results are filled in as the block is built; an
> unfilled row is an unproven claim and blocks the merge.
>
> 2026-11-08.

## 1. The exit criteria, and what proves each

### Phase 4

| Criterion | Proof | Result |
|---|---|---|
| All endpoints return deliberately *different* field names from canonical | `tests/test_sim_api.py` asserts the exact outward key set per endpoint, and that it shares no name with the canonical column set | |
| The seed dataset reconciles perfectly by hand | §2's table below, computed by hand and pinned by `seed.verify()` as a test | |
| `GET /money/example` is gone, and the service index no longer lists it | A request returns 404; the index response's route list is asserted not to contain it | |

### Phase 5

| Criterion | Proof | Result |
|---|---|---|
| A manifest records every seeded fault and its expected classification | The manifest has one entry per fault; a test asserts every fault id the builder can seed appears in it, and no manifest entry names a fault the builder does not seed | |
| Clean records and faulty records coexist in the same dataset | One builder run produces both; `verify()` passes over the clean subset with the faults present | |
| (The duplicate-event criterion is dropped with the emitter, R8) | Deferred to Block D's `processed_events` constraint | n/a |

### Phase 6

| Criterion | Proof | Result |
|---|---|---|
| The full seeded dataset ingests with zero data loss | Ingest into a second tenant, then compare row counts per table and the summed amount per currency per table against the seeded tenant. Equal, exactly, as `Decimal` | |
| A missing required mapping fails loudly at config-validation time, not at ingestion time | A mapping with a required field removed is rejected by `validate()` naming that field; a test asserts no ingestion function is reachable with it | |
| Re-running ingestion is idempotent | Ingest twice; row counts and amounts are unchanged after the second run | |

## 2. The hand-check — the clean baseline, tabulated

This table is the "reconciles perfectly by hand" criterion. It is computed by
hand, written here first, and *then* asserted by `seed.verify()`. If the code
and this table disagree, the table is the authority until a decision says
otherwise.

`ORD-0001` is [[mission]]'s worked example and is fixed:

| | Gross | Commission at 10% | Seller entitlement |
|---|---|---|---|
| Seller A item | $100.00 | $10.00 | $90.00 |
| Seller B item | $60.00 | $6.00 | $54.00 |
| **Order total** | **$160.00** | **$16.00** | **$144.00** |

Expected PayPal platform fee on `ORD-0001`: $16.00 — the figure Block B's
expected commission is compared against, not a recomputation of gross times
rate.

The remaining 11 orders are tabulated the same way when seeded. The
multi-currency order and the `DELAYED` order each get their own sub-table, and
the FX order's row records both the arithmetic product of gross and rate **and**
PayPal's cent-rounded settlement, with the one-cent difference stated as
expected and clean.

| Order | Seller(s) | Currency | Total | Commission | Entitlement | Notes |
|---|---|---|---|---|---|---|
| ORD-0001 | A, B | USD | 160.00 | 16.00 | 144.00 | mission worked example |
| ... | | | | | | filled in on seeding |

**Totals to carry forward:** per-seller ledger position, per-currency order
total, total commission. Block B and Block C both read them.

## 3. The guards, each of which must fail when it should

A check whose own setup performs the thing it checks cannot fail — Phase 3 shipped
one and it passed with the import commented out. So each guard below is
verified in both directions.

| Guard | Passes on | Must fail on | Result |
|---|---|---|---|
| `seed.verify()` order-total consistency (D6) | the clean dataset | a test that perturbs one order total by one cent | |
| `seed.verify()` entitlement consistency | the clean dataset | a test that perturbs one ledger amount | |
| R3's mapping guard | the real mapping | a mapping entry whose source path's leaf name equals its canonical field name | |
| §9.3 money gate (D8) | the real mapping | a money entry with an amount path and no currency path | |
| D1 sim isolation | `reckonai.sim` as written | an added `from reckonai.models import ...` in the sim | |
| Quarantine (D11) | a complete record | a record with a required field absent — one quarantine row, zero canonical rows, no `0` written | |

## 4. The two deliberate non-faults

Neither is in the manifest, and both must be in the dataset. Block C will prove
they are not flagged; this block proves they are **present and clean**.

| Non-fault | Proof | Result |
|---|---|---|
| `DELAYED` order at `funds_held`, no release requested | A disbursement row exists with that state, no `release_requested` predecessor, `released_at` `NULL`, and `verify()` passes with it | |
| PayPal's cent-rounding on a converted settlement | The `fx_conversion` row's rate times gross differs from the recorded settlement by exactly one cent, and `verify()` passes with it | |

## 5. Money and principle checks

| Check | Proof | Result |
|---|---|---|
| No `float` anywhere in new code | `tests/test_money_purity.py` extended to the new modules | |
| Every amount carries its currency, over JSON as a string | `tests/test_sim_api.py` asserts every money key pair in every endpoint response | |
| `fx_conversion.fee` is `NULL`, never `0` (P3) | Asserted on the seeded row; the `CHECK` refuses a zero independently | |
| `disbursement.released_at` carries no ingestion time (P3) | Asserted `NULL` on the `funds_held` row | |
| Every financial row links to its `raw_payload` row (P7) | A query asserting zero orphans across all eight financial tables, for both tenants | |
| Every new table carries `tenant_id` and every new query filters by it | `tests/test_conventions.py` extended | |
| No emojis in any new file | `tests/test_conventions.py`, already in place | |

## 6. Mechanical gates

| Gate | Command | Result |
|---|---|---|
| Full suite, compose database, `DATABASE_URL` exported | `uv run pytest` | |
| Lint and format | `uv run ruff check` and `ruff format --check` | |
| Types | `uv run mypy src` | |
| New model module registered | `tests/test_model_registry.py` | |
| Migration reverses | `alembic upgrade head`, `downgrade -1`, `upgrade head` on a clean database | |
| Autogenerate is empty after upgrade | `alembic revision --autogenerate` produces no operations | |
| CI green on the branch | GitHub Actions | |

## 7. Merge conditions

All of the above filled in and passing, plus:

1. Every table in §1, §3, §4, §5 and §6 has a result. An empty cell blocks.
2. §2's hand-check table is complete for all 12 orders, not just `ORD-0001`.
3. The three commits of `plan.md` are present and each is self-contained.
4. `requirements.md` §3's handoff list is accurate — Block B can start from it
   without reading this block's code.
5. No deploy. The replan allows one deploy, in Block F.

## 8. Not proven here, and who proves it

- That the faults are **detected** — Block C, against this manifest.
- That the two non-faults are **not flagged** — Block C. This block only
  guarantees they are present and clean.
- That `awaiting_evidence` is reached for F5 — Block C.
- That the manifest scores — Block G's eval harness.

## Related

- `requirements.md` — the decisions these checks enforce
- `plan.md` — the groups that produce them
- [[roadmap]] Phases 4, 5, 6 — the criteria restated in §1
- `specs/2026-11-08-replan/requirements.md` — Block A, R3, R4, R10
