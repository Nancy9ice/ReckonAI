"""Serializing `Money` to JSON, and reading it back.

specs/tech-stack.md section 2: "Serialize amounts as **strings**, not JSON
numbers - JSON numbers are IEEE-754 doubles and will silently lose precision in
the browser."

The shape is fixed here and every later phase depends on it, including the
Phase 14 dashboard:

    {"amount": "160.00", "currency": "USD"}

Two details are deliberate.

**The amount is serialized from `quantize()`.** A boundary that presents a
number to a person is a rounding boundary, and
specs/2026-11-07-canonical-core/requirements.md D5 requires every such boundary
to be a named one. Sending `104.04507010764778050` to a browser would be
precise and useless; sending `104.05` is the figure a finance team reconciles
against. The unrounded value stays in the database either way, so nothing is
lost - this rounds the *view*, not the record.

**Reading accepts both a `Money` and the dict form**, so the same annotated type
serves a response model and a request body without a second declaration that
could disagree with this one.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator

from reckonai.money import Money


class MoneyOut(BaseModel):
    """A monetary amount as it crosses JSON.

    `amount` is a string carrying a decimal number, quantized to the currency's
    exponent. `currency` is a three-letter code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: str = Field(
        description=(
            "The amount as a decimal string, quantized to the currency's "
            "exponent. A string rather than a number so no client parses it "
            "through a binary type."
        ),
        examples=["160.00", "12.345"],
    )
    currency: str = Field(
        min_length=3,
        max_length=3,
        description="Three-letter currency code.",
        examples=["USD", "KWD"],
    )

    @classmethod
    def from_money(cls, money: Money) -> MoneyOut:
        quantized = money.quantize()
        return cls(amount=str(quantized.amount), currency=quantized.currency)

    def to_money(self) -> Money:
        """Parse back to a `Money`.

        `Money` parses the string straight to `Decimal`, so the round trip
        never passes through an inexact type.
        """
        return Money(self.amount, self.currency)


def _serialize(money: Money) -> dict[str, str]:
    quantized = money.quantize()
    return {"amount": str(quantized.amount), "currency": quantized.currency}


def _validate(value: Any) -> Money:
    """Accept a `Money`, or the dict form, and reject anything else.

    A `PlainValidator` rather than a `BeforeValidator`: `Money` is not a type
    Pydantic can introspect, so there is no inner schema to run afterwards, and
    the validation this performs is the whole of it.
    """
    if isinstance(value, Money):
        return value

    if isinstance(value, MoneyOut):
        return value.to_money()

    if isinstance(value, dict):
        try:
            amount, currency = value["amount"], value["currency"]
        except KeyError as exc:
            raise ValueError(
                f"a money object needs both 'amount' and 'currency'; "
                f"{exc.args[0]!r} is missing"
            ) from None
        # Money raises InvalidAmount for a float amount, which is what keeps a
        # JSON number from entering through this door. specs/tech-stack.md
        # section 9.3's rule - an amount without a currency is a hard failure -
        # is the KeyError above.
        return Money(amount, currency)

    raise ValueError(
        f"cannot read a monetary amount from {type(value).__name__}"
    )


#: A `Money` that serializes to `{"amount": "...", "currency": "..."}`.
#:
#: Use this in a Pydantic model or a FastAPI response model where the attribute
#: is a real `Money`:
#:
#:     class SellerPosition(BaseModel):
#:         seller_id: UUID
#:         entitlement: MoneyField
MoneyField = Annotated[
    Money,
    PlainValidator(_validate),
    PlainSerializer(_serialize, return_type=dict[str, str]),
]
