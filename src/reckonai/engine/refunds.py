"""What a refund does to a seller's entitlement and to the commission.

**A refund's seller comes from its capture, never from an allocation**
(specs/2026-11-09-entitlement-engine/requirements.md D1). That is not a policy
choice this module makes - it is the only shape either source can report:

  - **PayPal cannot report a cross-seller refund.** A multi-seller order is one
    `purchase_unit` per seller, each with its own `payee`, its own
    `payment_instruction.platform_fees` and its own capture. A refund is issued
    against *one capture*, so it resolves to that capture's single payee.
    Verified against the captured fixtures: `ORD-0001` carries 100.00 USD with
    a 10.00 fee to one seller and 60.00 with a 6.00 fee to the other.
  - **The marketplace agrees independently.** Every `RefundSpec` in
    `reckonai.sim.data` carries a `seller_ref`.

specs/mission.md's worked example follows from this rather than contradicting
it: a 40.00 refund on A's item reduces A to 54.00 and leaves B untouched at
54.00.

**The commission reversal is computed on the refunded amount, not by
recommissioning the reduced share.** PayPal does the same and reports it: a
20.00 refund of a 70.00 capture at 10% carries a `platform_fees` entry of
2.00, leaving a net fee of 5.00. Those are two records, an original and a
reversal, and the engine's figure has to be comparable to both (D4). Rounding
can make the two routes differ by a minor unit, and only this one matches the
provider's own arithmetic.

**Pure, with no I/O** (specs/tech-stack.md section 6).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol, runtime_checkable

from reckonai.money import InvalidAmount, Money, allocate

from .entitlement import OrderEntitlement, commission_for
from .rules import EngineError


class UnattributableRefund(EngineError):
    """A refund that resolves to no seller.

    Raised rather than split proportionally. Neither source produces one, so an
    unattributable refund is a gap in the evidence - and a gap does not
    silently become a proportional allocation (D1). A caller that genuinely
    holds an order-level refund calls `allocate_refund_across_sellers`
    deliberately.
    """


class CaptureNotSupplied(UnattributableRefund):
    """The refund names a capture that is not among those supplied.

    A subclass of `UnattributableRefund`, so a caller catching that still
    catches this - but told apart because the two mean different things. "The
    provider gave no capture link" is a gap in the evidence; "the caller scoped
    `captures` too narrowly" is a bug in the call. Falling through to
    `refund.seller_id` in the second case would reduce *some* seller's line
    using the weaker source and report `via="refund_seller_id"` as though no
    link existed - and if that seller happens to have a line on the order being
    processed, silently reduce the wrong order. Found by review, 2026-11-09.
    """


class RefundAlreadyApplied(EngineError):
    """This refund's external id is already recorded on the order.

    Raised rather than applied twice. `OrderEntitlement.refunds_applied` has
    carried exactly what is needed to detect this since it was added; it simply
    was not consulted. A duplicated provider record is a finding for Phase 11,
    not an amount to subtract again - and the dataset deliberately seeds a
    duplicate-record fault, so a run that iterates refund rows will meet one.
    """


class RefundExceedsShare(EngineError):
    """A refund larger than the seller's gross share on the order.

    Either the refund belongs to another order, or the share was computed from
    an incomplete set of items. Both are findings rather than arithmetic to
    carry out: continuing would produce a negative entitlement and a negative
    commission, which would then be reported as a discrepancy against a figure
    this engine invented.
    """


class FlatComponentReversalUndefined(EngineError):
    """A refund against a commission rule carrying a flat component.

    **Deliberately unresolved.** A per-transaction flat fee on a *partial*
    refund has two defensible treatments - retained, because the transaction
    was still processed, or reversed pro rata - and the marketplace's own rules
    are what decide, not this engine. PayPal publishes nothing to settle it
    either, because a platform fee it reverses is the one the platform set.

    Guessing would produce a commission figure that Phase 11 then compares
    against PayPal's platform fee, so a wrong guess surfaces as a discrepancy
    in a *correct* order. specs/mission.md P5 prefers an escalation to an
    assembled answer.

    No rule in the dataset carries a flat component, so nothing is blocked by
    this. A tenant that configures one has to state the policy first.
    """


@runtime_checkable
class CaptureLike(Protocol):
    """The fields this module reads from a capture."""

    id: uuid.UUID
    seller_id: uuid.UUID | None


@runtime_checkable
class RefundLike(Protocol):
    """The fields this module reads from a refund.

    `capture_id` is nullable and `capture_linkage_is_derived` exists because
    PayPal supplies no `capture_id` on a refund - only a HATEOAS link, parsed
    by the ingester. Phase 11 inherits the consequence: an exception resting on
    this linkage must say the linkage was derived.
    """

    id: uuid.UUID
    external_id: str
    amount: Money
    capture_id: uuid.UUID | None
    capture_linkage_is_derived: bool
    seller_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class RefundAttribution:
    """Which seller a refund belongs to, and how that was established.

    `via` is `"capture"` or `"refund_seller_id"`. It is carried rather than
    discarded because the two are not equally strong evidence: the capture is
    the provider's own link, and the refund's own `seller_id` is a column our
    ingester populated.
    """

    refund_id: uuid.UUID
    refund_external_id: str
    seller_id: uuid.UUID
    amount: Money
    via: str
    linkage_is_derived: bool


def attribute_refund(
    refund: RefundLike, captures: Mapping[uuid.UUID, CaptureLike]
) -> RefundAttribution:
    """Resolve `refund` to the one seller it belongs to.

    `captures` is keyed by capture id - normally every capture on the order.
    The capture is preferred over the refund's own `seller_id` because it is
    the provider's link rather than a column we wrote.

    Raises `CaptureNotSupplied` if the refund names a capture that is not in
    `captures` - which is a scoping bug in the call rather than missing
    evidence, and must not fall through to the weaker source. Raises
    `UnattributableRefund` when there is genuinely nothing to resolve from.

        >>> attribute_refund(refund, {capture.id: capture}).seller_id
    """
    if refund.capture_id is not None:
        capture = captures.get(refund.capture_id)

        if capture is None:
            raise CaptureNotSupplied(
                f"refund {refund.external_id} names capture "
                f"{refund.capture_id}, which is not among the "
                f"{len(captures)} capture(s) supplied. That is a scoping "
                "error in the call, not an absent provider link: falling back "
                "to the refund's own seller_id here could reduce a different "
                "order's entitlement with no error at all."
            )

        if capture.seller_id is not None:
            return RefundAttribution(
                refund_id=refund.id,
                refund_external_id=refund.external_id,
                seller_id=capture.seller_id,
                amount=refund.amount,
                via="capture",
                linkage_is_derived=refund.capture_linkage_is_derived,
            )

    if refund.seller_id is not None:
        return RefundAttribution(
            refund_id=refund.id,
            refund_external_id=refund.external_id,
            seller_id=refund.seller_id,
            amount=refund.amount,
            via="refund_seller_id",
            linkage_is_derived=refund.capture_linkage_is_derived,
        )

    if refund.capture_id is None:
        detail = "it names no capture and carries no seller_id"
    else:
        detail = (
            f"capture {refund.capture_id} resolves to no seller and the refund "
            "carries no seller_id either"
        )

    raise UnattributableRefund(
        f"refund {refund.external_id} resolves to no seller: {detail}. This is "
        "a gap in the evidence, not a refund to split proportionally - see "
        "requirements.md D1."
    )


def apply_refund(
    expected: OrderEntitlement, attribution: RefundAttribution
) -> OrderEntitlement:
    """Reduce one seller's line by a refund, and reverse its commission.

    Returns a new `OrderEntitlement`; the input is frozen and untouched, so an
    order's pre-refund and post-refund states can both be cited.

    The reversal is computed from the **cumulative** refund against this line,
    as `commission_for(refunded_so_far + this_refund) - commission_for(
    refunded_so_far)`. For a single refund that is exactly
    `commission_for(refund, rate)` - the figure PayPal reports - and for a
    sequence it avoids accumulating the per-refund rounding error.

    *Why it cannot be a plain per-refund reversal.* Quantizing each refund's
    reversal independently drifts upward: twenty 0.05 refunds of a 1.00 share
    at 10% each reverse `quantize(0.005) = 0.01`, which sums to 0.20 against a
    commission of 0.10. That drove the commission to -0.10 and left this
    function asserting the marketplace owed the seller 0.10 on an order
    refunded in full - a fabricated figure ([[mission]] P3) that Phase 11 would
    then raise as a discrepancy on a correct order. Found by review, 2026-11-09.

    Computing the cumulative total and taking the delta keeps the running
    commission equal to `quantize(remaining_share * rate)` at every step, so it
    reaches exactly zero on a full refund and never goes below it.

    Raises `RefundExceedsShare` if the refund is larger than the remaining
    share, `RefundAlreadyApplied` if this refund's external id is already on
    the order, and `FlatComponentReversalUndefined` if the rule carries a flat
    component.

        >>> apply_refund(expected, attribution).for_seller(a).entitlement
        Money(amount=Decimal('54.00'), currency='USD')
    """
    line = expected.for_seller(attribution.seller_id)
    refund = attribution.amount

    if attribution.refund_external_id in expected.refunds_applied:
        raise RefundAlreadyApplied(
            f"refund {attribution.refund_external_id} has already been applied "
            f"to order {expected.order_id}. Applying it twice would reduce the "
            "seller's entitlement by a refund that happened once - which is an "
            "understatement this engine would then report as a discrepancy. A "
            "duplicated provider record is a finding for Phase 11, not an "
            "amount to subtract again."
        )

    if refund.currency != expected.currency:
        raise EngineError(
            f"refund {attribution.refund_external_id} is denominated in "
            f"{refund.currency} on a {expected.currency} order. This module "
            "converts nothing: see specs/mission.md P2."
        )

    if refund > line.gross_share:
        raise RefundExceedsShare(
            f"refund {attribution.refund_external_id} of {refund} exceeds "
            f"seller {attribution.seller_id}'s share of {line.gross_share} on "
            f"order {expected.order_id}. Continuing would produce a negative "
            "entitlement from a figure this engine invented."
        )

    if line.fixed_component is not None:
        raise FlatComponentReversalUndefined(
            f"commission rule {line.commission_rule_id} carries a flat "
            f"component of {line.fixed_component}, and whether a partial "
            "refund reverses it is the marketplace's policy to state rather "
            "than this engine's to infer."
        )

    # The cumulative figure, not a per-refund one. See the docstring.
    refunded_before = line.refunded_gross or Money("0", expected.currency)
    refunded_after = refunded_before + refund
    reversal = commission_for(refunded_after, line.commission_rate) - commission_for(
        refunded_before, line.commission_rate
    )

    share_after = line.gross_share - refund
    commission_after = line.commission - reversal
    reduced = replace(
        line,
        gross_share=share_after,
        commission=commission_after,
        entitlement=share_after - commission_after,
        refunded_gross=refunded_after,
    )

    return replace(
        expected,
        sellers=tuple(
            reduced if other.seller_id == reduced.seller_id else other
            for other in expected.sellers
        ),
        commission=expected.commission - reversal,
        gross=expected.gross - refund,
        refunds_applied=(*expected.refunds_applied, attribution.refund_external_id),
    )


def commission_reversal(refund: Money, rate: Decimal) -> Money:
    """The commission reversed by a refund, quantized once.

    Exported separately because Phase 11 compares it against the
    `platform_fees` entry PayPal records on the refund itself, and Phase 12
    exposes the engine as a recompute tool.

    >>> commission_reversal(Money("20.00", "USD"), Decimal("0.1"))
    Money(amount=Decimal('2.00'), currency='USD')
    """
    return commission_for(refund, rate)


def allocate_refund_across_sellers(
    expected: OrderEntitlement, refund: Money, refund_external_id: str
) -> tuple[RefundAttribution, ...]:
    """Split an order-level refund across its sellers by gross share.

    **The deliberate path, not the fallback.** `attribute_refund` raises rather
    than reaching here, because neither PayPal nor the marketplace produces an
    unattributed refund and a gap in the evidence must not silently become a
    proportional split (D1). A caller that genuinely holds an order-level
    refund calls this and owns the choice.

    Returns one attribution per seller, in `expected.sellers` order, which the
    caller then applies with `apply_refund`. The parts sum exactly to the
    refund: `reckonai.money.allocate` seats any spare minor unit rather than
    leaving a residue.
    """
    if refund.currency != expected.currency:
        raise EngineError(
            f"an order-level refund of {refund} cannot apply to a "
            f"{expected.currency} order."
        )

    if not expected.sellers:
        raise UnattributableRefund(
            f"order {expected.order_id} has no seller lines to allocate "
            f"refund {refund_external_id} across."
        )

    weights = [line.gross_share.amount for line in expected.sellers]

    # `allocate` raises `InvalidAmount` from the money layer when the weights
    # sum to zero - every line already fully refunded, or a zero-value order.
    # Every other refusal in this module is an `EngineError`, so letting that
    # through would escape the module's own contract and slip past a caller
    # catching `EngineError`. Found by review, 2026-11-09.
    try:
        parts = allocate(refund, weights)
    except InvalidAmount as exc:
        raise UnattributableRefund(
            f"order {expected.order_id} cannot take an allocated refund of "
            f"{refund}: its seller lines carry no gross share to weight by "
            f"({exc}). A refund against an order with nothing left to refund "
            "is a finding, not a split."
        ) from exc

    return tuple(
        RefundAttribution(
            refund_id=uuid.uuid5(
                uuid.NAMESPACE_OID, f"{refund_external_id}:{line.seller_id}"
            ),
            refund_external_id=refund_external_id,
            seller_id=line.seller_id,
            amount=part,
            via="allocated",
            linkage_is_derived=True,
        )
        for line, part in zip(expected.sellers, parts, strict=True)
    )


def split_by_rate(
    refund: Money,
    shares: Sequence[Money],
    rates: Sequence[Decimal],
) -> tuple[tuple[Money, Money], ...]:
    """Split one seller's refund across their own items and commission each.

    The case `entitlement._gross_share` refuses: a seller whose items on one
    order resolve to *different* commission rates, so the reversal is not a
    single rate times the refund.

    `shares` and `rates` are parallel, one entry per item. Returns one
    `(refunded_part, reversal)` pair per item, in the same order. The refunded
    parts sum exactly to `refund`; the reversals are each quantized at their
    own rate, which is why they are returned separately rather than summed
    here - a caller comparing against PayPal needs the per-unit figures.

    The current dataset has one item per seller per order, so this is covered
    by hand-computed fixtures rather than by a seeded row.
    """
    if len(shares) != len(rates):
        raise EngineError(
            f"{len(shares)} shares against {len(rates)} rates: one rate per "
            "item is required to commission each part at its own rate."
        )

    if not shares:
        raise EngineError("cannot split a refund across no items")

    for share in shares:
        if share.currency != refund.currency:
            raise EngineError(
                f"a share of {share} cannot take part in a "
                f"{refund.currency} refund: this module converts nothing."
            )

    # The same bound `apply_refund` enforces, and for the same reason: parts
    # exceeding the shares they split would carry reversals larger than the
    # commissions originally taken on them, which is a figure this module
    # would have invented. Found by review, 2026-11-09.
    total_share = shares[0]
    for share in shares[1:]:
        total_share = total_share + share

    if refund > total_share:
        raise RefundExceedsShare(
            f"a refund of {refund} exceeds the {total_share} of shares it "
            "would be split across. Continuing would produce reversals larger "
            "than the commissions taken on those items."
        )

    try:
        parts = allocate(refund, [share.amount for share in shares])
    except InvalidAmount as exc:
        raise EngineError(
            f"cannot split {refund} across shares that sum to nothing ({exc})."
        ) from exc

    return tuple(
        (part, commission_for(part, rate))
        for part, rate in zip(parts, rates, strict=True)
    )


def total_refunded(attributions: Iterable[RefundAttribution], currency: str) -> Money:
    """Sum attributions in one currency.

    Single-currency by parameter for the same reason
    `reckonai.engine.ledger.position` is: `Money` refuses mixed-currency
    addition, and skipping is friendlier than raising for a seller who trades
    in two.
    """
    total = Money("0", currency)
    for attribution in attributions:
        if attribution.amount.currency == currency:
            total = total + attribution.amount
    return total


__all__ = [
    "CaptureLike",
    "CaptureNotSupplied",
    "FlatComponentReversalUndefined",
    "RefundAlreadyApplied",
    "RefundAttribution",
    "RefundExceedsShare",
    "RefundLike",
    "UnattributableRefund",
    "allocate_refund_across_sellers",
    "apply_refund",
    "attribute_refund",
    "commission_reversal",
    "split_by_rate",
    "total_refunded",
]
