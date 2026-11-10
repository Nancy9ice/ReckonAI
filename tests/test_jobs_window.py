"""The two selection clauses.

`specs/2026-11-09-queue-scheduled-run/validation.md` section 3, and
`requirements.md` D3 including the amendment that gave it clause 2.

**Clause 2 is tested on its own**, so the test fails if it is removed. That
matters more than it sounds: clause 1 passes every test a seeded dataset can
offer, and the shape clause 2 exists for - a dispute settled by a refund 30
days after the order was placed - is invisible to clause 1 by construction.

`select_cases` is pure, so most of this needs no database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from reckonai.jobs import window
from reckonai.models import Order, Tenant
from reckonai.money import Money

NOV = datetime(2026, 11, 1, tzinfo=UTC)
WINDOW = window.Window(start=NOV, end=NOV + timedelta(days=10))


# ---------------------------------------------------------------------------
# Stand-ins, so the pure clause can be tested without assembling a real case
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Order:
    id: uuid.UUID
    total: Money
    placed_at: datetime


@dataclass(frozen=True)
class _Record:
    """Any record behind a case. Only its payload link is read here."""

    raw_payload_id: uuid.UUID


@dataclass(frozen=True)
class _Case:
    """Enough of an `OrderCase` for the selection clause.

    A real `OrderCase` is a frozen dataclass with ten fields and two required
    protocols; constructing one here would test the constructor rather than
    the clause. `select_cases` reads `order_ref`, `order.placed_at` and the
    record tuples, and this carries exactly those.
    """

    order_ref: str
    order: _Order
    items: tuple[_Record, ...] = ()
    captures: tuple[_Record, ...] = ()
    platform_fees: tuple[_Record, ...] = ()
    disbursements: tuple[_Record, ...] = ()
    marketplace_refunds: tuple[_Record, ...] = ()
    provider_refunds: tuple[_Record, ...] = ()
    ledger_entries: tuple[_Record, ...] = ()


def _case(ref: str, placed_at: datetime, **groups: tuple[_Record, ...]) -> _Case:
    return _Case(
        order_ref=ref,
        order=_Order(
            id=uuid.uuid4(), total=Money(Decimal("100.00"), "USD"), placed_at=placed_at
        ),
        **groups,
    )


# ---------------------------------------------------------------------------
# Clause 1
# ---------------------------------------------------------------------------


def test_an_order_placed_in_the_window_is_selected(
    session: Session, tenant: Tenant
) -> None:
    """Clause 1, in SQL, because it must cover orders that have no case."""
    session.add_all(
        [
            Order(
                tenant_id=tenant.id,
                external_ref="ORD-IN",
                total=Money(Decimal("10.00"), "USD"),
                placed_at=NOV + timedelta(days=2),
            ),
            Order(
                tenant_id=tenant.id,
                external_ref="ORD-BEFORE",
                total=Money(Decimal("10.00"), "USD"),
                placed_at=NOV - timedelta(days=1),
            ),
            Order(
                tenant_id=tenant.id,
                external_ref="ORD-AFTER",
                total=Money(Decimal("10.00"), "USD"),
                placed_at=NOV + timedelta(days=40),
            ),
        ]
    )
    session.flush()

    selected = window.orders_in_window(session, tenant.id, WINDOW)

    assert selected == {"ORD-IN"}


def test_the_window_is_half_open(session: Session, tenant: Tenant) -> None:
    """`>= start`, `< end`, so consecutive runs cover each day exactly once.

    An inclusive end would reconcile the boundary day in two runs, which is
    not wrong but writes two sets of rows for one day and makes a run summary
    double-count.
    """
    session.add_all(
        [
            Order(
                tenant_id=tenant.id,
                external_ref="ORD-START",
                total=Money(Decimal("10.00"), "USD"),
                placed_at=WINDOW.start,
            ),
            Order(
                tenant_id=tenant.id,
                external_ref="ORD-END",
                total=Money(Decimal("10.00"), "USD"),
                placed_at=WINDOW.end,
            ),
        ]
    )
    session.flush()

    selected = window.orders_in_window(session, tenant.id, WINDOW)

    assert selected == {"ORD-START"}


def test_clause_one_is_scoped_by_tenant(
    session: Session, tenant: Tenant, other_tenant: Tenant
) -> None:
    session.add(
        Order(
            tenant_id=other_tenant.id,
            external_ref="ORD-THEIRS",
            total=Money(Decimal("10.00"), "USD"),
            placed_at=NOV + timedelta(days=2),
        )
    )
    session.flush()

    assert window.orders_in_window(session, tenant.id, WINDOW) == frozenset()


# ---------------------------------------------------------------------------
# Clause 2 - the dispute case
# ---------------------------------------------------------------------------


def test_a_late_refund_selects_an_order_the_window_has_moved_past() -> None:
    """D3's amendment, and the only test that proves clause 2 exists.

    The shape: an order placed on 1 October, a dispute opened days later,
    PayPal deciding it weeks later, and the refund that settles it ingested on
    9 November. A trailing window over November cannot contain 1 October, so
    clause 1 will never re-select this order - the late refund would never be
    read and the verdict would never change.
    """
    last_run = datetime(2026, 11, 8, tzinfo=UTC)
    refund_payload = uuid.uuid4()

    old_order = _case(
        "ORD-DISPUTED",
        placed_at=datetime(2026, 10, 1, tzinfo=UTC),
        provider_refunds=(_Record(refund_payload),),
    )

    selection = window.select_cases(
        [old_order],
        [],
        window=window.Window(
            start=NOV, end=NOV + timedelta(days=10), evidence_since=last_run
        ),
        times={refund_payload: datetime(2026, 11, 9, tzinfo=UTC)},
        in_window=frozenset(),
    )

    assert selection.new_evidence == {"ORD-DISPUTED"}
    assert selection.order_refs == {"ORD-DISPUTED"}
    assert not selection.in_window, (
        "the order is far outside the window; clause 1 must not claim it"
    )


def test_an_old_order_with_no_new_evidence_is_not_selected() -> None:
    """Clause 2 is bounded by construction: a steady state selects nothing.

    Without this the clause would re-reconcile the entire history on every
    run, which is a correctness-neutral but unbounded amount of work.
    """
    payload = uuid.uuid4()

    selection = window.select_cases(
        [
            _case(
                "ORD-OLD",
                placed_at=datetime(2026, 10, 1, tzinfo=UTC),
                captures=(_Record(payload),),
            )
        ],
        [],
        window=window.Window(
            start=NOV,
            end=NOV + timedelta(days=10),
            evidence_since=datetime(2026, 11, 8, tzinfo=UTC),
        ),
        # Retrieved before the last run, so it is not new evidence.
        times={payload: datetime(2026, 10, 1, tzinfo=UTC)},
        in_window=frozenset(),
    )

    assert not selection.order_refs


def test_the_first_run_has_no_watermark_and_uses_clause_one_alone() -> None:
    """`evidence_since` is None on a tenant's first run, which is why clause 2
    needs no special case: it selects nothing and the window stands alone."""
    payload = uuid.uuid4()

    selection = window.select_cases(
        [
            _case(
                "ORD-OLD",
                placed_at=datetime(2026, 10, 1, tzinfo=UTC),
                captures=(_Record(payload),),
            )
        ],
        [],
        window=window.Window(start=NOV, end=NOV + timedelta(days=10)),
        times={payload: datetime(2026, 11, 9, tzinfo=UTC)},
        in_window=frozenset(),
    )

    assert not selection.new_evidence


def test_an_order_in_the_window_is_not_double_counted() -> None:
    """The two clauses are a union, and the sets are kept disjoint.

    `order_count` is read off this, so an order matching both clauses would
    otherwise be counted twice in a run summary.
    """
    payload = uuid.uuid4()

    selection = window.select_cases(
        [
            _case(
                "ORD-BOTH",
                placed_at=NOV + timedelta(days=1),
                captures=(_Record(payload),),
            )
        ],
        [],
        window=window.Window(
            start=NOV,
            end=NOV + timedelta(days=10),
            evidence_since=datetime(2026, 11, 8, tzinfo=UTC),
        ),
        times={payload: datetime(2026, 11, 9, tzinfo=UTC)},
        in_window=frozenset({"ORD-BOTH"}),
    )

    assert selection.in_window == {"ORD-BOTH"}
    assert not selection.new_evidence
    assert len(selection) == 1


def test_evidence_time_is_the_latest_of_a_cases_records() -> None:
    """An order's evidence is as new as its newest record.

    Taking the oldest, or the order's own, would miss exactly the case clause
    2 is for: everything about a disputed order is old except the refund that
    just settled it.
    """
    old, new = uuid.uuid4(), uuid.uuid4()
    case = _case(
        "ORD-MIXED",
        placed_at=datetime(2026, 10, 1, tzinfo=UTC),
        captures=(_Record(old),),
        provider_refunds=(_Record(new),),
    )

    latest = window.latest_evidence(
        case,  # type: ignore[arg-type]
        {
            old: datetime(2026, 10, 1, tzinfo=UTC),
            new: datetime(2026, 11, 9, tzinfo=UTC),
        },
    )

    assert latest == datetime(2026, 11, 9, tzinfo=UTC)


def test_a_case_with_no_known_payloads_has_no_evidence_time() -> None:
    """Rather than raising. A record whose payload is unknown contributes no
    evidence time and is simply not selected by clause 2 - it can never mean
    a wrong figure, which is why `_payload_ids` tolerates the absence."""
    case = _case("ORD-BARE", placed_at=datetime(2026, 10, 1, tzinfo=UTC))

    assert window.latest_evidence(case, {}) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unmatched provider payments
# ---------------------------------------------------------------------------


def test_a_newly_arrived_provider_payment_is_selected() -> None:
    """A payment matched to no order has no `placed_at`, so clause 1 cannot
    reach it. Dropping it would make a late provider record invisible, which
    is the same failure clause 2 exists to prevent."""
    selection = window.select_cases(
        [],
        ["PAY-NEW", "PAY-OLD"],
        window=window.Window(
            start=NOV,
            end=NOV + timedelta(days=10),
            evidence_since=datetime(2026, 11, 8, tzinfo=UTC),
        ),
        times={},
        provider_times={
            "PAY-NEW": datetime(2026, 11, 9, tzinfo=UTC),
            "PAY-OLD": datetime(2026, 10, 1, tzinfo=UTC),
        },
        in_window=frozenset(),
    )

    assert selection.provider_refs == {"PAY-NEW"}
    assert selection.all_refs == {"PAY-NEW"}


# ---------------------------------------------------------------------------
# Code review finding 2 - unmatched provider payments were dropped forever
# ---------------------------------------------------------------------------


def test_an_unmatched_provider_payment_is_reported_on_the_first_run() -> None:
    """With no watermark, every unmatched payment is selected.

    Review found these dropped permanently: clause 1 cannot reach a payment
    with no `placed_at`, the first run has no watermark, and every later run's
    watermark is already past the payload's `retrieved_at` - so a payment
    matching no marketplace order produced an `awaiting_evidence` finding that
    the windowed run filtered out of every run, forever. `reconcile.py` goes
    out of its way not to lose that side.
    """
    selection = window.select_cases(
        [],
        ["PAY-ORPHAN"],
        window=window.Window(start=NOV, end=NOV + timedelta(days=10)),
        times={},
        provider_times={"PAY-ORPHAN": datetime(2026, 10, 1, tzinfo=UTC)},
        in_window=frozenset(),
    )

    assert selection.provider_refs == {"PAY-ORPHAN"}


def test_an_already_reported_provider_payment_is_not_re_reported() -> None:
    """Once a watermark exists, only newly arrived ones are selected.

    An unmatched payment already reported does not need re-reporting on every
    run until someone resolves it - the append-only history already holds the
    finding.
    """
    selection = window.select_cases(
        [],
        ["PAY-OLD"],
        window=window.Window(
            start=NOV,
            end=NOV + timedelta(days=10),
            evidence_since=datetime(2026, 11, 8, tzinfo=UTC),
        ),
        times={},
        provider_times={"PAY-OLD": datetime(2026, 10, 1, tzinfo=UTC)},
        in_window=frozenset(),
    )

    assert not selection.provider_refs
