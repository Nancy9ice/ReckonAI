"""The judge scenarios: a fixed set of live transactions per judge.

`specs/2026-11-11-judge-scenarios/`. A judge supplies a name; this creates six
real sandbox orders under it, each shaped to one reconciliation outcome, through
the write side in `live.py`. Nothing here takes an amount, a seller or a ref
from the caller.

**Idempotent per name, never cleared.** Every step checks the books before it
acts, so a repeated call creates nothing and a failed one resumes. Records are
append-only, and PayPal's cannot be deleted at all.

**It never waits.** PayPal's side of an order normally arrives by webhook. The
caller polls `status`; `ingest_missing` is the fallback that fetches what has
not arrived. Which path an order took is recorded and reported, never hidden.

Everything it writes is labelled simulated by `live.py`; the markers it adds
here are marketplace-side `raw_payload` rows and move no money.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx2 as httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from reckonai.config import get_settings
from reckonai.models import Order, RawPayload, Seller
from reckonai.paypal import capture, replay
from reckonai.paypal import live as paypal_live
from reckonai.sim import data, seed
from reckonai.sim import live as write_side

#: The seller every scenario uses. A seeded, onboarded sandbox merchant.
SELLER = data.B

NAME = re.compile(r"^[A-Za-z0-9]{1,12}$")

#: Marker record types, all marketplace-side.
SET_MARKER = "judge_set"
ORDER_MARKER = "judge_order"
STEP_MARKER = "judge_step"


class JudgeError(ValueError):
    """The request cannot be honoured (a bad name)."""


class CapReached(RuntimeError):
    """A new set would exceed a cap. An existing name is never refused."""


@dataclass(frozen=True)
class Scenario:
    key: str
    suffix: str
    title: str
    expects: str
    fee: str = "10.00"
    gross: str = "100.00"
    disbursement_mode: str = "INSTANT"
    ledger_commission: str | None = None
    duplicate_credit: bool = False
    #: "marketplace_only" books the refund and not PayPal's (late evidence);
    #: "unbooked" refunds at PayPal and leaves the ledger out (F1).
    refund: str | None = None


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("S1", "CLEAN", "Correct payment", "no exception"),
    Scenario(
        "S2",
        "COMMISSION",
        "Commission booked above PayPal's fee",
        "commission_mismatch",
        ledger_commission="15.00",
    ),
    Scenario(
        "S3",
        "HELD",
        "Delayed disbursement, funds held",
        "no exception",
        disbursement_mode="DELAYED",
    ),
    Scenario(
        "S4",
        "LATE-REFUND",
        "Refund recorded, PayPal's not yet available",
        "awaiting_evidence (not an exception)",
        refund="marketplace_only",
    ),
    Scenario(
        "S5",
        "NO-LEDGER",
        "Refund made at PayPal, ledger never adjusted",
        "missing_ledger_adjustment",
        refund="unbooked",
    ),
    Scenario(
        "S6",
        "DUPLICATE",
        "Sale credited twice",
        "duplicate_ledger_entry",
        duplicate_credit=True,
    ),
)

REFUND_AMOUNT = "20.00"


def normalise(name: str) -> str:
    if not NAME.match(name or ""):
        raise JudgeError("name must be 1 to 12 letters or digits")
    return name.upper()


def ref_for(name: str, scenario: Scenario) -> str:
    return f"JUDGE-{name}-{scenario.key}-{scenario.suffix}"


def _lock(session: Session, name: str) -> None:
    """Serialise calls for one name; released at commit or rollback."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"judge:{name}"}
    )


def _marker(
    session: Session, tenant_id: uuid.UUID, record_type: str, record_id: str
) -> dict[str, Any] | None:
    row = session.scalars(
        select(RawPayload).where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source == "marketplace",
            RawPayload.source_record_type == record_type,
            RawPayload.source_record_id == record_id,
        )
    ).first()
    return None if row is None else dict(row.body)


def _write_marker(
    session: Session,
    tenant_id: uuid.UUID,
    record_type: str,
    record_id: str,
    body: dict[str, Any],
) -> None:
    with write_side._labelled():
        seed._payload(
            session,
            tenant_id=tenant_id,
            record_type=record_type,
            record_id=record_id,
            body=body,
            retrieved_at=datetime.now(UTC),
        )


