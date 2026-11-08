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
| All endpoints return deliberately *different* field names from canonical | `tests/test_sim_api.py` asserts the exact outward key set per endpoint, and that it shares no name with the canonical column set | **Pass.** 10 endpoints, key set asserted whole so an extra leaked key fails too |
| The seed dataset reconciles perfectly by hand | §2's table below, computed by hand and pinned by `seed.verify()` as a test | **Pass.** All 12 orders tabulated in §2; `verify()` recomputes through `resolve_commission_rule` |
| `GET /money/example` is gone, and the service index no longer lists it | A request returns 404; the index response's route list is asserted not to contain it | **Pass.** `test_the_money_probe_is_gone` checks both halves |

### Phase 5

| Criterion | Proof | Result |
|---|---|---|
| A manifest records every seeded fault and its expected classification | The manifest has one entry per fault; a test asserts every fault id the builder can seed appears in it, and no manifest entry names a fault the builder does not seed | **Pass.** 6 faults, set equality both ways |
| Clean records and faulty records coexist in the same dataset | One builder run produces both; `verify()` passes over the clean subset with the faults present | **Pass.** `data.FULL` is 18 orders; exclusion proven load-bearing by `test_a_faulty_order_would_fail_verify_if_it_were_not_excluded` |
| (The duplicate-event criterion is dropped with the emitter, R8) | Deferred to Block D's `processed_events` constraint | n/a |

### Phase 6

| Criterion | Proof | Result |
|---|---|---|
| The full seeded dataset ingests with zero data loss | Ingest into a second tenant, then compare row counts per table and the summed amount per currency per table against the seeded tenant. Equal, exactly, as `Decimal` | **Pass.** `retrieved == written + quarantined`, 0 quarantined; counts and per-currency `Money` sums equal |
| A missing required mapping fails loudly at config-validation time, not at ingestion time | A mapping with a required field removed is rejected by `validate_document()` naming that field, with no records fetched | **Pass.** `required_unmapped`, naming `placed_at`; all six §9.4 checks have a failing-direction test |
| Re-running ingestion is idempotent | Ingest twice; row counts and amounts are unchanged after the second run | **Pass.** Across 7 tables including `raw_payload`; first run inserts, second inserts nothing |

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

Rates: tenant default 10%, Citrine 8%, Dunlin 0% (stated, not defaulted — the
seller was not onboarded for `PARTNER_FEE`, so PayPal cannot take a platform
fee on its orders at all).

| Order | Seller(s) | Ccy | Total | Commission | Entitlement | Notes |
|---|---|---|---|---|---|---|
| ORD-0001 | aurora, bellweather | USD | 160.00 | 16.00 | aurora 90.00; bellweather 54.00 | mission worked example |
| ORD-0002 | aurora | USD | 50.00 | 5.00 | aurora 45.00 | |
| ORD-0003 | bellweather | USD | 120.00 | 12.00 | bellweather 108.00 | |
| ORD-0004 | dunlin | USD | 80.00 | 0.00 | dunlin 80.00 | no platform fee row at all |
| ORD-0005 | citrine | EUR | 200.00 | 16.00 | citrine 184.00 | **non-fault 1**: DELAYED, `funds_held` |
| ORD-0006 | aurora, bellweather | USD | 75.00 | 7.50 | aurora 27.00; bellweather 40.50 | |
| ORD-0007 | aurora | USD | 33.33 | 3.33 | aurora 30.00 | 3.3330 quantizes to 3.33 |
| ORD-0008 | citrine | USD | 150.00 | 12.00 | citrine 138.00 | **non-fault 2**: FX 0.9183 → 126.73 EUR |
| ORD-0009 | bellweather | USD | 59.97 | 6.00 | bellweather 53.97 | 5.9970 → 6.00; open dispute |
| ORD-0010 | dunlin | USD | 45.00 | 0.00 | dunlin 45.00 | |
| ORD-0011 | aurora | USD | 70.00 | 7.00 | aurora 63.00 | 20.00 refund, fully in ledger |
| ORD-0012 | citrine | EUR | 120.00 | 9.60 | citrine 110.40 | |

