"""The rules that turn a compared order into classified findings.

specs/roadmap.md Phase 11's four exit criteria all land here, and the second -
"the clean baseline produces zero exceptions" - is the one that shapes the
design. Two structural choices follow from it.

**Specific rules explain, and a fallback reports only the remainder.** A
generic "expected position versus ledger position" check would fire on every
fault, so F1 would be reported both as a missing ledger adjustment and as a
position discrepancy. Instead each specific rule, when it fires, *accounts
for* an amount, and the residual check reports only what no rule explained.
One fault produces one finding, and an unexplained difference is still never
silently dropped.

**Every refund is applied to the expected position, from whichever source
recorded it.** F5 is why: the marketplace refunded the buyer and recorded it
correctly, and PayPal's record is merely late. Withholding the refund from the
expected figure would make the marketplace's own correct books look wrong by
the refund - the exact false positive R10 exists to prevent. The missing
provider record is reported as `awaiting_evidence`, which is not an exception.

## What this module will not do

It never recomputes a figure PayPal reported. `requirements.md` D3: the
expected entitlement is compared against `net_amount`, the commission against
`platform_fee.amount`, and a converted settlement against the reported
`receivable_amount`. The consequence is worth stating because it is easy to
read as an omission - **the one-cent artefact ORD-0008 carries never arises
here at all.** It appears only when a sum of separately-converted legs is set
against a total converted in one step, and that total is not a figure PayPal
reports; producing it would be the recomputation D3 forbids. So ORD-0008 is
clean because the forbidden arithmetic is absent, not because a tolerance
absorbs it. `tolerance.compare_converted_total` remains the guard for the
sum-versus-total shape, and no path here reaches it today.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reckonai.engine import (
    CaptureNotSupplied,
    EngineError,
    OrderEntitlement,
    apply_refund,
    attribute_refund,
    expected_entitlement,
    position,
)
from reckonai.engine.entitlement import OrderItemLike, OrderLike
from reckonai.engine.rules import CommissionRuleLike
from reckonai.money import Money
from reckonai.recon.matcher import (
    LedgerEntryLike,
    RefundRecordLike,
    ledger_by_category,
    match_refunds,
)
from reckonai.recon.tolerance import (
    Comparison,
    compare_exact,
    is_healthy_disbursement,
)
from reckonai.recon.vocabulary import (
    EvidenceBasis,
    ExceptionCode,
    code_for_engine_error,
    is_exception,
)

#: Ledger categories that move a seller's position on an order. `fee` and
#: `dispute` are absent because neither appears in this dataset and treating
#: an unseen category as position-moving would change every expected figure
#: on the strength of no evidence.
POSITION_CATEGORIES: tuple[str, ...] = (
    "sale",
    "commission",
    "refund",
    "commission_reversal",
    "adjustment",
)


@runtime_checkable
class OrderItemRecordLike(OrderItemLike, Protocol):
    """An order item, plus the reference an investigation would cite.

    The engine's `OrderItemLike` carries no `external_ref` - it computes
    rather than reports and has no need of one. Citation does need it:
    `reckonai.sim.manifest` names the item among the records a correct
    investigation should cite for F1, F2, F3 and F6, and Block E is scored on
    citing the right evidence.
    """

    external_ref: str


@runtime_checkable
class CaptureRecordLike(Protocol):
    """PayPal's capture, including the breakdown that can be entirely absent.

    Every money field below `amount` is nullable, and that is observed
    behaviour: a capture loses its whole `seller_receivable_breakdown` while
    `PENDING`, and an open dispute puts it there.
    """

    id: uuid.UUID
    external_id: str
    seller_id: uuid.UUID | None
    amount: Money
    gross_amount: Money | None
    net_amount: Money | None
    paypal_fee: Money | None
    settlement_amount: Money | None
    status: str
    disbursement_state: str


@runtime_checkable
class PlatformFeeLike(Protocol):
    """The commission as PayPal actually took it - independent evidence."""

    external_id: str
    capture_id: uuid.UUID | None

    #: Set on the fee PayPal reverses when a refund is issued, and NULL on the
    #: fee it took at capture. The two must stay distinguishable: adding a
    #: reversal to a capture fee would double the commission.
    refund_id: uuid.UUID | None

    seller_id: uuid.UUID | None
    amount: Money


@runtime_checkable
class DisbursementLike(Protocol):
    """A release record. `state` answers whether the seller has the money."""

    external_id: str
    capture_id: uuid.UUID | None
    seller_id: uuid.UUID | None
    amount: Money
    state: str


@dataclass(frozen=True, slots=True)
class Finding:
    """One classified conclusion about one order.

    Phase 11's fourth exit criterion is this dataclass: every row carries its
    financial impact and the ids of its evidence. `requirements.md` D8 keeps it
    a frozen contract rather than a table - Block D owns the table - and
    `as_dict` is the shape Block F renders.
    """

    code: ExceptionCode
    order_ref: str
    detail: str

    #: The magnitude of the difference. Zero on a non-error finding, so a
    #: verdict and its impact can never contradict each other.
    impact: Money

    #: External references an investigation should cite. Real ids only; Block
    #: E's exit criterion is that every cited id resolves to a real record.
    evidence_refs: tuple[str, ...]

    evidence_basis: EvidenceBasis = EvidenceBasis.PROVIDER_REPORTED

    seller_id: uuid.UUID | None = None

    #: The two figures compared, where the finding came from a comparison.
    expected: Money | None = None
    reported: Money | None = None

    #: False where the pairing behind the finding was ambiguous or derived, so
    #: Block F can show a confirmed-versus-suspected distinction without
    #: re-deriving how each pairing was made.
    confirmed: bool = True

    @property
    def is_exception(self) -> bool:
        """Whether this counts toward the false-positive gate."""
        return is_exception(self.code)

    def as_dict(self) -> dict[str, object]:
        """The frozen JSON contract. Amounts are strings, always with currency.

        specs/tech-stack.md: amounts cross JSON as
        `{"amount": "160.00", "currency": "USD"}`. No client-side arithmetic is
        possible from this shape, which is what Block F's "no arithmetic in the
        dashboard" rule rests on.
        """
        return {
            "code": str(self.code),
            "order_ref": self.order_ref,
            "detail": self.detail,
            "is_exception": self.is_exception,
            "impact": _amount(self.impact),
            "expected": _amount(self.expected) if self.expected else None,
            "reported": _amount(self.reported) if self.reported else None,
            "evidence_refs": list(self.evidence_refs),
            "evidence_basis": str(self.evidence_basis),
            "seller_id": str(self.seller_id) if self.seller_id else None,
            "confirmed": self.confirmed,
        }


def _item_refs(case: OrderCase, seller_id: uuid.UUID | None) -> tuple[str, ...]:
    """The references of the items a finding rests on.

    Scoped to the seller: citing another seller's line as evidence for this
    seller's shortfall sends an investigation to the wrong record.
    """
    return tuple(
        item.external_ref
        for item in case.items
        if seller_id is None or item.seller_id == seller_id
    )


def _amount(money: Money) -> dict[str, str]:
    return {"amount": str(money.quantize().amount), "currency": money.currency}


@dataclass(frozen=True, slots=True)
class OrderCase:
    """Everything known about one order, from both sources.

    Assembled by the caller, which is where the queries belong. Nothing here
    is fetched, and no field is optional-by-default: a caller that forgot the
    platform fees would otherwise get a clean reconciliation rather than an
    error.
    """

    order_ref: str
    order: OrderLike
    items: tuple[OrderItemRecordLike, ...]
    rules: tuple[CommissionRuleLike, ...]

    captures: tuple[CaptureRecordLike, ...] = ()
    platform_fees: tuple[PlatformFeeLike, ...] = ()
    disbursements: tuple[DisbursementLike, ...] = ()

    #: Partitioned by source, because which source a record came from is a
    #: property of its `raw_payload` row and resolving it needs a query.
    marketplace_refunds: tuple[RefundRecordLike, ...] = ()
    provider_refunds: tuple[RefundRecordLike, ...] = ()

    ledger_entries: tuple[LedgerEntryLike, ...] = ()


def classify_order(case: OrderCase) -> tuple[Finding, ...]:
    """Every finding on one order, or nothing at all.

    Nothing at all is the expected result for 15 of the 18 orders in the
    dataset, including the two non-faults, and that is the criterion this
    function is judged on.

    Raises:
        CaptureNotSupplied: propagated, never classified. It means this
            function was called with captures that do not cover the order's
            refunds - a caller bug, which must fail the run rather than appear
            as a finding about the data.
    """
    findings: list[Finding] = []

    # --- the expected position, or the reason there isn't one --------------
    try:
        expected = expected_entitlement(case.order, case.items, case.rules)
    except CaptureNotSupplied:
        raise
    except EngineError as error:
        return (
            Finding(
                code=code_for_engine_error(error),
                order_ref=case.order_ref,
                detail=(
                    f"No expected position could be derived: "
                    f"{type(error).__name__}: {error}"
                ),
                impact=Money.zero(case.order.total.currency),
                evidence_refs=(case.order_ref,),
            ),
        )

    # --- refunds, from whichever source recorded them ----------------------
    matches, marketplace_only, provider_only = match_refunds(
        case.marketplace_refunds, case.provider_refunds
    )
    captures_by_id = {capture.id: capture for capture in case.captures}

    known: list[RefundRecordLike] = []
    for match in matches:
        known.append(_by_id(case.provider_refunds, match.provider_refund_id))
    known.extend(marketplace_only)
    known.extend(provider_only)

    grouped = ledger_by_category(case.ledger_entries)

    # **A refund the ledger never recorded is held out of the expected
    # figure.** F1 is the case: the ledger's rows are internally consistent
    # with the pre-refund world, and the single defect is the missing pair.
    # Applying the refund to the expected position anyway would make the same
    # fault surface three times - as a commission mismatch, as a missing
    # adjustment, and as a residual shortfall - and two of those three would
    # be false positives on a correctly detected fault.
    reflected, unreflected = _partition_by_ledger(known, case, grouped)

    for refund in reflected:
        try:
            attribution = attribute_refund(refund, captures_by_id)
            expected = apply_refund(expected, attribution)
        except CaptureNotSupplied:
            raise
        except EngineError as error:
            findings.append(
                Finding(
                    code=code_for_engine_error(error),
                    order_ref=case.order_ref,
                    detail=(
                        f"Refund {refund.external_id} could not be applied: "
                        f"{type(error).__name__}: {error}"
                    ),
                    impact=refund.amount,
                    evidence_refs=(refund.external_id, case.order_ref),
                    evidence_basis=(
                        EvidenceBasis.DERIVED_LINKAGE
                        if refund.capture_linkage_is_derived
                        else EvidenceBasis.PROVIDER_REPORTED
                    ),
                    seller_id=refund.seller_id,
                )
            )

    # --- R10: a marketplace refund PayPal has not reported yet -------------
    for refund in marketplace_only:
        findings.append(
            Finding(
                code=ExceptionCode.AWAITING_EVIDENCE,
                order_ref=case.order_ref,
                detail=(
                    f"The marketplace recorded refund {refund.external_id} and "
                    "PayPal has no record of it yet. The reporting APIs run "
                    "about a day behind, so the record is late rather than "
                    "absent; the next scheduled run re-evaluates it."
                ),
                impact=Money.zero(refund.amount.currency),
                evidence_refs=(refund.external_id, case.order_ref),
                seller_id=refund.seller_id,
            )
        )

    # The marketplace's own reference for each matched provider refund, so a
    # finding cites both sides. The manifest names the marketplace refund for
    # F1, and Block E is scored on citing the right record.
    marketplace_counterparts = {
        match.provider_refund_id: (match.marketplace_external_id,) for match in matches
    }

    explained: dict[tuple[uuid.UUID, str], Money] = {}

    # **Duplicates first, and the surplus row is held out of every later
    # comparison.** A duplicated commission debit is a duplicate, a
    # commission mismatch and a position shortfall at once; offsetting it
    # afterwards double-counted the amount and gave three findings for one
    # fault. Excluding the surplus is the same move as holding an unreflected
    # refund out of the expected position: the defect is named once, and every
    # later comparison sees books that are internally consistent.
    duplicate_findings, surplus = _duplicate_findings(case, grouped)
    grouped = {
        key: tuple(entry for entry in entries if entry.id not in surplus)
        for key, entries in grouped.items()
    }

    findings.extend(_commission_findings(case, expected, grouped, explained))
    findings.extend(
        _missing_adjustment_findings(
            case, unreflected, grouped, marketplace_counterparts
        )
    )
    findings.extend(duplicate_findings)
    findings.extend(_residual_findings(case, expected, grouped, explained))

    return tuple(findings)


def _partition_by_ledger(
    refunds: Sequence[RefundRecordLike],
    case: OrderCase,
    grouped: Mapping[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]],
) -> tuple[tuple[RefundRecordLike, ...], tuple[RefundRecordLike, ...]]:
    """Split refunds into those the seller ledger records and those it does not.

    Paired on amount, and each ledger row is consumed once: two refunds of
    equal value need two rows, or the second is correctly reported as
    unrecorded. Without consumption one row would vouch for both, and a real
    missing adjustment would disappear.

    Only a refund with a provider record can be reported as a missing
    adjustment, so a marketplace-only refund is treated as reflected here -
    its own finding is `awaiting_evidence`, and the provider record is the
    evidence that the refund happened at all.
    """
    provider_ids = {refund.id for refund in case.provider_refunds}
    available: dict[tuple[uuid.UUID, str, str], int] = {}

    for (order_id, category), entries in grouped.items():
        if order_id != case.order.id or category != "refund":
            continue
        for entry in entries:
            key = (
                entry.seller_id,
                str(entry.amount.quantize().amount),
                entry.amount.currency,
            )
            available[key] = available.get(key, 0) + 1

    reflected: list[RefundRecordLike] = []
    unreflected: list[RefundRecordLike] = []

    for refund in sorted(refunds, key=lambda r: r.external_id):
        if refund.seller_id is None or refund.id not in provider_ids:
            reflected.append(refund)
            continue

        key = (
            refund.seller_id,
            str(refund.amount.quantize().amount),
            refund.amount.currency,
        )
        if available.get(key, 0) > 0:
            available[key] -= 1
            reflected.append(refund)
        else:
            unreflected.append(refund)

    return tuple(reflected), tuple(unreflected)


def _by_id(records: Sequence[RefundRecordLike], wanted: uuid.UUID) -> RefundRecordLike:
    for record in records:
        if record.id == wanted:
            return record
    raise KeyError(f"no refund {wanted}")


def _entries_for(
    grouped: Mapping[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]],
    order_id: uuid.UUID,
    seller_id: uuid.UUID,
    categories: Iterable[str],
) -> tuple[LedgerEntryLike, ...]:
    found: list[LedgerEntryLike] = []
    for category in categories:
        found.extend(
            entry
            for entry in grouped.get((order_id, category), ())
            if entry.seller_id == seller_id
        )
    return tuple(found)


def _credit(
    explained: dict[tuple[uuid.UUID, str], Money],
    seller_id: uuid.UUID,
    amount: Money,
) -> None:
    """Record that `amount` of a seller's residual is accounted for.

    **Keyed by currency as well as by seller.** A ledger can carry a currency
    the order does not - F3's whole shape - so one running total per seller
    could be asked to add EUR to USD, and `Money` would raise
    mid-reconciliation rather than produce a finding (specs/mission.md P2).
    """
    key = (seller_id, amount.currency)
    running = explained.get(key)
    explained[key] = amount if running is None else running + amount


def _commission_findings(
    case: OrderCase,
    expected: OrderEntitlement,
    grouped: Mapping[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]],
    explained: dict[tuple[uuid.UUID, str], Money],
) -> list[Finding]:
    """The commission the marketplace kept against the commission due.

    F2's rule. The expected figure is the engine's, which Block B pinned equal
    to `platform_fee.amount` on all 15 clean purchase units - so this compares
    the marketplace's books against a figure PayPal independently corroborates,
    which is what makes the disagreement evidence rather than opinion.

    The running commission comes from the engine's state, never from summing
    per-refund reversals (`requirements.md` D6).
    """
    findings: list[Finding] = []

    for line in expected.sellers:
        entries = _entries_for(
            grouped,
            case.order.id,
            line.seller_id,
            ("commission", "commission_reversal"),
        )
        if not entries:
            continue

        currency = line.commission.currency
        if any(entry.amount.currency != currency for entry in entries):
            # A currency mismatch is the FX rule's business, not this one.
            continue

        # Commission is a debit against the seller, so `position` returns it
        # negative; the magnitude is what compares to the engine's figure.
        recorded = abs(position(entries, currency))
        result = compare_exact(line.commission, recorded)
        if result.agrees:
            continue

        fee_refs = tuple(
            fee.external_id
            for fee in case.platform_fees
            if fee.seller_id == line.seller_id
        )
        # The capture is cited beside the fee because it is the stable
        # reference: `platform_fee.external_id` is built as
        # `f"pf-{payload_uuid}-{index}"` and changes on every seed, so it can
        # name the evidence but cannot be relied on to find it again.
        capture_refs = tuple(
            capture.external_id
            for capture in case.captures
            if capture.seller_id == line.seller_id
        )
        findings.append(
            Finding(
                code=ExceptionCode.COMMISSION_MISMATCH,
                order_ref=case.order_ref,
                detail=(
                    f"The marketplace retained {recorded} where the applicable "
                    f"rule gives {line.commission}. PayPal's platform fee is "
                    "the independent evidence."
                ),
                impact=result.impact,
                evidence_refs=(
                    case.order_ref,
                    *_item_refs(case, line.seller_id),
                    *capture_refs,
                    *fee_refs,
                ),
                seller_id=line.seller_id,
                expected=line.commission,
                reported=recorded,
            )
        )
        # The ledger keeping too much commission understates the seller by the
        # same amount, which is the residual this explains.
        _credit(explained, line.seller_id, line.commission - recorded)

    return findings


def _missing_adjustment_findings(
    case: OrderCase,
    unreflected: tuple[RefundRecordLike, ...],
    grouped: Mapping[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]],
    counterparts: Mapping[uuid.UUID, tuple[str, ...]] | None = None,
) -> list[Finding]:
    """A refund both sources agree happened, that the ledger never recorded.

    specs/mission.md section 6 Scenario 1, and F1. The absence is findable
    because `LEDGER_ENTRY_CATEGORIES` gives it a category to be absent from -
    there is no row to look up.

    The impact is the seller position's overstatement: the refund less the
    commission PayPal reversed automatically, because the marketplace does not
    keep commission on a reversed sale.
    """
    findings: list[Finding] = []
    counterparts = counterparts or {}

    for refund in unreflected:
        seller_id = refund.seller_id
        if seller_id is None:
            continue

        # The commission PayPal reversed on this refund, as PayPal recorded it.
        # `platform_fee.refund_id` identifies a reversal; a capture fee has
        # none. Never recomputed from the rate: the reversal is pro rata and
        # PayPal performs it automatically, so its own record is the evidence.
        reversals = tuple(
            fee
            for fee in case.platform_fees
            if fee.refund_id == refund.id
            and fee.amount.currency == refund.amount.currency
        )
        reversed_commission = Money.zero(refund.amount.currency)
        for fee in reversals:
            reversed_commission = reversed_commission + fee.amount

        reversal_refs = tuple(fee.external_id for fee in reversals)
        impact = refund.amount - reversed_commission
        findings.append(
            Finding(
                code=ExceptionCode.MISSING_LEDGER_ADJUSTMENT,
                order_ref=case.order_ref,
                detail=(
                    f"PayPal recorded refund {refund.external_id} of "
                    f"{refund.amount} and the seller ledger carries neither "
                    "the refund debit nor the commission reversal, so the "
                    "seller's position is overstated."
                ),
                impact=impact,
                evidence_refs=(
                    refund.external_id,
                    case.order_ref,
                    *counterparts.get(refund.id, ()),
                    *_item_refs(case, seller_id),
                    *reversal_refs,
                ),
                evidence_basis=(
                    EvidenceBasis.DERIVED_LINKAGE
                    if refund.capture_linkage_is_derived
                    else EvidenceBasis.PROVIDER_REPORTED
                ),
                seller_id=seller_id,
            )
        )

    return findings


def _duplicate_findings(
    case: OrderCase,
    grouped: Mapping[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]],
) -> tuple[list[Finding], set[uuid.UUID]]:
    """One movement recorded twice under distinct references. F4.

    Distinct references are the only form this can take: `UNIQUE (tenant_id,
    external_ref)` refuses a literal repeat, and a real double-post carries
    its own id. So the duplicate is found by equal amount and category within
    one order, not by a repeated reference.
    """
    findings: list[Finding] = []
    surplus: set[uuid.UUID] = set()

    for (order_id, category), entries in sorted(
        grouped.items(), key=lambda item: (str(item[0][0]), item[0][1])
    ):
        if order_id != case.order.id or len(entries) < 2:
            continue

        # **Keyed by seller as well as by amount.** Two sellers with equal
        # shares produce two identical `sale` credits and two identical
        # `commission` debits, and a key without the seller reported that
        # healthy order as duplicated twice, plus a self-contradicting
        # residual. No seeded order splits equally, so the gate passed on luck.
        seen: dict[tuple[uuid.UUID, str, str, str], LedgerEntryLike] = {}
        for entry in entries:
            key = (
                entry.seller_id,
                str(entry.amount.quantize().amount),
                entry.amount.currency,
                entry.transaction_type,
            )
            first = seen.get(key)
            if first is None:
                seen[key] = entry
                continue

            findings.append(
                Finding(
                    code=ExceptionCode.DUPLICATE_LEDGER_ENTRY,
                    order_ref=case.order_ref,
                    detail=(
                        f"Ledger entries {first.external_ref} and "
                        f"{entry.external_ref} both record a {category} of "
                        f"{entry.amount} on this order, overstating the "
                        "seller's position by the repeated amount."
                    ),
                    impact=entry.amount,
                    evidence_refs=(
                        first.external_ref,
                        entry.external_ref,
                        case.order_ref,
                    ),
                    seller_id=entry.seller_id,
                )
            )
            # The surplus row, held out of every later comparison rather
            # than offset against them afterwards.
            surplus.add(entry.id)

    return findings, surplus


def _residual_findings(
    case: OrderCase,
    expected: OrderEntitlement,
    grouped: Mapping[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]],
    explained: dict[tuple[uuid.UUID, str], Money],
) -> list[Finding]:
    """What no specific rule explained.

    The fallback, and the reason a fault produces one finding rather than two.
    It fires only on the part of a seller's residual that the rules above did
    not account for, which is what keeps F1 from also being reported as a
    position discrepancy.

    Two shapes reach it:

      - **the ledger in another currency** (F3). The marketplace recorded what
        it owes in its home currency at a stale rate. Compared against
        PayPal's reported `receivable_amount` - never against a recomputation
        of net times rate, which on this dataset disagrees with the reported
        figure on every converted transaction.
      - **a plain shortfall on a completed release** (F6), where PayPal has
        discharged the obligation and the ledger records less.

    Held funds reach it and produce nothing: under `DELAYED`, funds held with
    no release requested is a healthy intermediate state, and PayPal
    auto-releases after 28 days so an absent instruction is not evidence of a
    problem.
    """
    findings: list[Finding] = []

    for line in expected.sellers:
        entries = _entries_for(
            grouped, case.order.id, line.seller_id, POSITION_CATEGORIES
        )
        if not entries:
            continue

        currencies = {entry.amount.currency for entry in entries}

        # --- the ledger recorded in another currency (F3) ------------------
        if currencies and currencies != {line.entitlement.currency}:
            findings.extend(
                _currency_findings(case, line.seller_id, entries, currencies)
            )
            continue

        recorded = position(entries, line.entitlement.currency)
        result = compare_exact(line.entitlement, recorded)
        accounted = explained.get(
            (line.seller_id, line.entitlement.currency),
            Money.zero(line.entitlement.currency),
        )
        residual = result.difference - accounted
        if residual.is_zero():
            continue

        # **Both sides of the held-funds question.** A capture's
        # `disbursement_state` only ever reads `disbursed_at_capture`,
        # `disbursement_completed` or `funds_held`; `release_requested` and
        # `disbursement_pending` exist solely on the `disbursement` row. So
        # consulting the capture alone left a release in flight - the exact
        # thing T3 tolerates - reported as a mismatch.
        capture = _capture_for(case, line.seller_id)
        states = [
            release.state
            for release in case.disbursements
            if release.seller_id == line.seller_id
        ]
        if capture is not None:
            states.append(capture.disbursement_state)

        if any(is_healthy_disbursement(state) for state in states):
            # Not a discrepancy. Classifying it as one makes every DELAYED
            # transaction an exception, which is the trap by design.
            continue

        release_refs = tuple(
            release.external_id
            for release in case.disbursements
            if release.seller_id == line.seller_id
        )
        findings.append(
            Finding(
                code=ExceptionCode.DISBURSEMENT_MISMATCH,
                order_ref=case.order_ref,
                detail=(
                    f"PayPal discharged {line.entitlement} and the seller "
                    f"ledger records {recorded}, a shortfall of "
                    f"{abs(residual)} with nothing in flight to explain it."
                ),
                impact=abs(residual),
                evidence_refs=(
                    case.order_ref,
                    *_item_refs(case, line.seller_id),
                    *release_refs,
                ),
                seller_id=line.seller_id,
                expected=line.entitlement,
                reported=recorded,
            )
        )

    return findings


def _currency_findings(
    case: OrderCase,
    seller_id: uuid.UUID,
    entries: tuple[LedgerEntryLike, ...],
    currencies: set[str],
) -> list[Finding]:
    """The ledger carries a currency the order does not. F3's rule.

    The comparison is against PayPal's reported settlement, which is the only
    figure in that currency anyone measured. Where no settlement was reported,
    the gap is reported as unresolvable rather than closed with a rate: a
    quoted rate is never substituted for a conversion record
    (specs/mission.md P3).
    """
    findings: list[Finding] = []

    for currency in sorted(currencies):
        recorded = position(
            tuple(e for e in entries if e.amount.currency == currency), currency
        )
        settlement = next(
            (
                capture.settlement_amount
                for capture in case.captures
                if capture.seller_id == seller_id
                and capture.settlement_amount is not None
                and capture.settlement_amount.currency == currency
            ),
            None,
        )
        if settlement is None:
            # **Reported, not dropped.** The ledger is in a currency the order
            # is not and PayPal reported no settlement in it, so there is no
            # measured figure to compare and none may be derived - a quoted
            # rate is never substituted for a conversion record (P3). The
            # impact is zero because none can be *stated*, which is different
            # from there being none, and the detail says so.
            if currency == case.order.total.currency or recorded.is_zero():
                continue
            findings.append(
                Finding(
                    code=ExceptionCode.MIXED_CURRENCY_ORDER,
                    order_ref=case.order_ref,
                    detail=(
                        f"The seller ledger records {recorded} on an order "
                        f"denominated in {case.order.total.currency}, and "
                        "PayPal reported no settlement in that currency. No "
                        "comparable figure exists, so the difference cannot "
                        "be quantified without inventing a rate."
                    ),
                    impact=Money.zero(currency),
                    evidence_refs=(
                        case.order_ref,
                        *(
                            entry.external_ref
                            for entry in entries
                            if entry.amount.currency == currency
                        ),
                    ),
                    seller_id=seller_id,
                    reported=recorded,
                    confirmed=False,
                )
            )
            continue

        result = compare_exact(settlement, recorded)
        if result.agrees:
            continue

        capture_refs = tuple(
            capture.external_id
            for capture in case.captures
            if capture.seller_id == seller_id
        )
        findings.append(
            Finding(
                code=ExceptionCode.FX_DISCREPANCY,
                order_ref=case.order_ref,
                detail=(
                    f"The marketplace records {recorded} where PayPal's "
                    f"reported settlement is {settlement}, a difference of "
                    f"{result.impact}. Compared against the reported "
                    "settlement, not against net times rate."
                ),
                impact=result.impact,
                evidence_refs=(
                    case.order_ref,
                    *_item_refs(case, seller_id),
                    *capture_refs,
                ),
                seller_id=seller_id,
                expected=settlement,
                reported=recorded,
            )
        )

    return findings


def _capture_for(case: OrderCase, seller_id: uuid.UUID) -> CaptureRecordLike | None:
    for capture in case.captures:
        if capture.seller_id == seller_id:
            return capture
    return None


def exceptions_only(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """The findings that count toward the false-positive gate.

    `awaiting_evidence` is a finding and not an exception, so the gate's "zero
    exceptions on the clean baseline" is counted through this rather than
    through `len(findings)`.
    """
    return tuple(finding for finding in findings if finding.is_exception)


__all__ = [
    "CaptureRecordLike",
    "Comparison",
    "DisbursementLike",
    "Finding",
    "OrderCase",
    "PlatformFeeLike",
    "POSITION_CATEGORIES",
    "classify_order",
    "exceptions_only",
]
