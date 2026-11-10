"""The human decision on an exception: append-only, and it moves no money.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D4 and D5.

**A decision is intent, recorded.** Approving a recommendation writes one row
here and nothing else: no ledger entry, refund or payout is touched, which is
`mission.md` P6. A rejection is the same row with the other outcome, so the two
carry equal weight by construction.

**Append-only.** Changing one's mind inserts a second row; the effective
decision is the newest, a read and never an update. No code path updates or
deletes this table, and `tests/test_exception_decisions.py` pins that.

The approver is a free-text name. There is no authentication in the MVP, so the
column records what the operator typed and claims no verified identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import Base, TenantMixin, TimestampMixin
from reckonai.models.lifecycle import state_check

DECISION_OUTCOMES: tuple[str, ...] = ("approved", "rejected")


class ExceptionDecision(TenantMixin, TimestampMixin, Base):
    """One approve or reject of one persisted exception."""

    __tablename__ = "exception_decision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: The `reconciliation_exception` row decided on. Composite with the tenant,
    #: so a decision cannot name another tenant's exception.
    exception_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    approver: Mapped[str] = mapped_column(String(120), nullable=False)

    note: Mapped[str | None] = mapped_column(Text)

    #: The investigation the human was looking at, where there was one. Not a
    #: foreign key: a decision is valid with no investigation at all.
    investigation_id: Mapped[uuid.UUID | None] = mapped_column()

    decided_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        state_check("outcome", DECISION_OUTCOMES, name="outcome"),
        CheckConstraint("btrim(approver) <> ''", name="approver_not_blank"),
        ForeignKeyConstraint(
            ["tenant_id", "exception_id"],
            ["reconciliation_exception.tenant_id", "reconciliation_exception.id"],
            name="exception",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id"),
        Index(
            "ix_exception_decision_latest", "tenant_id", "exception_id", "decided_at"
        ),
    )
