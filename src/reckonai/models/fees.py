"""The platform fee: PayPal's own record of the commission it took.

**This table is why the multiparty direction made commission validation real.**
In the merchant-of-record model the marketplace's commission was implicit -
whatever it kept - with no provider figure to check it against. Under
multiparty, PayPal records the fee it actually took, and that is independent
evidence against the marketplace's own rules
(specs/mission.md section 3.1 and section 4).

So specs/roadmap.md Phase 10 must compute the expected commission **as a figure
comparable to this one**, not merely as the residue of what the marketplace
kept.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    money_columns,
)
from reckonai.models.lifecycle import (
    LifecycleMixin,
    reconciliation_state_check,
)
from reckonai.models.payloads import (
    RawPayloadMixin,
    capture_or_refund_parent,
    raw_payload_fk,
)
from reckonai.money import Money


class PlatformFee(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """One platform fee as PayPal recorded it, taken or reversed.

    **The direction is the parent, not a sign and not a flag**
    (specs/2026-11-08-canonical-events/requirements.md D6). A fee taken at
    capture is a row whose parent is the capture; a reversal is a row whose
    parent is the refund. The amount is stored **positive, exactly as PayPal
    reported it**, in both cases.

    There is no `is_reversal` column because it would be derivable from the
    parent, and a derivable column is a second place for one fact to disagree
    with itself.

    **The reversal is automatic and pro rata**, on an ordinary refund and on a
    lost dispute alike. A 30.00 refund of a 100.00 capture returned 3.00 of the
    10.00 commission; accepting a dispute claim returned the full 10.00. So the
    marketplace does not keep commission on a reversed transaction, and Phase
    10's expected position must **assume** the reversal rather than instruct
    it - nothing in this product instructs anything (specs/mission.md P6).

    The fee is taken **at capture in both disbursement modes**, which Phase 0b
    established and the disbursement amount confirms from the other direction:
    a release pays out the capture's net, fees already deducted.
    """

    __tablename__ = "platform_fee"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: PayPal returns platform fees as an array member with no id of its own,
    #: so this is composed by the connector - the parent's id plus the array
    #: index - rather than read from a field. Unique per tenant, like every
    #: other external id here (requirements.md D15).
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Exactly one of these two is populated. See `capture_or_refund_parent`.
    capture_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: Where PayPal routed the commission -
    #: `platform_fees[].payee.merchant_id`. It defaults to the API caller when
    #: the order does not name one, and PayPal fills it in explicitly rather
    #: than leaving it out.
    payee_reference: Mapped[str | None] = mapped_column(String(64))

    #: The commission as PayPal actually took it. Its currency must match the
    #: transaction's; PayPal rejects a mismatch outright.
    amount: Mapped[Money] = money_columns("amount")

    #: **Stays NULL, and is here anyway.**
    #:
    #: Partner fees settle as a single next-day consolidated bank payment, and
    #: no per-transaction settlement reference is exposed - so this cannot be
    #: populated from anything PayPal returns today. specs/mission.md section 7
    #: defers reconciling that aggregate as settlement-account work while
    #: requiring that the canonical model not foreclose it: "a platform fee
    #: record should be able to carry a settlement reference later".
    #:
    #: This column is that requirement, met at the cost of one nullable field
    #: rather than a migration against a deployed schema.
    settlement_reference: Mapped[str | None] = mapped_column(String(128))

    occurred_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        raw_payload_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "seller_id"],
            ["seller.tenant_id", "seller.id"],
            ondelete="RESTRICT",
        ),
        *capture_or_refund_parent(),
        UniqueConstraint("tenant_id", "external_id"),
        UniqueConstraint("tenant_id", "id"),
        reconciliation_state_check(),
    )
