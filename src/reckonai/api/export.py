"""CSV exports: the queue and the decision log.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D16.

Amounts leave as the decimal string and the currency in separate columns, never
as a spreadsheet number, so nothing is rounded on the way out. **Every cell is
neutralised against formula injection**: a value beginning with `=`, `+`, `-`
or `@` would be executed by a spreadsheet, and several cells here are text from
the marketplace (a seller name, a note) or from a model.
"""

from __future__ import annotations

import csv
import io
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.api import queries
from reckonai.models import ExceptionDecision, ReconciliationException
from reckonai.schemas.money import MoneyOut

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

#: A plain decimal, signed or not. A spreadsheet reads it as a number, which is
#: what a negative amount must stay: neutralising `-12.50` would turn the audit
#: export's money into text that SUM() ignores.
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?")


def safe(value: object) -> str:
    """A cell that a spreadsheet will read as text, whatever it holds."""
    text = "" if value is None else str(value)
    if _PLAIN_NUMBER.fullmatch(text):
        return text
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _write(rows: list[list[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([safe(cell) for cell in row])
    return buffer.getvalue()


def _money(value: MoneyOut | None) -> list[object]:
    return [None, None] if value is None else [value.amount, value.currency]


QUEUE_HEADER = [
    "priority",
    "status",
    "type",
    "order",
    "seller",
    "impact_amount",
    "impact_currency",
    "expected_amount",
    "expected_currency",
    "reported_amount",
    "reported_currency",
    "order_placed_at",
    "investigation",
    "decision",
    "decided_by",
    "decided_at",
    "evidence_refs",
    "detail",
]


def queue_csv(session: Session, tenant_id: uuid.UUID, run_id: uuid.UUID | None) -> str:
    rows: list[list[object]] = [list(QUEUE_HEADER)]
    for e in queries.exceptions(session, tenant_id, run_id):
        rows.append(
            [
                e.priority.score,
                e.certainty,
                e.code,
                e.order_ref,
                e.seller_name,
                *_money(e.impact),
                *_money(e.expected),
                *_money(e.reported),
                e.order_placed_at.isoformat() if e.order_placed_at else None,
                e.investigation.verdict if e.investigation else "not_investigated",
                e.decision.outcome if e.decision else "undecided",
                e.decision.approver if e.decision else None,
                e.decision.decided_at.isoformat() if e.decision else None,
                "; ".join(e.evidence_refs),
                e.detail,
            ]
        )
    return _write(rows)


DECISIONS_HEADER = [
    "decided_at",
    "outcome",
    "approver",
    "note",
    "order",
    "type",
    "impact_amount",
    "impact_currency",
    "investigation_id",
    "exception_id",
]


def decisions_csv(session: Session, tenant_id: uuid.UUID) -> str:
    """Every decision ever recorded, oldest first: the audit trail.

    Append-only, so superseded decisions are all here; the newest per exception
    is the effective one, and nothing is dropped to show only that.
    """
    rows: list[list[object]] = [list(DECISIONS_HEADER)]
    found = session.execute(
        select(ExceptionDecision, ReconciliationException)
        .join(
            ReconciliationException,
            (ReconciliationException.tenant_id == ExceptionDecision.tenant_id)
            & (ReconciliationException.id == ExceptionDecision.exception_id),
        )
        .where(ExceptionDecision.tenant_id == tenant_id)
        .order_by(ExceptionDecision.decided_at)
    )
    for decision, exc in found:
        money = MoneyOut.from_money(exc.impact)
        rows.append(
            [
                decision.decided_at.isoformat(),
                decision.outcome,
                decision.approver,
                decision.note,
                exc.order_external_ref,
                exc.code,
                money.amount,
                money.currency,
                decision.investigation_id,
                exc.id,
            ]
        )
    return _write(rows)
