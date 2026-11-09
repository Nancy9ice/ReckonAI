# Matching and exception classification — validation

> How each exit criterion was proven. Figures are measured, not estimated.
>
> Suite at the end of this block: **1527 passed**, `uv run mypy` clean over
> `src/reckonai/engine` and `src/reckonai/recon`.

## 1. Phase 11's exit criteria

| Criterion | Proof | Result |
|---|---|---|
| Every fault in the manifest is detected and correctly classified | `tests/test_recon_gate.py`, over the seeded tenant, parameterised per fault | **Pass**, 6 of 6 |
| **The clean baseline produces zero exceptions** | Same module, over the 15 clean purchase units including ORD-0005 and ORD-0008 | **Pass**, zero |
| A delayed event is classified as delayed, not as a discrepancy | F5 reaches `awaiting_evidence` and `is_exception` is False; ORD-0005 and ORD-0008 produce nothing | **Pass** |
| Each exception row carries financial impact and the ids of its evidence | Per-fault impact against the manifest's own figure, plus every cited reference resolved against the database | **Pass** |

Counted as a whole: **6 findings, 5 exceptions**, matching
`manifest.FAULTS` and `manifest.EXCEPTIONS`. Counted rather than filtered, so
an extra finding anywhere fails even where a per-fault test would tolerate it.

## 2. The two false-positive traps

| Trap | Guard | Result |
|---|---|---|
| FX rounding | Compare only reported figures; the converted total that disagrees is never produced | **Pass.** ORD-0008 yields nothing |
| Held funds under `DELAYED` | `funds_held`, `release_requested`, `disbursement_pending` are healthy | **Pass**, but see below |

**The FX guard is not a tolerance.** `requirements.md` D4a: the one-cent
artefact only arises from a sum-of-legs against a once-converted total, and
that total is a recomputation D3 forbids. ORD-0008 is clean because the
arithmetic is absent. T2 remains implemented and tested with no caller.

**The held-funds guard is proven in unit tests, not by the gate.** Measured:
deleting `funds_held` from `HEALTHY_HELD_STATES` does **not** fail the gate,
because ORD-0005's ledger credits its full 180.00 EUR entitlement and the
residual is zero. The same deletion fails three unit tests, so the guard
works; `test_the_held_funds_order_is_clean_for_the_reason_we_think` pins the
facts so the clean result is not mistaken for coverage. D4b, and a recorded
limitation.

## 3. Per-fault detection

Every row produced by the classifier, against the manifest's independently
stated figure.

| Fault | Order | Expected | Produced | Impact | Agrees |
|---|---|---|---|---|---|
| F1 | ORD-0013 | `missing_ledger_adjustment` | same | 27.00 USD | yes |
| F2 | ORD-0014 | `commission_mismatch` | same | 10.00 USD | yes |
| F3 | ORD-0015 | `fx_discrepancy` | same | 32.16 USD | yes, **after correcting the manifest** |
| F4 | ORD-0016 | `duplicate_ledger_entry` | same | 90.00 USD | yes |
| F5 | ORD-0017 | `awaiting_evidence` | same | 0.00 USD | yes, and not an exception |
| F6 | ORD-0018 | `disbursement_mismatch` | same | 15.00 EUR | yes, **after correcting the manifest** |

F3 and F6 are the two corrections in `requirements.md` D11. The manifest's
figures described no record in the dataset; the fixtures, the seed and the
capture block's hand-check all agreed against it.

## 4. Tolerance decisions, each exercised

Each rule passes at its stated bound and fails one unit outside it. A rule
with no failing case is a rule that was never exercised.

- **T1** zero tolerance: a one-cent same-currency difference **is** reported.
- **T2** one minor unit per leg: ORD-0008's arithmetic clean at two legs, two
  cents at the bound, three cents reported. No caller (D4a).
- **T3** held funds: all three held states healthy, `disbursement_failed` and
  an unknown state not.
- **T4** absent is not zero: `require_stated` raises `AmountNotStated`, and a
  **measured** `0.00` passes through - the two stay different claims in both
  directions.

## 5. Engine refusals, each mapped

All nine `EngineError` subclasses are enumerated off the package by
`tests/test_recon_vocabulary.py`, so one added later appears without anyone
remembering. Each reaches a named code; an unmapped subclass raises
`TypeError` rather than defaulting.

`CaptureNotSupplied` propagates and is asserted to be absent from the mapping
table, so the test is proving the re-raise rather than an accidental omission.

## 6. The gate, seen failing

Both perturbations were run and reverted.

| Perturbation | Result |
|---|---|
| T1 widened to 100.00 | **5 failures** - F2 and F3 undetected, the total count wrong |
| `funds_held` removed from the healthy set | Gate still passes; **3 unit tests fail**. This is what D4b records |

## 7. Gates that stay green

- `uv run mypy` clean, `src/reckonai/recon/` inside `[tool.mypy]` files.
- `tests/test_money_purity.py` watches `recon` in both lists: no `float`, no
  `round`, no SQLAlchemy, no HTTP client, no `reckonai.db`, no clock read.
- Full suite green in collection order, 1527 tests. `test_recon_gate.py`
  commits nothing - it seeds into the rolled-back `session` fixture - so the
  suite's order-dependence (Block A debt 4) is untouched and randomisation
  stays off.
- `reckonai.reconcile` is **not** in the pure packages, deliberately: it holds
  the session. It is in the float gate's money path.

## 8. Contract frozen for Block F

`Finding.as_dict()` pinned against a literal payload. Amounts are strings with
their currency and always quantized, so `0.5` cannot reach the dashboard as
something different from `0.50`, and no client-side arithmetic is possible
from the shape. A field rename fails the test rather than reshaping Block F
silently.

## 9. Corrections this block made to earlier documents

1. **`reckonai.sim.manifest`** - F3's impact 5.05 EUR → **32.16 USD**, F6's
   18.00 → **15.00 EUR**, both with their descriptions, each carrying a dated
   comment explaining what was stale and why. `requirements.md` D11.
2. **`specs/2026-11-09-paypal-capture/validation.md` §2** - ORD-0009's "open
   dispute" note removed. No fixture has a `PENDING` capture or a missing
   breakdown; ORD-0009 is `COMPLETED` with its breakdown intact and a
   `HIGH_RISK` indicator only.
3. **Block A `validation.md` debt 1 discharged** - the vocabulary is
   `reckonai.recon.vocabulary.ExceptionCode`, pinned to the manifest's six
   strings by test.
4. **Block A debt 3 decided** rather than inherited - declined as a seeded
   order, taken as a rule. `requirements.md` D7.

## 10. Not proven here, and who proves it

- That findings **persist** - Block D owns the table (D8); Block F's dashboard
  depends on it.
- That the manifest **scores** - Block G's eval harness.
- That a real disputed capture classifies correctly - no such fixture exists;
  D7's rule is proven on a hand-built one.
- That an investigation cites these findings correctly - Block E.
- The held-funds guard at database level (D4b).

## Related

- `requirements.md` — the decisions these checks enforce
- [[roadmap]] Phase 11 — the criteria, quoted rather than reinterpreted
- `specs/2026-11-09-paypal-capture/validation.md` §2 — the hand-check figures
