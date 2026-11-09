"""What demo dataset a database currently holds.

`specs/2026-11-09-paypal-capture/requirements.md` D13.

**This exists because the loader's idempotence had a second edge.**
`demo_data --ensure` returns early when the demo tenant is present, which
stops a redeploy duplicating anything - but it also means production never
picks up a dataset change. Alter an amount in `reckonai/sim/data.py` or
re-capture a fixture, deploy, and the check finds the tenant and stops: no
error, no warning, and the demo runs on figures the repository no longer
states.

That is a worse failure than duplication, because duplication is visible and
this is not. So the fingerprint of what was loaded is recorded, and `--ensure`
re-seeds when it no longer matches.

**A dedicated table rather than somewhere convenient.** The alternative was a
`raw_payload` row, which Phase 3's D2 defines as "one retrieved source
document, kept verbatim" - a fingerprint is not that, and using the evidence
store as a key-value store would make every later query over it wrong.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import Base, TenantMixin, TimestampMixin
from reckonai.models.payloads import SHA256_HEX_LENGTH


class DemoSeed(TenantMixin, TimestampMixin, Base):
    """One record of which dataset a tenant was seeded with.

    One row per tenant, replaced on each re-seed. It carries no financial
    value and no provider record, which is why it has no `raw_payload_id`:
    there is nothing it is evidence *of*.
    """

    __tablename__ = "demo_seed"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Hex SHA-256 over the marketplace dataset **and** the captured
    #: fixtures. Both, because either changing alone makes what is loaded
    #: wrong: a re-captured fixture with unchanged `data.py` is just as stale
    #: as the reverse.
    fingerprint: Mapped[str] = mapped_column(
        String(SHA256_HEX_LENGTH), nullable=False
    )

    #: How many orders the dataset declared, for a log line that reads
    #: usefully without resolving the hash against anything.
    order_count: Mapped[int] = mapped_column(nullable=False)

    seeded_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # One row per tenant: the record is *which* dataset is loaded, and
        # two answers would be no answer.
        UniqueConstraint("tenant_id"),
        # Composite-foreign-key target. Nothing is a child of this table
        # today, but Phase 2's D8 makes the constraint universal rather than
        # per-table, so a cross-tenant link stays unwritable by construction
        # instead of by whoever adds the child remembering.
        UniqueConstraint("tenant_id", "id"),
    )
