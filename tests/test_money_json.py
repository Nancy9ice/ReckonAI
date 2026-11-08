"""The JSON boundary: amounts leave this system as strings.

specs/roadmap.md Phase 2's fifth exit criterion, and specs/tech-stack.md
section 2's reason for it - a JSON number is an IEEE-754 double, so a bare
`160.00` on the wire is parsed by a browser into a binary value that cannot
represent some decimals exactly.

**This module now covers the serializer only.** The end-to-end half - that
FastAPI's own response encoding does not undo the string form - lived on `GET
/money/example`, which Block A removed as Phase 4 required. The decoder hook
that made it a real check moved to `tests/strict_json.py`, and the property is
asserted against the simulated marketplace's endpoints in
`tests/test_sim_api.py`, which carry money in nearly every response.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from reckonai.money import Money, supported_currencies
from reckonai.schemas import MoneyOut

# ---------------------------------------------------------------------------
# MoneyOut
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("currency", supported_currencies())
def test_money_out_round_trips_every_currency(currency: str) -> None:
    """Iterating the registry, so adding a currency cannot leave this behind."""
    original = Money(Decimal("1234.5"), currency).quantize()

    crossed = MoneyOut.from_money(original)

    assert isinstance(crossed.amount, str)
    assert crossed.to_money() == original


def test_the_amount_is_quantized_on_the_way_out() -> None:
    """requirements.md D5: a boundary that presents a number to a person is a
    rounding boundary, and must be a named one.

    The unrounded value stays in the database; this rounds the view.
    """
    crossed = MoneyOut.from_money(Money(Decimal("104.04507010764778050"), "USD"))

    assert crossed.amount == "104.05"


def test_a_three_decimal_currency_keeps_all_three_digits() -> None:
    """The case a two-decimal assumption would silently truncate."""
    crossed = MoneyOut.from_money(Money(Decimal("12.345"), "KWD"))

    assert crossed.amount == "12.345"
    assert crossed.to_money() == Money(Decimal("12.345"), "KWD")


def test_a_zero_exponent_currency_has_no_decimal_point() -> None:
    assert MoneyOut.from_money(Money(Decimal("1699"), "JPY")).amount == "1699"


def test_money_out_rejects_an_unknown_field() -> None:
    """`extra="forbid"`, so a renamed field upstream fails here rather than
    being dropped on the floor."""
    with pytest.raises(ValueError):
        MoneyOut(amount="1.00", currency="USD", value="1.00")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Reading money in, which is where a JSON number would try to enter
# ---------------------------------------------------------------------------


def test_a_money_field_reads_the_dict_form() -> None:
    from pydantic import BaseModel

    from reckonai.schemas import MoneyField

    class Carrier(BaseModel):
        value: MoneyField

    parsed = Carrier.model_validate({"value": {"amount": "10.00", "currency": "USD"}})

    assert parsed.value == Money("10.00", "USD")


def test_a_money_field_refuses_an_inexact_amount() -> None:
    """A JSON number arriving in a request body is refused at the door.

    `Money` raises for an inexact amount, which Pydantic surfaces as a
    validation error - so the rule in specs/tech-stack.md section 2 holds on the
    way in as well as on the way out.
    """
    from pydantic import BaseModel, ValidationError

    from reckonai.schemas import MoneyField

    class Carrier(BaseModel):
        value: MoneyField

    with pytest.raises(ValidationError):
        Carrier.model_validate({"value": {"amount": 10.00, "currency": "USD"}})


def test_a_money_field_refuses_an_amount_without_a_currency() -> None:
    """specs/tech-stack.md section 9.3 calls this a hard failure, not a warning:
    an amount with no currency is how a system compares across currencies and
    reports confident nonsense."""
    from pydantic import BaseModel, ValidationError

    from reckonai.schemas import MoneyField

    class Carrier(BaseModel):
        value: MoneyField

    with pytest.raises(ValidationError, match="currency"):
        Carrier.model_validate({"value": {"amount": "10.00"}})
