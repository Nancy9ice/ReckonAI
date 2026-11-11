"""The simulated marketplace's write side.

`specs/2026-11-11-live-ingestion/requirements.md` F10 to F15.

For a live PayPal transaction to reconcile, the marketplace has to record it
too; otherwise the matcher sees a PayPal record with no counterpart. This is
the marketplace placing an order and refunding it, creating the real sandbox
transaction and its own books together.

**It reuses the seed's builders** (`seed._build_order`, `_build_refund`,
`_entry`), so a live order is shaped exactly as a seeded one is, faults
included. A fault is never a side effect: it is a field on the request, and
what it plants is the same lever the seed's `OrderSpec` already has.

**Everything it writes is labelled simulated at the record level.** Each
marketplace payload it creates carries `endpoint = SIM_ENDPOINT`, where the
seed's carry `NULL`, and none of it is ever `source = "paypal"`: a simulated
value cannot enter a result as provider evidence (D4, `multiparty-direction.md`
section 7).

**It is off unless enabled and needs a shared secret** (F15). The read
endpoints are public by design; a public write endpoint on a deployed demo
would let anyone plant orders and ledger entries.

**It is test-data generation, not ReckonAI acting.** The PayPal refund it makes
is a sandbox call that moves no real money, made by the simulated marketplace
and never by the product's own code paths (P6).
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.config import get_settings
from reckonai.models import Order, Seller, SellerLedgerEntry, Tenant
from reckonai.money import Money
from reckonai.paypal import capture, live, replay
from reckonai.sim import data, seed

#: What every payload this module writes carries in `endpoint`.
SIM_ENDPOINT = "SIMULATED POST /sim/marketplace/live"

router = APIRouter(
    prefix="/marketplace/live", tags=["simulated marketplace write side"]
)


class WriteError(RuntimeError):
    """A request the write side cannot honestly carry out."""


class Gateway(Protocol):
    """The PayPal calls the write side makes. Replaced in tests."""

    def create_order(
        self,
        *,
        order_ref: str,
        merchant_id: str,
        gross: str,
        fee: str,
        currency: str,
        disbursement_mode: str,
    ) -> capture.LiveOrder: ...

    def refund(
        self, *, capture_id: str, merchant_id: str, currency: str, amount: str
    ) -> dict[str, Any]: ...

    def read_order(self, order_id: str, merchant_id: str) -> dict[str, Any]: ...


class SandboxGateway:
    """The real sandbox. Every call refuses a non-sandbox host."""

    def create_order(
        self,
        *,
        order_ref: str,
        merchant_id: str,
        gross: str,
        fee: str,
        currency: str,
        disbursement_mode: str,
    ) -> capture.LiveOrder:
        return capture.capture_live_order(
            payees=[(merchant_id, gross, fee)],
            currency=currency,
            order_ref=order_ref,
            disbursement_mode=disbursement_mode,
        )

    def refund(
        self, *, capture_id: str, merchant_id: str, currency: str, amount: str
    ) -> dict[str, Any]:
        return capture.refund_capture(
            capture_id=capture_id,
            merchant_id=merchant_id,
            currency=currency,
            amount=amount,
        )

    def read_order(self, order_id: str, merchant_id: str) -> dict[str, Any]:
        order = live.LiveClient(live.credentials()).order_as(order_id, merchant_id)
        if order is None:
            raise WriteError(f"PayPal refused the read of order {order_id}")
        return order


# --- requests --------------------------------------------------------------


class PlaceOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_ref: str = Field(min_length=1, max_length=64)
    seller_ref: str
    #: Strings, never numbers: a JSON number is a double.
    gross: str
    #: The platform fee PayPal takes. For a clean order this is the commission
    #: rule's figure (10% of gross); the caller states it so the sim does no
    #: rounding of its own.
    fee: str
    currency: str = "USD"
    #: `DELAYED` holds the seller's funds at PayPal until a release, which is a
    #: healthy state and must not be flagged.
    disbursement_mode: Literal["INSTANT", "DELAYED"] = "INSTANT"
    #: Plants F2: the commission the marketplace books where it differs from
    #: PayPal's fee.
    ledger_commission: str | None = None
    #: Plants F4: the sale credited twice under distinct references.
    duplicate_credit: bool = False
    #: Ingest the PayPal side now, as the webhook job would. For a deployment
    #: with no webhook, and for tests.
    ingest_now: bool = False


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_ref: str
    paypal_capture_id: str
    amount: str
    #: Plants F1 when false: the refund exists and the ledger never hears of it.
    reflect_in_ledger: bool = True
    #: Book the refund in the marketplace and its ledger but make no PayPal
    #: refund yet: PayPal's record is late, not absent. The later PayPal refund
    #: is a second, separate call without this flag.
    marketplace_only: bool = False
    ingest_now: bool = False


class LedgerEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_ref: str
    order_ref: str | None = None
    category: Literal["sale", "commission", "adjustment"]
    direction: Literal["credit", "debit"]
    amount: str
    currency: str = "USD"


# --- the work --------------------------------------------------------------


@contextmanager
def _labelled() -> Iterator[None]:
    token = seed.ENDPOINT_MARKER.set(SIM_ENDPOINT)
    try:
        yield
    finally:
        seed.ENDPOINT_MARKER.reset(token)


def _result(session: Session, tenant_id: uuid.UUID) -> seed.SeedResult:
    sellers = session.scalars(select(Seller).where(Seller.tenant_id == tenant_id))
    return seed.SeedResult(
        tenant_id=tenant_id, seller_ids={s.external_ref: s.id for s in sellers}
    )


def _seller(session: Session, tenant_id: uuid.UUID, ref: str) -> Seller:
    seller = session.scalars(
        select(Seller).where(Seller.tenant_id == tenant_id, Seller.external_ref == ref)
    ).first()
    if seller is None:
        raise WriteError(f"no seller {ref!r}")
    if not seller.paypal_merchant_id:
        raise WriteError(f"seller {ref!r} has no PayPal merchant id")
    return seller


def _dec(value: str, what: str) -> Decimal:
    try:
        amount = Decimal(value)
    except ArithmeticError:
        raise WriteError(f"{what} is not a decimal amount: {value!r}") from None
    # NaN and Infinity both construct, and NaN then raises on comparison, which
    # would surface as a 500 rather than a refusal.
    if not amount.is_finite() or amount <= 0:
        raise WriteError(f"{what} must be a positive, finite amount")
    return amount


def place_order(
    session: Session,
    tenant_id: uuid.UUID,
    req: PlaceOrderIn,
    gateway: Gateway,
) -> dict[str, Any]:
    """Create the sandbox order and the marketplace's books for it.

    PayPal first: if the sandbox call fails nothing is written here.
    """
    seller = _seller(session, tenant_id, req.seller_ref)
    gross = _dec(req.gross, "gross")
    fee = _dec(req.fee, "fee")
    if fee >= gross:
        raise WriteError("fee must be less than gross")
    booked = _dec(req.ledger_commission, "ledger_commission") if (
        req.ledger_commission
    ) else fee
    if session.scalars(
        select(Order.id).where(
            Order.tenant_id == tenant_id, Order.external_ref == req.order_ref
        )
    ).first():
        raise WriteError(f"order {req.order_ref!r} already exists")

    paypal = gateway.create_order(
        order_ref=req.order_ref,
        merchant_id=str(seller.paypal_merchant_id),
        gross=str(gross),
        fee=str(fee),
        currency=req.currency,
        disbursement_mode=req.disbursement_mode,
    )

    now = datetime.now(UTC)
    spec = data.OrderSpec(
        ref=req.order_ref,
        currency=req.currency,
        total=str(gross),
        placed_at=now,
        items=(
            data.ItemSpec(
                ref=f"ITEM-{req.order_ref}-A",
                seller_ref=req.seller_ref,
                quantity=1,
                unit_price=str(gross),
                line_total=str(gross),
            ),
        ),
        expected_commission=str(booked),
        expected_entitlements={req.seller_ref: str(gross - booked)},
        disbursement_mode=req.disbursement_mode,
        duplicate_ledger_entry=req.duplicate_credit,
    )
    with _labelled():
        seed._build_order(session, _result(session, tenant_id), spec)

    if req.ingest_now:
        replay.ingest_document(session, paypal.document, tenant_id=tenant_id)

    return {
        "order_ref": req.order_ref,
        "paypal_order_id": paypal.order_id,
        "paypal_capture_ids": list(paypal.captures),
        "simulated": True,
    }


def refund_order(
    session: Session,
    tenant_id: uuid.UUID,
    req: RefundIn,
    gateway: Gateway,
) -> dict[str, Any]:
    """Refund part of an order at PayPal and in the marketplace's books."""
    order = session.scalars(
        select(Order).where(
            Order.tenant_id == tenant_id, Order.external_ref == req.order_ref
        )
    ).first()
    if order is None:
        raise WriteError(f"no order {req.order_ref!r}")
    amount = _dec(req.amount, "amount")
    if req.marketplace_only and (req.ingest_now or not req.reflect_in_ledger):
        raise WriteError(
            "marketplace_only books the refund and its ledger entries and makes "
            "no PayPal refund, so there is nothing to ingest and no fault to plant"
        )
    gross = order.total.amount
    if amount > gross:
        raise WriteError("a refund cannot exceed the order total")

    commission = sum(
        (
            entry.amount.amount
            for entry in session.scalars(
                select(SellerLedgerEntry).where(
                    SellerLedgerEntry.tenant_id == tenant_id,
                    SellerLedgerEntry.order_id == order.id,
                    SellerLedgerEntry.entry_category == "commission",
                )
            )
        ),
        Decimal("0"),
    )
    # PayPal reverses the platform fee pro rata on a refund (confirmed against
    # real captures: 2.00 on 20 of 70). The marketplace books the same.
    reversal = (commission * amount / gross).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )

    seller_ref = session.scalars(
        select(Seller.external_ref).where(
            Seller.tenant_id == tenant_id,
            Seller.id
            == select(SellerLedgerEntry.seller_id)
            .where(
                SellerLedgerEntry.tenant_id == tenant_id,
                SellerLedgerEntry.order_id == order.id,
            )
            .limit(1)
            .scalar_subquery(),
        )
    ).first()
    if seller_ref is None:
        raise WriteError(f"order {req.order_ref!r} has no ledger entry for a seller")
    seller = _seller(session, tenant_id, seller_ref)

    # Everything that can still refuse is checked before PayPal is touched: a
    # refund made at PayPal and then abandoned here would be made twice on retry.
    paypal_order_id = (
        _paypal_order_id(session, tenant_id, req.paypal_capture_id)
        if req.ingest_now
        else None
    )

    refund: dict[str, Any] = {}
    if not req.marketplace_only:
        refund = gateway.refund(
            capture_id=req.paypal_capture_id,
            merchant_id=str(seller.paypal_merchant_id),
            currency=order.total.currency,
            amount=str(amount),
        )

    now = datetime.now(UTC)
    n = uuid.uuid4().hex[:6]
    spec = data.OrderSpec(
        ref=req.order_ref,
        currency=order.total.currency,
        total=str(gross),
        placed_at=now,
        items=(),
        expected_commission="0",
        expected_entitlements={},
    )
    refund_spec = data.RefundSpec(
        ref=f"RF-{req.order_ref}-{n}",
        seller_ref=seller_ref,
        amount=str(amount),
        expected_commission_reversal=str(reversal),
        reflected_in_ledger=req.reflect_in_ledger,
    )
    with _labelled():
        seed._build_refund(
            session, _result(session, tenant_id), spec, refund_spec, order.id
        )

    # The refund now exists at PayPal and in the marketplace's books. Reading it
    # back is a convenience, so its failure is reported and must not roll the
    # booking back (a savepoint keeps a half-written ingestion out).
    ingest_error: str | None = None
    if paypal_order_id is not None:
        try:
            with session.begin_nested():
                fetched = gateway.read_order(
                    paypal_order_id, str(seller.paypal_merchant_id)
                )
                document = live.build_document(session, tenant_id, fetched)
                replay.ingest_document(session, document, tenant_id=tenant_id)
        except (WriteError, live.LiveFetchError, replay.ReplayError) as exc:
            ingest_error = str(exc)

    return {
        "order_ref": req.order_ref,
        "paypal_refund_id": refund.get("id"),
        "marketplace_only": req.marketplace_only,
        "marketplace_refund": refund_spec.ref,
        "reflected_in_ledger": req.reflect_in_ledger,
        "ingested": req.ingest_now and ingest_error is None,
        "ingest_error": ingest_error,
        "simulated": True,
    }


