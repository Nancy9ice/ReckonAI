"""The catalogue: tenants, sellers, orders, order items and commission rules.

These are the entities Phase 3's financial events reference and Phase 10's
entitlement engine computes over. specs/roadmap.md Phase 2 names exactly these
five.

Three shape decisions are worth reading before the code, because each one looks
like an omission:

**No constraint ties an order's total to the sum of its items.**
specs/2026-11-07-canonical-core/requirements.md D13. A marketplace whose order
total disagrees with its own line items is precisely what this product exists
to find, and a `CHECK` would turn a Phase 11 finding into an ingestion crash.
For the same reason `line_total` is stored as the marketplace stated it rather
than computed from `unit_price * quantity`.

**No native Postgres enum.** `disbursement_mode` is a checked `VARCHAR(16)`,
per requirements.md D9. Autogenerate does not reliably emit a native enum
type's `CREATE`/`DROP` alongside the table's, so a downgrade leaves an orphaned
type and the next upgrade fails. Phase 3 owns the lifecycle state enum and
inherits that warning.

**Primary keys are a single `id`, with a `UNIQUE (tenant_id, id)` beside it.**
requirements.md D8. The unique constraint exists only so children can carry a
composite foreign key on `(tenant_id, parent_id)`, which is what makes a
cross-tenant link unwritable rather than merely discouraged.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    money_columns,
    money_pair_check,
)
from reckonai.money import Money

#: The disbursement modes PayPal supports in a connected integration, and the
#: only values `order.disbursement_mode` may take. Under `INSTANT` the seller is
#: paid at capture; under `DELAYED` PayPal holds the funds until the marketplace
#: releases them or 28 days elapse (specs/mission.md section 2).
DISBURSEMENT_MODES: tuple[str, ...] = ("INSTANT", "DELAYED")


class Tenant(TimestampMixin, Base):
    """One marketplace operator: ReckonAI's customer.

    **The only table without a `tenant_id`**, since it is what a `tenant_id`
    points at. `tests/test_models_schema.py` exempts this table by name and
    nothing else.

    In PayPal's multiparty model this is the *platform*. Its sellers are the
    `payee` on each purchase unit.
    """

    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (UniqueConstraint("slug"),)


class Seller(TenantMixin, TimestampMixin, Base):
    """A merchant selling on a marketplace. PayPal's `payee`.

    The two capability flags are not decoration. specs/tech-stack.md section 8
    records that `PARTNER_FEE` and `DELAY_FUNDS_DISBURSEMENT` are granted **per
    seller at onboarding**, not as account-level switches, so whether a platform
    fee or a delayed disbursement is even possible varies by seller. Phase 4's
    seed data needs to express that, and Phase 11 needs it to avoid classifying
    a missing platform fee as a discrepancy for a seller who was never
    onboarded with the capability.

    `paypal_merchant_id` and `paypal_email` are both nullable because the join
    to PayPal is established by whichever of them the platform holds - the
    `PayPal-Auth-Assertion` header accepts either (specs/tech-stack.md
    section 8).
    """

    __tablename__ = "seller"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    paypal_merchant_id: Mapped[str | None] = mapped_column(String(64))
    paypal_email: Mapped[str | None] = mapped_column(String(320))

    #: The currency this seller is normally paid in. Advisory only - the
    #: authoritative currency is on each amount, per specs/mission.md P2.
    default_currency: Mapped[str | None] = mapped_column(String(3))

    partner_fee_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    delay_funds_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        # The marketplace's own identifier for this seller, unique within the
        # marketplace but not across marketplaces.
        UniqueConstraint("tenant_id", "external_ref"),
        # Composite-foreign-key target. See the module docstring and D8.
        UniqueConstraint("tenant_id", "id"),
    )


class Order(TenantMixin, TimestampMixin, Base):
    """A customer order, as the marketplace recorded it.

    `total` is the buyer-facing amount and its currency is the buyer-facing
    currency - which is **not necessarily the settlement currency**. PayPal may
    convert, and when it does the conversion is evidence recorded in Phase 3's
    `fx_conversion` table, never a replacement for this value
    (specs/mission.md P2).
    """

    __tablename__ = "order"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)

    total: Mapped[Money] = money_columns("total")

    placed_at: Mapped[datetime] = mapped_column(nullable=False)

    #: `INSTANT` or `DELAYED`, or NULL where the marketplace did not state it.
    #: Nullable rather than defaulted: assuming `INSTANT` for a silent record
    #: would make every held-funds transaction look like a completed one.
    disbursement_mode: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        UniqueConstraint("tenant_id", "external_ref"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(
            "disbursement_mode IS NULL OR disbursement_mode IN "
            "('INSTANT', 'DELAYED')",
            name="disbursement_mode",
        ),
    )


class OrderItem(TenantMixin, TimestampMixin, Base):
    """One line of an order, attributed to the seller who fulfils it.

    **`seller_id` is the join the whole reconciliation rests on.**
    specs/tech-stack.md section 9.5: seller attribution is the `payee` on the
    PayPal purchase unit, and under the multiparty model that identifies the
    seller rather than the marketplace.

    `line_total` is what the marketplace said this line came to. It is stored
    rather than derived - see the module docstring.
    """

    __tablename__ = "order_item"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seller_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Used by `commission_rule` scoping. Nullable because not every
    #: marketplace categorises its catalogue.
    category: Mapped[str | None] = mapped_column(String(100))

    quantity: Mapped[int] = mapped_column(nullable=False)

    unit_price: Mapped[Money] = money_columns("unit_price")
    line_total: Mapped[Money] = money_columns("line_total")

    __table_args__ = (
        # Composite foreign keys, carrying `tenant_id` into the reference. An
        # item cannot point at an order or a seller belonging to another
        # tenant, and the database is what enforces that - not a code review
        # (requirements.md D7).
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["order.tenant_id", "order.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "seller_id"],
            ["seller.tenant_id", "seller.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "order_id", "external_ref"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )


class CommissionRule(TenantMixin, TimestampMixin, Base):
    """What the marketplace's contract says it keeps from a sale.

    Scoped and effective-dated, per
    specs/2026-11-07-canonical-core/requirements.md D2:

      - `seller_id` NULL means a tenant-wide default
      - `category` NULL means all categories
      - `valid_to` NULL means open-ended

    **Effective dating is why a past reconciliation run stays re-derivable.**
    specs/mission.md P7 requires any decision to be reconstructable after the
    fact. Without `valid_from` / `valid_to`, correcting a seller's rate today
    would silently rewrite what last week's run *meant* -
    specs/tech-stack.md section 9.1 applies exactly this reasoning to connector
    mappings, and a commission rate is the same hazard with money attached.

    Resolution - which of several candidate rules applies - is a pure function
    in `reckonai.engine.rules`, not a method here, because
    specs/tech-stack.md section 6 keeps the calculation free of I/O.
    """

    __tablename__ = "commission_rule"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: NULL means this rule is the tenant's default for the given category.
    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: NULL means the rule applies to every category.
    category: Mapped[str | None] = mapped_column(String(100))

    #: A fraction, not a percentage: 10% is `0.100000`. `NUMERIC(9, 6)` rather
    #: than the money default, since a rate is not an amount and carries no
    #: currency. Six decimal places is a hundredth of a basis point.
    rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)

    #: An optional flat component alongside the percentage - a per-transaction
    #: fee. Nullable, and NULL is genuinely absent rather than zero
    #: (specs/tech-stack.md section 9.6).
    fixed: Mapped[Money | None] = money_columns("fixed", nullable=True)

    valid_from: Mapped[datetime] = mapped_column(nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "seller_id"],
            ["seller.tenant_id", "seller.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("rate >= 0 AND rate <= 1", name="rate_is_a_fraction"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="valid_range"
        ),
        money_pair_check("fixed"),
    )
