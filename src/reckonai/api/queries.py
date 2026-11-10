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

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from reckonai.api import schemas
from reckonai.jobs import investigation_job, queue
from reckonai.models import (
    ExceptionDecision,
    Investigation,
    InvestigationCause,
    Job,
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
) -> dict[tuple[str, str], ExceptionDecision]:
    """Newest decision per `(order, code)`, not per exception row.

    **Keyed like investigations, on purpose.** Every run writes a fresh
    `reconciliation_exception` row for the same fault, and the queue shows the
    newest. A decision keyed to one row's id would vanish from the queue when
    the next daily run re-concluded the same finding, and the same fault could
    be approved again. Found by code review.
    """
    rows = session.execute(
        select(ExceptionDecision, ReconciliationException)
        .join(
            ReconciliationException,
            (ReconciliationException.tenant_id == ExceptionDecision.tenant_id)
            & (ReconciliationException.id == ExceptionDecision.exception_id),
        )
        .where(ExceptionDecision.tenant_id == tenant_id)
        .distinct(
            ReconciliationException.order_external_ref, ReconciliationException.code
        )
        .order_by(
            ReconciliationException.order_external_ref,
            ReconciliationException.code,
            ExceptionDecision.decided_at.desc(),
        )
    )
    return {(exc.order_external_ref, exc.code): dec for dec, exc in rows}


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


def _seller_count(
    row: ReconciliationException,
    order: Order | None,
    counts: dict[uuid.UUID, int],
) -> int:
    """An exception naming one seller affects that seller; one about the order
    as a whole affects every seller on it."""
    if row.seller_id is not None or order is None:
        return 1
    return counts.get(order.id, 1)


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
        order = orders.get(r.order_external_ref)
        inv = investigations.get((r.order_external_ref, r.code))
        inputs.append(
            PriorityInput(
                key=str(r.id),
                impact=r.impact,
                # An exception naming one seller affects that seller; one about
                # the order as a whole affects every seller on it.
                seller_count=_seller_count(r, order, seller_counts),
                # No order row means no placed date to age from; zero rather
                # than a guess, and the field is null in the response.
                age_days=Decimal(0)
                if order is None
                else max(
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
        order = orders.get(r.order_external_ref)
        inv = investigations.get((r.order_external_ref, r.code))
        decision = decisions.get((r.order_external_ref, r.code))
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
                seller_count=_seller_count(r, order, seller_counts),
                order_placed_at=None if order is None else order.placed_at,
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
        .join(
            ReconciliationException,
            (ReconciliationException.tenant_id == ExceptionDecision.tenant_id)
            & (ReconciliationException.id == ExceptionDecision.exception_id),
        )
        .where(
            ExceptionDecision.tenant_id == tenant_id,
            ReconciliationException.order_external_ref == row.order_external_ref,
            ReconciliationException.code == row.code,
        )
        .order_by(ExceptionDecision.decided_at.desc())
    ).all()
    job = latest_investigation_job(session, tenant_id, row.id)
    return schemas.ExceptionDetail(
        **base.model_dump(),
        investigation_detail=detail,
        decisions=[_decision_out(d) for d in history],
        investigation_job=None if job is None else job_out(job),
    )


def record_decision(
    session: Session,
    tenant_id: uuid.UUID,
    exception_id: uuid.UUID,
    body: schemas.DecisionIn,
) -> ExceptionDecision | None:
    """Append one decision, or `None` if the exception is not this tenant's.

    **This is the whole of what approving does.** It inserts one row and
    touches no ledger, refund or disbursement (`mission.md` P6): a decision is
    a human's recorded intent, and acting on it is outside the product.
    """
    row = session.scalar(
        select(ReconciliationException).where(
            ReconciliationException.tenant_id == tenant_id,
            ReconciliationException.id == exception_id,
        )
    )
    if row is None:
        return None
    seen = latest_investigations(session, tenant_id).get(
        (row.order_external_ref, row.code)
    )
    decision = ExceptionDecision(
        tenant_id=tenant_id,
        exception_id=exception_id,
        outcome=body.outcome,
        approver=body.approver,
        note=body.note or None,
        investigation_id=None if seen is None else seen.id,
        # Set here rather than by the server default: now() is constant inside
        # a transaction, and "newest decision wins" needs distinct instants.
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    session.flush()
    return decision


def job_out(job: Job) -> schemas.JobOut:
    return schemas.JobOut(
        id=job.id,
        status=job.status,
        attempts=job.attempts,
        last_error=job.last_error,
        created_at=job.created_at,
    )


def latest_investigation_job(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID
) -> Job | None:
    return session.scalar(
        select(Job)
        .where(
            Job.tenant_id == tenant_id,
            Job.job_type == investigation_job.JOB_TYPE,
            Job.payload["exception_id"].astext == str(exception_id),
        )
        .order_by(Job.created_at.desc(), Job.run_after.desc())
        .limit(1)
    )


def request_investigation(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID
) -> Job | None:
    """Enqueue an investigation, or return the one already in flight.

    A double click is one job: while a `queued` or `claimed` job exists for the
    exception, that job is returned rather than a second enqueued. `None` if
    the exception is not this tenant's.
    """
    # Serialises concurrent requests for the same exception for the rest of
    # this transaction, so the check below and the enqueue are one step. Without
    # it two simultaneous clicks both see "no job in flight" and both enqueue,
    # and a paid investigation runs twice. Found by code review.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"investigate:{exception_id}"},
    )
    exists = session.scalar(
        select(ReconciliationException.id).where(
            ReconciliationException.tenant_id == tenant_id,
            ReconciliationException.id == exception_id,
        )
    )
    if exists is None:
        return None
    current = latest_investigation_job(session, tenant_id, exception_id)
    if current is not None and current.status in ("queued", "claimed"):
        return current
    return queue.enqueue(
        session,
        tenant_id=tenant_id,
        job_type=investigation_job.JOB_TYPE,
        payload=investigation_job.payload_for(exception_id),
    )
