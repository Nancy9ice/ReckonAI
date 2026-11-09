"""The reconciliation run: query both sides, classify, return findings.

The seam between the database and the pure packages. `reckonai.recon` performs
no I/O and takes `typing.Protocol` inputs, so something has to read the rows and
assemble them, and this is it - the only module in the money path that holds
both a `Session` and an `OrderCase`.

Block D owns the date-windowed run and the `jobs` table that drives it. This is
what that run will call, and what Block C's false-positive gate runs through,
so it is deliberately a plain function over a session rather than a job
runner: the scheduling, the windowing and the exception *table* are Block D's
(`specs/2026-11-09-matching-classification/requirements.md` D8).

**Every query filters by `tenant_id`**, which the conventions require and which
the composite foreign keys already make unavoidable for a cross-tenant link.

The one piece of real work here beyond fetching is the order-to-payment link.
PayPal's `purchase_units[].reference_id` is the identifier that ties its record
to a marketplace order, and the replay ingester does not land it in any column -
`Payment.order_id` is NULL on every provider-origin row by design, because the
tie is a match rather than a fact PayPal stated. So the retained payload is read
and `reckonai.recon.matcher` parses the reference out of it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import (
    Capture,
    CommissionRule,
    Disbursement,
    Order,
    OrderItem,
    Payment,
    PlatformFee,
    RawPayload,
    Refund,
    SellerLedgerEntry,
)
from reckonai.money import Money
from reckonai.recon import (
    ExceptionCode,
    Finding,
    OrderCase,
    OrderMatch,
    capture_reference_from_href,
    classify_order,
    exceptions_only,
    match_orders,
    order_ref_for_reference,
    order_references,
)

#: Payload sources, as `reckonai.models.payloads.PAYLOAD_SOURCES` spells them.
PAYPAL = "paypal"
MARKETPLACE = "marketplace"


def load_order_cases(
    session: Session, tenant_id: uuid.UUID
) -> tuple[tuple[OrderCase, ...], tuple[str, ...], tuple[str, ...]]:
    """Assemble one `OrderCase` per matched order.

    Returns the cases plus the two unmatched sides, which are reported rather
    than judged: a marketplace order with no PayPal record and a PayPal record
    with no marketplace order mean opposite things, and neither is an error on
    its own (specs/mission.md P4).
    """
    orders = tuple(
        session.scalars(
            select(Order)
            .where(Order.tenant_id == tenant_id)
            .order_by(Order.external_ref)
        )
    )
    payloads = _payloads(session, tenant_id)

    # **PayPal-origin payments only.** Both sources write rows to `payment`,
    # told apart by their payload's source. A marketplace payment carries no
    # purchase units, so including them would put all 18 into `provider_only`
    # and make the run look like it had matched nothing.
    payments = tuple(
        payment
        for payment in session.scalars(
            select(Payment).where(Payment.tenant_id == tenant_id)
        )
        if _source_of(payloads, payment.raw_payload_id) == PAYPAL
    )

    references = {
        payment.id: order_references(payloads[payment.raw_payload_id].body)
        for payment in payments
        if payment.raw_payload_id in payloads
    }

    matches = match_orders(orders, payments, references)
    orders_by_id = {order.id: order for order in orders}

    items = _group(
        session.scalars(select(OrderItem).where(OrderItem.tenant_id == tenant_id)),
        lambda row: row.order_id,
    )
    ledger = _group(
        session.scalars(
            select(SellerLedgerEntry).where(SellerLedgerEntry.tenant_id == tenant_id)
        ),
        lambda row: row.order_id,
    )
    captures = _group(
        session.scalars(select(Capture).where(Capture.tenant_id == tenant_id)),
        lambda row: row.payment_id,
    )

    rules = tuple(
        session.scalars(
            select(CommissionRule).where(CommissionRule.tenant_id == tenant_id)
        )
    )

    all_refunds = tuple(
        session.scalars(select(Refund).where(Refund.tenant_id == tenant_id))
    )
    refund_orders = _refund_orders(all_refunds, payloads, matches.matched, captures)
    fees = tuple(
        session.scalars(select(PlatformFee).where(PlatformFee.tenant_id == tenant_id))
    )
    releases = tuple(
        session.scalars(select(Disbursement).where(Disbursement.tenant_id == tenant_id))
    )

    # Which order each capture belongs to, per purchase unit. A payment may
    # legitimately carry units from two marketplace orders, and handing each
    # order *all* of the payment's captures would attribute the other order's
    # money - and its platform fees - to both.
    capture_orders = _capture_orders(payloads, payments, matches.matched, captures)
    release_captures = _release_captures(payloads)

    cases: list[OrderCase] = []
    for match in matches.matched:
        order = orders_by_id[match.order_id]
        order_captures = tuple(
            capture
            for capture in captures.get(match.payment_id, ())
            if capture_orders.get(capture.id, match.order_ref) == match.order_ref
        )
        capture_ids = {capture.id for capture in order_captures}
        capture_refs = {capture.external_id for capture in order_captures}

        # `refund.capture_id` is NULL on every row - the ingester records the
        # HATEOAS href and leaves the link unmade, because resolving it is a
        # matching claim. `_refund_orders` makes that claim.
        refunds = tuple(
            refund
            for refund in all_refunds
            if refund_orders.get(refund.id) == match.order_ref
        )
        refund_ids = {refund.id for refund in refunds}

        cases.append(
            OrderCase(
                order_ref=match.order_ref,
                order=order,
                items=tuple(items.get(order.id, ())),
                rules=rules,
                captures=order_captures,
                platform_fees=tuple(
                    fee
                    for fee in fees
                    if fee.capture_id in capture_ids or fee.refund_id in refund_ids
                ),
                # `Disbursement.capture_id` is NULL on every row, although
                # the release payload states the capture outright in
                # `reference_id` with `reference_type: TRANSACTION_ID` - see
                # `reckonai.models.disbursements`. Resolving it here is what
                # makes a release citable as evidence and lets T3 see a
                # release still in flight.
                disbursements=tuple(
                    release
                    for release in releases
                    if release_captures.get(release.raw_payload_id) in capture_refs
                ),
                marketplace_refunds=tuple(
                    refund
                    for refund in refunds
                    if _source_of(payloads, refund.raw_payload_id) == MARKETPLACE
                ),
                provider_refunds=tuple(
                    refund
                    for refund in refunds
                    if _source_of(payloads, refund.raw_payload_id) == PAYPAL
                ),
                ledger_entries=tuple(ledger.get(order.id, ())),
            )
        )

    return tuple(cases), matches.marketplace_only, matches.provider_only


def run(session: Session, tenant_id: uuid.UUID) -> tuple[Finding, ...]:
    """Every finding for a tenant, in order-reference order.

    Deterministic: the same rows produce the same findings in the same
    sequence, which is what makes a historical run re-derivable
    (specs/mission.md P7).
    """
    cases, marketplace_only, provider_only = load_order_cases(session, tenant_id)

    findings: list[Finding] = []
    for case in cases:
        findings.extend(classify_order(case))

    # **Neither unmatched side is dropped.** `load_order_cases` separates them
    # so they can be reported, and returning only the classified cases made
    # them vanish from the product entirely - an order PayPal has no record
    # of would simply not appear. Both are `awaiting_evidence` rather than
    # discrepancies: unmatched is not the same as wrong (specs/mission.md P4),
    # the reporting APIs run about a day behind, and R9's next scheduled run
    # is the retry.
    for order_ref in marketplace_only:
        findings.append(
            Finding(
                code=ExceptionCode.AWAITING_EVIDENCE,
                order_ref=order_ref,
                detail=(
                    "The marketplace recorded this order and no PayPal record "
                    "has been matched to it yet, so no expected position can "
                    "be compared. The next scheduled run re-evaluates it."
                ),
                impact=Money.zero(_currency_of(session, tenant_id, order_ref)),
                evidence_refs=(order_ref,),
                confirmed=False,
            )
        )

    for payment_ref in provider_only:
        findings.append(
            Finding(
                code=ExceptionCode.AWAITING_EVIDENCE,
                order_ref=payment_ref,
                detail=(
                    f"PayPal payment {payment_ref} could not be matched to any "
                    "marketplace order, so there is nothing to reconcile it "
                    "against. The next scheduled run re-evaluates it."
                ),
                impact=Money.zero("USD"),
                evidence_refs=(payment_ref,),
                confirmed=False,
            )
        )

    return tuple(findings)


def exceptions(session: Session, tenant_id: uuid.UUID) -> tuple[Finding, ...]:
    """Only the findings that are genuine discrepancies.

    `awaiting_evidence` is a finding and not an exception, so the
    false-positive gate counts through this rather than through `run`.
    """
    return exceptions_only(run(session, tenant_id))


def _group(rows, key):  # type: ignore[no-untyped-def]
    """Rows bucketed by `key`, skipping those whose key is None."""
    grouped = defaultdict(list)
    for row in rows:
        bucket = key(row)
        if bucket is not None:
            grouped[bucket].append(row)
    return grouped


def _currency_of(session: Session, tenant_id: uuid.UUID, order_ref: str) -> str:
    """The currency of an order we could not reconcile.

    Read from the order rather than defaulted, so a zero impact on an
    unmatched order still carries the right currency - an amount without its
    own currency is the mistake specs/tech-stack.md section 9.3 names as the
    likeliest place for a money figure to be quietly wrong.
    """
    order = session.scalar(
        select(Order).where(
            Order.tenant_id == tenant_id, Order.external_ref == order_ref
        )
    )
    return order.total.currency if order is not None else "USD"


def _capture_orders(
    payloads: dict[uuid.UUID, RawPayload],
    payments: tuple[Payment, ...],
    matched: tuple[OrderMatch, ...],
    captures_by_payment: dict[uuid.UUID, list[Capture]],
) -> dict[uuid.UUID, str]:
    """Which order each capture belongs to, from its own purchase unit.

    The payment-level match says which orders a payment covers; this says
    which of them each individual capture is for, by reading the capture id
    out of the purchase unit that carries it alongside that unit's
    `reference_id`.
    """
    known = {match.order_ref for match in matched}
    resolved: dict[uuid.UUID, str] = {}

    for payment in payments:
        payload = payloads.get(payment.raw_payload_id)
        if payload is None:
            continue

        ref_for_external: dict[str, str] = {}
        for unit in (payload.body or {}).get("purchase_units", []):
            if not isinstance(unit, dict):
                continue
            order_ref = order_ref_for_reference(
                str(unit.get("reference_id") or ""), frozenset(known)
            )
            if order_ref is None:
                continue
            for capture in unit.get("payments", {}).get("captures", []):
                if isinstance(capture, dict) and capture.get("id"):
                    ref_for_external[str(capture["id"])] = order_ref

        for capture in captures_by_payment.get(payment.id, ()):
            order_ref = ref_for_external.get(capture.external_id)
            if order_ref is not None:
                resolved[capture.id] = order_ref

    return resolved


def _release_captures(
    payloads: dict[uuid.UUID, RawPayload],
) -> dict[uuid.UUID, str]:
    """The capture each release discharged, keyed by the release's payload id.

    Keyed by payload rather than by release row, because the resolution is
    read out of the payload and a `Disbursement` is reached from it through
    `raw_payload_id`. Keying it by the release's own id looked right and
    silently matched nothing.

    `reckonai.models.disbursements` documents `reference_id` with
    `reference_type: TRANSACTION_ID` as "provider-supplied, and the capture id
    itself" - no parsing and no derivation flag. The ingester nonetheless
    leaves `capture_id` NULL, so the resolution happens here with the other
    cross-source claims.
    """
    resolved: dict[uuid.UUID, str] = {}
    for payload in payloads.values():
        if payload.source_record_type != "disbursement":
            continue
        body = payload.body or {}
        if body.get("reference_type") != "TRANSACTION_ID":
            continue
        reference = body.get("reference_id")
        if isinstance(reference, str) and reference:
            resolved[payload.id] = reference
    return resolved


def _source_of(
    payloads: dict[uuid.UUID, RawPayload], payload_id: uuid.UUID | None
) -> str | None:
    """Which source a record came from, through its retained payload."""
    if payload_id is None:
        return None
    payload = payloads.get(payload_id)
    return payload.source if payload is not None else None


def _refund_orders(
    refunds: tuple[Refund, ...],
    payloads: dict[uuid.UUID, RawPayload],
    matched: tuple[OrderMatch, ...],
    captures_by_payment: dict[uuid.UUID, list[Capture]],
) -> dict[uuid.UUID, str]:
    """Which order each refund belongs to, by source, as a claim.

    Neither side states it as a field, so each is resolved from the evidence
    that exists, and the two are not equally strong:

      - a **marketplace** refund's payload states its `order_ref` outright
      - a **PayPal** refund carries only `links[rel=up]`, pointing at the
        capture. The href is parsed, the capture matched by its external id,
        and the capture's payment matched to the order. Three hops, every one
        of them a claim, which is why a finding resting on it is marked
        `EvidenceBasis.DERIVED_LINKAGE`.

    A refund that resolves to nothing is simply absent from the result and so
    belongs to no order's reconciliation. That is the honest answer: inventing
    an order for it would attach a real refund to the wrong seller's position.
    """
    order_for_capture: dict[str, str] = {}
    for match in matched:
        for capture in captures_by_payment.get(match.payment_id, ()):
            order_for_capture[capture.external_id] = match.order_ref

    known_orders = {match.order_ref for match in matched}
    resolved: dict[uuid.UUID, str] = {}

    for refund in refunds:
        source = _source_of(payloads, refund.raw_payload_id)

        if source == MARKETPLACE:
            payload = payloads.get(refund.raw_payload_id)
            stated = (payload.body or {}).get("order_ref") if payload else None
            if isinstance(stated, str) and stated in known_orders:
                resolved[refund.id] = stated
            continue

        reference = capture_reference_from_href(refund.capture_linkage_href)
        if reference is not None and reference in order_for_capture:
            resolved[refund.id] = order_for_capture[reference]

    return resolved


def _payloads(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, RawPayload]:
    """Every retained payload for the tenant, by id.

    Fetched in one query rather than per record, because two separate things
    are read off it: each payload's `source`, and the purchase-unit references
    in an order payload's body.

    `source` is the only way to tell a marketplace refund from a PayPal one:
    both are rows in the same `refund` table, told apart by their payload
    (`reckonai.models.events`). Resolved here, in SQL, rather than passed into
    the pure package as a field it would have to trust.
    """
    return {
        row.id: row
        for row in session.scalars(
            select(RawPayload).where(RawPayload.tenant_id == tenant_id)
        )
    }
