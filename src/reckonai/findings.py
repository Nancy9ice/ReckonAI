"""The newest finding per `(order, code)`, and when one is retracted.

One definition of "open", used by the queue, the summary, Coverage, the CSV and
the nightly investigation sweep, so they cannot disagree about what is open.
Session-aware, so it sits beside `reconcile.py` and outside the purity gate.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import Order, ReconciliationException, ReconciliationRun


def newest_rows(
    session: Session,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID | None,
    *,
    is_exception: bool = True,
    code: str | None = None,
) -> list[ReconciliationException]:
    """The rows to show: one run's own, or the newest per `(order, code)`.

    With `run_id`, exactly that run as it left it (Past checks). Without, the
    newest row per `(order, code)` across all runs, less any a later run
    retracted (`drop_retracted`).
    """
    stmt = select(ReconciliationException).where(
        ReconciliationException.tenant_id == tenant_id,
        ReconciliationException.is_exception.is_(is_exception),
    )
    if code is not None:
        stmt = stmt.where(ReconciliationException.code == code)
    if run_id is not None:
        return list(
            session.scalars(stmt.where(ReconciliationException.run_id == run_id))
        )
    stmt = (
        stmt.join(
            ReconciliationRun,
            (ReconciliationRun.tenant_id == ReconciliationException.tenant_id)
            & (ReconciliationRun.id == ReconciliationException.run_id),
        )
        .distinct(
            ReconciliationException.order_external_ref, ReconciliationException.code
        )
        .order_by(
            ReconciliationException.order_external_ref,
            ReconciliationException.code,
            ReconciliationRun.started_at.desc(),
        )
    )
    return drop_retracted(session, tenant_id, list(session.scalars(stmt)))


def drop_retracted(
    session: Session, tenant_id: uuid.UUID, rows: list[ReconciliationException]
) -> list[ReconciliationException]:
    """Drop findings that a later run re-examined and stopped reporting.

    A run writes a row for each finding and nothing for an order that comes out
    clean, so the newest row for an `(order, code)` would stay the newest for
    ever: a fault that was fixed, or evidence that arrived late, would show as
    open for good. Absence of a row cannot mean "checked and clean" by itself,
    because a run records how many orders it covered, not which (P3).

    What can be said is narrower and sound: **a completed run examines every
    order whose `placed_at` falls inside its window** (`window.select_cases`
    takes clause 1 unconditionally, and `tests/test_api_retraction.py` pins
    it). So a finding is retracted if a completed run that **started after the
    finding's own run finished** has such a window and did not write the
    finding again. "After it finished", not "after it started": a run that
    overlapped the first never saw the first run's data, so it proves nothing.

    Orders a run re-examined only because new evidence arrived are not counted
    (their placement date is outside its window), so those findings stay until
    a run whose window contains them: this errs toward leaving a finding up. A
    row whose order is not a marketplace order (an unmatched PayPal payment,
    keyed by PayPal's id) cannot be placed in a window and is kept.
    """
    if not rows:
        return rows
    runs = list(
        session.scalars(
            select(ReconciliationRun).where(ReconciliationRun.tenant_id == tenant_id)
        )
    )
    finished = {r.id: r.finished_at for r in runs}
    completed = [r for r in runs if r.status == "completed"]
    placed = {
        o.external_ref: o.placed_at
        for o in session.scalars(
            select(Order).where(
                Order.tenant_id == tenant_id,
                Order.external_ref.in_({r.order_external_ref for r in rows}),
            )
        )
    }

    def retracted(row: ReconciliationException) -> bool:
        when = placed.get(row.order_external_ref)
        own_finished = finished.get(row.run_id)
        if when is None or own_finished is None:
            return False
        return any(
            run.started_at > own_finished
            and run.window_start <= when < run.window_end
            for run in completed
        )

    return [r for r in rows if not retracted(r)]
