"""Disputes, which the model knows about mostly from a contract.

**Every column except the identifiers is nullable**, per
specs/roadmap.md Phase 3's "nothing is required that Phase 0 could not observe"
and specs/2026-11-08-canonical-events/requirements.md D14. That is not caution
about sample size: ten canonical dispute fields in
`specs/paypal-fields.md` rest on PayPal's published OpenAPI document rather than
on an observed value, because `POST /v1/customer/disputes` rejects a card-funded
capture with `TRANSACTION_NOT_ELIGIBLE`.

**Dispute liability sits with the seller, not the marketplace.** PayPal's own
wording for a connected integration is that "merchants assume financial
liability and handle disputes and chargebacks". So whether the *marketplace* is
exposed is governed by its own seller terms, and an exposure this product
reports must rest on a marketplace rule - never be inferred from a PayPal
dispute record alone (specs/mission.md section 3.1).

One consequence for the engine, recorded here because this is where someone
will look: a lost dispute reverses the platform fee **automatically and in
full** - accepting a claim returned the whole 10.00 commission on the observed
fixture. So the marketplace does not keep commission on a lost dispute, and
Phase 10's expected position must assume the reversal rather than instruct it.
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
    money_pair_check,
)
from reckonai.models.lifecycle import (
    LifecycleMixin,
    reconciliation_state_check,
)
from reckonai.models.payloads import RawPayloadMixin, raw_payload_fk
from reckonai.money import Money


class Dispute(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """A buyer's challenge to a transaction.

    Read from `GET /v1/customer/disputes`. Note that the list endpoint filters
    on `dispute_state`: a default query returns a dispute that
    `dispute_state=OPEN_INQUIRIES` does not, which is a Phase 7 retrieval
    concern rather than a column here.
    """

    __tablename__ = "dispute"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Our capture, where it could be resolved. Nullable, and usually it
    #: cannot be: see `buyer_transaction_id`.
    capture_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: **Not our capture id.** `disputed_transactions[].buyer_transaction_id`
    #: is the buyer's side of the transaction, which carries its own
    #: identifier, so matching a dispute to a capture needs a join we have not
    #: established. Filtering the disputes endpoint *by* a capture id does
    #: work, which is the route Phase 7 should take.
    buyer_transaction_id: Mapped[str | None] = mapped_column(String(128), index=True)

    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: Nullable like everything else here. Observed on the one dispute we have,
    #: but one record is not a basis for NOT NULL.
    amount: Mapped[Money | None] = money_columns("amount", nullable=True)

    #: `WAITING_FOR_SELLER_RESPONSE` observed.
    status: Mapped[str | None] = mapped_column(String(48))

    #: `INQUIRY` observed - the pre-claim stage.
    lifecycle_stage: Mapped[str | None] = mapped_column(String(48))

    #: `REQUIRED_ACTION` observed.
    dispute_state: Mapped[str | None] = mapped_column(String(48))

    #: `MERCHANDISE_OR_SERVICE_NOT_RECEIVED` observed.
    reason: Mapped[str | None] = mapped_column(String(64))

    #: `INTERNAL` observed - raised through PayPal rather than as a card
    #: chargeback.
    channel: Mapped[str | None] = mapped_column(String(32))

    #: Undeclared in PayPal's contract and observed anyway.
    outcome: Mapped[str | None] = mapped_column(String(48))

    #: **Its absence is never a missed deadline.** Observed at
    #: `WAITING_FOR_SELLER_RESPONSE` with a date 20 days out, and **gone** once
    #: the seller accepted the claim - because nothing is due any more. That
    #: makes the absence correct rather than a gap, and
    #: `specs/paypal-fields.md` decides it `no_action` on exactly that basis.
    #:
    #: While it lasts, it is what specs/mission.md P4's `awaiting_evidence`
    #: state keys on.
    seller_response_due_date: Mapped[datetime | None] = mapped_column()

    #: Nullable, unlike every other table's `occurred_at`, for the same reason
    #: as the rest of this table: it is `create_time` on a record type we have
    #: barely observed.
    occurred_at: Mapped[datetime | None] = mapped_column()

    updated_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        raw_payload_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "seller_id"],
            ["seller.tenant_id", "seller.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "capture_id"],
            ["capture.tenant_id", "capture.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "external_id"),
        UniqueConstraint("tenant_id", "id"),
        money_pair_check("amount"),
        reconciliation_state_check(),
    )