def _paypal_side_in(session: Session, tenant_id: uuid.UUID, ref: str) -> bool:
    """Whether PayPal's order for this marketplace order has been ingested.

    The link between the two is the purchase unit's `reference_id`, which is
    `<ref>-1` (matcher.py): present in the retained payload, in no column.
    """
    return (
        session.scalars(
            select(RawPayload.id).where(
                RawPayload.tenant_id == tenant_id,
                RawPayload.source == "paypal",
                RawPayload.source_record_type == "order",
                RawPayload.body.contains(
                    {"purchase_units": [{"reference_id": f"{ref}-1"}]}
                ),
            )
        ).first()
        is not None
    )


def _check_caps(
    session: Session, tenant_id: uuid.UUID, client: str | None, settings: Any
) -> None:
    sets = session.scalar(
        select(func.count()).select_from(RawPayload).where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source_record_type == SET_MARKER,
        )
    )
    if (sets or 0) >= settings.judge_max_sets:
        raise CapReached("the number of judge sets this demo allows is used up")
    if client:
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        mine = session.scalar(
            select(func.count()).select_from(RawPayload).where(
                RawPayload.tenant_id == tenant_id,
                RawPayload.source_record_type == SET_MARKER,
                RawPayload.retrieved_at >= today,
                RawPayload.body["client"].astext == client,
            )
        )
        if (mine or 0) >= settings.judge_sets_per_client_per_day:
            raise CapReached("this client has created its sets for today")


def ensure(
    session: Session,
    tenant_id: uuid.UUID,
    name: str,
    gateway: write_side.Gateway,
    *,
    client: str | None = None,
    settings: Any,
) -> dict[str, Any]:
    """Create whatever is missing for this name, as far as it can without waiting.

    Returns `status`, plus an `error` when PayPal refused a step: progress
    already made is kept (the caller commits) and the next call resumes.
    """
    name = normalise(name)
    _lock(session, name)
    set_id = f"JUDGE-{name}"
    if _marker(session, tenant_id, SET_MARKER, set_id) is None:
        _check_caps(session, tenant_id, client, settings)
        _write_marker(
            session,
            tenant_id,
            SET_MARKER,
            set_id,
            {"client": client, "created": datetime.now(UTC).isoformat()},
        )

    error: str | None = None
    try:
        for sc in SCENARIOS:
            _advance(session, tenant_id, name, sc, gateway)
    except (write_side.WriteError, capture.CaptureUnavailable) as exc:
        error = str(exc)
    out = status(session, tenant_id, name)
    if error:
        out["error"] = error
        out["complete"] = False
    return out


def _advance(
    session: Session,
    tenant_id: uuid.UUID,
    name: str,
    sc: Scenario,
    gateway: write_side.Gateway,
) -> None:
    ref = ref_for(name, sc)
    placed = _marker(session, tenant_id, ORDER_MARKER, ref)
    if placed is None:
        if session.scalars(
            select(Order.id).where(
                Order.tenant_id == tenant_id, Order.external_ref == ref
            )
        ).first():
            raise write_side.WriteError(
                f"{ref} exists without its marker; refusing to guess"
            )
        out = write_side.place_order(
            session,
            tenant_id,
            write_side.PlaceOrderIn(
                order_ref=ref,
                seller_ref=SELLER,
                gross=sc.gross,
                fee=sc.fee,
                disbursement_mode=sc.disbursement_mode,  # type: ignore[arg-type]
                ledger_commission=sc.ledger_commission,
                duplicate_credit=sc.duplicate_credit,
            ),
            gateway,
        )
        merchant = session.scalar(
            select(Seller.paypal_merchant_id).where(
                Seller.tenant_id == tenant_id, Seller.external_ref == SELLER
            )
        )
        _write_marker(
            session,
            tenant_id,
            ORDER_MARKER,
            ref,
            {
                "paypal_order_id": out["paypal_order_id"],
                "capture_id": out["paypal_capture_ids"][0],
                "merchant_id": merchant,
            },
        )
        session.flush()

    # Refund steps. The marketplace-only booking needs nothing from PayPal. The
    # PayPal refund needs the order ingested first, or its capture-time fee
    # cannot be established (live.SnapshotUnavailable).
    if sc.refund is None:
        return
    placed = _marker(session, tenant_id, ORDER_MARKER, ref) or {}
    step = f"{ref}:refund"
    if _marker(session, tenant_id, STEP_MARKER, step) is not None:
        return
    if sc.refund == "unbooked" and not _paypal_side_in(session, tenant_id, ref):
        return
    write_side.refund_order(
        session,
        tenant_id,
        write_side.RefundIn(
            order_ref=ref,
            paypal_capture_id=str(placed["capture_id"]),
            amount=REFUND_AMOUNT,
            marketplace_only=sc.refund == "marketplace_only",
            reflect_in_ledger=sc.refund == "marketplace_only",
            # The refund is read back at once rather than waiting for a
            # refund event: that webhook path is unproven (requirements.md).
            ingest_now=sc.refund == "unbooked",
        ),
        gateway,
    )
    _write_marker(session, tenant_id, STEP_MARKER, step, {"done": True})
    session.flush()


