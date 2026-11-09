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
from reckonai.recon import (
    Finding,
    OrderCase,
    OrderMatch,
    capture_reference_from_href,
    classify_order,
    exceptions_only,
    match_orders,
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

    cases: list[OrderCase] = []
    for match in matches.matched:
        order = orders_by_id[match.order_id]
        order_captures = tuple(captures.get(match.payment_id, ()))
        capture_ids = {capture.id for capture in order_captures}

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
                disbursements=tuple(
                    release for release in releases if release.capture_id in capture_ids
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
    cases, _, _ = load_order_cases(session, tenant_id)

    findings: list[Finding] = []
    for case in cases:
        findings.extend(classify_order(case))
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
