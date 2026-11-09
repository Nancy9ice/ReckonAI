"""The `Money` value object.

Every monetary amount in ReckonAI is one of these. specs/mission.md P1 and P2
and specs/tech-stack.md section 2 are the governing rules, and this module is
where they stop being prose.

Four properties are load-bearing, and each exists because its absence produces
a confident wrong number rather than a crash:

**An amount is never separated from its currency.** The two are one frozen
value, so there is no signature anywhere that takes a bare amount
(specs/tech-stack.md section 2, "Shape").

**Mixed-currency arithmetic and ordering raise.** `100.00 USD + 100.00 EUR`
has no answer without a conversion record, and P3 forbids supplying an
estimated one. The alternative a careless implementation reaches for - add the
numbers, keep the first currency - returns `200.00 USD` and flags nothing.

**Equality is the one excluded operation.** `10.00 USD == 10.00 EUR` is
`False`, not an error, because "are these the same value?" has a correct answer
without a rate. This is not a convenience: SQLAlchemy's unit of work compares
attribute values to detect a change, so a raising `__eq__` would make the
object unusable as the composite() mapping in `reckonai.models`, and it could
not be a `set` member or a `dict` key. specs/tech-stack.md section 2 was
amended for this before this file was written.

**Construction does not round.** `quantize()` is the only thing in this class
that rounds, which is what makes specs/tech-stack.md section 2's "rounding
happens once, at an explicitly named boundary" true rather than aspirational.
A rate multiplication produces an amount with fifteen decimal places - see the
15-digit `exchange_rate` in specs/paypal-fields.md - and rounding it at every
intermediate step is how a settlement comparison drifts.

Note what is absent. There is no `__truediv__`: dividing money is allocation,
and `allocation.allocate` is how it is done, because naive division leaves a
residue that has to go somewhere. There is no `__round__` either - rounding is
spelled `quantize()` so it cannot happen by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from .currency import exponent, normalise, quantum
from .errors import CurrencyMismatch, InvalidAmount


def _coerce_amount(value: object) -> Decimal:
    """Return `value` as a finite Decimal, or raise `InvalidAmount`.

    **This module cannot mention the banned type by name.** The float gate in
    tests/test_money_purity.py parses this file's AST and fails on any
    reference to the `float` builtin, including inside an `isinstance` check.
    So a float is rejected by *exclusion* - it reaches the final `else` because
    it is not a Decimal, int or str - and the error message names it at runtime
    through `type(value).__name__`.

    That is stricter than an explicit check, not a workaround for being unable
    to write one: any numeric type that is not exact arithmetic is refused by
    the same branch, including a numpy scalar nobody has thought about yet.
    """
    if isinstance(value, bool):
        # bool is a subclass of int, so it would otherwise be accepted. True is
        # not an amount, and a bool arriving here means a caller passed the
        # wrong variable.
        raise InvalidAmount("a bool is not a monetary amount")

    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise InvalidAmount(f"{value!r} is not a decimal number") from None
    else:
        raise InvalidAmount(
            f"an amount must be a Decimal, int or str, not "
            f"{type(value).__name__}. Inexact binary types are refused "
            "outright: see specs/tech-stack.md section 2."
        )

    if not amount.is_finite():
        # NaN and Infinity are constructible Decimals. A NaN compares False
        # against everything including itself, so one entering the system would
        # make every downstream comparison quietly wrong rather than loud.
        raise InvalidAmount(f"an amount must be finite, not {amount}")

    return amount


@dataclass(frozen=True, slots=True, eq=False, order=False)
class Money:
    """A decimal amount and the currency it is denominated in.

    Immutable, hashable, and safe to use as a dict key or in a set. Construct
    from a `Decimal`, an `int`, or a `str` - PayPal states amounts as JSON
    strings, which are parsed here straight to `Decimal` and never through an
    inexact type.
    """

    amount: Decimal
    currency: str

    if TYPE_CHECKING:
        # The dataclass-generated `__init__` reuses the field annotation above
        # for its parameter, which declares a *narrower* constructor than this
        # class documents and than `_coerce_amount` actually accepts. Every
        # `Money("160.00", "USD")` in the codebase - the canonical form, and
        # the one PayPal's JSON strings arrive as - then fails a type check
        # while being entirely correct.
        #
        # Declared here so the constructor's type matches its behaviour, while
        # `money.amount` still reads as a `Decimal` downstream because
        # `__post_init__` has normalised it by then. Under TYPE_CHECKING only:
        # this adds no runtime `__init__` and the dataclass machinery is
        # untouched.
        #
        # Found by Phase 10 when the type checker was first run against this
        # project: 32 of its 39 errors were this one annotation
        # (specs/2026-11-09-entitlement-engine/requirements.md D9).
        def __init__(
            self, amount: Decimal | int | str, currency: str
        ) -> None: ...

    def __post_init__(self) -> None:
        # Validate, canonicalise, then write back through object.__setattr__,
        # which is how a frozen dataclass normalises its own fields.
        currency = normalise(self.currency)
        exponent(currency)  # raises UnknownCurrency if it is not in the registry
        amount = _coerce_amount(self.amount)

        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "amount", amount)

    # -- alternative constructors -------------------------------------------

    @classmethod
    def from_minor_units(cls, units: int, currency: str) -> Money:
        """Build from an integer count of the currency's smallest unit.

        1699 USD cents is `16.99 USD`; 1699 JPY is `1699 JPY`; 1699 KWD fils is
        `1.699 KWD`. specs/tech-stack.md section 9.3 requires this for a source
        that states amounts as integer minor units - such a value is scaled on
        the way in, never parsed as a decimal string.
        """
        if isinstance(units, bool) or not isinstance(units, int):
            raise InvalidAmount(
                f"minor units must be an int, not {type(units).__name__}"
            )

        code = normalise(currency)
        return cls(Decimal(units).scaleb(-exponent(code)), code)

    @classmethod
    def zero(cls, currency: str) -> Money:
        """Zero in `currency`.

        **An explicit choice by the caller, never a fallback.**
        specs/tech-stack.md section 9.6 forbids defaulting a missing amount to
        zero - that is a quarantine. This exists for the cases where zero is
        the real answer, such as the starting value of a sum or a weight that
        genuinely earns no share of an allocation.
        """
        return cls(Decimal(0), currency)

    # -- arithmetic ----------------------------------------------------------

    def _same_currency_as(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} with {other.currency}: there "
                "is no answer without a conversion record, and inventing one "
                "is forbidden by specs/mission.md P3"
            )

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency_as(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency_as(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        """Scale by an exact number. A quantity or a rate, never another Money.

        Raises rather than returning `NotImplemented` for an inexact factor, so
        the error says why instead of surfacing as a bare TypeError several
        frames away.
        """
        if isinstance(factor, bool) or not isinstance(factor, Decimal | int):
            raise InvalidAmount(
                f"money can only be scaled by a Decimal or an int, not "
                f"{type(factor).__name__}"
            )
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__

    # -- comparison ----------------------------------------------------------
    #
    # Ordering raises across currencies; equality does not. The split is
    # specs/2026-11-07-canonical-core/requirements.md D3 and is reflected in
    # specs/tech-stack.md section 2.

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            # NotImplemented, not False: Python then falls back rather than
            # this class claiming inequality with something it cannot compare.
            return NotImplemented
        return self.currency == other.currency and self.amount == other.amount

    def __hash__(self) -> int:
        # Decimal hashes consistently with numeric equality, so Decimal("10.0")
        # and Decimal("10.00") hash alike - which is what equal Money values
        # hashing alike requires.
        return hash((self.currency, self.amount))

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency_as(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency_as(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency_as(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency_as(other)
        return self.amount >= other.amount

    # -- rounding, the one named boundary -----------------------------------

    def quantize(self) -> Money:
        """Round to the currency's exponent, `ROUND_HALF_UP`.

        The only rounding in this class. specs/tech-stack.md section 2 fixes
        both the mode and that it happens once at a named boundary; this is
        that boundary, and calling it is always a deliberate act.

        `ROUND_HALF_UP` rather than Python's default `ROUND_HALF_EVEN`: half-up
        is what a finance team expects and what the spec states, and banker's
        rounding would disagree on exactly the `.005` cases a reconciliation
        surfaces.
        """
        return Money(
            self.amount.quantize(quantum(self.currency), rounding=ROUND_HALF_UP),
            self.currency,
        )

    def is_quantized(self) -> bool:
        """Whether quantizing would lose value.

        Defined by value rather than by the Decimal's exponent on purpose:
        `10.000 USD` carries a third decimal place but loses nothing when
        quantized, and calling it unquantized would fail a caller for a
        trailing zero.
        """
        return self.amount == self.quantize().amount

    def to_minor_units(self) -> int:
        """The amount as an integer count of the currency's smallest unit.

        Quantizes first, so a fractional minor unit cannot be dropped
        silently. This is the representation `allocation.allocate` works in,
        because integers make "the parts sum exactly to the whole" a property
        of the arithmetic rather than something checked afterwards.
        """
        return int(self.quantize().amount.scaleb(exponent(self.currency)))

    # -- predicates ----------------------------------------------------------

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def is_positive(self) -> bool:
        return self.amount > 0

    # -- representation ------------------------------------------------------

    def __composite_values__(self) -> tuple[Decimal, str]:
        """The column values for SQLAlchemy's `composite()` mapping.

        Declared explicitly rather than left to SQLAlchemy's dataclass
        inspection. It costs two lines and it means the database mapping in
        `reckonai.models` does not depend on how this class happens to be
        constructed - and this module stays free of any SQLAlchemy import,
        which the purity gate requires.
        """
        return self.amount, self.currency

    def __str__(self) -> str:
        """Quantized, for a human: `160.00 USD`."""
        return f"{self.quantize().amount} {self.currency}"

    def __repr__(self) -> str:
        """Unquantized, for a developer: `Money(Decimal('160.004999'), 'USD')`.

        Deliberately different from `__str__`. A debugging session must be able
        to see the full stored precision, or a residue hunt chases a display
        rounding instead of the arithmetic.
        """
        return f"Money({self.amount!r}, {self.currency!r})"

    def __format__(self, format_spec: str) -> str:
        """Format the quantized amount, so f-strings cannot show raw precision.

        `__format__` is what an f-string calls, and without this one an
        implicit `format(money, "")` would reach `__str__` while
        `format(money, ".2f")` would reach the inexact path this class exists
        to avoid. Any non-empty spec is refused.
        """
        if format_spec:
            raise InvalidAmount(
                "Money has no format specifiers: format the amount and the "
                "currency separately, or use str(money)"
            )
        return str(self)

    def __getstate__(self) -> dict[str, Any]:
        # slots=True plus frozen=True needs explicit pickle support, and
        # SQLAlchemy and pytest both pickle values in some paths.
        return {"amount": self.amount, "currency": self.currency}

    def __setstate__(self, state: dict[str, Any]) -> None:
        object.__setattr__(self, "amount", state["amount"])
        object.__setattr__(self, "currency", state["currency"])