def status(session: Session, tenant_id: uuid.UUID, name: str) -> dict[str, Any]:
    name = normalise(name)
    rows = []
    for sc in SCENARIOS:
        ref = ref_for(name, sc)
        placed = _marker(session, tenant_id, ORDER_MARKER, ref)
        arrived = placed is not None and _paypal_side_in(session, tenant_id, ref)
        fallback = _marker(session, tenant_id, STEP_MARKER, f"{ref}:fallback")
        refund_done = (
            sc.refund is None
            or _marker(session, tenant_id, STEP_MARKER, f"{ref}:refund") is not None
        )
        rows.append(
            {
                "ref": ref,
                "case": sc.title,
                "expected": sc.expects,
                "placed": placed is not None,
                "paypal_side_ingested": arrived,
                "path": (
                    None if not arrived else ("fallback" if fallback else "webhook")
                ),
                "refund_step_done": refund_done,
            }
        )
    return {
        "name": name,
        "scenarios": rows,
        "complete": all(
            r["placed"] and r["paypal_side_ingested"] and r["refund_step_done"]
            for r in rows
        ),
    }


def ingest_missing(
    session: Session,
    tenant_id: uuid.UUID,
    name: str,
    gateway: write_side.Gateway,
) -> dict[str, Any]:
    """Fetch PayPal's side of any placed order whose webhook has not arrived.

    One order failing (PayPal is reachable intermittently) does not undo the
    others: each is ingested in its own savepoint, and `error` names what is
    still missing so the caller can ask again.
    """
    name = normalise(name)
    _lock(session, name)
    errors: list[str] = []
    for sc in SCENARIOS:
        ref = ref_for(name, sc)
        placed = _marker(session, tenant_id, ORDER_MARKER, ref)
        if placed is None or _paypal_side_in(session, tenant_id, ref):
            continue
        try:
            with session.begin_nested():
                fetched = gateway.read_order(
                    str(placed["paypal_order_id"]), str(placed["merchant_id"])
                )
                document = paypal_live.build_document(session, tenant_id, fetched)
                replay.ingest_document(session, document, tenant_id=tenant_id)
                _write_marker(
                    session, tenant_id, STEP_MARKER, f"{ref}:fallback", {"done": True}
                )
        except (
            write_side.WriteError,
            paypal_live.LiveFetchError,
            paypal_live.SnapshotUnavailable,
            replay.ReplayError,
            httpx.HTTPError,
        ) as exc:
            errors.append(f"{ref}: {exc or type(exc).__name__}")
    out = status(session, tenant_id, name)
    if errors:
        out["error"] = "; ".join(errors)
    return out


