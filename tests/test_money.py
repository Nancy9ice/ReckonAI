"""Tests for the `Money` value object.

These are the tests specs/roadmap.md Phase 2 names as exit criteria, plus the
cases the decisions in specs/2026-11-07-canonical-core/requirements.md commit
to. Nothing here touches a database: `Money` is pure, and a test suite for it
that needed one would mean it was not.

The organising idea is that each test names the wrong answer it prevents, not
just the right answer it asserts. A test called `test_add_works` tells a later
reader nothing about why the code is shaped this way.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from reckonai.money import (
    CurrencyMismatch,
    InvalidAmount,
    Money,
    UnknownCurrency,
    exponent,
    quantum,
    supported_currencies,
)

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_constructs_from_decimal_int_and_string() -> None:
    """PayPal states amounts as JSON strings, so a string must be a first-class
    input - parsed straight to Decimal, never through an inexact type."""
    assert Money(Decimal("10.50"), "USD").amount == Decimal("10.50")
    assert Money(10, "USD").amount == Decimal("10")
    assert Money("95.50", "EUR").amount == Decimal("95.50")


def test_currency_is_normalised_to_upper_case() -> None:
    """Two spellings of one currency would be a CurrencyMismatch on two amounts
    that are in fact the same currency."""
    assert Money(1, "usd").currency == "USD"
    assert Money(1, " eur ").currency == "EUR"
    assert Money(1, "usd") == Money(1, "USD")


@pytest.mark.parametrize("bad", [1.5, 0.1, -2.75])
def test_an_inexact_amount_is_refused(bad: object) -> None:
    """The rule specs/tech-stack.md section 2 exists for, at the constructor.

    The values are written as literals here deliberately. This test file is not
    covered by the float gate - only the money path under src/ is - so it can
    name the type the implementation cannot.
    """
    with pytest.raises(InvalidAmount, match="must be a Decimal, int or str"):
        Money(bad, "USD")


def test_a_bool_is_not_an_amount() -> None:
    """bool subclasses int, so it is accepted unless refused explicitly. A bool
    arriving as an amount means a caller passed the wrong variable."""
    with pytest.raises(InvalidAmount, match="bool"):
        Money(True, "USD")


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_a_non_finite_amount_is_refused(bad: str) -> None:
    """NaN compares False against everything, itself included.

    One entering the system would not crash; it would make every downstream
    comparison quietly wrong, which is the failure mode this project can least
    afford.
    """
    with pytest.raises(InvalidAmount, match="finite"):
        Money(Decimal(bad), "USD")


def test_an_unparseable_string_is_refused() -> None:
    with pytest.raises(InvalidAmount, match="not a decimal number"):
        Money("about ten dollars", "USD")


def test_money_is_immutable() -> None:
    money = Money(Decimal("10.00"), "USD")
    with pytest.raises(AttributeError):
        money.amount = Decimal("20.00")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The currency registry
# ---------------------------------------------------------------------------


def test_unknown_currency_raises_and_nothing_defaults_to_two_decimals() -> None:
    """requirements.md D1. A two-decimal default is how a 3-decimal currency
    silently loses its third digit, disguised as cent-level noise."""
    with pytest.raises(UnknownCurrency, match="not in the currency registry"):
        Money(Decimal("1.00"), "XYZ")

    with pytest.raises(UnknownCurrency, match="not in the currency registry"):
        exponent("CHF")


@pytest.mark.parametrize("bad", ["US", "USDD", "12", "", "US1", "dollars", 840, None])
def test_a_malformed_currency_code_raises(bad: object) -> None:
    with pytest.raises(UnknownCurrency):
        Money(Decimal("1.00"), bad)  # type: ignore[arg-type]


def test_the_registry_covers_all_three_exponents() -> None:
    """A registry of only 2-decimal currencies would let every exponent bug
    through, because the exponent would never vary."""
    exponents = {exponent(code) for code in supported_currencies()}
    assert exponents == {0, 2, 3}


@pytest.mark.parametrize(
    ("code", "expected"),
    [("JPY", Decimal("1")), ("USD", Decimal("0.01")), ("KWD", Decimal("0.001"))],
)
def test_quantum_is_derived_from_the_exponent(code: str, expected: Decimal) -> None:
    assert quantum(code) == expected


# ---------------------------------------------------------------------------
# Arithmetic, and the currency mismatch rule
# ---------------------------------------------------------------------------


def test_same_currency_arithmetic() -> None:
    assert Money("10.00", "USD") + Money("5.50", "USD") == Money("15.50", "USD")
    assert Money("10.00", "USD") - Money("5.50", "USD") == Money("4.50", "USD")
    assert -Money("10.00", "USD") == Money("-10.00", "USD")
    assert abs(Money("-10.00", "USD")) == Money("10.00", "USD")


@pytest.mark.parametrize("operation", ["add", "subtract"])
def test_mixed_currency_arithmetic_raises_mismatch(operation: str) -> None:
    """The roadmap's first exit criterion for this phase.

    The wrong answer this prevents is `200.00 USD`: add the numbers, keep the
    first currency, flag nothing.
    """
    usd = Money("100.00", "USD")
    eur = Money("100.00", "EUR")

    with pytest.raises(CurrencyMismatch, match="cannot combine USD with EUR"):
        if operation == "add":
            usd + eur
        else:
            usd - eur


@pytest.mark.parametrize("op", ["lt", "le", "gt", "ge"])
def test_mixed_currency_ordering_raises_mismatch(op: str) -> None:
    """`100 USD > 90 EUR` looks obviously true and is unanswerable without a
    conversion record."""
    import operator

    usd = Money("100.00", "USD")
    eur = Money("90.00", "EUR")

    with pytest.raises(CurrencyMismatch):
        getattr(operator, op)(usd, eur)


def test_same_currency_ordering_works() -> None:
    small = Money("5.00", "USD")
    large = Money("10.00", "USD")

    assert small < large
    assert small <= large
    assert large > small
    assert large >= small
    assert not large < small


def test_scaling_by_an_exact_factor() -> None:
    assert Money("10.00", "USD") * 3 == Money("30.00", "USD")
    assert 3 * Money("10.00", "USD") == Money("30.00", "USD")
    assert Money("10.00", "USD") * Decimal("0.1") == Money("1.000", "USD")


@pytest.mark.parametrize("bad", [1.5, "3", None])
def test_scaling_by_an_inexact_or_wrong_factor_raises(bad: object) -> None:
    with pytest.raises(InvalidAmount, match="scaled by a Decimal or an int"):
        Money("10.00", "USD") * bad  # type: ignore[operator]


def test_money_cannot_be_multiplied_by_money() -> None:
    """Dollars times dollars is not a monetary quantity."""
    with pytest.raises(InvalidAmount):
        Money("10.00", "USD") * Money("2.00", "USD")  # type: ignore[operator]


def test_money_cannot_be_divided() -> None:
    """There is deliberately no __truediv__: dividing money is allocation, and
    naive division leaves a residue that has to go somewhere."""
    with pytest.raises(TypeError):
        Money("10.00", "USD") / 3  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Equality: the one excluded operation
# ---------------------------------------------------------------------------


def test_mixed_currency_equality_returns_false_and_does_not_raise() -> None:
    """requirements.md D3, and the half of the rule that is easy to miss.

    A `Money` whose `__eq__` raises passes a naive reading of the roadmap's
    exit criterion while being unusable: SQLAlchemy's unit of work compares
    attribute values to detect a change, so the composite() mapping in
    reckonai.models would break on every flush.
    """
    assert (Money("10.00", "USD") == Money("10.00", "EUR")) is False
    assert (Money("10.00", "USD") != Money("10.00", "EUR")) is True


def test_equality_against_a_non_money_is_false_not_an_error() -> None:
    """`money == None` appears in ordinary code and in SQLAlchemy internals.

    `__eq__` returns NotImplemented, so Python falls back to identity and the
    expression is False rather than a TypeError.
    """
    money = Money("10.00", "USD")

    assert (money == None) is False  # noqa: E711 - the point is the operator
    assert (money == "10.00 USD") is False
    assert (money == Decimal("10.00")) is False


def test_trailing_zeros_do_not_affect_equality_or_hashing() -> None:
    """Decimal("10.0") and Decimal("10.00") are numerically equal, and equal
    values must hash alike or a set of Money silently keeps both."""
    a = Money(Decimal("10.0"), "USD")
    b = Money(Decimal("10.00"), "USD")

    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_money_works_as_a_dict_key_and_in_a_set() -> None:
    keys = {Money("1.00", "USD"): "a", Money("1.00", "EUR"): "b"}

    assert keys[Money("1.00", "USD")] == "a"
    assert keys[Money("1.00", "EUR")] == "b"
    assert len(keys) == 2


# ---------------------------------------------------------------------------
# Rounding: the one named boundary
# ---------------------------------------------------------------------------


def test_construction_does_not_round() -> None:
    """requirements.md D5. A rate multiplication produces fifteen decimal
    places - see the exchange_rate in specs/paypal-fields.md - and rounding it
    at every intermediate step is how a settlement comparison drifts."""
    precise = Money(Decimal("104.04507010764778050"), "USD")

    assert precise.amount == Decimal("104.04507010764778050")
    assert not precise.is_quantized()


@pytest.mark.parametrize(
    ("code", "raw", "expected"),
    [
        ("JPY", "1234.56", "1235"),
        ("USD", "10.567", "10.57"),
        ("KWD", "1.23456", "1.235"),
        ("BHD", "0.0004", "0.000"),
    ],
)
def test_quantize_rounds_to_the_currency_exponent(
    code: str, raw: str, expected: str
) -> None:
    assert Money(Decimal(raw), code).quantize().amount == Decimal(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2.005", "2.01"), ("2.015", "2.02"), ("2.025", "2.03"), ("-2.005", "-2.01")],
)
def test_rounding_is_half_up_not_bankers(raw: str, expected: str) -> None:
    """specs/tech-stack.md section 2 states ROUND_HALF_UP.

    Python's default is ROUND_HALF_EVEN, which would give 2.00, 2.02, 2.02 for
    the first three cases. Without this test the stated mode is untested and
    the default would pass two of them by coincidence.
    """
    assert Money(Decimal(raw), "USD").quantize().amount == Decimal(expected)


def test_is_quantized_tolerates_a_trailing_zero() -> None:
    """Defined by value, not by the Decimal's exponent: 10.000 USD carries a
    third decimal place and loses nothing when quantized."""
    assert Money(Decimal("10.000"), "USD").is_quantized()
    assert not Money(Decimal("10.001"), "USD").is_quantized()


# ---------------------------------------------------------------------------
# Minor units, and the 3-decimal round-trip the roadmap requires
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "amount", "units"),
    [
        ("JPY", "1699", 1699),
        ("USD", "16.99", 1699),
        ("KWD", "1.699", 1699),
        ("BHD", "1.699", 1699),
        ("USD", "-16.99", -1699),
    ],
)
def test_minor_units_round_trip(code: str, amount: str, units: int) -> None:
    """The same integer means a different amount in each currency, which is the
    whole reason the exponent cannot be assumed."""
    money = Money(Decimal(amount), code)

    assert money.to_minor_units() == units
    assert Money.from_minor_units(units, code) == money


@pytest.mark.parametrize("code", supported_currencies())
def test_every_currency_round_trips_through_minor_units(code: str) -> None:
    """Iterating the registry rather than a chosen two, so adding a currency
    cannot leave this coverage behind."""
    original = Money(Decimal("1234.5"), code).quantize()

    assert Money.from_minor_units(original.to_minor_units(), code) == original


def test_a_three_decimal_currency_keeps_all_three_digits() -> None:
    """The roadmap's third exit criterion for this phase, stated directly."""
    kwd = Money(Decimal("12.345"), "KWD")

    assert kwd.quantize().amount == Decimal("12.345")
    assert kwd.to_minor_units() == 12345
    assert str(kwd) == "12.345 KWD"


