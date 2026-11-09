"""Refund attribution, commission reversal, and the allocation paths.

No database. Every expected figure is hand-computed; the orders are the real
ones from the captured dataset so the arithmetic is checkable against
specs/2026-11-09-paypal-capture/validation.md section 2, but nothing here reads
that table or the seed.

The tests that matter most are the ones asserting a **raise**:
`test_an_unattributable_refund_raises_rather_than_splitting`,
`test_a_refund_exceeding_the_share_raises` and
`test_a_flat_component_makes_the_reversal_undefined`. Each is a place where
producing a number would be worse than refusing, because the number would then
be compared against PayPal's own record and the disagreement reported as a
discrepancy in a correct order.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from reckonai.engine import (
    CaptureLike,
    EngineError,
    FlatComponentReversalUndefined,
    RefundExceedsShare,
    RefundLike,
    UnattributableRefund,
    allocate_refund_across_sellers,
    apply_refund,
    attribute_refund,
    commission_reversal,
    expected_entitlement,
    split_by_rate,
    total_refunded,
)
from reckonai.money import Money

PLACED = datetime(2026, 11, 1, tzinfo=UTC)

AURORA = uuid.uuid4()
BELLWEATHER = uuid.uuid4()
CITRINE = uuid.uuid4()
DUNLIN = uuid.uuid4()


@dataclass
class Rule:
    rate: Decimal
    seller_id: uuid.UUID | None = None
    category: str | None = None
    fixed: Money | None = None
    valid_from: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    valid_to: datetime | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Item:
    seller_id: uuid.UUID
    line_total: Money
    category: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Order:
    total: Money
    placed_at: datetime = PLACED
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Capture:
    seller_id: uuid.UUID | None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Refund:
    amount: Money
    external_id: str = "RFND-TEST"
    capture_id: uuid.UUID | None = None
    capture_linkage_is_derived: bool = True
    seller_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


TENTH = Rule(rate=Decimal("0.1"))


def _two_seller_order() -> tuple[Order, list[Item]]:
    """specs/mission.md's worked example, and ORD-0001."""
    order = Order(total=Money("160.00", "USD"))
    items = [
        Item(seller_id=AURORA, line_total=Money("100.00", "USD"), category="audio"),
        Item(seller_id=BELLWEATHER, line_total=Money("60.00", "USD"), category="books"),
    ]
    return order, items


def test_the_fixtures_satisfy_the_protocols() -> None:
    assert isinstance(Capture(seller_id=AURORA), CaptureLike)
    assert isinstance(Refund(amount=Money("1", "USD")), RefundLike)


# --- the worked example, second half ----------------------------------------


def test_mission_worked_example_second_half() -> None:
    """A 40.00 refund on A's item: A 54.00, B 54.00, commission 12.00.

    specs/roadmap.md Phase 10's first exit criterion, and the half Block A's
    handoff warns is *not* seeded - ORD-0001 carries no refund, so this is a
    pure-function fixture by design.

    B is untouched, which is the whole of D1: the refund reduces the seller
    whose capture it was issued against and nobody else.
    """
    order, items = _two_seller_order()
    expected = expected_entitlement(order, items, [TENTH])

    capture = Capture(seller_id=AURORA)
    refund = Refund(
        amount=Money("40.00", "USD"),
        external_id="RFND-0001-A",
        capture_id=capture.id,
    )

    attribution = attribute_refund(refund, {capture.id: capture})
    after = apply_refund(expected, attribution)

    assert after.for_seller(AURORA).entitlement == Money("54.00", "USD")
    assert after.for_seller(AURORA).commission == Money("6.00", "USD")
    assert after.for_seller(AURORA).gross_share == Money("60.00", "USD")

    assert after.for_seller(BELLWEATHER).entitlement == Money("54.00", "USD")
    assert after.for_seller(BELLWEATHER).commission == Money("6.00", "USD")

    assert after.commission == Money("12.00", "USD")
    assert after.gross == Money("120.00", "USD")
    assert after.refunds_applied == ("RFND-0001-A",)


