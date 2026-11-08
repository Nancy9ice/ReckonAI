"""The money exception family.

A common base class exists for one concrete reason rather than for tidiness.
specs/tech-stack.md section 9.6 says a record whose money cannot be parsed is
**quarantined** - visible and countable - never defaulted and never skipped
silently. Phase 6's ingestion therefore needs to catch the whole family at one
boundary and quarantine the record, which a shared base makes possible without
listing every subclass at the call site.

Each subclass is raised for a different reason and they are not
interchangeable:

  - `CurrencyMismatch` means two amounts in different currencies were added,
    subtracted or ordered. There is no answer without a conversion record, and
    specs/mission.md P3 forbids supplying an estimated one.
  - `UnknownCurrency` means a currency code is not in the registry. It is not a
    parse failure; it is a gap in `currency.py` that a human closes with a
    reviewed one-line addition.
  - `InvalidAmount` means the value offered as an amount cannot be one - a
    float, a NaN, a string that is not a number.
"""

from __future__ import annotations


class MoneyError(ValueError):
    """Base for every error raised by the money package.

    **Derives from `ValueError`, not `Exception`.** Each of these is literally a
    bad value - an amount that is not one, a currency that is not one, two
    amounts that cannot be combined - so `ValueError` is the accurate base, and
    `except MoneyError` is unaffected by the choice.

    It also earns its keep at the Pydantic boundary. A validator raising
    `ValueError` is wrapped into a `ValidationError` automatically, so
    `reckonai.schemas.money` needs no translation layer converting money errors
    into something Pydantic recognises - and a JSON number arriving in a request
    body is refused as an ordinary field validation failure rather than
    escaping as a 500.
    """


class CurrencyMismatch(MoneyError):
    """Two amounts in different currencies were combined or ordered."""


class UnknownCurrency(MoneyError):
    """A currency code is absent from the registry, or is not a currency code."""


class InvalidAmount(MoneyError):
    """A value cannot represent a monetary amount."""
