"""Per-seller, per-currency expected positions, and the unknown-fee record.

No database. The figures are the captured dataset's per-seller positions from
specs/2026-11-09-paypal-capture/validation.md section 2, hand-written here
rather than read from the seed - a third independent derivation alongside
`data.EXPECTED_POSITIONS` and that table.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from reckonai.engine import (
    CaptureFeeLike,
    SellerPosition,
    apply_refund,
    attribute_refund,
    expected_commission,
    expected_entitlement,
    expected_positions,
)
from reckonai.money import Money

PLACED = datetime(2026, 11, 1, tzinfo=UTC)

AURORA = uuid.uuid4()
BELLWEATHER = uuid.uuid4()


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
    """A stand-in for `reckonai.models.Capture`, fee fields only."""

    amount: Money
    seller_id: uuid.UUID | None
    paypal_fee: Money | None = None
    external_id: str = "CAP-TEST"


@dataclass
class Refund:
    amount: Money
    external_id: str = "RFND-TEST"
    capture_id: uuid.UUID | None = None
    capture_linkage_is_derived: bool = True
    seller_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


TENTH = Rule(rate=Decimal("0.1"))


def _one(seller: uuid.UUID, amount: str, currency: str = "USD") -> Order:
    return Order(total=Money(amount, currency))


def _entitlement(seller: uuid.UUID, amount: str, currency: str = "USD"):
    order = _one(seller, amount, currency)
    items = [Item(seller_id=seller, line_total=Money(amount, currency))]
    return expected_entitlement(order, items, [TENTH])


def test_the_capture_fixture_satisfies_the_protocol() -> None:
    assert isinstance(
        Capture(amount=Money("1", "USD"), seller_id=AURORA), CaptureFeeLike
    )


def test_one_seller_one_currency_sums_across_orders() -> None:
    """ORD-0002, ORD-0004, ORD-0010: 45.00 + 72.00 + 40.50."""
    results = [
        _entitlement(AURORA, "50.00"),
        _entitlement(AURORA, "80.00"),
        _entitlement(AURORA, "45.00"),
    ]

    positions = expected_positions(results)

    assert len(positions) == 1
    assert positions[0].seller_id == AURORA
    assert positions[0].currency == "USD"
    assert positions[0].expected == Money("157.50", "USD")
    assert len(positions[0].order_ids) == 3


def test_a_seller_trading_in_two_currencies_gets_two_rows() -> None:
    """Never a mixed total. `Money` would refuse the addition, and a single
    number over two currencies would be meaningless even if it did not."""
    results = [
        _entitlement(BELLWEATHER, "120.00", "USD"),
        _entitlement(BELLWEATHER, "200.00", "EUR"),
    ]

    positions = expected_positions(results)

    assert len(positions) == 2
    assert [(p.currency, p.expected) for p in positions] == [
        ("EUR", Money("180.00", "EUR")),
        ("USD", Money("108.00", "USD")),
    ]


def test_the_bellweather_position_from_the_hand_check_table() -> None:
    """bellweather USD 256.47 = 54 + 108 + 40.50 + 53.97.

    ORD-0001's share, ORD-0003, ORD-0006's share and ORD-0009.
    """
    first = expected_entitlement(
        Order(total=Money("160.00", "USD")),
        [
            Item(seller_id=AURORA, line_total=Money("100.00", "USD")),
            Item(seller_id=BELLWEATHER, line_total=Money("60.00", "USD")),
        ],
        [TENTH],
    )
    sixth = expected_entitlement(
        Order(total=Money("75.00", "USD")),
        [
            Item(seller_id=AURORA, line_total=Money("30.00", "USD")),
            Item(seller_id=BELLWEATHER, line_total=Money("45.00", "USD")),
        ],
        [TENTH],
    )
    results = [
        first,
        _entitlement(BELLWEATHER, "120.00"),
        sixth,
        _entitlement(BELLWEATHER, "59.97"),
    ]

    by_seller = {p.seller_id: p for p in expected_positions(results)}

    assert by_seller[BELLWEATHER].expected == Money("256.47", "USD")


def test_a_refunded_entitlement_reduces_the_position() -> None:
    """ORD-0011 contributes 63 - 20 + 2 = 45.00, which is the hand-check
    table's own derivation. The position function knows nothing about refunds:
    a refunded entitlement is simply a smaller one."""
    order = Order(total=Money("70.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("70.00", "USD"))]
    before = expected_entitlement(order, items, [TENTH])

    capture = Capture(amount=Money("70.00", "USD"), seller_id=AURORA)
    capture_id = uuid.uuid4()
    refund = Refund(amount=Money("20.00", "USD"), capture_id=capture_id)

    @dataclass
    class Linked:
        id: uuid.UUID
        seller_id: uuid.UUID | None

    after = apply_refund(
        before,
        attribute_refund(refund, {capture_id: Linked(capture_id, AURORA)}),
    )

    positions = expected_positions([after], [capture])

    assert positions[0].expected == Money("45.00", "USD")


def test_commission_totals_are_per_currency() -> None:
    """The hand-check table's totals are USD 67.33 and EUR 44.15, which are two
    answers rather than one."""
    results = [
        _entitlement(AURORA, "50.00", "USD"),
        _entitlement(BELLWEATHER, "200.00", "EUR"),
    ]

    totals = expected_commission(results)

    assert totals == {"USD": Money("5.00", "USD"), "EUR": Money("20.00", "EUR")}


def test_an_empty_input_produces_no_positions() -> None:
    assert expected_positions([]) == ()
    assert expected_commission([]) == {}


# --- the unknown capture fee (D3, revised) -----------------------------------


def test_a_capture_with_no_stated_fee_is_recorded_as_unknown() -> None:
    """specs/roadmap.md Phase 10: a capture fee is never defaulted to zero.

    Every real capture in the dataset omits `paypal_fee`, so this is the normal
    case rather than an edge one.
    """
    results = [_entitlement(AURORA, "100.00")]
    captures = [
        Capture(
            amount=Money("100.00", "USD"),
            seller_id=AURORA,
            paypal_fee=None,
            external_id="CAP-1",
        )
    ]

    position = expected_positions(results, captures)[0]

    assert position.unknown_fee_capture_ids == ("CAP-1",)
    assert not position.fee_is_known
    assert position.expected == Money("90.00", "USD")


def test_a_stated_fee_leaves_nothing_unknown() -> None:
    """A fee of 0.00 stated explicitly is a measurement, not an absence - which
    is how PayPal reports a refund's fee, and the distinction the engine must
    not collapse."""
    results = [_entitlement(AURORA, "100.00")]
    captures = [
        Capture(
            amount=Money("100.00", "USD"),
            seller_id=AURORA,
            paypal_fee=Money("0.00", "USD"),
            external_id="CAP-1",
        )
    ]

    position = expected_positions(results, captures)[0]

    assert position.unknown_fee_capture_ids == ()
    assert position.fee_is_known


def test_the_position_is_unaffected_by_whether_the_fee_is_known() -> None:
    """The reason D3 was revised. PayPal's `net_amount` equals
    `gross - platform_fees` exactly on all 21 captured captures, so the
    expected entitlement is comparable to it with or without a processing fee,
    and the fee's absence changes no figure - only what can be claimed about
    what the seller banked."""
    results = [_entitlement(AURORA, "100.00")]
    unknown = [Capture(amount=Money("100.00", "USD"), seller_id=AURORA)]
    known = [
        Capture(
            amount=Money("100.00", "USD"),
            seller_id=AURORA,
            paypal_fee=Money("2.90", "USD"),
        )
    ]

    assert (
        expected_positions(results, unknown)[0].expected
        == expected_positions(results, known)[0].expected
    )


def test_passing_no_captures_asserts_nothing_about_fees() -> None:
    """An empty `unknown_fee_capture_ids` from no captures means "not
    examined", and `fee_is_known` reading True there is why a caller wanting
    the claim must supply the captures."""
    position = expected_positions([_entitlement(AURORA, "100.00")])[0]

    assert position.unknown_fee_capture_ids == ()


def test_an_unknown_fee_is_scoped_to_its_own_currency() -> None:
    results = [
        _entitlement(AURORA, "100.00", "USD"),
        _entitlement(AURORA, "100.00", "EUR"),
    ]
    captures = [
        Capture(amount=Money("100.00", "USD"), seller_id=AURORA, external_id="CAP-USD")
    ]

    by_currency = {p.currency: p for p in expected_positions(results, captures)}

    assert by_currency["USD"].unknown_fee_capture_ids == ("CAP-USD",)
    assert by_currency["EUR"].unknown_fee_capture_ids == ()


def test_a_capture_with_no_seller_is_skipped() -> None:
    """`capture.seller_id` is nullable because resolution can fail. It cannot
    be attributed to a position, and guessing one would be worse."""
    results = [_entitlement(AURORA, "100.00")]
    orphan = [Capture(amount=Money("100.00", "USD"), seller_id=None)]

    assert expected_positions(results, orphan)[0].unknown_fee_capture_ids == ()


def test_an_unknown_fee_for_a_seller_with_no_entitlement_still_reaches_a_row() -> None:
    """Found by review, 2026-11-09.

    `unknown` was read only for `(seller, currency)` keys that already had an
    entitlement line, so a capture for a seller with no order in this set was
    discarded and the result reported `fee_is_known` as True - the exact
    collapse this module exists to prevent.

    The zero expected position is a real answer rather than an invented one:
    the currency came from the capture and the sum of no entitlements is
    genuinely nothing. What would be invented is a fee, and none is.
    """
    results = [_entitlement(AURORA, "100.00")]
    captures = [
        Capture(
            amount=Money("50.00", "USD"), seller_id=BELLWEATHER, external_id="CAP-B"
        )
    ]

    by_seller = {p.seller_id: p for p in expected_positions(results, captures)}

    assert BELLWEATHER in by_seller, "the capture's seller was dropped entirely"
    assert by_seller[BELLWEATHER].unknown_fee_capture_ids == ("CAP-B",)
    assert not by_seller[BELLWEATHER].fee_is_known
    assert by_seller[BELLWEATHER].expected == Money("0.00", "USD")
    assert by_seller[BELLWEATHER].order_ids == ()


def test_an_unknown_fee_in_an_unentitled_currency_still_reaches_a_row() -> None:
    """The same drop, by currency rather than by seller: a EUR capture for a
    seller who has only a USD entitlement."""
    results = [_entitlement(AURORA, "100.00", "USD")]
    captures = [
        Capture(amount=Money("50.00", "EUR"), seller_id=AURORA, external_id="CAP-EUR")
    ]

    by_currency = {p.currency: p for p in expected_positions(results, captures)}

    assert "EUR" in by_currency, "the EUR capture's fee gap was dropped"
    assert by_currency["EUR"].unknown_fee_capture_ids == ("CAP-EUR",)
    assert by_currency["EUR"].expected == Money("0.00", "EUR")
    assert by_currency["USD"].expected == Money("90.00", "USD")


def test_fee_is_known_cannot_disagree_with_the_ids() -> None:
    """A property rather than a stored field, so the two cannot drift."""
    position = SellerPosition(
        seller_id=AURORA,
        currency="USD",
        expected=Money("90.00", "USD"),
        order_ids=(),
        unknown_fee_capture_ids=("CAP-1",),
    )

    assert not position.fee_is_known
