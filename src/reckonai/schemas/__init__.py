"""Pydantic schemas: the boundary between canonical records and JSON.

The rule this package exists to enforce is specs/tech-stack.md section 2's:
**amounts cross JSON as strings, never as JSON numbers.** A JSON number is an
IEEE-754 double, so `160.00` sent as a bare number is parsed by every JavaScript
client into a value that cannot represent some decimals exactly - and
specs/tech-stack.md section 7 commits the frontend to formatting amounts from
strings with `Intl.NumberFormat`, which is only possible if strings arrive.
"""

from __future__ import annotations

from .money import MoneyField, MoneyOut

__all__ = ["MoneyField", "MoneyOut"]
