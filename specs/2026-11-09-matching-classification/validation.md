# Matching and exception classification — validation

> How each exit criterion is proven, and what blocks the merge. Filled in as
> the block is built; written now so the bar is set before the code exists.

## 1. Phase 11's exit criteria

| Criterion | Proof | Result |
|---|---|---|
| Every fault in the manifest is detected and correctly classified | `tests/test_recon_gate.py`, against `reckonai.sim.manifest` — code, evidence ids and financial impact | |
| **The clean baseline produces zero exceptions** | Same test, over the 15 clean purchase units including ORD-0005 and ORD-0008 | |
| A delayed event is classified as delayed, not as a discrepancy | T3 unit tests on all three held states, plus ORD-0005 inside the gate | |
| Each exception row carries financial impact and the ids of its evidence | The frozen `Finding` contract test, and the gate's per-fault impact assertion | |

## 2. The two false-positive traps

Both are gate conditions, not features, and neither may be dropped.

| Trap | How it is guarded | Proof |
|---|---|---|
| FX rounding | Compare against PayPal's reported figure; T2 allows one minor unit per conversion leg, only for sum-versus-total | ORD-0008 passes; the same test fails at two cents |
| Held funds under `DELAYED` | `funds_held`, `release_requested`, `disbursement_pending` are healthy; no release instruction is not a finding | ORD-0005 yields zero findings |

## 3. Per-fault detection

One row per manifest fault, filled with the classification actually produced.
A row disagreeing with the manifest fails the block — the manifest is ground
truth and is not edited to match the implementation.

| Fault | Order | Expected | Produced | Impact matches |
|---|---|---|---|---|
| F1 | ORD-0013 | `missing_ledger_adjustment` | | |
| F2 | ORD-0014 | `commission_mismatch` | | |
| F3 | ORD-0015 | `fx_discrepancy` | | |
| F4 | ORD-0016 | `duplicate_ledger_entry` | | |
| F5 | ORD-0017 | `awaiting_evidence` | | |
| F6 | ORD-0018 | `disbursement_mismatch` | | |

## 4. Tolerance decisions, each exercised

Each of D4's four rules has a test that passes at the stated bound and fails
one unit outside it. A rule with no failing case is a rule that was never
exercised.

- T1 zero tolerance: a one-cent same-currency difference **is** a finding.
- T2 one minor unit per leg: ORD-0008 clean, two cents flagged.
- T3 held funds: all three states healthy; `disbursement_failed` is not.
- T4 absent is not zero: comparing against a missing `paypal_fee` raises
  rather than returning a difference.

## 5. Engine errors, each mapped

Every `EngineError` subclass reaches its D5 code under test, and
`CaptureNotSupplied` propagates rather than being classified. A subclass with
no test is a path that fabricates or swallows on first contact with real data.

## 6. Gates that must stay green

- `uv run mypy` clean, with `src/reckonai/recon/` inside the scope.
- `tests/test_money_purity.py` watches `recon/`: no SQLAlchemy, no HTTP client,
  nothing from `reckonai.db` or `reckonai.models`, no clock read.
- `tests/test_model_registry.py` unaffected — this block adds no model module.
- Full suite green in collection order. Randomisation and `-n auto` stay off
  (Block A debt 4).
- `/code-review medium`, which the replan's R2 schedules for this block.

## 7. Contract frozen for Block F

The `Finding` JSON shape, pinned against a literal payload. Amounts cross as
`{"amount": "...", "currency": "..."}`; no client-side arithmetic is possible
from it. A field rename fails this test rather than reshaping Block F silently.

## 8. Not proven here, and who proves it

- That exception rows **persist** — Block D owns the table (D8), and Block F's
  dashboard depends on it.
- That the manifest **scores** — Block G's eval harness.
- That a real disputed capture classifies correctly — no such fixture exists;
  D7's rule is proven on a hand-built one, and the gap is a recorded
  limitation.
- That an investigation cites these findings correctly — Block E.

## 9. Corrections this block makes to earlier documents

- `specs/2026-11-09-paypal-capture/validation.md` §2 called ORD-0009 an "open
  dispute". No fixture has a `PENDING` capture or a missing breakdown; ORD-0009
  is `COMPLETED` with its breakdown intact and a `HIGH_RISK` indicator. Fixed.
- `specs/2026-11-08-block-a-marketplace-connector/validation.md` debt 1 is
  discharged: the vocabulary is `reckonai.recon.vocabulary.ExceptionCode`, and
  debt 3 is decided per D7 rather than left open.

## Related

- `requirements.md` — the decisions these checks enforce
- [[roadmap]] Phase 11 — the criteria, quoted above rather than reinterpreted
- `specs/2026-11-09-paypal-capture/validation.md` §2 — the hand-check figures
