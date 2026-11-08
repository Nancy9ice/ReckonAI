"""The simulated marketplace's HTTP endpoints.

specs/roadmap.md Phase 4 and
specs/2026-11-08-block-a-marketplace-connector/requirements.md D1, D2 and D3.

**This is a presentation layer over the marketplace's own database**, not a
fixture server. The seed builder writes the canonical tables; these endpoints
read them back and rename every field outward through `fields.py`. Three
consequences, all wanted:

  - The sim can hold A2's deliberately inconsistent records, because nothing
    here recomputes anything.
  - "The full seeded dataset ingests with zero data loss" becomes a checkable
    round trip - seed, serve, ingest, compare.
  - A real marketplace has its own database, so this is the honest shape.

**Every amount leaves as a JSON string with its currency beside it.** A JSON
number is an IEEE-754 double (specs/tech-stack.md section 2), and
`tests/strict_json.py` refuses to decode one.

**References cross as the marketplace's own references, not UUIDs.** An item
names its order as `ORD-0001` and its seller as `vendor-aurora`, because that
is what a marketplace API returns and because it forces A3 to resolve
relationships rather than carry primary keys across the boundary
(specs/tech-stack.md section 9.5).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import (
    CommissionRule,
    Disbursement,
    Dispute,
    Order,
    OrderItem,
    Payment,
    Refund,
    Seller,
    SellerLedgerEntry,
)
from reckonai.money import Money
from reckonai.sim import data, fields

router = APIRouter(prefix="/marketplace", tags=["simulated marketplace"])


def _session(request: Request) -> Iterator[Session]:
    """A read-only session per request, bound to the app's engine.

    The engine is built in `reckonai.app`'s lifespan, so a `TestClient` that
    does not enter the lifespan will not have one - which is deliberate: these
    endpoints read a database and a test that wants them must say so.
    """
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="no database engine on the application; the lifespan did not run",
        )
    with Session(bind=engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(_session)]


def _tenant_id(session: Session, slug: str = data.TENANT_SLUG) -> uuid.UUID:
    """The simulated marketplace's tenant, by slug.

    404 rather than an empty list when it is absent. An unseeded database is a
    different condition from a marketplace with no orders, and collapsing the
    two would let A3 report a successful ingestion of nothing.
    """
    from reckonai.models import Tenant

    found = session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no simulated marketplace seeded for slug {slug!r} - "
                "run the seed builder first"
            ),
        )
    return found


def _money(value: Money | None) -> dict[str, str] | None:
    """One amount, quantized, as a string with its currency.

    Returns `None` for an absent value rather than a zero. A missing amount is
    not nothing (specs/tech-stack.md section 9.6).
    """
    if value is None:
        return None
    quantized = value.quantize()
    return {"value": str(quantized.amount), "currency": quantized.currency}


def _envelope(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The list shape every endpoint returns.

    `count` is stated rather than left to be measured, so A3's zero-data-loss
    check compares a number the source asserted against rows it wrote, instead
    of comparing a list's length against itself.
    """
    return {"count": len(records), "records": records}


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def _seller_refs(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return {
        row.id: row.external_ref
        for row in session.scalars(
            select(Seller).where(Seller.tenant_id == tenant_id)
        )
    }


def _order_refs(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return {
        row.id: row.external_ref
        for row in session.scalars(select(Order).where(Order.tenant_id == tenant_id))
    }


def _marketplace_only(statement: Any, model: Any) -> Any:
    """Restrict a query to rows whose payload came from the marketplace.

    **The sim is the marketplace, and must not serve PayPal's records as its
    own.** One canonical table per record type holds both sources, told apart
    only by their payload's `source` (Phase 3's D3) - so a query that does not
    filter serves both, and the `refund` table holds two rows per refund.

    This was a real bug: `/marketplace/refunds` returned five records where the
    marketplace knows three, and the connector dutifully ingested PayPal's two
    as marketplace refunds. The count check caught it, which is the argument
    for having a count check that compares against an independent figure rather
    than against itself.

    `releases` is the deliberate exception - see `list_releases`.
    """
    from reckonai.models import RawPayload

    return statement.join(
        RawPayload,
        (RawPayload.id == model.raw_payload_id)
        & (RawPayload.tenant_id == model.tenant_id),
    ).where(RawPayload.source == "marketplace")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
def marketplace_index() -> dict[str, Any]:
    """The sim's own index. **Touches no database, deliberately.**

    It is what the service index at `/` advertises, and
    `tests/test_health.py::test_the_root_route_is_not_a_404` asserts every
    advertised path answers 200. Every other endpoint here 404s against an
    unseeded database - correctly, since an unseeded database is a different
    condition from a marketplace with no orders - so advertising one of those
    would make the root route's own test depend on seed state.
    """
    return {
        "marketplace": data.TENANT_NAME,
        "tenant_slug": data.TENANT_SLUG,
        "resources": [
            f"/sim/marketplace/{name}"
            for name in (
                "sellers",
                "orders",
                "order-items",
                "commission-rules",
                "payments",
                "refunds",
                "ledger-entries",
                "disputes",
                "entitlements",
                "releases",
            )
        ],
    }


@router.get("/sellers")
def list_sellers(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.SELLER_FIELDS
    rows = session.scalars(
        select(Seller).where(Seller.tenant_id == tenant_id).order_by(
            Seller.external_ref
        )
    )
    return _envelope(
        [
            {
                f["external_ref"]: row.external_ref,
                f["name"]: row.name,
                f["paypal_merchant_id"]: row.paypal_merchant_id,
                f["default_currency"]: row.default_currency,
                f["partner_fee_enabled"]: row.partner_fee_enabled,
                f["delay_funds_enabled"]: row.delay_funds_enabled,
            }
            for row in rows
        ]
    )


@router.get("/orders")
def list_orders(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.ORDER_FIELDS
    rows = session.scalars(
        select(Order).where(Order.tenant_id == tenant_id).order_by(Order.external_ref)
    )
    return _envelope([_order_record(row, f) for row in rows])


def _order_record(row: Order, f: Any) -> dict[str, Any]:
    total = row.total.quantize()
    return {
        f["external_ref"]: row.external_ref,
        f["total_amount"]: str(total.amount),
        f["total_currency"]: total.currency,
        f["placed_at"]: row.placed_at.isoformat(),
        f["disbursement_mode"]: row.disbursement_mode,
    }


@router.get("/orders/{order_ref}")
def get_order(order_ref: str, session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    row = session.scalar(
        select(Order).where(
            Order.tenant_id == tenant_id, Order.external_ref == order_ref
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"no order {order_ref!r}")
    return _order_record(row, fields.ORDER_FIELDS)


@router.get("/order-items")
def list_order_items(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.ORDER_ITEM_FIELDS
    sellers = _seller_refs(session, tenant_id)
    orders = _order_refs(session, tenant_id)
    rows = session.scalars(
        select(OrderItem)
        .where(OrderItem.tenant_id == tenant_id)
        .order_by(OrderItem.external_ref)
    )
    records = []
    for row in rows:
        unit = row.unit_price.quantize()
        line = row.line_total.quantize()
        records.append(
            {
                f["external_ref"]: row.external_ref,
                f["order_id"]: orders.get(row.order_id),
                f["seller_id"]: sellers.get(row.seller_id),
                f["category"]: row.category,
                f["quantity"]: row.quantity,
                f["unit_price_amount"]: str(unit.amount),
                f["line_total_amount"]: str(line.amount),
                f["line_total_currency"]: line.currency,
            }
        )
    return _envelope(records)


@router.get("/commission-rules")
def list_commission_rules(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.COMMISSION_RULE_FIELDS
    sellers = _seller_refs(session, tenant_id)
    rows = session.scalars(
        select(CommissionRule).where(CommissionRule.tenant_id == tenant_id)
    )
    return _envelope(
        [
            {
                f["id"]: str(row.id),
                f["seller_id"]: (
                    sellers.get(row.seller_id) if row.seller_id is not None else None
                ),
                f["category"]: row.category,
                # A rate crosses as a string for the same reason an amount
                # does: `0.08` is not exactly representable as a double, and a
                # commission rate multiplied by a large order is precisely
                # where that stops being academic.
                f["rate"]: str(row.rate),
                f["valid_from"]: row.valid_from.isoformat(),
                f["valid_to"]: row.valid_to.isoformat() if row.valid_to else None,
            }
            for row in rows
        ]
    )


@router.get("/payments")
def list_payments(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.PAYMENT_FIELDS
    sellers = _seller_refs(session, tenant_id)
    orders = _order_refs(session, tenant_id)
    rows = session.scalars(
        _marketplace_only(select(Payment), Payment)
        .where(Payment.tenant_id == tenant_id)
        .order_by(Payment.external_id)
    )
    records = []
    for row in rows:
        amount = row.amount.quantize()
        records.append(
            {
                f["external_id"]: row.external_id,
                f["order_id"]: orders.get(row.order_id) if row.order_id else None,
                f["seller_id"]: sellers.get(row.seller_id) if row.seller_id else None,
                f["amount_amount"]: str(amount.amount),
                f["amount_currency"]: amount.currency,
                f["status"]: row.status,
                f["disbursement_mode"]: row.disbursement_mode,
                f["occurred_at"]: row.occurred_at.isoformat(),
            }
        )
    return _envelope(records)


@router.get("/refunds")
def list_refunds(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.REFUND_FIELDS
    sellers = _seller_refs(session, tenant_id)
    rows = session.scalars(
        _marketplace_only(select(Refund), Refund)
        .where(Refund.tenant_id == tenant_id)
        .order_by(Refund.external_id)
    )
    records = []
    for row in rows:
        amount = row.amount.quantize()
        records.append(
            {
                f["external_id"]: row.external_id,
                # The marketplace knows which order a refund belongs to; it is
                # PayPal that does not expose the capture link. Derived there,
                # intrinsic here.
                f["order_id"]: _refund_order_ref(session, tenant_id, row),
                f["seller_id"]: sellers.get(row.seller_id) if row.seller_id else None,
                f["amount_amount"]: str(amount.amount),
                f["amount_currency"]: amount.currency,
                f["status"]: row.status,
                f["occurred_at"]: row.occurred_at.isoformat(),
            }
        )
    return _envelope(records)


def _refund_order_ref(
    session: Session, tenant_id: uuid.UUID, row: Refund
) -> str | None:
    """The order a refund belongs to, walked through capture and payment.

    **`refund` carries no `order_id` column**, so this has to be derived. The
    chain is `refund.capture_id -> capture.payment_id -> payment.order_id`,
    which is the only route that does not depend on the marketplace's ledger.

    An earlier version looked the order up through the refund's *ledger entry*,
    which was circular in exactly the case that matters: fault F1 is a refund
    with no ledger entry, so the sim would have served `order_ref: null` for it
    and the endpoint's output would have varied with the fault it is meant to
    expose. A reconciler would then be told the refund belonged to no order,
    which is a different and more confusing claim than the one F1 makes.
    """
    from reckonai.models import Capture

    if row.capture_id is None:
        return None

    payment_id = session.scalar(
        select(Capture.payment_id).where(
            Capture.tenant_id == tenant_id, Capture.id == row.capture_id
        )
    )
    if payment_id is None:
        return None

    order_id = session.scalar(
        select(Payment.order_id).where(
            Payment.tenant_id == tenant_id, Payment.id == payment_id
        )
    )
    if order_id is None:
        return None

    return session.scalar(
        select(Order.external_ref).where(
            Order.tenant_id == tenant_id, Order.id == order_id
        )
    )


@router.get("/ledger-entries")
def list_ledger_entries(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.LEDGER_ENTRY_FIELDS
    sellers = _seller_refs(session, tenant_id)
    orders = _order_refs(session, tenant_id)
    rows = session.scalars(
        _marketplace_only(select(SellerLedgerEntry), SellerLedgerEntry)
        .where(SellerLedgerEntry.tenant_id == tenant_id)
        .order_by(SellerLedgerEntry.external_ref)
    )
    records = []
    for row in rows:
        amount = row.amount.quantize()
        records.append(
            {
                f["external_ref"]: row.external_ref,
                f["seller_id"]: sellers.get(row.seller_id),
                f["order_id"]: orders.get(row.order_id) if row.order_id else None,
                f["entry_category"]: row.entry_category,
                f["transaction_type"]: row.transaction_type,
                # **Unsigned, with the direction in its own key.** The sign is
                # applied in exactly one place in this system, and it is not
                # here (`reckonai.engine.ledger.signed`).
                f["amount_amount"]: str(amount.amount),
                f["amount_currency"]: amount.currency,
                f["occurred_at"]: row.occurred_at.isoformat(),
            }
        )
    return _envelope(records)


@router.get("/disputes")
def list_disputes(session: SessionDep) -> dict[str, Any]:
    tenant_id = _tenant_id(session)
    f = fields.DISPUTE_FIELDS
    sellers = _seller_refs(session, tenant_id)
    rows = session.scalars(
        _marketplace_only(select(Dispute), Dispute)
        .where(Dispute.tenant_id == tenant_id)
        .order_by(Dispute.external_id)
    )
    records = []
    for row in rows:
        amount = _money(row.amount)
        records.append(
            {
                f["external_id"]: row.external_id,
                f["order_id"]: None,
                f["seller_id"]: sellers.get(row.seller_id) if row.seller_id else None,
                f["amount_amount"]: amount["value"] if amount else None,
                f["amount_currency"]: amount["currency"] if amount else None,
                f["status"]: row.status,
                f["reason"]: row.reason,
                f["occurred_at"]: (
                    row.occurred_at.isoformat() if row.occurred_at else None
                ),
            }
        )
    return _envelope(records)


@router.get("/entitlements")
def list_entitlements(session: SessionDep) -> dict[str, Any]:
    """What the marketplace believes each seller is owed, per order.

    **The marketplace's own claim, read from its ledger** - not a figure this
    service computed. Block B computes its own entitlement from the items and
    the commission rules, and Block C compares the two; a view that recomputed
    here would make that comparison circular and hide exactly the fault F2
    seeds.

    Phase 4 lists this endpoint under the multiparty amendment: the marketplace
    states an expected seller entitlement, and PayPal's platform fee is
    independent evidence against it.
    """
    tenant_id = _tenant_id(session)
    f = fields.ENTITLEMENT_FIELDS
    sellers = _seller_refs(session, tenant_id)
    orders = _order_refs(session, tenant_id)

    # Built from the ledger the marketplace wrote, grouped by (order, seller).
    owed: dict[tuple[str, str], dict[str, Money]] = {}
    for row in session.scalars(
        _marketplace_only(select(SellerLedgerEntry), SellerLedgerEntry).where(
            SellerLedgerEntry.tenant_id == tenant_id
        )
    ):
        order_ref = orders.get(row.order_id) if row.order_id else None
        seller_ref = sellers.get(row.seller_id)
        if order_ref is None or seller_ref is None:
            continue
        bucket = owed.setdefault(
            (order_ref, seller_ref),
            {
                "owed": Money("0", row.amount.currency),
                "retained": Money("0", row.amount.currency),
            },
        )
        if row.entry_category in {"commission", "commission_reversal"}:
            sign = 1 if row.entry_category == "commission" else -1
            bucket["retained"] = bucket["retained"] + row.amount * sign
            bucket["owed"] = bucket["owed"] - row.amount * sign
        else:
            direction = 1 if row.transaction_type == "credit" else -1
            bucket["owed"] = bucket["owed"] + row.amount * direction

    records = []
    for (order_ref, seller_ref), amounts in sorted(owed.items()):
        owed_q = amounts["owed"].quantize()
        records.append(
            {
                f["order_ref"]: order_ref,
                f["seller_ref"]: seller_ref,
                f["owed_amount"]: str(owed_q.amount),
                f["commission_amount"]: str(amounts["retained"].quantize().amount),
                f["currency"]: owed_q.currency,
            }
        )
    return _envelope(records)


@router.get("/releases")
def list_releases(session: SessionDep) -> dict[str, Any]:
    """Release records for `DELAYED` disbursement.

    Replaces the marketplace-run payout batch the first-party model assumed:
    under multiparty PayPal pays the seller, so what the marketplace holds is a
    record of release, not of payment (specs/roadmap.md Phase 3's amendment).

    `released_on` is `NULL` for held funds and stays that way. PayPal's release
    record carries no usable timestamp, and presenting an ingestion time as a
    provider one is the substitution specs/mission.md P3 forbids.
    """
    tenant_id = _tenant_id(session)
    f = fields.DISBURSEMENT_FIELDS
    sellers = _seller_refs(session, tenant_id)
    rows = session.scalars(
        select(Disbursement)
        .where(Disbursement.tenant_id == tenant_id)
        .order_by(Disbursement.external_id)
    )
    records = []
    for row in rows:
        amount = row.amount.quantize()
        records.append(
            {
                f["external_id"]: row.external_id,
                f["seller_id"]: sellers.get(row.seller_id) if row.seller_id else None,
                f["amount_amount"]: str(amount.amount),
                f["amount_currency"]: amount.currency,
                f["state"]: row.state,
                f["released_at"]: (
                    row.released_at.isoformat() if row.released_at else None
                ),
            }
        )
    return _envelope(records)