def _paypal_order_id(
    session: Session, tenant_id: uuid.UUID, capture_id: str
) -> str:
    from reckonai.models import Capture, Payment

    found = session.scalars(
        select(Payment.external_id)
        .join(Capture, Capture.payment_id == Payment.id)
        .where(Capture.tenant_id == tenant_id, Capture.external_id == capture_id)
    ).first()
    if found is None:
        raise WriteError(
            f"capture {capture_id!r} has not been ingested; place the order with "
            "ingest_now, or wait for its webhook"
        )
    return found


def record_ledger_entry(
    session: Session, tenant_id: uuid.UUID, req: LedgerEntryIn
) -> dict[str, Any]:
    """Write one seller ledger entry (F12)."""
    seller = _seller(session, tenant_id, req.seller_ref)
    order_id: uuid.UUID | None = None
    if req.order_ref:
        order = session.scalars(
            select(Order).where(
                Order.tenant_id == tenant_id, Order.external_ref == req.order_ref
            )
        ).first()
        if order is None:
            raise WriteError(f"no order {req.order_ref!r}")
        order_id = order.id
    amount = Money(_dec(req.amount, "amount"), req.currency)
    spec = data.OrderSpec(
        ref=req.order_ref or "NOORDER",
        currency=req.currency,
        total="0",
        placed_at=datetime.now(UTC),
        items=(),
        expected_commission="0",
        expected_entitlements={},
    )
    suffix = f"adhoc-{uuid.uuid4().hex[:8]}"
    with _labelled():
        seed._entry(
            session,
            _result(session, tenant_id),
            spec,
            order_id,
            seller.id,
            req.seller_ref,
            req.category,
            req.direction,
            amount,
            req.currency,
            suffix,
        )
    ref = f"LED-{spec.ref}-{req.seller_ref}-{suffix}"
    return {"ledger_ref": ref, "simulated": True}


