"""The JSON boundary: amounts leave this system as strings.

specs/roadmap.md Phase 2's fifth exit criterion, and specs/tech-stack.md
section 2's reason for it - a JSON number is an IEEE-754 double, so a bare
`160.00` on the wire is parsed by a browser into a binary value that cannot
represent some decimals exactly.

**The load-bearing test here is `test_no_amount_crosses_as_a_json_number`.**
Everything else checks the shape; that one checks the property, and it is the
only one of them that could fail while the others pass.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, NoReturn

import pytest
from fastapi.testclient import TestClient

from reckonai.app import app
from reckonai.money import Money, supported_currencies
from reckonai.schemas import MoneyOut


@pytest.fixture
def client() -> TestClient:
    """A client that does not run the lifespan, so no database is needed.

    `/money/example` touches nothing but the serializer. Entering the lifespan
    would build an engine and make this suite require a database for no reason,
    which would contradict the point of testing a pure boundary.
    """
    return TestClient(app)


def _reject_json_number(raw: str) -> NoReturn:
    """A `json.loads` hook that fails if the decoder meets a decimal number.

    This is the mechanism behind the main test, and it is worth stating plainly
    why the obvious alternative does not work.

    Asserting `isinstance(body["amount"], str)` proves nothing: by the time
    that assertion runs, `json.loads` has *already* turned a bare `160.00` into
    a binary number, and the assertion would be inspecting the wrong object -
    it would fail, but only by luck of type, and it would pass for an integer
    amount such as `160` which `json` parses with `parse_int`.

    Hooking the decoder catches the value as it is decoded, before anything can
    look at it.
    """
    raise AssertionError(
        f"a monetary value crossed JSON as a number, not a string: {raw!r}"
    )


def _decode_strictly(body: bytes) -> Any:
    """Decode a response body, failing on any non-integer JSON number."""
    return json.loads(body, parse_float=_reject_json_number)


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
# The annotated MoneyField, through the real application
# ---------------------------------------------------------------------------


def test_the_response_shape_is_fixed(client: TestClient) -> None:
    """Phase 14 formats from this shape, so it is asserted rather than assumed."""
    response = client.get("/money/example")

    assert response.status_code == 200
    assert response.json()["amount"] == {"amount": "104.05", "currency": "USD"}


def test_no_amount_crosses_as_a_json_number(client: TestClient) -> None:
    """The criterion itself, enforced by refusing to decode a JSON number.

    If FastAPI, Pydantic or a future encoder change ever emitted the amount as
    a bare number, `_reject_json_number` fires during decoding and this fails.
    """
    response = client.get("/money/example")

    decoded = _decode_strictly(response.content)

    assert decoded["amount"]["amount"] == "104.05"


@pytest.mark.parametrize("currency", supported_currencies())
def test_no_amount_crosses_as_a_json_number_in_any_currency(
    client: TestClient, currency: str
) -> None:
    """Including JPY, whose quantized amount is an integer.

    This is the case the `isinstance(str)` approach would wave through: `1699`
    is a legal JSON integer, so a broken serializer emitting it as a number
    would be parsed by `parse_int` and never reach `parse_float`. The assertion
    on the decoded value is what catches it.
    """
    response = client.get("/money/example", params={"currency": currency})

    decoded = _decode_strictly(response.content)
    amount = decoded["amount"]["amount"]

    assert isinstance(amount, str), f"{currency} amount decoded as {type(amount)}"
    assert decoded["amount"]["currency"] == currency


def test_the_probe_rejects_a_currency_outside_the_registry(
    client: TestClient,
) -> None:
    response = client.get("/money/example", params={"currency": "XYZ"})

    assert response.status_code == 404
    assert "not in the currency registry" in response.json()["detail"]


def test_the_service_index_lists_the_probe(client: TestClient) -> None:
    """So the route is discoverable rather than only known to this test."""
    assert client.get("/").json()["money_example"] == "/money/example"


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