def test_the_pre_refund_state_survives_the_refund() -> None:
    """Frozen results mean both states can be cited. An investigation that says
    "was 90.00, now 54.00" needs the first figure to still exist."""
    order, items = _two_seller_order()
    expected = expected_entitlement(order, items, [TENTH])
    capture = Capture(seller_id=AURORA)
    refund = Refund(amount=Money("40.00", "USD"), capture_id=capture.id)

    after = apply_refund(expected, attribute_refund(refund, {capture.id: capture}))

    assert expected.for_seller(AURORA).entitlement == Money("90.00", "USD")
    assert after.for_seller(AURORA).entitlement == Money("54.00", "USD")
    assert expected.refunds_applied == ()


@pytest.mark.parametrize(
    ("gross", "refund", "reversal", "entitlement", "source"),
    [
        ("70.00", "20.00", "2.00", "45.00", "ORD-0011"),
        ("110.00", "30.00", "3.00", "72.00", "ORD-0013"),
        ("60.00", "15.00", "1.50", "40.50", "ORD-0017"),
    ],
)
def test_the_seeded_refunds(
    gross: str, refund: str, reversal: str, entitlement: str, source: str
) -> None:
    """The three refunds in the dataset, each hand-computed.

    ORD-0011's reversal of 2.00 is the figure PayPal itself reports in the
    refund's `platform_fees`, and ORD-0013's 3.00 likewise - which is what
    makes these comparable rather than merely internally consistent.
    """
    order = Order(total=Money(gross, "USD"))
    items = [Item(seller_id=AURORA, line_total=Money(gross, "USD"), category="audio")]
    expected = expected_entitlement(order, items, [TENTH])

    capture = Capture(seller_id=AURORA)
    refunded = Refund(
        amount=Money(refund, "USD"), external_id=source, capture_id=capture.id
    )
    after = apply_refund(expected, attribute_refund(refunded, {capture.id: capture}))
    line = after.for_seller(AURORA)

    assert commission_reversal(Money(refund, "USD"), Decimal("0.1")) == Money(
        reversal, "USD"
    )
    assert line.entitlement == Money(entitlement, "USD"), source
    assert line.gross_share == line.commission + line.entitlement


def test_the_parts_still_sum_after_a_refund() -> None:
    """share == commission + entitlement, which is what keeps the reduced line
    internally exact rather than approximately right."""
    order = Order(total=Money("33.33", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("33.33", "USD"))]
    expected = expected_entitlement(order, items, [TENTH])
    capture = Capture(seller_id=AURORA)
    refund = Refund(amount=Money("0.07", "USD"), capture_id=capture.id)

    line = apply_refund(
        expected, attribute_refund(refund, {capture.id: capture})
    ).for_seller(AURORA)

    assert line.gross_share == Money("33.26", "USD")
    assert line.gross_share == line.commission + line.entitlement


