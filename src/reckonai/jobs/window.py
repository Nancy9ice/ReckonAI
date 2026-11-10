"""Which orders a date-windowed run covers.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D3, including the
amendment that gave it a second clause.

**Two clauses, unioned.**

1. Orders whose own `placed_at` falls in the window. Half-open: `>= start`,
   `< end`.
2. Orders **of any age** holding evidence retrieved since the last completed
   run finished.

Clause 1 alone was the original decision, and it is not sufficient. The case
that breaks it is a dispute settled by a refund: the dispute opens days after
the order, PayPal decides it weeks later, and the refund that settles it is
ingested 30-plus days after `placed_at`. By then every scheduled window has
moved past that order, so it is never re-selected, the late refund is never
read, and the append-only history never gets the second row that would show
the verdict changing. The replan's R10 mechanism silently does not fire.

**Why clause 2 cannot be a SQL join.** A late PayPal refund reaches its order
only through the matcher: `refund.capture_id` is a derived linkage, a capture
points at a payment, and **nothing ties a payment to an order by column** -
that pairing is a claim made in `reckonai/reconcile.py`
(`specs/2026-11-09-matching-classification/requirements.md` D10). So selection
runs over assembled `OrderCase`s, where that claim has already been made,
rather than over the tables.

Clause 2 is bounded by construction: an order enters it only when evidence
actually arrived, so a steady state selects nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import Order, Payment, RawPayload, ReconciliationRun
from reckonai.recon.classifier import OrderCase


@dataclass(frozen=True, slots=True)
class Window:
    """What one run covers.

    `evidence_since` is `None` on the first run for a tenant, because there is
    no completed run to read a `finished_at` from. That is not a special case
    to code around: clause 2 then selects nothing and clause 1 stands alone.
    """

    start: datetime
    end: datetime
    evidence_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class Selection:
    """The refs one run reconciles, and why each was chosen.

    The two clauses are kept apart rather than merged into one set, so a run
    row can record which clause pulled an order in and a reader is not left
    inferring it from dates.
    """

    #: Orders selected because they were placed in the window.
    in_window: frozenset[str]

    #: Orders selected because evidence arrived since the last run, whatever
    #: their age.
    new_evidence: frozenset[str]

    #: Provider payments matched to no order, whose own evidence is new. They
    #: have no `placed_at` to test, so clause 1 cannot reach them.
    provider_refs: frozenset[str] = frozenset()

    @property
    def order_refs(self) -> frozenset[str]:
        return self.in_window | self.new_evidence

    @property
    def all_refs(self) -> frozenset[str]:
        """Every ref a finding of this run may be about.

        A finding for an unmatched provider payment carries that payment's
        reference in `order_ref`, which is why the two sets are unioned here
        rather than only the order ones.
        """
        return self.order_refs | self.provider_refs

    def __len__(self) -> int:
        return len(self.all_refs)


def last_completed_watermark(
    session: Session, tenant_id: uuid.UUID
) -> datetime | None:
    """The instant the next run should treat as "evidence seen up to here".

    **The previous completed run's `started_at`, not its `finished_at`.**
    Code review found the `finished_at` form lost evidence: `payload_times` is
    read near the beginning of a run, and `finished_at` is stamped at the end,
    so a `raw_payload` inserted in between is invisible to that run *and* has
    a `retrieved_at` earlier than the `finished_at` the next run reads as its
    watermark. Clause 2's strict `>` then skips it too, and for an order older
    than the date window clause 1 cannot rescue it - so the late refund is
    never read, which is the exact failure clause 2 exists to prevent.

    `started_at` makes the window of lost evidence empty. The cost is that a
    run re-covers evidence the previous run already saw, which appends
    identical rows and is the safe direction to be wrong in.

    **`completed` only**, because a run that died partway cannot be trusted to
    have covered its window.
    """
    return session.scalars(
        select(ReconciliationRun.started_at)
        .where(
            ReconciliationRun.tenant_id == tenant_id,
            ReconciliationRun.status == "completed",
            ReconciliationRun.finished_at.is_not(None),
        )
        .order_by(ReconciliationRun.started_at.desc())
        .limit(1)
    ).first()


def orders_in_window(
    session: Session, tenant_id: uuid.UUID, win: Window
) -> frozenset[str]:
    """Clause 1, in SQL over `order.placed_at`.

    **Over every order, not only the matched ones.** An order with no PayPal
    record yet is exactly the case the replan's R10 is about: it belongs in
    the window so the run reports it as `awaiting_evidence` and the next run
    re-evaluates it. Reading clause 1 off the assembled cases would silently
    drop it, because an unmatched order has no case.

    Half-open, so a day is covered by exactly one window of consecutive runs.
    """
    return frozenset(
        session.scalars(
            select(Order.external_ref).where(
                Order.tenant_id == tenant_id,
                Order.placed_at >= win.start,
                Order.placed_at < win.end,
            )
        )
    )


def payload_times(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
    """Every payload's `retrieved_at`, by payload id.

    **`retrieved_at`, not `created_at`.** The former is when the document was
    fetched and the latter when the row was written; a replayed or backfilled
    payload does not share the two, and reading our own write clock as the
    provider's would be the substitution specs/mission.md P3 forbids.
    """
    return {
        row.id: row.retrieved_at
        for row in session.scalars(
            select(RawPayload).where(RawPayload.tenant_id == tenant_id)
        )
    }


def provider_payment_times(
    session: Session, tenant_id: uuid.UUID
) -> dict[str, datetime]:
    """When each payment's evidence was retrieved, by the payment's own id."""
    times = payload_times(session, tenant_id)

    return {
        payment.external_id: times[payment.raw_payload_id]
        for payment in session.scalars(
            select(Payment).where(Payment.tenant_id == tenant_id)
        )
        if payment.raw_payload_id in times
    }


def _payload_ids(case: OrderCase) -> set[uuid.UUID]:
    """Every payload the records behind one case rest on.

    `getattr` rather than attribute access, and deliberately so. The case's
    fields are typed as the classifier's own `Protocol`s, which declare only
    what a *comparison* reads - none of them carries `raw_payload_id`, because
    the engine and the classifier have no use for it. The objects actually
    passed are ORM rows, which do.

    Widening those protocols to satisfy this module would push an ingestion
    concern into the pure package's interface, which is the wrong direction.
    A missing attribute here means an order contributes no evidence time and
    is simply not selected by clause 2 - it never means a wrong figure.
    """
    groups: Iterable[Sequence[object]] = (
        case.items,
        case.captures,
        case.platform_fees,
        case.disbursements,
        case.marketplace_refunds,
        case.provider_refunds,
        case.ledger_entries,
    )

    found: set[uuid.UUID] = set()
    for group in groups:
        for record in group:
            payload_id = getattr(record, "raw_payload_id", None)
            if isinstance(payload_id, uuid.UUID):
                found.add(payload_id)

    return found


def latest_evidence(
    case: OrderCase, times: dict[uuid.UUID, datetime]
) -> datetime | None:
    """The most recent moment any evidence behind this order was retrieved."""
    moments = [
        times[payload_id]
        for payload_id in _payload_ids(case)
        if payload_id in times
    ]
    return max(moments) if moments else None


def select_cases(
    cases: Sequence[OrderCase],
    provider_only: Sequence[str],
    *,
    window: Window,
    times: dict[uuid.UUID, datetime],
    provider_times: dict[str, datetime] | None = None,
    in_window: frozenset[str] = frozenset(),
) -> Selection:
    """Apply clause 2, and union it with clause 1's refs.

    Pure: every database read is done by the callers above and handed in, so
    the selection rule itself is testable without a session and cannot
    acquire a hidden clock read. `in_window` is clause 1's result, computed by
    `orders_in_window` because it has to cover orders that have no case.
    """
    new_evidence: set[str] = set()

    for case in cases:
        if case.order_ref in in_window:
            continue

        if window.evidence_since is None:
            continue

        moment = latest_evidence(case, times)
        if moment is not None and moment > window.evidence_since:
            new_evidence.add(case.order_ref)

    # **Unmatched provider payments, which clause 1 can never reach** - they
    # have no `placed_at` to test. Code review found them dropped permanently:
    # the first run has no watermark, and every later run's watermark is
    # already past the payload's `retrieved_at`, so a payment matched to no
    # marketplace order produced an `awaiting_evidence` finding that was
    # filtered out of every run forever. `reconcile.py` goes out of its way
    # not to lose that side, and the windowed run quietly did.
    #
    # With no watermark - a tenant's first run - every one is selected, so it
    # is reported once rather than never. After that, only newly arrived ones,
    # because an unmatched payment that has already been reported does not
    # need re-reporting on every run until someone resolves it.
    provider_refs: set[str] = set()
    if window.evidence_since is None:
        provider_refs = set(provider_only)
    elif provider_times:
        provider_refs = {
            ref
            for ref in provider_only
            if ref in provider_times
            and provider_times[ref] > window.evidence_since
        }

    return Selection(
        in_window=in_window,
        new_evidence=frozenset(new_evidence),
        provider_refs=frozenset(provider_refs),
    )
