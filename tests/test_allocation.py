"""Tests for largest-remainder allocation.

The roadmap's exit criterion is "allocation parts sum exactly to the whole with
no residue", and the first test below is that criterion as a **property** over
a parameterised space rather than as a handful of examples. An example-only
suite passes against an implementation that happens to be right for two sellers
and wrong for three, which is the realistic bug.

The rest pin the decisions in specs/2026-11-07-canonical-core/requirements.md
D6 - the tie-break order, the negative mirror, and the degenerate inputs that
raise - because each of those changes the output and so each needs a test that
would notice it changing.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from functools import reduce

import pytest

from reckonai.money import (
    InvalidAmount,
    Money,
    allocate,
    allocate_evenly,
    supported_currencies,
)


def total_of(parts: list[Money]) -> Money:
    """Sum a list of Money without seeding a currency from outside it.

    `sum(parts, Money.zero("USD"))` would hard-code a currency and pass a
    mixed-currency bug; reducing over the parts themselves cannot.
    """
    return reduce(lambda left, right: left + right, parts)


# ---------------------------------------------------------------------------
# The property: the parts always sum exactly to the whole
# ---------------------------------------------------------------------------

#: Amounts chosen to include the cases where a residue appears at all: the
#: single smallest unit, values whose thirds and sevenths do not divide, and a
#: large value where a proportional share is a long decimal.
_AMOUNTS = ["0.01", "0.02", "0.10", "1.00", "9.99", "100.00", "160.00", "12345.67"]

#: Weight vectors covering equal splits that do not divide, lopsided splits, a
#: single party, a zero weight, and fractional weights such as a percentage.
_WEIGHTS: list[list[Decimal | int]] = [
    [1],
    [1, 1],
    [1, 1, 1],
    [1, 1, 1, 1, 1, 1, 1],
    [2, 1],
    [1, 2, 97],
    [0, 1, 1],
    [Decimal("0.1"), Decimal("0.2")],
    [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")],
]


@pytest.mark.parametrize(
    ("amount", "currency", "weights"),
    [
        (amount, currency, weights)
        for amount, currency, weights in itertools.product(
            _AMOUNTS, supported_currencies(), _WEIGHTS
        )
    ],
)
def test_parts_sum_exactly_to_the_total(
    amount: str, currency: str, weights: list[Decimal | int]
) -> None:
    """The roadmap's second exit criterion for this phase.

    Note what is **not** here: no tolerance, and no comparison of rounded sums.
    A tolerance would be the "rounding constant found while chasing noise" that
    specs/roadmap.md Phase 11 names as a way to fail the false-positive gate.
    """
    total = Money(Decimal(amount), currency).quantize()

    parts = allocate(total, weights)

    assert len(parts) == len(weights)
    assert total_of(parts) == total


@pytest.mark.parametrize("currency", supported_currencies())
def test_negative_totals_also_sum_exactly(currency: str) -> None:
    """Refunds allocate negative amounts, so the property must hold signed."""
    total = Money(Decimal("-100.00"), currency).quantize()

    parts = allocate(total, [1, 1, 1])

    assert total_of(parts) == total


def test_every_part_carries_the_totals_currency() -> None:
    parts = allocate(Money("100.00", "KWD"), [1, 1, 1])

    assert {part.currency for part in parts} == {"KWD"}


# ---------------------------------------------------------------------------
# The residue cases, and the tie-break order
# ---------------------------------------------------------------------------


def test_the_single_cent_case() -> None:
    """One cent across three parties. A third of a cent does not exist.

    Rounding each share gives 0.00 three times or 0.01 three times; truncating
    loses the cent entirely. Largest remainder seats it on the first party.
    """
    parts = allocate(Money("0.01", "USD"), [1, 1, 1])

    assert parts == [
        Money("0.01", "USD"),
        Money("0.00", "USD"),
        Money("0.00", "USD"),
    ]


def test_the_hundred_across_three_case_and_its_order() -> None:
    """The canonical case, and the order matters as much as the values.

    A reproducible financial calculation cannot have the spare cent land on a
    different seller between runs, so this asserts the sequence rather than the
    multiset.
    """
    parts = allocate(Money("100.00", "USD"), [1, 1, 1])

    assert parts == [
        Money("33.34", "USD"),
        Money("33.33", "USD"),
        Money("33.33", "USD"),
    ]


def test_ties_break_to_the_lowest_index() -> None:
    """requirements.md D6. Three equal weights tie on remainder, so the two
    spare units go to indices 0 and 1, never to 1 and 2."""
    parts = allocate(Money("1.00", "USD"), [1, 1, 1, 1, 1, 1])

    assert parts == [Money("0.17", "USD")] * 4 + [Money("0.16", "USD")] * 2


def test_remainders_are_compared_exactly_not_at_context_precision() -> None:
    """Weights whose shares need more than 28 significant digits to separate.

    Decimal division is evaluated at the context precision, so computing the
    ideal shares that way would round the remainders and could seat the spare
    unit on the wrong party. The implementation scales the weights to integers
    for this reason; this test is what would notice a regression to division.
    """
    weights = [Decimal("1.000000000000000000000000001"), Decimal("1")]

    parts = allocate(Money("0.01", "USD"), weights)

    assert parts == [Money("0.01", "USD"), Money("0.00", "USD")]
    assert total_of(parts) == Money("0.01", "USD")


def test_a_negative_allocation_is_the_exact_mirror_of_the_positive() -> None:
    """requirements.md D6, and the reason the sign is reapplied rather than
    flooring through zero.

    Flooring moves toward negative infinity, so running the algorithm directly
    on a negative total seats the spare cent on a different party. A refunded
    order would then net to a cent rather than to zero.
    """
    charge = allocate(Money("100.00", "USD"), [1, 1, 1])
    refund = allocate(Money("-100.00", "USD"), [1, 1, 1])

    assert refund == [-part for part in charge]
    assert total_of(charge) + total_of(refund) == Money("0.00", "USD")


def test_a_zero_weight_receives_nothing_rather_than_a_share() -> None:
    parts = allocate(Money("1.00", "USD"), [0, 1, 1])

    assert parts[0] == Money("0.00", "USD")
    assert total_of(parts) == Money("1.00", "USD")


def test_a_single_weight_receives_the_whole() -> None:
    assert allocate(Money("9.99", "USD"), [7]) == [Money("9.99", "USD")]


def test_lopsided_weights() -> None:
    """A 1:2:97 split, where the small parties' shares are sub-cent."""
    parts = allocate(Money("1.00", "USD"), [1, 2, 97])

    assert parts == [
        Money("0.01", "USD"),
        Money("0.02", "USD"),
        Money("0.97", "USD"),
    ]