**ORD-0008, the cent-rounding artefact, in full.** Citrine's share is
50.00 − 4.00 = 46.00 and 100.00 − 8.00 = 92.00, so 138.00 USD. At PayPal's
reported rate of 0.9183:

| | Arithmetic | Result |
|---|---|---|
| Converting the total once | 138.00 × 0.9183 = 126.7254 | **126.73** |
| Converting each line | 46.00 × 0.9183 = 42.2418 → 42.24; 92.00 × 0.9183 = 84.4836 → 84.48 | **126.72** |

One cent apart, and **neither figure is wrong.** PayPal reported 126.73, so
that is the settlement. A reconciler that recomputes per line gets 126.72 and
reports a discrepancy that does not exist. F3's fault is 5.05 EUR on the same
rate — 500 times the artefact — so no threshold can confuse them, and
`test_f3_settlement_does_not_follow_from_its_rate` asserts that ratio rather
than assuming it.

**Totals to carry forward.** Block B and Block C both read these.

Net seller positions across the clean set, refunds and commission reversals
included:

| Seller | Currency | Position | Derivation |
|---|---|---|---|
| aurora | USD | 237.00 | 90.00 + 45.00 + 27.00 + 30.00 + (63.00 − 20.00 + 2.00) |
| bellweather | USD | 256.47 | 54.00 + 108.00 + 40.50 + 53.97 |
| citrine | USD | 138.00 | ORD-0008, settled as EUR 126.73 |
| citrine | EUR | 294.40 | 184.00 + 110.40 |
| dunlin | USD | 125.00 | 80.00 + 45.00 |

Total expected commission: **USD 66.83**, **EUR 25.60**.

These live in `data.EXPECTED_POSITIONS` and
`data.EXPECTED_COMMISSION_TOTALS` as hand-written literals, are recomputed from
the rows by `seed.verify()`, and are asserted against this table by
`tests/test_sim_seed.py`. Three independent derivations that have to agree.

### The six faults

| # | Order | Fault | Expected | Impact |
|---|---|---|---|---|
| F1 | ORD-0013 | 30.00 refund, no ledger entries | `missing_ledger_adjustment` | USD 27.00 |
| F2 | ORD-0014 | retained 30.00 where the rule says 20.00 | `commission_mismatch` | USD 10.00 |
| F3 | ORD-0015 | settlement 248.40 where rate 0.9183 implies 253.45 | `fx_discrepancy` | EUR 5.05 |
| F4 | ORD-0016 | one 90.00 sale credited twice | `duplicate_ledger_entry` | USD 90.00 |
| F5 | ORD-0017 | marketplace refund correct, PayPal's record absent | `awaiting_evidence` — **not an exception** | USD 0.00 |
| F6 | ORD-0018 | released 120.00 against an obligation of 138.00 | `disbursement_mismatch` | EUR 18.00 |

## 3. The guards, each of which must fail when it should

A check whose own setup performs the thing it checks cannot fail — Phase 3 shipped
one and it passed with the import commented out. So each guard below is
verified in both directions.

| Guard | Passes on | Must fail on | Result |
|---|---|---|---|
| `seed.verify()` order-total consistency (D6) | the clean dataset | total perturbed to 160.01 | **Both.** `SeedInconsistency`, naming ORD-0001 |
| `seed.verify()` line-total consistency | the clean dataset | a line total contradicting unit price × quantity | **Both.** Names the item |
| `seed.verify()` entitlement consistency | the clean dataset | a stated entitlement of 91.00 on a 10% rule over 100.00 | **Both.** Names the seller |
| `seed.verify()` consults the real resolver | the clean dataset | Citrine's 8% rule deleted, so the resolver falls back to 10% | **Both.** Proves it reads the database, not the literals twice |
| `seed.verify()` ledger position | the clean dataset | one commission entry flipped to `credit` | **Both.** The invariant the per-order check cannot see |
| R3's mapping guard | the real mapping | an entry whose source path leaf equals its canonical name | **Both.** `identity_mapping`, in the validator *and* in `test_sim_fields.py` |
| §9.3 money gate (D8) | the real mapping | a money field mapped as a plain `FieldMapping` | **Both.** `money_without_currency`; the two-path form is otherwise unexpressible |
| §9.4 sample gate | real-shaped samples | **no samples at all** | **Both.** `insufficient_samples` — a gate over an empty list is vacuously true, which is worse than failing |
| Outward-name disjointness | the sim's names | a name colliding with a canonical column *or composite* | **Both.** The composite half caught `gross_amount`, renamed to `order_gross` |
| Quarantine (D11) | a complete record | a required field absent; an amount with no currency; an unresolved reference | **Both, three ways.** One quarantine row each, zero canonical rows, nothing defaulted |

