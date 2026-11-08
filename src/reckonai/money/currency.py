"""The currency registry: how many decimal places each currency has.

This is the whole of what ReckonAI knows about a currency, and it is
deliberately small. See specs/2026-11-07-canonical-core/requirements.md D1 for
the two alternatives that were rejected - a generated ISO 4217 table of ~180
codes, and adding a currency library, which specs/tech-stack.md section 1 locks
out without an amendment.

**An unknown code raises. There is no two-decimal default**, and that is the
single most important property in this module. A default would mean a 3-decimal
currency silently losing its third digit at every rounding boundary, and the
loss would look like ordinary cent-level noise rather than like a bug. Growing
the table is a one-line change plus a test; that is the intended cost of
meeting a new currency.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from .errors import UnknownCurrency

#: Currency code to its exponent - the number of decimal places in its minor
#: unit. Wrapped in a MappingProxyType so a caller cannot mutate the registry
#: at runtime; a currency is added here, in a reviewed commit, or not at all.
#:
#: Each entry carries why it is present, because a registry nobody can justify
#: grows until it is the ISO table this module exists to avoid being.
CURRENCY_EXPONENTS: Final[Mapping[str, int]] = MappingProxyType(
    {
        # Observed in 221 PayPal fixture amounts across Phases 0 and 0b.
        "USD": 2,
        # Observed in 59, and the source currency of the one cross-currency
        # capture: EUR 95.50 settling as USD 89.59. See specs/paypal-fields.md.
        "EUR": 2,
        # A plausible seller currency for the Phase 4 simulated dataset.
        "GBP": 2,
        # As above, and its symbol exercises the rule in specs/tech-stack.md
        # section 13 that non-ASCII in real data is never stripped.
        "NGN": 2,
        # The zero-exponent edge. Quantization and allocation are wrong in a
        # different way for a currency with no minor unit, so it is not
        # optional coverage.
        "JPY": 0,
        # The three-exponent edge named by the roadmap's own round-trip
        # criterion for this phase.
        "KWD": 3,
        # A second 3-decimal code, so no test can pass by special-casing one
        # string.
        "BHD": 3,
    }
)


def normalise(code: object) -> str:
    """Return `code` as a canonical three-letter upper-case currency code.

    Normalising here rather than at each call site means `Money` can never hold
    two spellings of one currency - `usd` and `USD` would otherwise be
    different currencies to every comparison in the system, which is a
    `CurrencyMismatch` on two amounts that are in fact the same currency.

    A marketplace sending a lower-cased code is a mapping concern
    (specs/tech-stack.md section 9.3) and is handled there; this is the floor
    beneath it.
    """
    if not isinstance(code, str):
        raise UnknownCurrency(
            f"a currency code must be a string, not {type(code).__name__}"
        )

    candidate = code.strip().upper()

    if len(candidate) != 3 or not candidate.isascii() or not candidate.isalpha():
        raise UnknownCurrency(
            f"{code!r} is not a three-letter currency code"
        )

    return candidate


def exponent(code: str) -> int:
    """Return the number of decimal places in `code`'s minor unit.

    Raises `UnknownCurrency` for anything absent from the registry. The absence
    of a fallback is the point of the function.
    """
    candidate = normalise(code)

    try:
        return CURRENCY_EXPONENTS[candidate]
    except KeyError:
        raise UnknownCurrency(
            f"{candidate} is not in the currency registry. Add it to "
            "CURRENCY_EXPONENTS with its exponent and a reason, rather than "
            "assuming two decimal places."
        ) from None


def quantum(code: str) -> Decimal:
    """Return `code`'s smallest representable unit as a Decimal.

    `Decimal("0.01")` for USD, `Decimal("1")` for JPY, `Decimal("0.001")` for
    KWD. Derived from the exponent with `scaleb` rather than held in a second
    table, so the two cannot disagree - a table of quanta beside a table of
    exponents is two sources of truth for one fact.
    """
    return Decimal(1).scaleb(-exponent(code))


def supported_currencies() -> tuple[str, ...]:
    """Every code in the registry, sorted.

    Exists for the tests, which must exercise every currency rather than a
    chosen two. A test that iterates this cannot fall behind the registry when
    a currency is added.
    """
    return tuple(sorted(CURRENCY_EXPONENTS))
