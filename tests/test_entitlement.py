"""Expected seller entitlement and expected commission.

No database: the engine is pure functions and these fixtures are dataclasses
satisfying the protocols structurally, which is exactly the code a real
reconciliation run executes.

**Every expected figure here is hand-computed**, per specs/roadmap.md Phase 10.
The orders are the real ones from the captured dataset so the arithmetic is
checkable against
specs/2026-11-09-paypal-capture/validation.md section 2, but nothing in this
file reads that table or the seed - a figure derived from the code under test
would prove only that the code agrees with itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from reckonai.engine import (
    CommissionRuleAmbiguity,
    EngineError,
    FixedCommissionCurrencyMismatch,
    MixedCurrencyOrder,
    NoApplicableCommissionRule,
    OrderEntitlement,
    OrderItemLike,
    OrderLike,
    SellerEntitlement,
    commission_for,
    expected_entitlement,
)
from reckonai.money import Money

PLACED = datetime(2026, 11, 1, tzinfo=UTC)

AURORA = uuid.uuid4()
BELLWEATHER = uuid.uuid4()


@dataclass
class Rule:
    """A stand-in for `reckonai.models.CommissionRule`."""

    rate: Decimal
    seller_id: uuid.UUID | None = None
    category: str | None = None
    fixed: Money | None = None
    valid_from: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    valid_to: datetime | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Item:
    """A stand-in for `reckonai.models.OrderItem`."""

    seller_id: uuid.UUID
    line_total: Money
    category: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Order:
    """A stand-in for `reckonai.models.Order`."""

    total: Money
    placed_at: datetime = PLACED
    id: uuid.UUID = field(default_factory=uuid.uuid4)


#: The tenant's one rule, as the captured dataset has it: 10%, all sellers,
#: all categories, no flat component.
TENTH = Rule(rate=Decimal("0.1"))


def test_the_fixtures_satisfy_the_protocols() -> None:
    """Asserted rather than assumed: a mismatch would otherwise surface as a
    confusing `AttributeError` inside the calculation."""
    assert isinstance(
        Item(seller_id=AURORA, line_total=Money("1", "USD")), OrderItemLike
    )
    assert isinstance(Order(total=Money("1", "USD")), OrderLike)


def test_mission_worked_example_first_half() -> None:
    """specs/mission.md's worked example, and ORD-0001 in the captured set.

    160.00 across two sellers at 10%: aurora 90.00, bellweather 54.00,
    commission 16.00. The commission is 10.00 + 6.00, which is what PayPal
    recorded as two separate platform fees on two purchase units - and the
    reason it is summed per seller rather than taken over the order total.
    """
    order = Order(total=Money("160.00", "USD"))
    items = [
        Item(seller_id=AURORA, line_total=Money("100.00", "USD"), category="audio"),
        Item(seller_id=BELLWEATHER, line_total=Money("60.00", "USD"), category="books"),
    ]

    result = expected_entitlement(order, items, [TENTH])

    assert result.commission == Money("16.00", "USD")
    assert result.gross == Money("160.00", "USD")
    assert result.for_seller(AURORA).entitlement == Money("90.00", "USD")
    assert result.for_seller(AURORA).commission == Money("10.00", "USD")
    assert result.for_seller(BELLWEATHER).entitlement == Money("54.00", "USD")
    assert result.for_seller(BELLWEATHER).commission == Money("6.00", "USD")


def test_commission_is_summed_per_seller_not_taken_over_the_order_total() -> None:
    """ORD-0006: 30.00 and 45.00 at 10% give 3.00 and 4.50, summing to 7.50.

    Here the two routes agree. The test exists for the structural claim rather
    than the figure: `commission` is built by adding the per-seller amounts, so
    it stays comparable to the per-purchase-unit platform fees PayPal records
    even where rounding makes the two routes differ.
    """
    order = Order(total=Money("75.00", "USD"))
    items = [
        Item(seller_id=AURORA, line_total=Money("30.00", "USD"), category="audio"),
        Item(seller_id=BELLWEATHER, line_total=Money("45.00", "USD"), category="books"),
    ]

    result = expected_entitlement(order, items, [TENTH])

    assert result.commission == Money("7.50", "USD")
    assert (
        sum((line.commission for line in result.sellers), Money("0", "USD"))
        == result.commission
    )
    assert result.for_seller(AURORA).entitlement == Money("27.00", "USD")
    assert result.for_seller(BELLWEATHER).entitlement == Money("40.50", "USD")


@pytest.mark.parametrize(
    ("gross", "commission", "entitlement", "why"),
    [
        ("33.33", "3.33", "30.00", "3.3330 rounds down"),
        ("59.97", "6.00", "53.97", "5.9970 rounds up"),
        ("50.00", "5.00", "45.00", "exact"),
    ],
)
def test_quantization_happens_once_at_the_commission(
    gross: str, commission: str, entitlement: str, why: str
) -> None:
    """ORD-0007, ORD-0009 and ORD-0002.

    The entitlement is `share - commission` and so is exact by construction.
    Quantizing it independently would let the two disagree by a cent on
    ORD-0007 with neither being wrong, and that is a one-cent exception on a
    correct order.
    """
    order = Order(total=Money(gross, "USD"))
    items = [Item(seller_id=AURORA, line_total=Money(gross, "USD"), category="audio")]

    result = expected_entitlement(order, items, [TENTH])
    line = result.for_seller(AURORA)

    assert line.commission == Money(commission, "USD"), why
    assert line.entitlement == Money(entitlement, "USD")
    assert line.commission + line.entitlement == Money(gross, "USD")


def test_a_non_usd_order_is_computed_in_its_own_currency() -> None:
    """ORD-0008: 121.54 EUR across two sellers.

    No conversion anywhere. The one-cent artefact that makes this order
    interesting is a *comparison* against PayPal's reported settlement, which
    is Phase 11's, not a defect in this calculation.
    """
    order = Order(total=Money("121.54", "EUR"))
    items = [
        Item(seller_id=BELLWEATHER, line_total=Money("40.02", "EUR"), category="books"),
        Item(seller_id=AURORA, line_total=Money("81.52", "EUR"), category="audio"),
    ]

    result = expected_entitlement(order, items, [TENTH])

    assert result.currency == "EUR"
    assert result.commission == Money("12.15", "EUR")
    assert result.for_seller(BELLWEATHER).entitlement == Money("36.02", "EUR")
    assert result.for_seller(AURORA).entitlement == Money("73.37", "EUR")


def test_a_seller_with_several_lines_is_commissioned_on_the_sum() -> None:
    """Two lines, one seller, one category: one share and one commission."""
    order = Order(total=Money("100.00", "USD"))
    items = [
        Item(seller_id=AURORA, line_total=Money("60.00", "USD"), category="audio"),
        Item(seller_id=AURORA, line_total=Money("40.00", "USD"), category="audio"),
    ]

    result = expected_entitlement(order, items, [TENTH])

    assert len(result.sellers) == 1
    assert result.for_seller(AURORA).gross_share == Money("100.00", "USD")
    assert result.for_seller(AURORA).commission == Money("10.00", "USD")
    assert len(result.for_seller(AURORA).item_ids) == 2


# --- the raises, which are the tests that matter most ------------------------


def test_no_applicable_rule_raises_rather_than_charging_zero() -> None:
    """specs/tech-stack.md section 9.6. A zero commission silently applied is a
    wrong financial answer that looks like a correct one."""
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    elsewhere = [Rule(rate=Decimal("0.1"), seller_id=BELLWEATHER)]

    with pytest.raises(NoApplicableCommissionRule):
        expected_entitlement(order, items, elsewhere)


def test_a_stated_zero_rate_resolves_normally() -> None:
    """A 0% rule is a real rate, and distinct from no rule at all (D7).

    The seller that exercised this in Block A - `vendor-dunlin`, not onboarded
    for `PARTNER_FEE` and carrying no platform fee row - no longer exists, so
    the case lives here as a fixture rather than as a seeded row.
    """
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    free = [Rule(rate=Decimal("0"), seller_id=AURORA)]

    result = expected_entitlement(order, items, free)

    assert result.commission == Money("0.00", "USD")
    assert result.for_seller(AURORA).entitlement == Money("50.00", "USD")


def test_ambiguous_rules_raise_rather_than_picking_one() -> None:
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    both = [
        Rule(rate=Decimal("0.1"), seller_id=AURORA),
        Rule(rate=Decimal("0.2"), seller_id=AURORA),
    ]

    with pytest.raises(CommissionRuleAmbiguity):
        expected_entitlement(order, items, both)


def test_an_item_in_another_currency_raises() -> None:
    """specs/mission.md P2: this module converts nothing."""
    order = Order(total=Money("100.00", "USD"))
    items = [
        Item(seller_id=AURORA, line_total=Money("60.00", "USD")),
        Item(seller_id=AURORA, line_total=Money("40.00", "EUR")),
    ]

    with pytest.raises(MixedCurrencyOrder):
        expected_entitlement(order, items, [TENTH])


def test_one_seller_spanning_two_categories_raises() -> None:
    """Two categories resolve to two rules, so the share cannot be commissioned
    as one figure. Raising names the problem; splitting silently would pick a
    rate."""
    order = Order(total=Money("100.00", "USD"))
    items = [
        Item(seller_id=AURORA, line_total=Money("60.00", "USD"), category="audio"),
        Item(seller_id=AURORA, line_total=Money("40.00", "USD"), category="books"),
    ]

    with pytest.raises(EngineError, match="more than one category"):
        expected_entitlement(order, items, [TENTH])


def test_a_naive_placed_at_raises() -> None:
    order = Order(total=Money("50.00", "USD"), placed_at=datetime(2026, 11, 1))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]

    with pytest.raises(EngineError, match="timezone-aware"):
        expected_entitlement(order, items, [TENTH])


# --- the flat component ------------------------------------------------------


def test_a_flat_component_is_added_to_the_percentage() -> None:
    """`commission_rule.fixed` has existed since Phase 2 and was absent from
    the engine's protocol until Phase 10 (D8). A rule carrying one would
    otherwise have had its flat part silently dropped."""
    order = Order(total=Money("100.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("100.00", "USD"))]
    with_flat = [Rule(rate=Decimal("0.1"), fixed=Money("0.30", "USD"))]

    result = expected_entitlement(order, items, with_flat)
    line = result.for_seller(AURORA)

    assert line.commission == Money("10.30", "USD")
    assert line.entitlement == Money("89.70", "USD")
    assert line.fixed_component == Money("0.30", "USD")


def test_an_absent_flat_component_contributes_nothing_and_is_recorded_as_none() -> None:
    """`None` means the rule declares no flat part - a stated structure, not an
    unknown measurement. Distinct from an absent capture fee, which stays
    unknown rather than becoming zero."""
    order = Order(total=Money("100.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("100.00", "USD"))]

    line = expected_entitlement(order, items, [TENTH]).for_seller(AURORA)

    assert line.commission == Money("10.00", "USD")
    assert line.fixed_component is None


def test_a_flat_component_in_another_currency_raises() -> None:
    """Dropping it would understate the commission; converting it is not this
    module's to do."""
    order = Order(total=Money("100.00", "EUR"))
    items = [Item(seller_id=AURORA, line_total=Money("100.00", "EUR"))]
    mismatched = [Rule(rate=Decimal("0.1"), fixed=Money("0.30", "USD"))]

    with pytest.raises(FixedCommissionCurrencyMismatch):
        expected_entitlement(order, items, mismatched)