## 4. The two deliberate non-faults

Neither is in the manifest, and both must be in the dataset. Block C will prove
they are not flagged; this block proves they are **present and clean**.

| Non-fault | Proof | Result |
|---|---|---|
| `DELAYED` order at `funds_held`, no release requested | A disbursement row exists with that state, no `release_requested` predecessor, `released_at` `NULL`, and `verify()` passes with it | **Pass.** ORD-0005; also asserted over HTTP, where `released_on` is `null` |
| PayPal's cent-rounding on a converted settlement | Per-line conversion sums to 126.72 where the total converts to 126.73, one cent apart, and `verify()` passes with it | **Pass.** ORD-0008; §2 tabulates both figures |
| Neither is in the manifest | A manifest entry for either would instruct the eval to expect an exception | **Pass.** `test_the_two_non_faults_are_not_in_the_manifest` |

## 5. Money and principle checks

| Check | Proof | Result |
|---|---|---|
| No `float` anywhere in new code | The existing purity gate plus the `decimal` transform refusing a `float` outright | **Pass.** `test_the_decimal_transform_refuses_a_float` |
| Every amount leaves as a JSON string with its currency | `strict_json.decode_strictly` over all 10 endpoints, plus an `isinstance` check for the integer case `parse_float` cannot see | **Pass.** This is the property the removed probe used to carry, now over real endpoints |
| An amount key never appears without a currency key | Asserted per record across every endpoint | **Pass.** §9.3's hard failure, checked at the source |
| `fx_conversion.fee` is `NULL`, never `0` (P3) | Asserted on the seeded rows; the `CHECK` refuses a zero independently | **Pass.** Both the clean and the F3 conversion |
| `disbursement.released_at` carries no ingestion time (P3) | Asserted `NULL` on the `funds_held` row **and** on F6's completed release | **Pass.** No default, fault or not |
| A capture fee is never defaulted to zero | Every seeded capture states a fee and none is zero | **Pass.** `NULL` stays available for "unknown" |
| No platform fee row for a seller without `PARTNER_FEE` | An absence, not a zero-amount row | **Pass.** A zero row would assert a measured fee of nothing |
| Every financial row links to its `raw_payload` row (P7) | Zero orphans across all seven tables the builder writes, for both tenants | **Pass.** |
| Every new table carries `tenant_id` | The existing schema gate, which covers the two new tables automatically | **Pass.** 207 checks in the two registry modules |
| No emojis in any new file | `tests/test_conventions.py`, already in place | **Pass.** |
| The synthetic PayPal side announces itself (D12) | Every `paypal`-source payload body carries `_synthetic` and `replaced_by` | **Pass.** Block D's debt is recorded in §8 |

## 6. Mechanical gates