def test_to_minor_units_quantizes_rather_than_truncating() -> None:
    """An over-precise amount must not silently drop a fractional minor unit."""
    assert Money(Decimal("16.996"), "USD").to_minor_units() == 1700


@pytest.mark.parametrize("bad", [16.99, "1699", Decimal("1699")])
def test_from_minor_units_requires_an_int(bad: object) -> None:
    with pytest.raises(InvalidAmount, match="minor units must be an int"):
        Money.from_minor_units(bad, "USD")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Predicates, representation, and the explicit zero
# ---------------------------------------------------------------------------


def test_predicates() -> None:
    assert Money(0, "USD").is_zero()
    assert Money("-0.01", "USD").is_negative()
    assert Money("0.01", "USD").is_positive()
    assert not Money(0, "USD").is_negative()


def test_zero_is_an_explicit_constructor() -> None:
    """specs/tech-stack.md section 9.6 forbids defaulting a missing amount to
    zero. This exists for the cases where zero is the real answer."""
    assert Money.zero("JPY") == Money(0, "JPY")
    assert Money.zero("USD").is_zero()


def test_str_is_quantized_and_repr_is_not() -> None:
    """A debugging session must be able to see the full stored precision, or a
    residue hunt chases a display rounding instead of the arithmetic."""
    precise = Money(Decimal("10.004999"), "USD")

    assert str(precise) == "10.00 USD"
    assert repr(precise) == "Money(Decimal('10.004999'), 'USD')"


def test_an_f_string_cannot_show_raw_precision() -> None:
    precise = Money(Decimal("10.004999"), "USD")

    assert f"{precise}" == "10.00 USD"
    with pytest.raises(InvalidAmount, match="no format specifiers"):
        f"{precise:.4f}"


def test_composite_values_are_the_column_pair() -> None:
    """What SQLAlchemy's composite() mapping in reckonai.models reads.

    Asserted here rather than only through the ORM, so a change to the order of
    this tuple fails in the money tests instead of as a confusing column
    mismatch three phases later.
    """
    assert Money(Decimal("10.00"), "USD").__composite_values__() == (
        Decimal("10.00"),
        "USD",
    )
