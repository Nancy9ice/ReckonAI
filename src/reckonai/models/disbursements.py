"""Disbursements: the only evidence a seller was actually paid.

**Two things make this the most dangerous entity in the model**, and
`specs/paypal-fields.md` says so in those words.

First, **releasing funds changes nothing on the capture.** Status, mode and
breakdown are byte-identical before and after a release, so the capture can
never tell you whether a seller was paid, and every `DELAYED` transaction must
be joined to its disbursement.

Second, **PayPal publishes no contract at all for referenced payouts** -
neither the generated SDK nor its own OpenAPI documents model them. Every field
here is known from a single observed record, so a change would arrive without
warning and nothing would flag it. That is the strongest argument in the
project for retaining raw payloads and diffing them against the model
(specs/roadmap.md Phase 7).

**ReckonAI never sends a release** (specs/mission.md P6). The call is
`POST /v1/payments/referenced-payouts-items`, it moves money, and this product
may observe that a release is due, overdue or approaching PayPal's 28-day
automatic disbursement, and recommend one. Nothing in this package sends it, and
no column here is an instruction.

One mechanic worth not rediscovering: the release is **idempotent per
reference**. A second attempt against the same capture answers
`422 PAYOUT_ALREADY_COMPLETED_FOR_REFERENCE`, so a retry cannot double-pay.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    money_columns,
)
from reckonai.models.lifecycle import (
    DISBURSEMENT_STATES,
    LifecycleMixin,
    reconciliation_state_check,
    state_check,
)
from reckonai.models.payloads import RawPayloadMixin, raw_payload_fk
from reckonai.money import Money


class Disbursement(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """One release of held funds to a seller, under `DELAYED` disbursement.

    **Under `INSTANT` no row exists here at all** - the seller is paid at
    capture. That is why `capture.disbursement_state` rather than this table is
    the single place to ask "was this seller paid?"
    (specs/2026-11-08-canonical-events/requirements.md D1).
    """

    __tablename__ = "disbursement"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: PayPal's `item_id`, returned by the release call.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: **Provider-supplied, and the capture id itself** - `$.reference_id`,
    #: with `reference_type: TRANSACTION_ID`. Unlike the refund-to-capture
    #: linkage, this one needs no parsing and carries no derivation flag.
    #: Nullable only because resolution to a local capture row can fail.
    capture_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: `$.payout_destination` - the seller's merchant id, so a disbursement
    #: attributes to a seller without a join.
    recipient_reference: Mapped[str | None] = mapped_column(String(64))

    #: The money movement itself, distinct from the release instruction.
    payout_transaction_id: Mapped[str | None] = mapped_column(String(128))

    #: `$.payout_amount` - the capture's **net**. 86.02 observed against a
    #: 100.00 capture, which confirms from a second direction that fees were
    #: already taken at capture.
    amount: Mapped[Money] = money_columns("amount")

    #: `$.processing_state.status`, retained verbatim. `SUCCESS` observed.
    #: **This is the only evidence a seller was paid**, since the capture does
    #: not change when funds are released.
    #:
    #: Kept beside `state` rather than replaced by it: `state` is our
    #: projection onto a fixed vocabulary, and PayPal's own word is the
    #: evidence (specs/mission.md P7).
    provider_status: Mapped[str | None] = mapped_column(String(32))

    #: Our projection, over `DISBURSEMENT_STATES`.
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    #: **Stays NULL, and has no default of any kind.**
    #:
    #: The release record carries no usable timestamp: `time_updated` is the
    #: integer `0`, not a date. So when a seller was paid is not readable from
    #: the disbursement, and must come from the reporting ledger or be recorded
    #: at ingestion as our own observation - `created_at` already is that.
    #:
    #: Writing an ingestion time here would present our clock as a provider
    #: timestamp, which is the substitution specs/mission.md P3 forbids.
    #: requirements.md D13, and `tests/test_disbursements.py` asserts the
    #: absence of a default against the reflected schema.
    released_at: Mapped[datetime | None] = mapped_column()

    #: `items[].errors` on a failed release, retained whole.
    #:
    #: specs/roadmap.md Phase 7: when it is absent the answer is
    #: `insufficient_evidence`, **never an assembled cause**. So the column is
    #: nullable and its absence is meaningful rather than a gap to fill.
    provider_errors: Mapped[dict | None] = mapped_column(JSONB)

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
        # The release is idempotent per reference on PayPal's side; this is the
        # same guarantee on ours.
        UniqueConstraint("tenant_id", "external_id"),
        UniqueConstraint("tenant_id", "id"),
        state_check("state", DISBURSEMENT_STATES, name="state"),
        reconciliation_state_check(),
    )
