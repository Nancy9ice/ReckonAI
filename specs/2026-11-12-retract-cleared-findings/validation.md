# Retract cleared findings — validation

## Before merge

1. Suite, `mypy`, `ruff check` and `python -m reckonai.eval` pass (after
   `demo_data --reset`). **Done:** 2102 passed, mypy and ruff clean, gate PASS.
2. The retraction tests fail without the rule and pass with it. **Done:** with
   `queries.py` stashed, the 4 retraction tests fail and the 3 "must not
   retract" tests pass.
3. The sweep test fails against the old `open_exceptions` and passes with the
   shared definition. **Done.**
4. Coverage still accounts for every order exactly once (asserted).

## After merge (not proven until seen on the deployed site)

- On the demo, `JUDGE-WALKTHRU-S4-LATE-REFUND` leaves "On hold" after the next
  run and appears under "Checked, no issues". The run must be one whose window
  contains the order: the UI's Run reconciliation covers the dataset.
- The 40-odd unmatched PayPal payments stay on hold (D3).
