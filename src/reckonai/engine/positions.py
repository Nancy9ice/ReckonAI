"""A seller's expected position, per currency.

The figure Phase 11 compares against the marketplace's own ledger and against
what PayPal records. One row per (seller, currency), because `Money` refuses
mixed-currency addition and a seller trading in two currencies has two
positions rather than one meaningless total (specs/mission.md P2).

**On the absent capture fee.** specs/roadmap.md Phase 10 requires that a
capture fee is never defaulted to zero. This module satisfies that by carrying
the ids of the captures whose fee is unknown, and **not** by publishing a
fee-inclusive position - see
specs/2026-11-09-entitlement-engine/requirements.md D3, revised.

The reason is evidence rather than taste. Across all 21 captured sandbox
captures, `net_amount` equals `gross_amount - platform_fees` **exactly, with
zero residual**, and the refunds close the same way. So PayPal's own breakdown
is arithmetically complete without a processing fee, and the expected
entitlement computed here is directly comparable to PayPal's `net_amount`:
90.00 against 90.00, nothing deducted in between. A fee-inclusive figure is
only needed to answer what a seller banked after PayPal's own cut, which is the
seller financial position view that the replan's R13 drops from the MVP.

An optional field that is `None` for every real record would be worse than its
absence: the next reader treats `None` as "no fee", which is exactly the
collapse the criterion forbids.

**Pure, with no I/O** (specs/tech-stack.md section 6).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reckonai.money import Money

from .entitlement import OrderEntitlement


@runtime_checkable
class CaptureFeeLike(Protocol):
    """The fields this module reads from a capture, to find unknown fees.

    `paypal_fee` is nullable on the ORM class on purpose: `NULL` is what makes
    "unknown" representable, and a `CHECK` is not what enforces it - the
    ingester is told never to overwrite a populated breakdown with `NULL` on a
    later read, because a capture loses its whole `seller_receivable_breakdown`
    while `PENDING`.

    The currency comes from `amount` rather than from `paypal_fee`, since an
    absent fee carries no currency to read.
    """

    external_id: str
    seller_id: uuid.UUID | None
    amount: Money
    paypal_fee: Money | None


@dataclass(frozen=True, slots=True)
class SellerPosition:
    """What one seller is owed in one currency, and what is unknown about it.

    `expected` is the sum of that seller's entitlements across the orders
    supplied, after any refunds those entitlements already carry. It is the
    marketplace's obligation to the seller, and - because PayPal's breakdown
    closes without a processing fee - it is directly comparable to the sum of
    PayPal's `net_amount` figures for the same captures.

    `unknown_fee_capture_ids` is the recorded limitation, not a defect. Empty
    means every contributing capture stated a fee; populated means one or more
    did not, and any claim about what the seller *banked* rests on a gap.
    """

    seller_id: uuid.UUID
    currency: str
    expected: Money
    order_ids: tuple[uuid.UUID, ...]
    unknown_fee_capture_ids: tuple[str, ...] = ()

    @property
    def fee_is_known(self) -> bool:
        """Whether every contributing capture stated a processing fee.

        A property rather than a stored field, so it cannot disagree with the
        ids beside it.
        """
        return not self.unknown_fee_capture_ids


def expected_positions(
    entitlements: Iterable[OrderEntitlement],
    captures: Iterable[CaptureFeeLike] = (),
) -> tuple[SellerPosition, ...]:
    """Every seller's expected position, one row per currency.

    `entitlements` are the per-order results from
    `reckonai.engine.expected_entitlement`, with any refunds already applied by
    `reckonai.engine.apply_refund` - this function does not know about refunds
    and does not need to, because a refunded entitlement is just a smaller one.

    `captures` is optional and used only to find unknown processing fees. A
    caller that passes none gets positions with an empty
    `unknown_fee_capture_ids`, which asserts nothing about the fees rather than
    asserting they were known.

    Rows come back in first-seen seller order, then currency alphabetically, so
    the output is deterministic without imposing an ordering on UUIDs that
    would be arbitrary.

        >>> [(p.currency, p.expected) for p in expected_positions(results)]
        [('USD', Money(amount=Decimal('349.50'), currency='USD'))]
    """
    totals: dict[tuple[uuid.UUID, str], Money] = {}
    orders: dict[tuple[uuid.UUID, str], list[uuid.UUID]] = {}
    seller_order: list[uuid.UUID] = []

    for entitlement in entitlements:
        for line in entitlement.sellers:
            key = (line.seller_id, entitlement.currency)

            if line.seller_id not in seller_order:
                seller_order.append(line.seller_id)

            running = totals.get(key)
            totals[key] = (
                line.entitlement if running is None else running + line.entitlement
            )
            orders.setdefault(key, []).append(entitlement.order_id)

    unknown: dict[tuple[uuid.UUID, str], list[str]] = {}
    for capture in captures:
        if capture.seller_id is None or capture.paypal_fee is not None:
            continue
        unknown.setdefault((capture.seller_id, capture.amount.currency), []).append(
            capture.external_id
        )

    return tuple(
        SellerPosition(
            seller_id=seller_id,
            currency=currency,
            expected=totals[(seller_id, currency)],
            order_ids=tuple(orders[(seller_id, currency)]),
            unknown_fee_capture_ids=tuple(unknown.get((seller_id, currency), ())),
        )
        for seller_id in seller_order
        for currency in sorted({c for (s, c) in totals if s == seller_id})
    )


def expected_commission(
    entitlements: Iterable[OrderEntitlement],
) -> dict[str, Money]:
    """The tenant's expected commission, per currency.

    Keyed by currency for the same reason positions are: there is no single
    number. Each value is the sum of the per-seller commissions the engine
    computed, so it stays comparable to the sum of the platform fees PayPal
    recorded on the same purchase units (requirements.md D4).
    """
    totals: dict[str, Money] = {}

    for entitlement in entitlements:
        running = totals.get(entitlement.currency)
        totals[entitlement.currency] = (
            entitlement.commission
            if running is None
            else running + entitlement.commission
        )

    return totals


__all__ = [
    "CaptureFeeLike",
    "SellerPosition",
    "expected_commission",
    "expected_positions",
]