# ---------------------------------------------------------------------------
# Exponent edges
# ---------------------------------------------------------------------------


def test_a_zero_exponent_currency_allocates_in_whole_units() -> None:
    """JPY has no minor unit, so the smallest indivisible share is 1, not 0.01.

    An implementation that assumed two decimal places would produce parts that
    sum correctly and are individually unpayable.
    """
    parts = allocate(Money("100", "JPY"), [1, 1, 1])

    assert parts == [Money("34", "JPY"), Money("33", "JPY"), Money("33", "JPY")]
    assert total_of(parts) == Money("100", "JPY")


def test_a_three_exponent_currency_allocates_to_the_fil() -> None:
    parts = allocate(Money("1.000", "KWD"), [1, 1, 1])

    assert parts == [
        Money("0.334", "KWD"),
        Money("0.333", "KWD"),
        Money("0.333", "KWD"),
    ]


# ---------------------------------------------------------------------------
# Quantization, determinism, and the inputs that raise
# ---------------------------------------------------------------------------


def test_an_unquantized_total_is_quantized_before_allocation() -> None:
    """The allocation is of a real payable amount, not of an intermediate with
    fifteen decimal places. 100.006 USD quantizes to 100.01."""
    parts = allocate(Money(Decimal("100.006"), "USD"), [1, 1, 1])

    assert total_of(parts) == Money("100.01", "USD")
    assert parts == [
        Money("33.34", "USD"),
        Money("33.34", "USD"),
        Money("33.33", "USD"),
    ]


