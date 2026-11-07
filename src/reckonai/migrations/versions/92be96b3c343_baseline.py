"""baseline

Empty on purpose. Roadmap Phase 1 is a deployable spine and creates no tables;
Phase 2 adds the first of them (tenant, seller, order, order_item,
commission_rule) and Phase 3 the financial events.

This revision exists to prove the Alembic wiring end to end before there is a
schema to risk: that env.py reads the URL from Settings, that script_location
resolves, and that `alembic_version` is created on the Render database by the
web service's preDeployCommand. Proving that against an empty revision costs
nothing; discovering it against Phase 2's first real schema costs a migration.

It is reversible, like every revision here, so the property is established from
the first one rather than retrofitted later.

Revision ID: 92be96b3c343
Revises:
Create Date: 2026-11-07 21:03:11.285640

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '92be96b3c343'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    pass


def downgrade() -> None:
    """Reverse this revision.

    Every revision in this project is reversible. A migration that cannot be
    reversed is one that was never tested against a copy of production.
    """
    pass
