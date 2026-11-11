"""paypal_event job type

Live ingestion's webhook handler job
(`specs/2026-11-11-live-ingestion/requirements.md` F4). Widens the two
`job_type` CHECK constraints. Autogenerate does not detect a changed CHECK, so
this is written by hand, as `b3a91c47d2e0` was.

Revision ID: d7e1f3a9c2b4
Revises: b3a91c47d2e0
Create Date: 2026-11-11 02:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7e1f3a9c2b4"
down_revision: str | Sequence[str] | None = "b3a91c47d2e0"
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
    _swap("'reconciliation_run', 'investigation', 'paypal_event'")


def downgrade() -> None:
    """Reverse this revision.

    Refuses, by the narrower constraint failing, if a `paypal_event` row
    exists, rather than deleting queue history.
    """
    _swap("'reconciliation_run', 'investigation'")
