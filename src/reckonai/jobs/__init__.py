"""The execution substrate: enqueueing, claiming, and the scheduled run.

**This package is deliberately impure.** `reckonai/engine/` and
`reckonai/recon/` hold no session, no client and no clock, and
`tests/test_money_purity.py` enforces that. Everything those packages refuse is
here: a `Session`, `datetime.now`, a transaction boundary.

That asymmetry is the design rather than an exemption
(`specs/2026-11-09-queue-scheduled-run/requirements.md` section 3). A pure
engine is only pure if something impure calls it, and keeping the impurity in
one named package is what stops it leaking into the modules where a hidden
clock read would make a financial answer irreproducible.

It is in strict mypy's scope, which the purity gate's absence makes more
important rather than less.

Three modules:

  - `queue.py` - enqueue, claim with `FOR UPDATE SKIP LOCKED`, complete, fail.
  - `window.py` - which orders a date-windowed run covers (D3).
  - `reconcile_job.py` - the handler: run the reconciliation and persist it.

`schedule.py` is the `python -m` entry point the Render Cron Job invokes. It
enqueues and returns; the worker is what executes.
"""

from __future__ import annotations