def test_two_refunds_apply_in_sequence() -> None:
    """Each returns a new state, so they compose."""
    order = Order(total=Money("100.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("100.00", "USD"))]
    expected = expected_entitlement(order, items, [TENTH])
    capture = Capture(seller_id=AURORA)
    captures = {capture.id: capture}

    once = apply_refund(
        expected,
        attribute_refund(
            Refund(
                amount=Money("30.00", "USD"), external_id="R1", capture_id=capture.id
            ),
            captures,
        ),
    )
    twice = apply_refund(
        once,
        attribute_refund(
            Refund(
                amount=Money("20.00", "USD"), external_id="R2", capture_id=capture.id
            ),
            captures,
        ),
    )

    assert twice.for_seller(AURORA).gross_share == Money("50.00", "USD")
    assert twice.for_seller(AURORA).commission == Money("5.00", "USD")
    assert twice.for_seller(AURORA).entitlement == Money("45.00", "USD")
    assert twice.refunds_applied == ("R1", "R2")


# --- attribution -------------------------------------------------------------


def test_the_capture_is_preferred_over_the_refunds_own_seller_id() -> None:
    """The capture is the provider's link; `refund.seller_id` is a column our
    ingester populated. They should agree, and when they do not the provider
    wins and the disagreement is Phase 11's to report."""
    capture = Capture(seller_id=AURORA)
    refund = Refund(
        amount=Money("10.00", "USD"),
        capture_id=capture.id,
        seller_id=BELLWEATHER,
    )

    attribution = attribute_refund(refund, {capture.id: capture})

    assert attribution.seller_id == AURORA
    assert attribution.via == "capture"


def test_the_refunds_seller_id_is_used_when_no_capture_resolves() -> None:
    refund = Refund(amount=Money("10.00", "USD"), seller_id=BELLWEATHER)

    attribution = attribute_refund(refund, {})

    assert attribution.seller_id == BELLWEATHER
    assert attribution.via == "refund_seller_id"


def test_the_derived_linkage_flag_is_carried() -> None:
    """PayPal supplies no capture_id on a refund, only a HATEOAS link. Phase 11
    must be able to say an exception rests on a derived linkage."""
    capture = Capture(seller_id=AURORA)
    derived = Refund(
        amount=Money("10.00", "USD"),
        capture_id=capture.id,
        capture_linkage_is_derived=True,
    )

    assert attribute_refund(derived, {capture.id: capture}).linkage_is_derived


def test_an_unattributable_refund_raises_rather_than_splitting() -> None:
    """D1: a gap in the evidence does not silently become a proportional
    split."""
    orphan = Refund(amount=Money("10.00", "USD"), capture_id=uuid.uuid4())

    with pytest.raises(UnattributableRefund):
        attribute_refund(orphan, {})


def test_a_capture_without_a_seller_does_not_attribute() -> None:
    """`capture.seller_id` is nullable because resolution can fail."""
    unresolved = Capture(seller_id=None)
    refund = Refund(amount=Money("10.00", "USD"), capture_id=unresolved.id)

    with pytest.raises(UnattributableRefund):
        attribute_refund(refund, {unresolved.id: unresolved})


# --- the raises --------------------------------------------------------------


def test_a_refund_exceeding_the_share_raises() -> None:
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    expected = expected_entitlement(order, items, [TENTH])
    capture = Capture(seller_id=AURORA)
    too_big = Refund(amount=Money("50.01", "USD"), capture_id=capture.id)

    with pytest.raises(RefundExceedsShare):
        apply_refund(expected, attribute_refund(too_big, {capture.id: capture}))


def test_a_full_refund_is_allowed_and_zeroes_the_line() -> None:
    """The boundary the test above sits just past."""
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    expected = expected_entitlement(order, items, [TENTH])
    capture = Capture(seller_id=AURORA)
    whole = Refund(amount=Money("50.00", "USD"), capture_id=capture.id)

    line = apply_refund(
        expected, attribute_refund(whole, {capture.id: capture})
    ).for_seller(AURORA)

    assert line.gross_share == Money("0.00", "USD")
    assert line.commission == Money("0.00", "USD")
    assert line.entitlement == Money("0.00", "USD")


def test_a_refund_in_another_currency_raises() -> None:
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    expected = expected_entitlement(order, items, [TENTH])
    capture = Capture(seller_id=AURORA)
    wrong = Refund(amount=Money("10.00", "EUR"), capture_id=capture.id)

    with pytest.raises(EngineError, match="converts nothing"):
        apply_refund(expected, attribute_refund(wrong, {capture.id: capture}))


def test_a_flat_component_makes_the_reversal_undefined() -> None:
    """Retained or reversed pro rata are both defensible, and the marketplace's
    rules decide. Guessing produces a commission figure Phase 11 compares
    against PayPal's platform fee, so a wrong guess becomes a discrepancy in a
    correct order."""
    order = Order(total=Money("100.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("100.00", "USD"))]
    with_flat = [Rule(rate=Decimal("0.1"), fixed=Money("0.30", "USD"))]
    expected = expected_entitlement(order, items, with_flat)
    capture = Capture(seller_id=AURORA)
    refund = Refund(amount=Money("10.00", "USD"), capture_id=capture.id)

    with pytest.raises(FlatComponentReversalUndefined):
        apply_refund(expected, attribute_refund(refund, {capture.id: capture}))


def test_a_refund_for_a_seller_with_no_line_raises() -> None:
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    expected = expected_entitlement(order, items, [TENTH])
    elsewhere = Capture(seller_id=BELLWEATHER)
    refund = Refund(amount=Money("10.00", "USD"), capture_id=elsewhere.id)

    with pytest.raises(KeyError):
        apply_refund(expected, attribute_refund(refund, {elsewhere.id: elsewhere}))


# --- the deliberate proportional path ---------------------------------------


def test_an_order_level_refund_allocates_by_gross_share() -> None:
    """The path `attribute_refund` refuses to take by accident."""
    order, items = _two_seller_order()
    expected = expected_entitlement(order, items, [TENTH])

    parts = allocate_refund_across_sellers(
        expected, Money("16.00", "USD"), "RFND-ORDER"
    )

    assert [part.amount for part in parts] == [
        Money("10.00", "USD"),
        Money("6.00", "USD"),
    ]
    assert {part.seller_id for part in parts} == {AURORA, BELLWEATHER}
    assert all(part.via == "allocated" for part in parts)
    assert total_refunded(parts, "USD") == Money("16.00", "USD")


@pytest.mark.parametrize("sellers", [3, 4, 5])
def test_allocation_across_three_or_more_sellers_leaves_no_residue(
    sellers: int,
) -> None:
    """specs/roadmap.md Phase 10's second exit criterion.

    0.01 across three equal shares is the canonical residue case: a third of a
    cent cannot be paid, so one seller takes the spare unit and the parts must
    still sum exactly.
    """
    ids = [AURORA, BELLWEATHER, CITRINE, DUNLIN, uuid.uuid4()][:sellers]
    order = Order(total=Money("100.00", "USD"))
    items = [Item(seller_id=seller, line_total=Money("20.00", "USD")) for seller in ids]
    expected = expected_entitlement(order, items, [TENTH])

    for amount in ("0.01", "0.02", "33.33", "100.00"):
        parts = allocate_refund_across_sellers(
            expected, Money(amount, "USD"), "RFND-ORDER"
        )
        assert len(parts) == sellers
        assert total_refunded(parts, "USD") == Money(amount, "USD"), amount


def test_an_order_level_refund_on_an_order_with_no_sellers_raises() -> None:
    order = Order(total=Money("0.00", "USD"))
    expected = expected_entitlement(order, [], [TENTH])

    with pytest.raises(UnattributableRefund):
        allocate_refund_across_sellers(expected, Money("1.00", "USD"), "RFND")


# --- one seller, two rates ---------------------------------------------------


def test_split_by_rate_commissions_each_item_at_its_own_rate() -> None:
    """D1 case 1, and the case `expected_entitlement` refuses outright.

    A 50.00 refund across a 60.00 item at 10% and a 40.00 item at 20%: the
    refund splits 30.00 / 20.00 by share, and the reversals are 3.00 and 4.00.
    A single blended rate would give neither.
    """
    pairs = split_by_rate(
        Money("50.00", "USD"),
        [Money("60.00", "USD"), Money("40.00", "USD")],
        [Decimal("0.1"), Decimal("0.2")],
    )

    assert [part for part, _ in pairs] == [
        Money("30.00", "USD"),
        Money("20.00", "USD"),
    ]
    assert [reversal for _, reversal in pairs] == [
        Money("3.00", "USD"),
        Money("4.00", "USD"),
    ]


def test_split_by_rate_parts_sum_exactly() -> None:
    pairs = split_by_rate(
        Money("0.01", "USD"),
        [Money("1.00", "USD"), Money("1.00", "USD"), Money("1.00", "USD")],
        [Decimal("0.1"), Decimal("0.1"), Decimal("0.1")],
    )

    assert sum((part for part, _ in pairs), Money("0", "USD")) == Money("0.01", "USD")


def test_split_by_rate_refuses_mismatched_lengths() -> None:
    with pytest.raises(EngineError, match="one rate per"):
        split_by_rate(
            Money("10.00", "USD"),
            [Money("10.00", "USD")],
            [Decimal("0.1"), Decimal("0.2")],
        )


def test_split_by_rate_refuses_a_foreign_share() -> None:
    with pytest.raises(EngineError, match="converts nothing"):
        split_by_rate(Money("10.00", "USD"), [Money("10.00", "EUR")], [Decimal("0.1")])


def test_split_by_rate_refuses_no_items() -> None:
    with pytest.raises(EngineError):
        split_by_rate(Money("10.00", "USD"), [], [])


def test_total_refunded_skips_other_currencies() -> None:
    order, items = _two_seller_order()
    expected = expected_entitlement(order, items, [TENTH])
    usd = allocate_refund_across_sellers(expected, Money("16.00", "USD"), "R")

    assert total_refunded(usd, "EUR") == Money("0.00", "EUR")
