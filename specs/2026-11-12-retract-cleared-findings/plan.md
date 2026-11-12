# Retract cleared findings — plan

**1. The rule.** `findings.drop_retracted` in the new `reckonai/findings.py`,
applied to the cross-run branch of `newest_rows`; `api/queries.py` and
`jobs/investigation_job.open_exceptions` both call it. Docstrings and the
`basis` text updated.

**2. Tests.** `tests/test_api_retraction.py`: late evidence clears a parked
finding; a fixed exception leaves the queue and summary; the decision log keeps
its rows; a window that misses the order, a failed run and a fault still present
retract nothing; a specific run is unchanged; an overlapping run retracts nothing; the nightly
sweep agrees with the queue; a run examines every order placed in its window.

**3. Docs.** `CLAUDE.md`; the limitation noted in the 2026-11-10 dashboard
validation no longer holds.
