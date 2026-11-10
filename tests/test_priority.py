"""Prioritisation: four factors, own-currency impact, absent is not zero."""

from __future__ import annotations

from decimal import Decimal

from reckonai.money import Money
from reckonai.recon.priority import WEIGHTS, PriorityInput, prioritise


def _item(
    key: str,
    amount: str,
    currency: str = "USD",
    sellers: int = 1,
    age: str = "0",
    confidence: str | None = "0.5",
) -> PriorityInput:
    return PriorityInput(
        key=key,
        impact=Money(Decimal(amount), currency),
        seller_count=sellers,
        age_days=Decimal(age),
        confidence=None if confidence is None else Decimal(confidence),
    )


def test_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == Decimal(1)


def test_larger_impact_scores_higher_all_else_equal() -> None:
    small, large = prioritise([_item("a", "10.00"), _item("b", "100.00")])
    assert large.score > small.score
    assert large.factors["impact"] == Decimal(1)
    assert small.factors["impact"] == Decimal("0.1")


def test_each_other_factor_moves_the_score() -> None:
    base = prioritise([_item("a", "10.00"), _item("b", "10.00")])[0].score
    for kwargs in ({"sellers": 3}, {"age": "30"}, {"confidence": "0.9"}):
        scored = prioritise([_item("a", "10.00"), _item("b", "10.00", **kwargs)])
        assert scored[1].score > base, kwargs


def test_impact_is_ranked_within_its_own_currency_never_converted() -> None:
    usd, eur = prioritise([_item("u", "10.00", "USD"), _item("e", "9000.00", "EUR")])
    # Each is the largest of its own currency, so both have the full factor.
    assert usd.factors["impact"] == eur.factors["impact"] == Decimal(1)


def test_absent_confidence_is_not_scored_as_zero() -> None:
    (none,) = prioritise([_item("a", "10.00", confidence=None)])
    (zero,) = prioritise([_item("a", "10.00", confidence="0")])
    assert none.factors["confidence"] is None
    assert zero.factors["confidence"] == Decimal(0)
    assert none.score > zero.score


def test_zero_impact_everywhere_does_not_divide_by_zero() -> None:
    (only,) = prioritise([_item("a", "0.00")])
    assert only.factors["impact"] == Decimal(0)


def test_factors_saturate() -> None:
    (only,) = prioritise([_item("a", "5.00", sellers=9, age="400")])
    assert only.factors["sellers"] == Decimal(1)
    assert only.factors["age"] == Decimal(1)
