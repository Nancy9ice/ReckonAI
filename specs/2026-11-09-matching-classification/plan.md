# Matching and exception classification — task groups

> Decisions in `requirements.md`; proof in `validation.md`.
>
> **This is the plan as written before the work, kept as the record of the
> intended shape.** It is not maintained against what was built - that is what
> `validation.md` is for, and the replan's R1 drops `plan.md` for exactly this
> reason. Where the two disagree, `validation.md` is right. The notable
> divergences: a foreign-currency ledger with no reported settlement is
> *reported* rather than passed over (group 4), the block took six commits
> rather than R2's one because each group was validated and committed
> separately, and `reckonai/reconcile.py` was added in group 5 so the query
> layer was not test-only code.

## 1. Exception vocabulary · ~30 min

`src/reckonai/recon/__init__.py`, `vocabulary.py`.

`ExceptionCode(StrEnum)` — the manifest's six, plus `unresolvable_commission_rule`,
`mixed_currency_order`, `refund_exceeds_share`, `refund_unattributable`,
`duplicate_provider_record`. `EvidenceBasis(StrEnum)` — `provider_reported`,
`derived_linkage`. Docstring on each member saying what it claims; nothing is
a synonym of another.

**Check:** a test asserts every string constant in `reckonai.sim.manifest` is an
`ExceptionCode` member, and that the enum shares no member name with
`RECONCILIATION_STATES` except `awaiting_evidence`.

## 2. Matching · ~45 min

`matcher.py`. Protocol inputs, no ORM.

Keys: order ↔ `purchase_unit.reference_id`; capture and refund by external id;
seller by `merchant_id`; ledger entries grouped by `(order, entry_category)`
for presence checks. Refund → capture through the derived HATEOAS linkage,
returning `EvidenceBasis.derived_linkage` with the pair.

Returns a `MatchSet`: matched pairs, marketplace-only, provider-only. Being
unmatched is **not** a verdict (P4) — group 4 decides what each means.

**Check:** unit tests for each key, including an unmatched record on both sides
and a refund matched only through the derived linkage.

## 3. Tolerance · ~30 min

`tolerance.py`. The four D4 rules as named functions, one per rule, each
docstring citing its reason.

`compare_exact()` (T1), `compare_converted_total(legs)` (T2, one minor unit per
leg), `is_healthy_disbursement(state)` (T3), `absent_is_not_zero()` (T4) —
which refuses a comparison against a missing amount rather than defaulting it.

**Check:** T2 passes on ORD-0008's 119.17-versus-119.18 and fails at two cents;
T4 raises rather than returning a difference; T3 returns healthy for all three
held states.

## 4. Classification · ~90 min

`classifier.py`. The block's substance.

Per order: build the expected entitlement from the engine, compare against the
marketplace ledger and against PayPal's reported `net_amount`,
`platform_fee.amount` and `target_amount` (D3). Emit zero or more `Finding`
frozen dataclasses — code, financial impact as `Money`, evidence ids,
`evidence_basis`, the reconciliation state it implies.

Then: map every `EngineError` to its code per D5, re-raising
`CaptureNotSupplied`; return `awaiting_evidence` where a marketplace refund has
no provider counterpart (R10) and where a capture carries no breakdown (D7);
return nothing at all for a healthy held-funds order.

Take the running commission from engine state, never from summed reversals (D6).

**Check:** one unit test per `ExceptionCode`, plus a test per manifest fault on
hand-built rows, plus the two non-fault shapes and the hand-built
`PENDING`-no-breakdown capture.

## 5. The false-positive gate, database-backed · ~45 min

`tests/test_recon_gate.py`. Skips without `DATABASE_URL`, as the suite already
does. Name it so it collects between `test_migrations` and `test_sim_api`, and
read nothing it does not commit — debt 4's order-dependence still stands, and
this block does not enable randomisation.

Runs the classifier over the whole seeded tenant and asserts, in one place:

1. All six manifest faults detected, each with the manifest's
   `expected_classification` and its `evidence_refs` cited.
2. **Exactly zero findings on the 15 clean purchase units**, ORD-0005 and
   ORD-0008 included. The hardest criterion.
3. Financial impact on each finding matches the manifest's stated figure.

**Check:** it passes, and it fails when a tolerance is widened or a fault
removed — verified by perturbing both.

## 6. Contract freeze and documents · ~30 min

Extend `tests/test_money_purity.py` to `recon/`; widen `[tool.mypy]` to
`src/reckonai/recon/`; `uv run mypy` clean.

Freeze the dashboard's JSON contract: the `Finding` serialization, amounts as
`{"amount": "...", "currency": "..."}`, pinned by a test against a literal
expected payload so a field rename fails rather than silently reshapes Block F.

Fix `specs/2026-11-09-paypal-capture/validation.md` §2's stale "open dispute"
note on ORD-0009 (D7). Write `validation.md`. Update `CLAUDE.md` status and
the replan's §State to point at Block D, and record D8's table as Block D's.
