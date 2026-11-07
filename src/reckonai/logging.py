"""Logging setup and the event-line format.

specs/tech-stack.md section 13: a financial system's log lines get grepped,
piped and parsed, so status is carried by a dotted event name rather than by
prose or a pictograph. A line says `reconciliation.failed`, not a cross mark.

Fields are appended as `key=value` pairs so a line stays parseable by `awk` and
by a log search, without taking on a structured-logging dependency that
specs/tech-stack.md section 1 does not list.
"""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger. Safe to call more than once per process."""
    logging.basicConfig(
        level=level.upper(),
        format=_FORMAT,
        datefmt=_DATE_FORMAT,
        force=True,
    )


def event(name: str, **fields: object) -> str:
    """Render a dotted event name plus `key=value` fields as one log message.

    >>> event("reckonai.worker.start", pool_size=3)
    'reckonai.worker.start pool_size=3'

    Values containing whitespace are quoted so the pairs stay separable. No
    value here is ever a credential - see specs/tech-stack.md section 10.
    """
    if not fields:
        return name

    parts = []
    for key, value in fields.items():
        rendered = str(value)
        if any(char.isspace() for char in rendered):
            rendered = f'"{rendered}"'
        parts.append(f"{key}={rendered}")
    return f"{name} {' '.join(parts)}"