def deliver_late_refund(
    session: Session,
    tenant_id: uuid.UUID,
    name: str,
    gateway: write_side.Gateway,
) -> dict[str, Any]:
    """Make PayPal's side of S4's refund. Once only; needs the order ingested."""
    name = normalise(name)
    _lock(session, name)
    sc = next(s for s in SCENARIOS if s.key == "S4")
    ref = ref_for(name, sc)
    placed = _marker(session, tenant_id, ORDER_MARKER, ref)
    if placed is None:
        raise JudgeError("run the scenarios first")
    if _marker(session, tenant_id, STEP_MARKER, f"{ref}:late") is not None:
        out = status(session, tenant_id, name)
        out["late_refund"] = "already delivered"
        return out
    if _marker(session, tenant_id, STEP_MARKER, f"{ref}:refund") is None:
        raise JudgeError("the marketplace refund is not booked yet")
    if not _paypal_side_in(session, tenant_id, ref):
        raise JudgeError("PayPal's order has not arrived yet; wait or ingest it first")
    write_side.refund_order(
        session,
        tenant_id,
        write_side.RefundIn(
            order_ref=ref,
            paypal_capture_id=str(placed["capture_id"]),
            amount=REFUND_AMOUNT,
            paypal_only=True,
            ingest_now=True,
        ),
        gateway,
    )
    _write_marker(session, tenant_id, STEP_MARKER, f"{ref}:late", {"done": True})
    session.flush()
    out = status(session, tenant_id, name)
    out["late_refund"] = "delivered"
    return out


# --- the HTTP surface -------------------------------------------------------
#
# Hidden (404) unless JUDGE_SCENARIOS_ENABLED. No secret: every route takes only
# a name, so there is nothing a caller can aim at anything but their own set.

router = APIRouter(prefix="/judge", tags=["judge scenarios"])


class NameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


def require_enabled() -> None:
    if not get_settings().judge_scenarios_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


def _session(request: Request) -> Iterator[Session]:
    with Session(bind=request.app.state.engine) as session:
        yield session


def _client(request: Request) -> str | None:
    """A short hash of the caller's address; the address itself is not stored."""
    forwarded = request.headers.get("x-forwarded-for", "")
    host = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else ""
    )
    return hashlib.sha256(host.encode()).hexdigest()[:16] if host else None


SessionDep = Annotated[Session, Depends(_session)]
GatewayDep = Annotated[write_side.Gateway, Depends(write_side.get_gateway)]
EnabledDep = Annotated[None, Depends(require_enabled)]


def _run(session: Session, call: Any) -> dict[str, Any]:
    tenant_id = write_side._tenant(session)
    try:
        out: dict[str, Any] = call(tenant_id)
    except JudgeError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CapReached as exc:
        session.rollback()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (
        write_side.WriteError,
        capture.CaptureUnavailable,
        paypal_live.LiveFetchError,
        paypal_live.SnapshotUnavailable,
        replay.ReplayError,
        httpx.HTTPError,
    ) as exc:
        session.rollback()
        raise HTTPException(
            status_code=502, detail=str(exc) or type(exc).__name__
        ) from exc
    # Commit even when `ensure` reports an error: progress already made stays,
    # and the next call resumes.
    session.commit()
    return out


@router.post("/scenarios")
def post_scenarios(
    body: NameIn,
    request: Request,
    _on: EnabledDep,
    session: SessionDep,
    gateway: GatewayDep,
) -> dict[str, Any]:
    client = _client(request)
    return _run(
        session,
        lambda t: ensure(
            session, t, body.name, gateway, client=client, settings=get_settings()
        ),
    )


@router.get("/scenarios/{name}")
def get_scenarios(name: str, _on: EnabledDep, session: SessionDep) -> dict[str, Any]:
    return _run(session, lambda t: status(session, t, name))


@router.post("/scenarios/{name}/ingest-missing")
def post_ingest_missing(
    name: str, _on: EnabledDep, session: SessionDep, gateway: GatewayDep
) -> dict[str, Any]:
    return _run(session, lambda t: ingest_missing(session, t, name, gateway))


@router.post("/scenarios/{name}/late-refund")
def post_late_refund(
    name: str, _on: EnabledDep, session: SessionDep, gateway: GatewayDep
) -> dict[str, Any]:
    return _run(session, lambda t: deliver_late_refund(session, t, name, gateway))
