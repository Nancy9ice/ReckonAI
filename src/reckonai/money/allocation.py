"""Allocating money across weights, with no residue.

specs/tech-stack.md section 2: "Allocation of a refund across sellers uses a
largest-remainder method so the parts always sum exactly to the whole - no
rounding residue is created or discarded."

The problem this solves is small and unavoidable. Split `0.01 USD` three ways
and each share is a third of a cent, which does not exist. Round each share and
the parts sum to `0.00` or `0.03` instead of `0.01`; truncate and a cent
vanishes. In a reconciliation system a vanished cent is worse than it sounds:
it appears later as an unexplained difference, and the investigation that
chases it finds nothing, because the discrepancy was manufactured by the tool
doing the checking.

**The method.** Work in integer minor units, give each party the floor of its
ideal share, then hand the remaining units out one each to the parties with the
largest fractional remainders. The parts sum exactly because the units were
never divided - only distributed.

Three properties are decided rather than incidental, and each changes the
output. All three are specs/2026-11-07-canonical-core/requirements.md D6.

**Ties break by lowest index.** Sort by remainder descending, then by index
ascending. A financial calculation must be reproducible: the same inputs have
to allocate the same way on every run and every machine, or two reconciliation
runs over identical data disagree about which seller got the spare cent.

**A negative total allocates by absolute value, then the sign is reapplied.**
Refunds allocate negative amounts. Running largest-remainder directly on a
negative total gives the spare unit to a different party than the positive case
would, because flooring moves toward negative infinity. Mirroring instead keeps
a refund's allocation the exact inverse of the charge it reverses - which is
what makes a refunded order net to zero rather than to a cent.

**Degenerate inputs raise.** No weights, weights summing to zero, or a negative
weight. None has a defensible answer, and specs/tech-stack.md section 9.6
forbids defaulting money.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .errors import InvalidAmount
from .value import Money


def _coerce_weights(weights: Sequence[Decimal | int]) -> list[Decimal]:
    """Validate the weight vector and return it as exact Decimals."""
    if len(weights) == 0:
        raise InvalidAmount("cannot allocate across an empty set of weights")

    coerced: list[Decimal] = []
    for index, weight in enumerate(weights):
        if isinstance(weight, bool) or not isinstance(weight, Decimal | int):
            raise InvalidAmount(
                f"weight {index} must be a Decimal or an int, not "
                f"{type(weight).__name__}"
            )

        value = Decimal(weight) if isinstance(weight, int) else weight

        if not value.is_finite():
            raise InvalidAmount(f"weight {index} must be finite, not {value}")
        if value < 0:
            raise InvalidAmount(
                f"weight {index} is negative ({value}). A negative weight has "
                "no meaning in an allocation; negate the total instead."
            )

        coerced.append(value)

    if sum(coerced) == 0:
        raise InvalidAmount(
            "the weights sum to zero, so no allocation is defined. This is a "
            "caller error rather than an all-zero result: see "
            "specs/tech-stack.md section 9.6."
        )

    return coerced


def allocate(total: Money, weights: Sequence[Decimal | int]) -> list[Money]:
    """Split `total` across `weights`, exactly.

    Returns one `Money` per weight, in the same order, each in `total`'s
    currency. The parts sum to `total.quantize()` with no residue.

    An unquantized total is quantized first - the allocation is of a real
    payable amount, not of an intermediate with fifteen decimal places. The
    quantization is the named boundary specs/tech-stack.md section 2 requires,
    and it happens once, here.

        >>> allocate(Money("100.00", "USD"), [1, 1, 1])
        [Money(Decimal('33.34'), 'USD'),
         Money(Decimal('33.33'), 'USD'),
         Money(Decimal('33.33'), 'USD')]
    """
    if not isinstance(total, Money):
        raise InvalidAmount(
            f"can only allocate Money, not {type(total).__name__}"
        )

    coerced = _coerce_weights(weights)
    currency = total.currency

    # Mirror a negative total rather than flooring through zero. See the module
    # docstring: Python's floor division rounds toward negative infinity, so
    # allocating -0.01 directly would seat the spare unit differently from
    # +0.01, and a refund would not be the exact inverse of its charge.
    quantized = total.quantize()
    negative = quantized.is_negative()
    units = abs(quantized.to_minor_units())

    # Put the weights on a common integer basis, so every step below is exact
    # integer arithmetic.
    #
    # Decimal division would be wrong here, and subtly. It is evaluated at the
    # context precision - 28 significant digits by default - so `units * weight
    # / weight_total` is a rounded result, and the remainders it produces are
    # rounded too. Comparing rounded remainders can seat the spare unit on the
    # wrong party, which is exactly the residue this function exists to
    # eliminate. Scaling to integers removes the possibility rather than making
    # it unlikely.
    scale = max((-weight.as_tuple().exponent for weight in coerced), default=0)
    scale = max(scale, 0)
    int_weights = [int(weight.scaleb(scale)) for weight in coerced]
    weight_total = sum(int_weights)

    # Floor each ideal share and keep its remainder's numerator. The remainders
    # all share the denominator `weight_total`, so their numerators order them
    # exactly - no division needed to compare them.
    floors: list[int] = []
    remainders: list[int] = []
    for weight in int_weights:
        share, remainder = divmod(units * weight, weight_total)
        floors.append(share)
        remainders.append(remainder)

    # Distribute what flooring left over: one unit each, largest remainder
    # first, ties to the lowest index.
    leftover = units - sum(floors)
    order = sorted(
        range(len(coerced)),
        key=lambda index: (-remainders[index], index),
    )
    for index in order[:leftover]:
        floors[index] += 1

    parts = [Money.from_minor_units(count, currency) for count in floors]

    return [-part for part in parts] if negative else parts


def allocate_evenly(total: Money, parts: int) -> list[Money]:
    """Split `total` into `parts` equal shares, exactly.

    Delegates to `allocate` rather than dividing, because even division has the
    same residue problem - `100.00 / 3` is the canonical case - and solving it
    again at a call site is how the two implementations drift apart.
    """
    if isinstance(parts, bool) or not isinstance(parts, int):
        raise InvalidAmount(
            f"parts must be an int, not {type(parts).__name__}"
        )
    if parts < 1:
        raise InvalidAmount(f"cannot allocate into {parts} parts")

    return allocate(total, [1] * parts)
