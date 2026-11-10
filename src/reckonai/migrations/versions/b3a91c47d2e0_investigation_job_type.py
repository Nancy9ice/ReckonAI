"""investigation job type

Block F's queued agent run (`specs/2026-11-10-queue-approval-dashboard/
requirements.md` D2). Widens the two `job_type` CHECK constraints.
Autogenerate does not detect a changed CHECK, so this is written by hand.

Revision ID: b3a91c47d2e0
Revises: 6dfbfc1b6929
Create Date: 2026-11-10 22:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b3a91c47d2e0"
down_revision: str | Sequence[str] | None = "6dfbfc1b6929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("job", "processed_event")


def _swap(types: str) -> None:
    for table in _TABLES:
        name = op.f(f"ck_{table}_job_type")
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"job_type IN ({types})")


def upgrade() -> None:
    """Apply this revision."""
    _swap("'reconciliation_run', 'investigation'")


def downgrade() -> None:
    """Reverse this revision.

    Refuses if an `investigation` job exists, rather than deleting it: the
    narrower constraint would fail on that row anyway, and silently dropping
    queue history is not a downgrade's call to make.
    """
    _swap("'reconciliation_run'")