# --- the guarded HTTP surface ----------------------------------------------


def require_write_access(
    x_sim_secret: Annotated[str | None, Header()] = None,
) -> None:
    """F15. Hidden (404) unless enabled; a shared secret once it is."""
    settings = get_settings()
    if not settings.sim_write_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    secret = settings.sim_write_secret
    if secret is None:
        raise HTTPException(
            status_code=503, detail="the write side is enabled with no secret set"
        )
    if x_sim_secret is None or not hmac.compare_digest(
        x_sim_secret.encode(), secret.get_secret_value().encode()
    ):
        raise HTTPException(status_code=403, detail="forbidden")


def get_gateway() -> Gateway:
    return SandboxGateway()


def _session(request: Request) -> Iterator[Session]:
    with Session(bind=request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(_session)]
GatewayDep = Annotated[Gateway, Depends(get_gateway)]
GuardDep = Annotated[None, Depends(require_write_access)]


def _tenant(session: Session) -> uuid.UUID:
    found = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    if found is None:
        raise HTTPException(status_code=404, detail="no simulated marketplace seeded")
    return found


@router.post("/orders", status_code=201)
def post_order(
    body: PlaceOrderIn, _guard: GuardDep, session: SessionDep, gateway: GatewayDep
) -> dict[str, Any]:
    try:
        out = place_order(session, _tenant(session), body, gateway)
    except (WriteError, capture.CaptureUnavailable, live.LiveFetchError) as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return out


@router.post("/refunds", status_code=201)
def post_refund(
    body: RefundIn, _guard: GuardDep, session: SessionDep, gateway: GatewayDep
) -> dict[str, Any]:
    try:
        out = refund_order(session, _tenant(session), body, gateway)
    except (WriteError, capture.CaptureUnavailable, live.LiveFetchError) as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return out


@router.post("/ledger-entries", status_code=201)
def post_ledger_entry(
    body: LedgerEntryIn, _guard: GuardDep, session: SessionDep
) -> dict[str, Any]:
    try:
        out = record_ledger_entry(session, _tenant(session), body)
    except WriteError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return out
