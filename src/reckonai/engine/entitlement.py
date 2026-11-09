"""What a seller is owed, and what the marketplace should have taken.

The deterministic core. Phase 10 in specs/roadmap.md, Block B in
specs/2026-11-08-replan/requirements.md, and the highest-value calculation in
the product: every figure the AI agent explains and every exception Phase 11
raises is a comparison against something computed here.

**The commission is computed as a figure comparable to PayPal's platform fee**,
not as the residue of what the marketplace kept
(specs/2026-11-09-entitlement-engine/requirements.md D4). Phase 11 compares it
against `platform_fee.amount` - PayPal's own record of the cut it took - and a
residue is not that figure.

**Quantization happens once, at the commission.** The entitlement is then
`share - commission`, which is exact. Quantizing both independently lets the
two disagree by a minor unit with neither being wrong, and that disagreement
would surface as a one-cent exception on a correct order.

**Pure, with no I/O** (specs/tech-stack.md section 6). Inputs are
`typing.Protocol` types so an ORM row and a test dataclass both fit, which is
what keeps `reckonai.engine` free of any import from `reckonai.models` and the
purity gate in tests/test_money_purity.py satisfiable. The timestamp used to
resolve a rule is the order's own `placed_at`, never a clock read, which is
what makes a historical run re-derivable (specs/mission.md P7).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from reckonai.money import CurrencyMismatch, Money

from .rules import CommissionRuleLike, EngineError, resolve_commission_rule


class MixedCurrencyOrder(EngineError):
    """An order's items do not all carry the order's currency.

    Raised rather than converted. specs/mission.md P2: two amounts are never
    comparable because their numbers look alike, and this module performs no
    conversion - an FX-bearing comparison is Phase 11's, against PayPal's
    reported settlement figure rather than a recomputation.
    """


class FixedCommissionCurrencyMismatch(EngineError):
    """A rule's flat component is denominated in another currency.

    `commission_rule.fixed` carries its own currency. Adding a USD flat fee to
    a EUR percentage would be the mixed-currency arithmetic `Money` refuses,
    and silently dropping it would understate the commission.
    """


@runtime_checkable
class OrderItemLike(Protocol):
    """The fields this module reads from an order line.

    `line_total` rather than `unit_price * quantity`: the stated line total is
    what the marketplace asserts it charged, and recomputing it here would
    hide a disagreement that specs/mission.md P4 wants surfaced. The seed's own
    consistency check compares the two, which is the right place for it.
    """

    id: uuid.UUID
    seller_id: uuid.UUID
    category: str | None
    line_total: Money


@runtime_checkable
class OrderLike(Protocol):
    """The fields this module reads from an order."""

    id: uuid.UUID
    total: Money
    placed_at: datetime


@dataclass(frozen=True, slots=True)
class SellerEntitlement:
    """One seller's expected outcome on one order.

    Frozen, and with no arithmetic of its own - no `__post_init__` recomputing
    a field. A dataclass that recomputes is a second implementation of the
    calculation, and the two can disagree
    (specs/2026-11-09-entitlement-engine/requirements.md D2).

    `commission_rule_id` is carried so an exception can cite the rule it rests
    on, and `fixed_component` records whether the flat part of that rule
    contributed - `None` means the rule declared none, which is a stated
    structure rather than a defaulted amount.
    """

    seller_id: uuid.UUID
    gross_share: Money
    commission: Money
    entitlement: Money
    commission_rule_id: uuid.UUID

    #: The resolved rule's rate, carried so a refund can be commissioned at the
    #: same rate without re-resolving the rule - and so an investigation can
    #: cite the rate a figure rests on rather than only the rule's id.
    commission_rate: Decimal

    fixed_component: Money | None = None

    #: Gross refunded against this line so far, cumulative. `None` means no
    #: refund has been applied.
    #:
    #: Carried because the commission reversal must be computed from the
    #: **cumulative** refund rather than per refund. Quantizing each refund's
    #: reversal independently accumulates rounding: twenty 0.05 refunds of a
    #: 1.00 share at 10% each reverse `quantize(0.005) = 0.01`, summing to 0.20
    #: against a commission of 0.10 - which drove the commission negative and
    #: left the engine claiming a seller was owed 0.10 on a fully refunded
    #: order. See `reckonai.engine.refunds.apply_refund`.
    refunded_gross: Money | None = None

    #: Line ids that made up `gross_share`, so Phase 12 can cite them.
    item_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderEntitlement:
    """Every seller's expected outcome on one order, plus the order totals.

    `commission` is the sum of the per-seller figures, never a separate
    calculation over the order total. Summing rounded parts and rounding a sum
    are different numbers, and only the first is comparable to the per-unit
    platform fees PayPal actually recorded.
    """

    order_id: uuid.UUID
    currency: str
    sellers: tuple[SellerEntitlement, ...]
    commission: Money
    gross: Money

    #: Refunds applied to reach this state, in order. Empty on a fresh
    #: calculation; `reckonai.engine.refunds.apply_refund` appends to it.
    refunds_applied: tuple[str, ...] = field(default=())

    def for_seller(self, seller_id: uuid.UUID) -> SellerEntitlement:
        """The line for one seller, or raise if they have none on this order."""
        for line in self.sellers:
            if line.seller_id == seller_id:
                return line
        raise KeyError(f"seller {seller_id} has no line on order {self.order_id}")


def _gross_share(
    items: Sequence[OrderItemLike], currency: str
) -> tuple[Money, str | None, tuple[uuid.UUID, ...]]:
    """One seller's share of an order, with the category to resolve against.

    The category is taken from the seller's items and must be unique among
    them: two categories would resolve to two different rules and the share
    could not be commissioned as one figure. That case is handled by
    `reckonai.engine.refunds.split_by_rate`, which the caller reaches
    deliberately rather than by accident, so here it raises.
    """
    share = Money("0", currency)
    categories: set[str | None] = set()
    ids: list[uuid.UUID] = []

    for item in items:
        if item.line_total.currency != currency:
            raise MixedCurrencyOrder(
                f"item {item.id} is denominated in "
                f"{item.line_total.currency} on a {currency} order. This "
                "module performs no conversion: see specs/mission.md P2."
            )
        share = share + item.line_total
        categories.add(item.category)
        ids.append(item.id)

    single = categories.pop() if len(categories) == 1 else None
    if categories:
        raise EngineError(
            f"items {ids} span more than one category, so one commission rule "
            "cannot be resolved for the whole share. Commission each category "
            "separately."
        )

    return share, single, tuple(ids)


def commission_for(
    share: Money,
    rate: Decimal,
    fixed: Money | None = None,
) -> Money:
    """The commission on a gross share, quantized once.

    `rate` is a fraction, not a percentage: 10% is `Decimal("0.1")`, matching
    `commission_rule.rate`'s `NUMERIC(9, 6)`.

    `fixed` is the rule's optional flat per-transaction component. `None` means
    the rule declares none - a stated structure, not a defaulted amount, which
    is why this does not raise the way an unknown capture fee is kept unknown
    (requirements.md D8). A flat component in another currency raises rather
    than being dropped.

    >>> commission_for(Money("100.00", "USD"), Decimal("0.1"))
    Money(amount=Decimal('10.00'), currency='USD')
    """
    commission = (share * rate).quantize()

    if fixed is None:
        return commission

    try:
        return commission + fixed.quantize()
    except CurrencyMismatch as exc:
        raise FixedCommissionCurrencyMismatch(
            f"a flat commission component of {fixed} cannot apply to a "
            f"{share.currency} share. Dropping it would understate the "
            "commission; converting it is not this module's to do."
        ) from exc


def expected_entitlement(
    order: OrderLike,
    items: Iterable[OrderItemLike],
    rules: Iterable[CommissionRuleLike],
    *,
    sellers: Mapping[uuid.UUID, object] | None = None,
) -> OrderEntitlement:
    """What each seller on `order` is owed, and what commission is due.

    `rules` are the candidate commission rules - normally every rule for the
    tenant. Filtering by tenant is the caller's job: this function performs no
    I/O and `tenant_id` is not on any protocol here.

    `sellers` is accepted and unused, so a caller already holding the mapping
    need not drop it; it is reserved for a per-seller override that does not
    exist yet. Passing it changes nothing.

    Raises `NoApplicableCommissionRule` if a seller's share is covered by no
    rule - never a zero rate (specs/tech-stack.md section 9.6), and
    `CommissionRuleAmbiguity` if two rules of equal specificity apply.

        >>> expected_entitlement(order, items, rules).commission
        Money(amount=Decimal('16.00'), currency='USD')
    """
    del sellers  # reserved; see the docstring

    currency = order.total.currency
    by_seller: dict[uuid.UUID, list[OrderItemLike]] = {}

    for item in items:
        by_seller.setdefault(item.seller_id, []).append(item)

    lines: list[SellerEntitlement] = []
    commission_total = Money("0", currency)
    gross_total = Money("0", currency)

    for seller_id, seller_items in by_seller.items():
        share, category, item_ids = _gross_share(seller_items, currency)

        rule = resolve_commission_rule(
            rules,
            seller_id=seller_id,
            category=category,
            at=order.placed_at,
        )
        fixed = rule.fixed
        commission = commission_for(share, rule.rate, fixed)

        lines.append(
            SellerEntitlement(
                seller_id=seller_id,
                gross_share=share,
                commission=commission,
                entitlement=share - commission,
                commission_rule_id=rule.id,
                commission_rate=rule.rate,
                fixed_component=fixed,
                item_ids=item_ids,
            )
        )
        commission_total = commission_total + commission
        gross_total = gross_total + share

    return OrderEntitlement(
        order_id=order.id,
        currency=currency,
        sellers=tuple(lines),
        commission=commission_total,
        gross=gross_total,
    )
