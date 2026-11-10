"""The reads behind the routes.

**"Latest" is a read, never an update.** Runs, exceptions, investigations and
decisions are append-only, so the newest row per key is chosen here.

`current_exceptions` takes the newest row per `(order, code)` across runs. That
cannot retract an exception a later run no longer emits, because a run records
how many orders it covered but not which; the limitation is recorded in the
requirements rather than papered over.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.api import schemas
from reckonai.models import (
    ExceptionDecision,
    Investigation,
    InvestigationCause,
    Order,
    OrderItem,
    ReconciliationException,
    ReconciliationRun,
    Seller,
)
from reckonai.money import Money
from reckonai.recon.priority import Priority, PriorityInput, prioritise
from reckonai.schemas.money import MoneyOut

SECONDS_PER_DAY = Decimal(86400)


def list_runs(session: Session, tenant_id: uuid.UUID) -> list[ReconciliationRun]:
    return list(
        session.scalars(
            select(ReconciliationRun)
            .where(ReconciliationRun.tenant_id == tenant_id)
            .order_by(ReconciliationRun.started_at.desc())
        )
    )


def _newest_exception_rows(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID | None
) -> list[ReconciliationException]:
    stmt = select(ReconciliationException).where(
        ReconciliationException.tenant_id == tenant_id,
        ReconciliationException.is_exception.is_(True),
    )
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
    return list(session.scalars(stmt))


def latest_investigations(
    session: Session, tenant_id: uuid.UUID
) -> dict[tuple[str, str], Investigation]:
    """Newest investigation per `(order, code)`, on the purpose-built index."""
    rows = session.scalars(
        select(Investigation)
        .where(Investigation.tenant_id == tenant_id)
        .distinct(Investigation.order_external_ref, Investigation.code)
        .order_by(
            Investigation.order_external_ref,
            Investigation.code,
            Investigation.started_at.desc(),
        )
    )
    return {(r.order_external_ref, r.code): r for r in rows}


def latest_decisions(
    session: Session, tenant_id: uuid.UUID
) -> dict[uuid.UUID, ExceptionDecision]:
    rows = session.scalars(
        select(ExceptionDecision)
        .where(ExceptionDecision.tenant_id == tenant_id)
        .distinct(ExceptionDecision.exception_id)
        .order_by(ExceptionDecision.exception_id, ExceptionDecision.decided_at.desc())
    )
    return {r.exception_id: r for r in rows}


def _top_confidence(
    session: Session, tenant_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    if not ids:
        return {}
    rows = session.execute(
        select(
            InvestigationCause.investigation_id, InvestigationCause.confidence
        ).where(
            InvestigationCause.tenant_id == tenant_id,
            InvestigationCause.investigation_id.in_(ids),
            InvestigationCause.rank == 1,
        )
    )
    return {inv_id: conf for inv_id, conf in rows}


def _decision_out(row: ExceptionDecision) -> schemas.DecisionOut:
    return schemas.DecisionOut(
        id=row.id,
        outcome=row.outcome,
        approver=row.approver,
        note=row.note,
        decided_at=row.decided_at,
        investigation_id=row.investigation_id,
    )


def _money(value: Money | None) -> MoneyOut | None:
    return None if value is None else MoneyOut.from_money(value)


def _fraction(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.0001")))


def _priority_out(p: Priority) -> schemas.PriorityOut:
    return schemas.PriorityOut(
        score=str(p.score),
        factors={k: _fraction(v) for k, v in p.factors.items()},
    )


def exceptions(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID | None = None
) -> list[schemas.ExceptionOut]:
    """The queue: scored, with the newest verdict and decision beside each row."""
    rows = _newest_exception_rows(session, tenant_id, run_id)
    if not rows:
        return []

    refs = {r.order_external_ref for r in rows}
    orders = {
        o.external_ref: o
        for o in session.scalars(
            select(Order).where(
                Order.tenant_id == tenant_id, Order.external_ref.in_(refs)
            )
        )
    }
    seller_counts = {
        order_id: count
        for order_id, count in session.execute(
            select(OrderItem.order_id, func.count(func.distinct(OrderItem.seller_id)))
            .where(
                OrderItem.tenant_id == tenant_id,
                OrderItem.order_id.in_([o.id for o in orders.values()]),
            )
            .group_by(OrderItem.order_id)
        )
    }
    seller_ids = {r.seller_id for r in rows if r.seller_id is not None}
    sellers = {
        s.id: s.name
        for s in session.scalars(
            select(Seller).where(
                Seller.tenant_id == tenant_id, Seller.id.in_(seller_ids)
            )
        )
    }
    investigations = latest_investigations(session, tenant_id)
    decisions = latest_decisions(session, tenant_id)
    confidences = _top_confidence(
        session, tenant_id, [i.id for i in investigations.values()]
    )

    now = datetime.now(UTC)
    inputs: list[PriorityInput] = []
    for r in rows:
        order = orders[r.order_external_ref]
        inv = investigations.get((r.order_external_ref, r.code))
        inputs.append(
            PriorityInput(
                key=str(r.id),
                impact=r.impact,
                # An exception naming one seller affects that seller; one about
                # the order as a whole affects every seller on it.
                seller_count=1
                if r.seller_id is not None
                else seller_counts.get(order.id, 1),
                age_days=max(
                    Decimal(0),
                    Decimal(str((now - order.placed_at).total_seconds()))
                    / SECONDS_PER_DAY,
                ),
                confidence=confidences.get(inv.id) if inv is not None else None,
            )
        )
    priorities = {p.key: p for p in prioritise(inputs)}

    out: list[schemas.ExceptionOut] = []
    for r in rows:
        order = orders[r.order_external_ref]
        inv = investigations.get((r.order_external_ref, r.code))
        decision = decisions.get(r.id)
        out.append(
            schemas.ExceptionOut(
                id=r.id,
                run_id=r.run_id,
                code=r.code,
                order_ref=r.order_external_ref,
                detail=r.detail,
                certainty="confirmed" if r.confirmed else "suspected",
                impact=MoneyOut.from_money(r.impact),
                expected=_money(r.expected),
                reported=_money(r.reported),
                seller_id=r.seller_id,
                seller_name=sellers.get(r.seller_id) if r.seller_id else None,
                seller_count=1
                if r.seller_id is not None
                else seller_counts.get(order.id, 1),
                order_placed_at=order.placed_at,
                evidence_refs=list(r.evidence_refs),
                evidence_basis=r.evidence_basis,
                priority=_priority_out(priorities[str(r.id)]),
                investigation=None
                if inv is None
                else schemas.InvestigationSummary(
                    id=inv.id,
                    verdict=inv.verdict,
                    provider=inv.provider,
                    model_requested=inv.model_requested,
                    started_at=inv.started_at,
                    top_confidence=_fraction(confidences.get(inv.id)),
                ),
                decision=None if decision is None else _decision_out(decision),
            )
        )
    out.sort(key=lambda e: Decimal(e.priority.score), reverse=True)
    return out


def exception_detail(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID
) -> schemas.ExceptionDetail | None:
    row = session.scalar(
        select(ReconciliationException).where(
            ReconciliationException.tenant_id == tenant_id,
            ReconciliationException.id == exception_id,
        )
    )
    if row is None:
        return None

    # Scored against the same population as the queue, so the number agrees
    # with what the list showed.
    base = next((e for e in exceptions(session, tenant_id) if e.id == row.id), None)
    if base is None:
        # An older run's row, superseded in the queue: score it on its own.
        base = next(
            (e for e in exceptions(session, tenant_id, row.run_id) if e.id == row.id),
            None,
        )
    if base is None:
        return None

    inv = latest_investigations(session, tenant_id).get(
        (row.order_external_ref, row.code)
    )
    detail: schemas.InvestigationDetail | None = None
    if inv is not None:
        causes = session.scalars(
            select(InvestigationCause)
            .where(
                InvestigationCause.tenant_id == tenant_id,
                InvestigationCause.investigation_id == inv.id,
            )
            .order_by(InvestigationCause.rank)
        ).all()
        top = next((c.confidence for c in causes if c.rank == 1), None)
        detail = schemas.InvestigationDetail(
            id=inv.id,
            verdict=inv.verdict,
            provider=inv.provider,
            model_requested=inv.model_requested,
            started_at=inv.started_at,
            top_confidence=_fraction(top),
            downgraded=inv.downgraded,
            stop_reason=inv.stop_reason,
            model_returned=inv.model_returned,
            summarized_reasoning=inv.summarized_reasoning,
            withheld=list(inv.withheld),
            finished_at=inv.finished_at,
            causes=[
                schemas.CauseOut(
                    rank=c.rank,
                    description=c.description,
                    confidence=str(c.confidence),
                    cited_refs=list(c.cited_refs),
                    unresolved_refs=list(c.unresolved_refs),
                )
                for c in causes
            ],
        )

    history = session.scalars(
        select(ExceptionDecision)
        .where(
            ExceptionDecision.tenant_id == tenant_id,
            ExceptionDecision.exception_id == row.id,
        )
        .order_by(ExceptionDecision.decided_at.desc())
    ).all()
    return schemas.ExceptionDetail(
        **base.model_dump(),
        investigation_detail=detail,
        decisions=[_decision_out(d) for d in history],
    )
