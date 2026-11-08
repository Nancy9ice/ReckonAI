"""Money: the one monetary type in ReckonAI.

This is the import path every later phase uses, so it is fixed here rather than
left to each caller to reach into a submodule:

    from reckonai.money import Money

Two rules govern everything in this package, and both are enforced by tests
rather than trusted:

  - **No inexact arithmetic anywhere in the money path.** The float gate in
    tests/test_money_purity.py parses each module's AST and fails on a
    reference to the `float` builtin, a `float` annotation, a call to `round`,
    or an attribute access on `math`.
  - **No I/O.** This package imports no SQLAlchemy, no database handle and no
    HTTP client, per specs/tech-stack.md section 6. The database mapping lives
    in `reckonai.models`, which imports this - never the reverse.
"""

from __future__ import annotations

from .currency import (
    CURRENCY_EXPONENTS,
    exponent,
    normalise,
    quantum,
    supported_currencies,
)
from .errors import CurrencyMismatch, InvalidAmount, MoneyError, UnknownCurrency
from .value import Money

__all__ = [
    "CURRENCY_EXPONENTS",
    "CurrencyMismatch",
    "InvalidAmount",
    "Money",
    "MoneyError",
    "UnknownCurrency",
    "exponent",
    "normalise",
    "quantum",
    "supported_currencies",
]
