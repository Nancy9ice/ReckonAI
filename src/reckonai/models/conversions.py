"""FX conversions, and the four rules that keep them honest.

**The reason Phase 0 ran first.** specs/mission.md P3 forbids substituting an
estimate for a provider record, so this table may only carry what PayPal
actually supplies - and what it supplies is a rate and both amounts, with **no
conversion fee and no conversion timestamp** anywhere in the transaction APIs.

Four rules follow, three of them enforced in DDL
(specs/2026-11-08-canonical-events/requirements.md D9 and D10):

1. **A row exists only where PayPal reported an `exchange_rate`.** A
   same-currency transaction has no conversion and must carry no row at all -
   an empty row would be indistinguishable from a conversion whose fields went
   missing. `CHECK (source_currency <> target_currency)` is the enforceable
   half of that.
2. **`fee` is absent, never zero.** `CHECK (fee_amount IS NULL OR
   fee_amount <> 0)` permits a real fee if a provider ever supplies one and
   refuses the zero that would read downstream as "a fee was measured and it
   was nothing". The residue of net times rate against the reported settlement
   is PayPal's rounding, not a fee, and `paypal_fee` is not a conversion fee.
3. **`target` is PayPal's reported settlement figure, never a recomputation.**
   PayPal rounds the settlement to the cent and leaves the rate unrounded, so
   `source * rate` disagrees with the reported figure on every converted
   transaction - on our own fixture by `-0.00492989235221950`. Phase 11 compares
   against the stored `target`, and recomputing is one of the two named ways to
   fail its zero-false-positive gate.
4. **`source` is the net, not the gross.** PayPal deducts fees in the
   transaction currency and converts the remainder. Converting the gross
   instead is wrong by the whole fee - 14.46 USD on the observed fixture.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import CheckConstraint, Numeric, String, UniqueConstraint
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
from reckonai.models.payloads import (
    RawPayloadMixin,
    capture_or_refund_parent,
    raw_payload_fk,
)
from reckonai.money import Money

#: The scale specs/tech-stack.md section 2 sets as a **floor, not a target**.
#: PayPal's observed rate carried 15 decimal places as a string; it is parsed
#: into `Decimal` and stored whole, never through `float`.
RATE_PRECISION = 20
RATE_SCALE = 10


class FxConversion(
    RawPayloadMixin, LifecycleMixin, TenantMixin, TimestampMixin, Base
):
    """One currency conversion, as PayPal reported it.

    **More than one row per parent is legal and expected.** A capture declares
    a single conversion object, but a refund declares an **array** of them, so
    nothing here may assume one conversion per record - that is an explicit
    specs/roadmap.md Phase 3 exit criterion.

    `specs/paypal-fields.md` is also explicit that two conversion sites were
    never observed: a refund's `net_amount_breakdown` and a payout item's, both
    recorded **unanswered rather than absent**, because our fixtures were
    same-currency. Phase 11 should not assume the capture site is the only one,
    and Phase 7 should expect the refund shape to differ.
    """

    __tablename__ = "fx_conversion"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Exactly one of these two is populated. See `capture_or_refund_parent`.
    capture_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: **The net, not the gross.** See the module docstring, rule 4.
    source: Mapped[Money] = money_columns("source")

    #: PayPal's reported settlement amount - `receivable_amount`. Rounded to
    #: the cent while the rate is not. **Compare against this; never recompute
    #: it** (rule 3).
    target: Mapped[Money] = money_columns("target")

    #: 15 decimal places observed, parsed from PayPal's string into `Decimal`
    #: and stored at full precision.
    rate: Mapped[Decimal] = mapped_column(
        Numeric(RATE_PRECISION, RATE_SCALE), nullable=False
    )

    #: When the conversion happened, where that is knowable. Nullable, and
    #: paired with `is_derived` below.
    #:
    #: PayPal declares no timestamp on any conversion structure. The reporting
    #: ledger's `T0200` event carries its own
    #: `transaction_initiation_date` and is provider evidence, but it lags
    #: about a day; where reporting has not caught up, the capture's
    #: `create_time` is used instead and `is_derived` records that.
    converted_at: Mapped[datetime | None] = mapped_column()

    #: **Governs `converted_at`, and nothing else.**
    #:
    #: Of the nine conversion fields, `specs/paypal-fields.md` authorises
    #: computing exactly one: the timestamp. The rate and both amounts are
    #: never derived, and a derived value may never be presented as provider
    #: evidence (specs/mission.md P3).
    #:
    #: The column keeps the name specs/roadmap.md Phase 3 gives it rather than
    #: being renamed `converted_at_is_derived` for precision - the exit
    #: criterion names `is_derived` and the gate checks for `is_derived`
    #: (requirements.md D10). NOT NULL, so the provenance of a timestamp can
    #: never be unstated while the timestamp exists.
    is_derived: Mapped[bool] = mapped_column(nullable=False)

    #: **Absent, never zero.** See the module docstring, rule 2.
    fee: Mapped[Money | None] = money_columns("fee", nullable=True)

    #: Set by the ingestion connector, which knows which provider it called.
    #: `specs/paypal-fields.md` classifies this `connector_metadata`: not a
    #: PayPal field at all.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        raw_payload_fk(),
        *capture_or_refund_parent(),
        UniqueConstraint("tenant_id", "id"),
        money_pair_check("fee"),
        # Rule 1: a conversion between one currency and itself is not a
        # conversion. This is what enforces "created only where PayPal
        # reported a rate" rather than merely documenting it.
        CheckConstraint(
            "source_currency <> target_currency", name="currencies_differ"
        ),
        # Rule 2: a real fee is storable; a zero one is not.
        CheckConstraint(
            "fee_amount IS NULL OR fee_amount <> 0", name="fee_not_zero"
        ),
        # A rate of zero or less is not a rate. Not a tolerance or a rounding
        # constant - specs/roadmap.md Phase 11 warns against introducing one of
        # those while chasing noise, and this is neither.
        CheckConstraint("rate > 0", name="rate_positive"),
        reconciliation_state_check(),
    )
