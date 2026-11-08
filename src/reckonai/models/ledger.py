"""The marketplace's own seller ledger.

This is the record specs/mission.md section 6 Scenario 1 is missing from: a
partial refund never reflected in the seller ledger. So the table's job is to
make an **absence** findable, which is why `entry_category` is a checked
vocabulary rather than free text - Phase 11 asks "is there a refund entry for
this refund?" against it.

Three shape decisions, all from
specs/2026-11-08-canonical-events/requirements.md D7, and each one looks like an
omission:

**Append-only.** Never updated, never deleted. A seller's position is the sum of
the entries, so a correction is a new entry and the history stays re-derivable
(specs/mission.md P7).

**The amount is unsigned, and the direction is its own column.** That is what
buys `CHECK (amount_amount > 0)`: with a signed amount there is no way to
distinguish a wrong sign from a legitimate negative, so a zero or negative
amount could not be refused. A position is
`SUM(credit) - SUM(debit)` per currency, computed at read time, and the sign is
applied in exactly one place - `reckonai.engine.ledger.signed`.

**No stored running balance.** Considered at length and dropped. A
`balance_after` column is a derived monetary value persisted beside its inputs,
which specs/tech-stack.md section 9.6 forbids, and an out-of-order ingestion
makes it confidently wrong with nothing to recompute it. A variant carrying only
the balance the marketplace itself *stated* - never one we compute - was viable,
and was dropped because it adds a comparison surface to
specs/roadmap.md Phase 11's zero-false-positive gate for a figure that is
already derivable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    money_columns,
)
from reckonai.models.lifecycle import (
    LEDGER_ENTRY_CATEGORIES,
    TRANSACTION_TYPES,
    LifecycleMixin,
    reconciliation_state_check,
    state_check,
)
from reckonai.models.payloads import RawPayloadMixin, raw_payload_fk
from reckonai.money import Money


class SellerLedgerEntry(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """One movement on a seller's balance, as the marketplace recorded it.

    **`transaction_type` and `entry_category` are partly redundant, and no
    `CHECK` ties them.** A `sale` is always a credit and a `refund` always a
    debit, so a row could say `sale` + `debit`. The marketplace supplies both,
    and a disagreement between them is a finding about the marketplace's data
    rather than an insert to reject - Phase 2's D13 reasoning applied
    unchanged, since enforcing it would convert a Phase 11 exception into an
    ingestion crash.

    `tests/test_ledger.py` pins that absence by asserting such a row inserts,
    so a later contributor does not add the constraint as a tidy-up.
    """

    __tablename__ = "seller_ledger_entry"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: NOT NULL: an entry with no seller has no position to belong to.
    seller_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    #: Nullable. Not every entry comes from an order - a manual adjustment, a
    #: dispute debit or a periodic fee has none behind it.
    order_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: What kind of movement this is, over `LEDGER_ENTRY_CATEGORIES`.
    entry_category: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )

    #: `debit` or `credit`. The sign, as its own fact rather than as a
    #: property of the amount.
    transaction_type: Mapped[str] = mapped_column(String(6), nullable=False)

    #: Unsigned magnitude, with `CHECK (> 0)` below.
    amount: Mapped[Money] = money_columns("amount")

    #: When the marketplace says the movement happened. Distinct from
    #: `created_at`, which is when we ingested it - conflating the two is how a
    #: delayed event gets reported as a discrepancy (specs/mission.md P4).
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)

    #: The marketplace's own identifier for the entry. The unique constraint
    #: below is what makes specs/roadmap.md Phase 6's "re-running ingestion is
    #: idempotent" a database property rather than a connector's promise.
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        raw_payload_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "seller_id"],
            ["seller.tenant_id", "seller.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["order.tenant_id", "order.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "external_ref"),
        UniqueConstraint("tenant_id", "id"),
        state_check(
            "entry_category", LEDGER_ENTRY_CATEGORIES, name="entry_category"
        ),
        state_check(
            "transaction_type", TRANSACTION_TYPES, name="transaction_type"
        ),
        # The whole reason the amount is unsigned. See the module docstring.
        CheckConstraint("amount_amount > 0", name="amount_positive"),
        reconciliation_state_check(),
    )