def test_allocation_is_deterministic() -> None:
    """Same inputs, same output, element-wise. Twice in one process is a weak
    check on its own; it is the regression guard for an implementation that
    reached for a set or a dict ordering somewhere."""
    first = allocate(Money("0.07", "USD"), [3, 3, 1])
    second = allocate(Money("0.07", "USD"), [3, 3, 1])

    assert first == second


@pytest.mark.parametrize(
    ("weights", "message"),
    [
        ([], "empty set of weights"),
        ([0], "sum to zero"),
        ([0, 0, 0], "sum to zero"),
        ([1, -1], "negative"),
        ([1, 1.5], "must be a Decimal or an int"),
        ([1, "2"], "must be a Decimal or an int"),
        ([1, True], "must be a Decimal or an int"),
        ([Decimal("NaN")], "finite"),
    ],
)
def test_degenerate_weights_raise(
    weights: list[object], message: str
) -> None:
    """specs/tech-stack.md section 9.6: a monetary value is never defaulted.

    An empty allocation returning `[]` and an all-zero allocation returning
    zeros would both be a quiet wrong answer - the caller believes the total was
    distributed when it was not.
    """
    with pytest.raises(InvalidAmount, match=message):
        allocate(Money("1.00", "USD"), weights)  # type: ignore[arg-type]


def test_allocating_something_that_is_not_money_raises() -> None:
    with pytest.raises(InvalidAmount, match="can only allocate Money"):
        allocate(Decimal("100.00"), [1, 1])  # type: ignore[arg-type]


def test_zero_allocates_to_zeros() -> None:
    """A zero total is legitimate - unlike zero weights - and distributing it
    gives each party nothing."""
    parts = allocate(Money.zero("USD"), [1, 2, 3])

    assert parts == [Money("0.00", "USD")] * 3


# ---------------------------------------------------------------------------
# allocate_evenly
# ---------------------------------------------------------------------------


def test_allocate_evenly_matches_equal_weights() -> None:
    """It delegates rather than dividing, so the two cannot drift apart."""
    assert allocate_evenly(Money("100.00", "USD"), 3) == allocate(
        Money("100.00", "USD"), [1, 1, 1]
    )


@pytest.mark.parametrize("parts", [0, -1])
def test_allocate_evenly_rejects_a_non_positive_part_count(parts: int) -> None:
    with pytest.raises(InvalidAmount, match="cannot allocate into"):
        allocate_evenly(Money("1.00", "USD"), parts)


@pytest.mark.parametrize("parts", [1.0, "3", True])
def test_allocate_evenly_requires_an_int(parts: object) -> None:
    with pytest.raises(InvalidAmount, match="parts must be an int"):
        allocate_evenly(Money("1.00", "USD"), parts)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The mission's worked example, as a primitive
# ---------------------------------------------------------------------------


def test_the_mission_worked_example_commission_split() -> None:
    """specs/mission.md section 6's example is Phase 10's exit criterion, not
    this phase's - the entitlement engine owns the rules that produce it.

    What this asserts is narrower and is this phase's job: that the primitive
    Phase 10 will call can carry that example's arithmetic without a residue.
    A 10% commission on a $160 order across a $100 and a $60 item is $16, and
    allocating it by item value gives $10 and $6 exactly.
    """
    commission = Money("16.00", "USD")

    parts = allocate(commission, [Decimal("100.00"), Decimal("60.00")])

    assert parts == [Money("10.00", "USD"), Money("6.00", "USD")]
    assert total_of(parts) == commission
