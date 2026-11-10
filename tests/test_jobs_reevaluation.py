"""Criterion 5: a parked exception is re-evaluated by a later run.

`specs/2026-11-09-queue-scheduled-run/validation.md` section 1 criterion 5, and
the replan's R10 - "the run is the retry".

**Proven on the real seeded dataset, against the fault built for it.** F5 is
ORD-0017: the marketplace recorded a 15.00 USD refund and reflected it in the
ledger correctly, and PayPal's own refund record has not arrived because the
reporting APIs run roughly a day behind. The manifest's expected answer is
`awaiting_evidence` and explicitly **not** an exception - flagging it is the
false positive Phase 11's hardest criterion forbids.

So the shape under test is the one the product exists to get right: park it,
let the late record arrive, re-evaluate, and watch the verdict change without
anything being rewritten.

Nothing here commits; the shared `session` fixture rolls back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import make_payload
from reckonai import demo_data, reconcile
from reckonai.jobs import reconcile_job, window
from reckonai.models import ReconciliationException, Refund, Tenant
from reckonai.recon.vocabulary import ExceptionCode
from reckonai.sim import data

PARKED_ORDER = "ORD-0017"

#: Wide enough to contain the whole seeded dataset, whose `placed_at` values
#: straddle today. A trailing window is the production shape and is what
#: `schedule.py` builds; this module is about re-evaluation, not windowing, so
#: it fixes the window and varies the evidence.
WHOLE_DATASET = window.Window(
    start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2027, 1, 1, tzinfo=UTC)
)


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    """The demo tenant, seeded into the rolled-back session.

    `demo_data.ensure` is the entry point the deploy runs, so these are the
    rows the demo shows rather than a second fixture that could drift.
    """
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()

    tenant_id = session.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    assert tenant_id is not None, "the demo tenant was not seeded"
    return tenant_id


def _rows(session: Session, run_id: uuid.UUID, order_ref: str) -> list[str]:
    return list(
        session.scalars(
            select(ReconciliationException.code).where(
                ReconciliationException.run_id == run_id,
                ReconciliationException.order_external_ref == order_ref,
            )
        )
    )


def _late_provider_refund(session: Session, tenant_id: uuid.UUID) -> datetime:
    """Insert the PayPal refund record F5 says has not arrived yet.

    Built the way `reckonai.paypal.replay` builds one, including the two
    things that matter for matching: the linkage to the capture is **derived**
    (PayPal supplies no `capture_id` on a refund), and the refund states an
    explicit `0.00` fee where a capture omits the key entirely.

    Returns the moment the payload was retrieved, which is what clause 2
    compares against.
    """
    cases, _marketplace_only, _provider_only = reconcile.load_order_cases(
        session, tenant_id
    )
    case = next(case for case in cases if case.order_ref == PARKED_ORDER)
    capture = case.captures[0]
    marketplace_refund = case.marketplace_refunds[0]

    retrieved_at = datetime.now(UTC)
    payload = make_payload(
        tenant_id,
        source="paypal",
        source_record_type="refund",
        source_record_id="LATE-RFND-0017",
        endpoint="GET /v2/payments/refunds/{id}",
        retrieved_at=retrieved_at,
    )
    session.add(payload)
    session.flush()

    session.add(
        Refund(
            tenant_id=tenant_id,
            raw_payload_id=payload.id,
            external_id="LATE-RFND-0017",
            capture_id=capture.id,
            capture_linkage_is_derived=True,
            capture_linkage_href=(
                f"https://api.sandbox.paypal.com/v2/payments/captures/"
                f"{capture.external_id}"
            ),
            seller_id=marketplace_refund.seller_id,
            amount=marketplace_refund.amount,
            gross_amount=marketplace_refund.amount,
            net_amount=marketplace_refund.amount,
            status="COMPLETED",
            occurred_at=retrieved_at,
        )
    )
    session.flush()
    return retrieved_at


# ---------------------------------------------------------------------------
# Criterion 5
# ---------------------------------------------------------------------------


def test_the_parked_order_starts_parked(
    session: Session, seeded: uuid.UUID
) -> None:
    """The precondition, asserted rather than assumed.

    If F5 stopped being parked - because the dataset changed, or because a
    rule started flagging it - the re-evaluation test below would pass
    vacuously while proving nothing.
    """
    first = reconcile_job.run_window(
        session, tenant_id=seeded, win=WHOLE_DATASET
    )

    assert _rows(session, first.id, PARKED_ORDER) == [
        str(ExceptionCode.AWAITING_EVIDENCE)
    ]

    parked = session.scalars(
        select(ReconciliationException).where(
            ReconciliationException.run_id == first.id,
            ReconciliationException.order_external_ref == PARKED_ORDER,
        )
    ).one()

    assert parked.is_exception is False, (
        "F5's refund is late, not absent; counting it as an exception is the "
        "false positive the manifest exists to measure"
    )


def test_a_parked_order_is_re_evaluated_once_its_record_arrives(
    session: Session, seeded: uuid.UUID
) -> None:
    """Criterion 5, end to end: park, arrive, re-evaluate.

    The second run's window is identical. **Only the evidence changed**, which
    is the point: R10's answer to a late record is to run again, not to build
    a parking mechanism with its own state transitions.
    """
    first = reconcile_job.run_window(
        session, tenant_id=seeded, win=WHOLE_DATASET
    )
    assert _rows(session, first.id, PARKED_ORDER) == [
        str(ExceptionCode.AWAITING_EVIDENCE)
    ]

    retrieved_at = _late_provider_refund(session, seeded)

    second = reconcile_job.run_window(
        session,
        tenant_id=seeded,
        win=window.Window(
            start=WHOLE_DATASET.start,
            end=WHOLE_DATASET.end,
            evidence_since=retrieved_at - timedelta(minutes=1),
        ),
    )

    assert str(ExceptionCode.AWAITING_EVIDENCE) not in _rows(
        session, second.id, PARKED_ORDER
    ), (
        "the PayPal refund has arrived, so the order is no longer awaiting "
        "evidence - the verdict must change"
    )


def test_the_earlier_verdict_is_not_rewritten(
    session: Session, seeded: uuid.UUID
) -> None:
    """D1's append-only property, on the pair of runs that proves R10.

    The parked row **stays**, and that is what makes the re-evaluation
    observable at all: two rows for one order that differ across runs are the
    evidence the mechanism fired. A current-state table would overwrite
    exactly the row that proves it.
    """
    first = reconcile_job.run_window(
        session, tenant_id=seeded, win=WHOLE_DATASET
    )
    retrieved_at = _late_provider_refund(session, seeded)
    second = reconcile_job.run_window(
        session,
        tenant_id=seeded,
        win=window.Window(
            start=WHOLE_DATASET.start,
            end=WHOLE_DATASET.end,
            evidence_since=retrieved_at - timedelta(minutes=1),
        ),
    )

    assert _rows(session, first.id, PARKED_ORDER) == [
        str(ExceptionCode.AWAITING_EVIDENCE)
    ], "the first run's row was rewritten; the history is supposed to be append-only"
    assert first.id != second.id
    assert first.finished_at is not None and second.finished_at is not None


def test_clause_two_selects_the_parked_order_against_the_real_dataset(
    session: Session, seeded: uuid.UUID
) -> None:
    """Clause 2 on real rows, not stand-ins.

    `test_jobs_window.py` proves the clause in isolation; this proves the
    plumbing that feeds it - `payload_times`, `_payload_ids` and the case
    assembly - actually reaches a real order's real refund payload.
    """
    retrieved_at = _late_provider_refund(session, seeded)

    cases, _marketplace_only, provider_only = reconcile.load_order_cases(
        session, seeded
    )

    selection = window.select_cases(
        cases,
        provider_only,
        window=window.Window(
            start=datetime(2019, 1, 1, tzinfo=UTC),
            end=datetime(2019, 1, 8, tzinfo=UTC),
            evidence_since=retrieved_at - timedelta(minutes=1),
        ),
        times=window.payload_times(session, seeded),
        provider_times=window.provider_payment_times(session, seeded),
        in_window=frozenset(),
    )

    assert PARKED_ORDER in selection.new_evidence, (
        "the window deliberately contains no order at all, so clause 2 is the "
        "only thing that can select this one"
    )
    assert not selection.in_window
