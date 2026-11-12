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
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from reckonai.api import schemas
from reckonai.config import get_settings
from reckonai.findings import newest_rows as _newest_exception_rows
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


def first_found(
    session: Session, tenant_id: uuid.UUID, refs: set[str]
) -> dict[tuple[str, str], datetime]:
    """The start of the earliest run that reported each `(order, code)`.

    Every run writes a fresh row for the same fault, so a row's own run says
    when it was last reported, not when it was first. This reads across runs.
    """
    return {
        (order_ref, code): started
        for order_ref, code, started in session.execute(
            select(
                ReconciliationException.order_external_ref,
                ReconciliationException.code,
                func.min(ReconciliationRun.started_at),
            )
            .join(
                ReconciliationRun,
                (ReconciliationRun.tenant_id == ReconciliationException.tenant_id)
                & (ReconciliationRun.id == ReconciliationException.run_id),
            )
            .where(
                ReconciliationException.tenant_id == tenant_id,
                ReconciliationException.is_exception.is_(True),
                ReconciliationException.order_external_ref.in_(refs),
            )
            .group_by(
                ReconciliationException.order_external_ref,
                ReconciliationException.code,
            )
        )
    }


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
    found = first_found(session, tenant_id, refs)
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
                # **How long it has been open, not how old the order is.** The
                # age of a problem is the days since a check first reported it
                # (`first_found`), which is what a reviewer waits on; an old
                # order with a problem found today is new work. A fault found
                # in this run therefore scores zero for age by construction.
                age_days=max(
                    Decimal(0),
                    Decimal(
                        str(
                            (
                                now
                                - found.get(
                                    (r.order_external_ref, r.code), r.created_at
                                )
                            ).total_seconds()
                        )
                    )
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
                # Every row has a key (the query reads the same rows), so the
                # fallback is for a future caller that does not; it is the row's
                # own write time, never an exception on the queue page.
                first_found_at=found.get(
                    (r.order_external_ref, r.code), r.created_at
                ),
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
    return investigation_job.latest_job(session, tenant_id, exception_id)


def request_investigation(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID
) -> Job | None:
    """Enqueue an investigation, or return the one already in flight."""
    found = investigation_job.request(session, tenant_id, exception_id)
    return None if found is None else found[0]


def request_investigations(
    session: Session,
    tenant_id: uuid.UUID,
    body: schemas.BulkInvestigateIn,
) -> schemas.BulkInvestigateOut:
    """Queue investigations for several exceptions at once.

    Per exception: one already settled is skipped when `skip_settled` is set,
    one already queued or running is reported as in flight rather than queued
    again, and an id that is not this tenant's is counted, not queued.
    """
    queued = in_flight = skipped = missing = 0
    jobs: list[schemas.JobOut] = []
    for exception_id in dict.fromkeys(body.exception_ids):
        row = session.scalar(
            select(ReconciliationException).where(
                ReconciliationException.tenant_id == tenant_id,
                ReconciliationException.id == exception_id,
            )
        )
        if row is None:
            missing += 1
            continue
        if body.skip_settled and investigation_job.is_settled(session, row):
            skipped += 1
            continue
        found = investigation_job.request(session, tenant_id, exception_id)
        if found is None:
            missing += 1
            continue
        job, created = found
        jobs.append(job_out(job))
        if created:
            queued += 1
        else:
            in_flight += 1
    return schemas.BulkInvestigateOut(
        queued=queued,
        in_flight=in_flight,
        skipped=skipped,
        not_found=missing,
        jobs=jobs,
    )


def get_job(session: Session, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    return session.scalar(
        select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id)
    )


def coverage(session: Session, tenant_id: uuid.UUID) -> schemas.CoverageOut:
    """Account for every order exactly once.

    Four disjoint states, in this precedence: **with an exception** (it is in
    the queue), **parked** (`awaiting_evidence` and not flagged), **not yet
    examined**, and **clean**. They sum to the number of orders, which a test
    pins.

    **"Clean" is a claim that something looked and found nothing**, so it needs
    a run to have looked. An order counts as examined only if a completed run's
    window contains its `placed_at` *and* that run started after the order was
    ingested; otherwise "no findings" would mean "never checked", the
    absence-versus-measured-zero confusion `mission.md` P3 forbids. (An old
    order a run re-examined because new evidence arrived is not recognised as
    examined here unless its window also covers it, so this errs toward saying
    "not yet examined".)

    A finding a later completed run re-examined and stopped reporting is
    retracted (`_drop_retracted`), so a fixed fault or late evidence leaves the
    queue and the on-hold list, and the order reads clean. A run records how
    many orders it covered, not which, so only a run whose window contains the
    order counts; others leave the finding up.
    """
    orders = list(
        session.scalars(
            select(Order)
            .where(Order.tenant_id == tenant_id)
            .order_by(Order.external_ref)
        )
    )
    flagged = {
        r.order_external_ref for r in _newest_exception_rows(session, tenant_id, None)
    }
    parked_rows = [
        r
        for r in _newest_exception_rows(
            session, tenant_id, None, is_exception=False, code="awaiting_evidence"
        )
        # An order with an open exception is counted once, as flagged.
        if r.order_external_ref not in flagged
    ]
    parked_refs = {r.order_external_ref for r in parked_rows}

    runs = list(
        session.scalars(
            select(ReconciliationRun).where(
                ReconciliationRun.tenant_id == tenant_id,
                ReconciliationRun.status == "completed",
            )
        )
    )

    def examined(order: Order) -> bool:
        return any(
            run.window_start <= order.placed_at < run.window_end
            and run.started_at >= order.created_at
            for run in runs
        )

    seller_rows = session.execute(
        select(OrderItem.order_id, Seller.name)
        .join(
            Seller,
            (Seller.tenant_id == OrderItem.tenant_id)
            & (Seller.id == OrderItem.seller_id),
        )
        .where(OrderItem.tenant_id == tenant_id)
        .distinct()
    )
    sellers: dict[uuid.UUID, list[str]] = {}
    for order_id, name in seller_rows:
        sellers.setdefault(order_id, []).append(name)

    clean: list[schemas.CleanOrderOut] = []
    not_examined: list[str] = []
    for o in orders:
        if o.external_ref in flagged or o.external_ref in parked_refs:
            continue
        if not examined(o):
            not_examined.append(o.external_ref)
            continue
        clean.append(
            schemas.CleanOrderOut(
                order_ref=o.external_ref,
                total=MoneyOut.from_money(o.total),
                placed_at=o.placed_at,
                sellers=sorted(sellers.get(o.id, [])),
            )
        )

    return schemas.CoverageOut(
        orders_total=len(orders),
        with_exceptions=len(flagged),
        parked=len(parked_refs),
        clean=len(clean),
        not_examined=len(not_examined),
        not_examined_orders=not_examined,
        parked_items=[
            schemas.ParkedOut(
                id=r.id,
                order_ref=r.order_external_ref,
                detail=r.detail,
                run_id=r.run_id,
            )
            for r in parked_rows
        ],
        clean_orders=clean,
        basis=(
            "Clean means a completed run's window covered the order after it "
            "arrived and found nothing. A run records how many orders it "
            "covered, not which, so a finding is taken down only when a later "
            "completed run's window contains the order and does not report it "
            "again; an order re-examined only because new evidence arrived is "
            "not counted."
        ),
    )


#: What a refused run request says. Plain words for the reader of the page.
_RUN_REFUSAL = {
    "already_done": (
        "This check has already run over the same records, so nothing was queued."
    ),
    "nothing_new": (
        "Nothing new has arrived since the last check, so nothing was queued. "
        "A new day starts a new check on its own."
    ),
    "cooling_down": (
        "New records have arrived, but a check finished a moment ago. Try again "
        "shortly; the next check will include them."
    ),
}


def request_run(session: Session, tenant_id: uuid.UUID) -> schemas.RunRequestOut:
    """Queue a reconciliation over every order the tenant has.

    The window runs from the earliest order to the end of the day after the
    latest (and never earlier than tomorrow), so one request covers the whole
    dataset. **It is idempotent by design**: the window is date-grained and so
    is the dedupe key, so asking twice on the same day is answered, not
    queued twice. That is `reconcile_job.idempotency_key`'s rule and this
    does not route around it.
    """
    from reckonai.jobs import reconcile_job, window

    bounds = session.execute(
        select(func.min(Order.placed_at), func.max(Order.placed_at)).where(
            Order.tenant_id == tenant_id
        )
    ).one()
    first, last = bounds
    if first is None or last is None:
        return schemas.RunRequestOut(
            status="no_orders",
            window_start=None,
            window_end=None,
            job=None,
            message="There are no orders to reconcile yet.",
        )

    def midnight(d: datetime) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=UTC)

    tomorrow = midnight(datetime.now(UTC)) + timedelta(days=1)
    win = window.Window(
        start=midnight(first),
        end=max(tomorrow, midnight(last) + timedelta(days=1)),
    )

    # One request at a time per tenant: the check and the enqueue are one step.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"run:{tenant_id}"},
    )

    decision = reconcile_job.decide_run(
        session,
        tenant_id,
        win,
        now=datetime.now(UTC),
        cooldown_seconds=get_settings().run_cooldown_seconds,
    )
    if decision.verdict is not reconcile_job.RunVerdict.PERMITTED:
        return schemas.RunRequestOut(
            status=str(decision.verdict),
            window_start=win.start,
            window_end=win.end,
            job=None,
            message=_RUN_REFUSAL[decision.verdict],
        )

    in_flight = session.scalar(
        select(Job)
        .where(
            Job.tenant_id == tenant_id,
            Job.job_type == reconcile_job.JOB_TYPE,
            Job.status.in_(("queued", "claimed")),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if in_flight is not None:
        return schemas.RunRequestOut(
            status="in_flight",
            window_start=win.start,
            window_end=win.end,
            job=job_out(in_flight),
            message="A run is already queued or running.",
        )

    job = queue.enqueue(
        session,
        tenant_id=tenant_id,
        job_type=reconcile_job.JOB_TYPE,
        payload=reconcile_job.payload_for(decision.window),
    )
    return schemas.RunRequestOut(
        status="queued",
        window_start=win.start,
        window_end=win.end,
        job=job_out(job),
        message="Run queued. The worker picks it up within its poll interval.",
    )


def _bucket(rows: list[schemas.ExceptionOut]) -> schemas.SummaryBucket:
    totals: dict[str, Money] = {}
    for row in rows:
        impact = row.impact.to_money()
        totals[impact.currency] = totals.get(
            impact.currency, Money.zero(impact.currency)
        ) + impact
    return schemas.SummaryBucket(
        count=len(rows),
        at_risk=[MoneyOut.from_money(totals[c]) for c in sorted(totals)],
    )


def summary(
    session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID | None = None
) -> schemas.SummaryOut:
    """Volume and value of the open queue, summed per currency.

    Built from the same rows as `exceptions`, so the two cannot disagree.
    """
    rows = exceptions(session, tenant_id, run_id)
    codes = sorted({r.code for r in rows})
    buckets = {c: _bucket([r for r in rows if r.code == c]) for c in codes}
    largest: dict[str, Decimal] = {}
    for b in buckets.values():
        for m in b.at_risk:
            seen = largest.get(m.currency, Decimal(0))
            largest[m.currency] = max(seen, Decimal(m.amount))
    by_type = [
        schemas.SummaryType(
            code=c,
            count=b.count,
            at_risk=b.at_risk,
            shares={
                m.currency: str(
                    (Decimal(m.amount) / largest[m.currency]).quantize(
                        Decimal("0.0001")
                    )
                )
                for m in b.at_risk
                if largest[m.currency] > 0
            },
        )
        for c, b in buckets.items()
    ]
    # Largest share first, so the client draws the order it is given.
    by_type.sort(
        key=lambda t: (
            -max((Decimal(v) for v in t.shares.values()), default=Decimal(0)),
            -t.count,
            t.code,
        )
    )
    return schemas.SummaryOut(
        open=_bucket(rows),
        confirmed=_bucket([r for r in rows if r.certainty == "confirmed"]),
        suspected=_bucket([r for r in rows if r.certainty == "suspected"]),
        undecided=_bucket([r for r in rows if r.decision is None]),
        decided=_bucket([r for r in rows if r.decision is not None]),
        by_type=by_type,
    )