| Gate | Command | Result |
|---|---|---|
| Full suite, compose database, `DATABASE_URL` exported | `uv run pytest` | **1248 passed, 0 skipped** (1117 at Phase 3; 1107 after the probe's removal) |
| Lint | `uv run ruff check` | **Pass.** |
| Lockfile committed with its dependency change | `git diff --exit-code uv.lock` in CI | **Pass.** `uv.lock` updated for `httpx2` moving to the runtime group |
| New model module registered | `tests/test_model_registry.py` | **Pass.** `connector.py` added to `models/__init__.py` and `expected_schema.py` |
| Migration reverses | `alembic upgrade head`, `downgrade -1`, `upgrade head` | **Pass.** Revision `7a1b6f633076` |
| Autogenerate is empty after upgrade | `alembic check` | **Pass.** "No new upgrade operations detected." |
| CI green on the branch | GitHub Actions | Pending push |

Per-module counts, for the record:

| Module | Tests |
|---|---|
| `test_sim_seed.py` | 22 |
| `test_sim_api.py` | 29 |
| `test_sim_fields.py` | 13 |
| `test_sim_faults.py` | 18 |
| `test_connector_mapping.py` | 25 |
| `test_connector_ingest.py` | 14 |

These are the gates this project actually has. `ruff format --check` and `mypy`
are **not** among them — an earlier draft of this document listed both, which
would have been a merge condition no one could meet: `ruff format` reports 30
pre-existing files it would rewrite, and `mypy` is not a dependency. `ruff`
lints with `E`, `F`, `I`, `UP` and `B` selected, and `scripts/` is excluded
because those PEP 723 probes produced the committed evidence in
`specs/paypal-fields.md`.

## 7. Merge conditions

1. Every table in §1, §3, §4, §5 and §6 has a result — **met**, no empty cells.
2. §2's hand-check table complete for all 12 orders — **met**.
3. The commits of `plan.md` present and self-contained — **met**: `e352200`
   (group 1), `9b397cd` (group 2), `5cd585f` (group 3), `a1f3413` (group 4),
   plus this document.
4. `requirements.md` §3's handoff accurate — **met**; amended while building,
   see §9.
5. No deploy — **met**. The replan allows one, in Block F.
6. CI green on the branch — pending push.

## 9. Corrections made while building

Recorded because each one was a decision in `requirements.md` that turned out
to be wrong against the actual schema, and a spec that quietly disagrees with
the code is worse than one that records the correction.

| Decision | What was wrong | Resolution |
|---|---|---|
| D1 | "a test asserting `reckonai.sim` imports nothing from `reckonai.models`" — unimplementable, since the seed builder's job is writing those tables, and never the real risk | The guard moved to the outward field names, which is where the risk lives. Stronger: it fails on the mistake rather than on a proxy for it |
| D2 | `source="marketplace_seed"` / `"marketplace_api"` — `PAYLOAD_SOURCES` is a `CHECK`-constrained `("paypal", "marketplace")`, so the database refuses both | Both sides use `"marketplace"`; a separate tenant and `raw_payload.endpoint` (`NULL` for a seeded row) separate them |
| D5 | Faults as perturbations of the 12 clean orders | Faults get their own orders, ORD-0013 to ORD-0018, so the hand-checked baseline stays intact |
| D13 (new) | Not addressed: §9.1 requires every canonical record to carry its mapping version | Carried on `raw_payload`, one column rather than nine, reachable from every financial record through a `NOT NULL` link |
| D12 (new) | A sequencing gap in the replan: Block C compares against PayPal's figures on Day 1, but Block D captures them on Day 2 | The seed writes a synthetic PayPal side, every row marked `_synthetic`. Block D replaces them |
| §6 gates | Listed `ruff format --check` and `mypy src`, neither of which this project has | Replaced with the gates CI actually runs |
| tech-stack §1 | Locked no HTTP client, although the connector needs one at runtime | Amended, dated 2026-11-08, adding `httpx2` — the fork this project already used for tests, rather than a second HTTP stack |

### Two tests that could not fail, found by mutation testing

The suite was audited by deliberately breaking each guarantee and checking that
something failed. **24 mutations, 22 caught immediately, 2 missed** — and the
two misses were the same defect in different places: a test asserting what the
data happened to be rather than what the code must do.

| Missed mutation | Why nothing failed | Fix |
|---|---|---|
| `paypal_fee` defaulted to `Money("0")` when absent | Every seeded order *states* a fee, so the builder's `else None` branch was never reached. `test_a_capture_fee_is_never_defaulted_to_zero` asserted "no fee is zero", which was true of the dataset and said nothing about the code | Split in two. `test_an_unstated_capture_fee_stays_null_and_is_not_defaulted` seeds an order with `paypal_fee=None` and asserts `NULL`, plus `net_amount is None` — a net derived from a defaulted zero would be the gross wearing a fee's name |
| `accounts_for_everything()` → `return True` | Every call site was a case where the identity genuinely holds, so the method was both the claim and its own witness — the Phase 3 gate's failure shape exactly | `test_the_accounting_identity_can_actually_fail` exercises it on a lossy and a double-counted result, and `test_zero_data_loss_measured_from_rows_not_from_counters` re-proves the claim from database rows against what the sim served, using none of the counters |

Both mutations are now caught. The remaining 22 were caught by between 1 and 6
tests each; `verify()` reduced to a no-op fails 6.

### Verified independently of the test suite

Run against the deployed-shape app on a real port, with arithmetic recomputed
from the HTTP responses rather than from `data.py`:

| Check | Result |
|---|---|
| `GET /` advertises `/sim/marketplace`; `GET /money/example` | 200; **404** |
| [[mission]]'s worked example, recomputed from `/order-items` at 10% | aurora 90.00, bellweather 54.00, commission 16.00, total 160.00 — **matches** |
| Any money key carrying a bare JSON number, by regex over raw bytes across all 10 endpoints | **none** |
| Any outward key colliding with one of the 100 canonical column names | **none** |
| ORD-0008's artefact, recomputed at rate 0.9183 | total-once 126.73, per-line 126.72, difference **0.01** |
| Each of the six faults visible from the HTTP surface | **all six**; F1's clean counterpart still carries its 2 entries, so F1 is a fault and not a broken builder |
| Both non-faults indistinguishable from healthy records | **confirmed**; `funds_held` with `released_on: null` |

### Two bugs the checks caught, worth keeping because each argues for a check:

**The sim served PayPal's refunds as its own.** `/marketplace/refunds`
returned five records where the marketplace knows three, and the connector
ingested PayPal's two as marketplace refunds. One table per record type holds
both sources, and the query did not filter. Caught by the row-count comparison
against the seeded tenant — which is the argument for comparing against an
independent figure rather than against `len()` of the same list.

**Two payload bodies hashed identically.** ORD-0003 and ORD-0008 both carry a
commission of 12.00 USD, so their platform-fee bodies were byte-identical and
the second insert violated `UNIQUE (tenant_id, source, source_record_type,
body_sha256)`. That constraint working correctly: Phase 3 built it so an
unchanged re-read stores nothing. Fixed by putting each record's own id in its
body, which a real provider response carries anyway.

## 8. Not proven here, and who proves it

- That the faults are **detected** — Block C, against this manifest.
- That the two non-faults are **not flagged** — Block C. This block only
  guarantees they are present and clean.
- That `awaiting_evidence` is reached for F5 — Block C.
- That the manifest scores — Block G's eval harness.

**Three debts this block hands on explicitly**, so none is discovered:

1. **`manifest.py`'s classification strings are not yet a shared vocabulary.**
   Block C owns the exception vocabulary; these six strings were written here
   because the manifest needs them, and reconciling the two is Block C's first
   task. They are deliberately not drawn from anything this block defines, so
   the manifest is not a restatement of the seeder.
2. **No `_synthetic` payload may survive into the demo tenant** once Block D's
   replay ingester has run (D12). That is this block's debt, named here rather
   than left for Block D to notice.
3. **An open dispute reverting its capture to `PENDING`** is a real
   false-positive trap — the capture loses its whole
   `seller_receivable_breakdown` — and is a good Block C case. It is
   deliberately *not* in the clean baseline, because D4 fixes the non-faults at
   two and adding a third silently would change what Phase 11's gate is
   measured against. Block C should take it or decline it deliberately.

## Related

- `requirements.md` — the decisions these checks enforce
- `plan.md` — the groups that produce them
- [[roadmap]] Phases 4, 5, 6 — the criteria restated in §1
- `specs/2026-11-08-replan/requirements.md` — Block A, R3, R4, R10
