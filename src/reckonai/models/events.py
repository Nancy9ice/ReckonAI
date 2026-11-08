"""The provider-side transaction records: payments, captures and refunds.

specs/roadmap.md Phase 3. Shaped by what PayPal was **observed** to return, per
`specs/paypal-fields.md`, rather than by what its contract declares - which is
why so much here is nullable.

Three shape decisions are worth reading before the code, because each one looks
like an omission:

**One table per record type holds both sources.** A marketplace refund and a
PayPal refund are rows in the same `refund` table, told apart by their
payload's `source`
(specs/2026-11-08-canonical-events/requirements.md D3 and D4). **Nothing here
links one to the other**: that link is a *claim* that two records describe one
event, and establishing it is Phase 11's work, with a confidence and an evidence
trail behind it. A `matched_refund_id` added here would be a matching decision
with no run behind it.

**`order_id` means only what the source itself stated.** Intrinsic for a
marketplace-origin record, NULL for a PayPal-origin one, where the tie to a
marketplace order is a match rather than a fact.

**A capture's breakdown is entirely nullable.** Not caution about small
samples - see `Capture`.
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
from reckonai.models.catalogue import DISBURSEMENT_MODES
from reckonai.models.lifecycle import (
    DISBURSEMENT_STATES,
    LifecycleMixin,
    reconciliation_state_check,
    state_check,
)
from reckonai.models.payloads import RawPayloadMixin, raw_payload_fk
from reckonai.money import Money


def _seller_fk() -> ForeignKeyConstraint:
    """`(tenant_id, seller_id)` into `seller`, for a nullable seller column.

    `RESTRICT` because deleting a seller with financial records against it is
    never something to do silently.
    """
    return ForeignKeyConstraint(
        ["tenant_id", "seller_id"],
        ["seller.tenant_id", "seller.id"],
        ondelete="RESTRICT",
    )


def _order_fk() -> ForeignKeyConstraint:
    """`(tenant_id, order_id)` into `order`, for a nullable order column."""
    return ForeignKeyConstraint(
        ["tenant_id", "order_id"],
        ["order.tenant_id", "order.id"],
        ondelete="RESTRICT",
    )


class Payment(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """The buyer-facing transaction. PayPal's order, or the marketplace's.

    Read from `GET /v2/checkout/orders/{id}` on the PayPal side, where
    `specs/paypal-fields.md` section 1 records every field below as `present`
    on 4 of 4 sampled records.

    `seller_identity` - `purchase_units[].payee.merchant_id` - is called **the
    join the whole product rests on** in that document, because under multiparty
    the payee is the seller and not the marketplace. Here it is resolved to a
    `seller_id`, and it is nullable because resolution can fail: a payee we
    have no seller row for is a quarantined record Phase 11 should see, not an
    insert to reject (specs/tech-stack.md section 9.6).
    """

    __tablename__ = "payment"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: The source's own identifier - PayPal's order id, or the marketplace's
    #: reference. One column for both, because the same table holds both
    #: sources (requirements.md D4), and unique per tenant so re-running
    #: ingestion is idempotent in the database rather than in the connector
    #: (requirements.md D15).
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: PayPal's `payee`, resolved. Nullable - see the class docstring.
    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: The marketplace order this record itself names. NULL on a PayPal-origin
    #: record, where the tie is a match and Phase 11 owns it.
    order_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    amount: Mapped[Money] = money_columns("amount")

    #: PayPal's own word, retained verbatim rather than mapped onto our
    #: vocabulary. specs/mission.md P7: the provider's word is the evidence, and
    #: `reconciliation_state` is our projection beside it.
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    intent: Mapped[str | None] = mapped_column(String(16))

    #: `payer.payer_id`. Present under multiparty and absent before it: Phase
    #: 0's card-funded orders carried no payer at all.
    payer_reference: Mapped[str | None] = mapped_column(String(64))

    #: `INSTANT` or `DELAYED`, nullable where the source did not state it.
    #: Nullable rather than defaulted, for the reason `order.disbursement_mode`
    #: gives: assuming `INSTANT` for a silent record would make every
    #: held-funds transaction look like a completed one.
    disbursement_mode: Mapped[str | None] = mapped_column(String(16))

    #: PayPal's `create_time`. Distinct from `created_at`, which is when we
    #: wrote the row - conflating the two is how a delayed event becomes a
    #: discrepancy.
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        raw_payload_fk(),
        _seller_fk(),
        _order_fk(),
        UniqueConstraint("tenant_id", "external_id"),
        UniqueConstraint("tenant_id", "id"),
        reconciliation_state_check(),
        state_check(
            "disbursement_mode",
            DISBURSEMENT_MODES,
            name="disbursement_mode",
            nullable=True,
        ),
    )


class Capture(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """The settlement-side record, and where PayPal puts its fee evidence.

    **Every breakdown column is nullable, and that is an observed behaviour
    rather than caution about sample size.** A capture loses its entire
    `seller_receivable_breakdown` while it is `PENDING`, and an open dispute
    puts it there: the disputed fixture reverted from `COMPLETED` to `PENDING`
    and the fee, the net and the platform fee vanished with it, then returned
    once the dispute resolved.

    Two rules follow, and Phase 7 owns the second because this phase writes no
    ingestion code (requirements.md D12):

      1. The columns are nullable, because a legitimate capture can have none
         of them.
      2. **Capture these values at ingestion, and never overwrite a populated
         breakdown with NULL on a later read.** The absence is not evidence
         that no fee was charged.

    **`status` is mutable and does not mean the seller was paid.** A `DELAYED`
    capture reads `COMPLETED` while PayPal still holds the funds, and a
    disputed capture reverts to `PENDING`. `disbursement_state` is the column
    that answers whether the seller has the money.
    """

    __tablename__ = "capture"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: **Provider-supplied, not parsed.**
    #: `supplementary_data.related_ids.order_id` is a real field on the
    #: capture - unlike the refund-to-capture linkage, which has to be read out
    #: of a HATEOAS href. Nullable only because resolution can fail.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    amount: Mapped[Money] = money_columns("amount")

    #: Pre-fee, pre-conversion.
    gross_amount: Mapped[Money | None] = money_columns("gross_amount", nullable=True)

    #: `gross - paypal_fee - platform_fees`, observed exactly on every capture
    #: carrying a breakdown.
    net_amount: Mapped[Money | None] = money_columns("net_amount", nullable=True)

    #: PayPal's own cut. Phase 0 recorded this absent; under multiparty it is
    #: there. **Never defaulted to zero** - specs/roadmap.md Phase 10 requires
    #: that an unknown capture fee be treated as unknown and said so, and NULL
    #: is what makes that representable (specs/tech-stack.md section 9.6).
    paypal_fee: Mapped[Money | None] = money_columns("paypal_fee", nullable=True)

    #: `receivable_amount` - present only where a conversion occurred.
    #: **Compare against this figure, never a recomputation of gross times
    #: rate**: PayPal rounds the settlement to the cent and leaves the rate
    #: unrounded, so the two disagree on every converted transaction.
    settlement_amount: Mapped[Money | None] = money_columns(
        "settlement_amount", nullable=True
    )

    #: The account's settlement currency, which the balances endpoint answers
    #: unconditionally. Not part of a money pair: it is the currency the seller
    #: is paid in, not the currency of an amount on this row.
    settlement_currency: Mapped[str | None] = mapped_column(String(3))

    status: Mapped[str] = mapped_column(String(32), nullable=False)

    disbursement_mode: Mapped[str | None] = mapped_column(String(16))

    #: **Whether the seller actually got the money**, and the reason this
    #: column lives here rather than on `disbursement`: under `INSTANT` the
    #: seller is paid at capture and **no disbursement record exists at all**,
    #: so a state that lived only on `disbursement` could not answer the
    #: question for half the dataset (requirements.md D1).
    #:
    #: Our projection, not PayPal's word - `disbursement.provider_status`
    #: retains that. Deriving a non-monetary state is fine;
    #: specs/mission.md P3 governs financial values.
    disbursement_state: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )

    occurred_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        raw_payload_fk(),
        _seller_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "external_id"),
        UniqueConstraint("tenant_id", "id"),
        money_pair_check("gross_amount"),
        money_pair_check("net_amount"),
        money_pair_check("paypal_fee"),
        money_pair_check("settlement_amount"),
        reconciliation_state_check(),
        state_check(
            "disbursement_state", DISBURSEMENT_STATES, name="disbursement_state"
        ),
        state_check(
            "disbursement_mode",
            DISBURSEMENT_MODES,
            name="disbursement_mode",
            nullable=True,
        ),
    )


class Refund(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """Money returned to the buyer, and the weakest linkage in the model.

    **PayPal supplies no `capture_id` on a refund.** The only route back is
    `links[rel=up]`, whose `href` has to be parsed - so the linkage is
    *derived*, and specs/roadmap.md Phase 3 requires that it be recorded as
    derived rather than presented as provider evidence. Three columns carry it
    (requirements.md D11):

      - `capture_id`, **nullable**: an unresolved linkage is a recorded state
        and a Phase 11 `unmatched`, not a failed insert
      - `capture_linkage_is_derived`, NOT NULL: the provenance can never be
        unstated
      - `capture_linkage_href`, nullable: the evidence it was parsed from, so a
        Phase 11 exception resting on this linkage can cite what it rests on
        (specs/mission.md P5)

    **A refund carries `seller_payable_breakdown` where a capture carries
    `seller_receivable_breakdown`**, so mapping code cannot share a path
    between the two. The arithmetic identity is the same:
    `30.00 - 0.00 - 3.00 = 27.00` on the observed fixture, where the 3.00 is
    the pro-rata platform fee reversal.
    """

    __tablename__ = "refund"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Derived, not provider-supplied. See the class docstring.
    capture_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: Whether `capture_id` was derived rather than read from a provider field.
    #: NOT NULL with no default: stating the provenance is not optional, and a
    #: default would let a caller skip the question.
    capture_linkage_is_derived: Mapped[bool] = mapped_column(nullable=False)

    #: The `links[rel=up]` href the linkage was parsed out of. The evidence
    #: behind the derivation, kept because P5 requires a conclusion to cite its
    #: sources.
    capture_linkage_href: Mapped[str | None] = mapped_column(String(512))

    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    amount: Mapped[Money] = money_columns("amount")

    gross_amount: Mapped[Money | None] = money_columns("gross_amount", nullable=True)
    net_amount: Mapped[Money | None] = money_columns("net_amount", nullable=True)

    #: `0.00` observed - PayPal did not return its own fee on the refund.
    #: Still nullable rather than defaulted: an observed zero and an absent
    #: value are different facts (specs/tech-stack.md section 9.6).
    paypal_fee: Mapped[Money | None] = money_columns("paypal_fee", nullable=True)

    #: What makes partial-refund tracking possible, and therefore what
    #: specs/mission.md section 6 Scenario 1 is detected against.
    total_refunded_amount: Mapped[Money | None] = money_columns(
        "total_refunded_amount", nullable=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        raw_payload_fk(),
        _seller_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "capture_id"],
            ["capture.tenant_id", "capture.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "external_id"),
        UniqueConstraint("tenant_id", "id"),
        money_pair_check("gross_amount"),
        money_pair_check("net_amount"),
        money_pair_check("paypal_fee"),
        money_pair_check("total_refunded_amount"),
        reconciliation_state_check(),
    )
