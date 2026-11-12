# Retract cleared findings — plan

**1. The rule.** `_drop_retracted` in `api/queries.py`, applied to the cross-run
branch of `_newest_exception_rows`. Docstrings and the `basis` text updated.

**2. Tests.** `tests/test_api_retraction.py`: late evidence clears a parked
finding; a fixed exception leaves the queue and summary; the decision log keeps
its rows; a window that misses the order, a failed run and a fault still present
retract nothing; a specific run is unchanged.

**3. Docs.** `CLAUDE.md`; the limitation noted in the 2026-11-10 dashboard
validation no longer holds.