def test_commission_for_is_callable_on_its_own() -> None:
    """It is exported because Phase 12 exposes the engine as a recompute tool,
    and a commission is the figure most often recomputed in isolation."""
    assert commission_for(Money("100.00", "USD"), Decimal("0.1")) == Money(
        "10.00", "USD"
    )


# --- the result shapes (D2) --------------------------------------------------


def test_the_results_are_frozen() -> None:
    """A mutable result could be adjusted after the fact, which is how a figure
    stops matching the evidence cited beside it."""
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    result = expected_entitlement(order, items, [TENTH])

    with pytest.raises(AttributeError):
        result.commission = Money("0.00", "USD")  # type: ignore[misc]

    with pytest.raises(AttributeError):
        result.sellers[0].entitlement = Money("0.00", "USD")  # type: ignore[misc]


def test_for_seller_raises_on_a_seller_with_no_line() -> None:
    order = Order(total=Money("50.00", "USD"))
    items = [Item(seller_id=AURORA, line_total=Money("50.00", "USD"))]
    result = expected_entitlement(order, items, [TENTH])

    with pytest.raises(KeyError):
        result.for_seller(BELLWEATHER)


def test_the_result_dataclasses_recompute_nothing() -> None:
    """D2: no `__post_init__` arithmetic. A dataclass that recomputes its own
    fields is a second implementation of the calculation, and the two can
    disagree. Constructing a deliberately inconsistent one must therefore be
    possible - this asserts the shape is a record, not a calculator."""
    nonsense = SellerEntitlement(
        seller_id=AURORA,
        gross_share=Money("100.00", "USD"),
        commission=Money("10.00", "USD"),
        entitlement=Money("1.00", "USD"),
        commission_rule_id=uuid.uuid4(),
    )
    assert nonsense.entitlement == Money("1.00", "USD")

    empty = OrderEntitlement(
        order_id=uuid.uuid4(),
        currency="USD",
        sellers=(),
        commission=Money("0.00", "USD"),
        gross=Money("0.00", "USD"),
    )
    assert empty.refunds_applied == ()
