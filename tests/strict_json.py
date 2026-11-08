"""A JSON decoder that refuses to decode a monetary value as a number.

Extracted from `tests/test_money_json.py` in Block A, when `GET /money/example`
was removed. The probe route went; **this mechanism is the part worth keeping**,
and it now runs against the simulated marketplace's real endpoints, which carry
money in nearly every response.

specs/tech-stack.md section 2's reason for the rule: a JSON number is an
IEEE-754 double, so a bare `160.00` on the wire is parsed by a browser into a
binary value that cannot represent some decimals exactly.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn


def reject_json_number(raw: str) -> NoReturn:
    """A `json.loads` hook that fails if the decoder meets a decimal number.

    Worth stating plainly why the obvious alternative does not work.

    Asserting `isinstance(body["amount"], str)` proves nothing: by the time that
    assertion runs, `json.loads` has *already* turned a bare `160.00` into a
    binary number, and the assertion would be inspecting the wrong object - it
    would fail, but only by luck of type, and it would pass for an integer
    amount such as `160`, which `json` parses with `parse_int` instead.

    Hooking the decoder catches the value as it is decoded, before anything can
    look at it.
    """
    raise AssertionError(
        f"a monetary value crossed JSON as a number, not a string: {raw!r}"
    )


def decode_strictly(body: bytes) -> Any:
    """Decode a response body, failing on any non-integer JSON number.

    **An integer amount still needs an `isinstance` check at the call site.**
    `1699` - a quantized JPY amount - is a legal JSON integer, so a broken
    serializer emitting it as a number is parsed by `parse_int` and never
    reaches `parse_float`. This function catches the decimal case; the caller
    catches the integer one.
    """
    return json.loads(body, parse_float=reject_json_number)
