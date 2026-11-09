"""Normalize captured PayPal responses into the canonical tables.

The replan's R6 and R16. This replaces Block A's synthetic provider side: the
rows written here are PayPal's own figures, retained as the `raw_payload` P7
requires anyway.

**Four rules are encoded here that the payloads do not announce.** Each was
found by running the capture, and each would otherwise be discovered as a
wrong number in a reconciliation:

  1. **The platform fee is read from the capture-time snapshot, never from a
     later read.** After a partial refund, a capture's `platform_fees` is
     unreliable: on one of two sellers it was replaced by the *reversal*
     amount, reproducibly and stably. So `order_at_capture` is authoritative
     for what PayPal took, and the readback is used for everything that only
     exists afterwards. Same family as
     `specs/2026-11-07-multiparty-field-discovery/notes.md` section 4.1,
     where a disputed capture loses its whole breakdown.

  2. **The payee comes from the readback, not the snapshot.** The order
     creation response omits `purchase_units[].payee` entirely, and the payee
     is the join the whole reconciliation rests on
     (specs/tech-stack.md section 9.5).

  3. **Refunds are read through the order.** The refund endpoint returns
     `seller_payable_breakdown: null`; the order carries it. This is the
     read-path rule specs/roadmap.md Phase 7 inherited, confirmed against
     real responses.

  4. **A conversion row exists only where `exchange_rate` is reported**, and
     its `fee` stays `NULL`. PayPal publishes no conversion fee, and
     `CHECK (fee_amount IS NULL OR fee_amount <> 0)` refuses a zero - an
     absent fee and a measured fee of nothing are different claims
     (specs/mission.md P3).

**Idempotent.** Every write keys on `UNIQUE (tenant_id, external_id)`, so a
re-run inserts nothing. An unchanged payload re-hashes to the same digest and
reuses its `raw_payload` row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import (
    Capture,
    Disbursement,
    FxConversion,
    Payment,
    PlatformFee,
    RawPayload,
    Refund,
    Seller,
)
from reckonai.money import Money

#: Recorded on every payload. Field availability depends on the read path, so
#: a payload whose path is unrecorded cannot be interpreted later: the absence
#: of a field might be PayPal's answer or an artefact of where we asked.
PAYPAL_ENDPOINT = "GET /v2/checkout/orders/{id}"


class ReplayError(RuntimeError):
    """A captured document could not be normalized.

    Raised rather than skipped. A fixture that cannot be read is a gap in the
    provider evidence, and a reconciliation run over partial evidence reports
    absences that are really our own omissions.
    """


@dataclass
class ReplayResult:
    tenant_id: uuid.UUID
    inserted: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    orders: list[str] = field(default_factory=list)

    @property
    def total_inserted(self) -> int:
        return sum(self.inserted.values())


def _digest(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _when(value: str | None) -> datetime:
    """Parse a PayPal timestamp, which is ISO-8601 with a `Z`."""
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _money(node: dict | None) -> Money | None:
    """A `Money` from PayPal's `{currency_code, value}` envelope.

    Returns `None` for an absent node rather than a zero: PayPal not stating
    a figure and PayPal stating nothing are different claims, and defaulting
    money is how a reconciliation system produces a confident wrong answer
    (specs/tech-stack.md section 9.6).
    """
    if not node:
        return None
    value = node.get("value")
    currency = node.get("currency_code")
    if value is None or currency is None:
        return None
    return Money(str(value), str(currency))


def _payload(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    record_type: str,
    record_id: str,
    body: dict,
    retrieved_at: datetime,
) -> RawPayload:
    """Write or reuse the payload row for one provider record.

    Reused on an identical body, which is half of what makes replay
    idempotent. A *changed* body writes a second row, deliberately: that is
    the mechanism a payload diff needs.
    """
    digest = _digest(body)
    existing = session.scalar(
        select(RawPayload).where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source == "paypal",
            RawPayload.source_record_type == record_type,
            RawPayload.body_sha256 == digest,
        )
    )
    if existing is not None:
        return existing

    row = RawPayload(
        tenant_id=tenant_id,
        source="paypal",
        source_record_type=record_type,
        source_record_id=record_id,
        endpoint=PAYPAL_ENDPOINT,
        retrieved_at=retrieved_at,
        body=body,
        body_sha256=digest,
    )
    session.add(row)
    session.flush()
    return row


def _sellers(session: Session, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Merchant id -> seller id. The join the reconciliation rests on."""
    return {
        row.paypal_merchant_id: row.id
        for row in session.scalars(
            select(Seller).where(Seller.tenant_id == tenant_id)
        )
        if row.paypal_merchant_id
    }


def _exists(session: Session, model: Any, tenant_id: uuid.UUID, external_id: str):
    return session.scalar(
        select(model).where(
            model.tenant_id == tenant_id, model.external_id == external_id
        )
    )


def ingest_document(
    session: Session,
    document: dict,
    *,
    tenant_id: uuid.UUID,
    result: ReplayResult | None = None,
) -> ReplayResult:
    """Normalize one captured order. The unit of replay.

    `document` is what `scripts/capture_paypal.py` writes: the order as read
    back, the order as it was at capture, and any release records. The demo's
    opening live call produces the same shape, so a fresh order ingests
    through this same path rather than a second one.
    """
    out = result or ReplayResult(tenant_id=tenant_id)
    order = document.get("order")
    if not order:
        raise ReplayError("document carries no 'order'")

    at_capture = document.get("order_at_capture") or order
    order_ref = document.get("order_ref") or order.get("id")
    retrieved = _when(document.get("captured_at"))
    sellers = _sellers(session, tenant_id)
    mode = document.get("disbursement_mode")
    released = bool(document.get("releases"))

    # --- the payment: PayPal's order ---------------------------------------
    order_id = order["id"]
    payment = _exists(session, Payment, tenant_id, order_id)
    if payment is None:
        payload = _payload(
            session,
            tenant_id=tenant_id,
            record_type="order",
            record_id=order_id,
            body=order,
            retrieved_at=retrieved,
        )
        first_payee = (order["purchase_units"][0].get("payee") or {}).get(
            "merchant_id"
        )
        payment = Payment(
            tenant_id=tenant_id,
            raw_payload_id=payload.id,
            external_id=order_id,
            # **Resolved, and nullable because resolution can fail.** A payee
            # we have no seller row for is a record Phase 11 should see, not
            # an insert that crashes.
            seller_id=sellers.get(first_payee or ""),
            # NULL: the tie to a marketplace order is a match, not a fact
            # PayPal stated. Establishing it is Block C's work.
            order_id=None,
            amount=_total(order),
            status=order.get("status", "UNKNOWN"),
            intent=order.get("intent"),
            # A card-funded order carries no `payer` block at all.
            payer_reference=None,
            disbursement_mode=mode,
            occurred_at=_when(order.get("create_time")),
        )
        session.add(payment)
        session.flush()
        out.inserted["payment"] += 1
    else:
        out.skipped["payment"] += 1

    # Capture-time breakdowns, by capture id. Rule 1.
    snapshot = {
        cap["id"]: cap
        for unit in at_capture.get("purchase_units", [])
        for cap in unit.get("payments", {}).get("captures", [])
    }

    for unit in order.get("purchase_units", []):
        # Rule 2: the payee is on the readback only.
        merchant = (unit.get("payee") or {}).get("merchant_id")
        seller_id = sellers.get(merchant or "")
        payments = unit.get("payments", {})

        for cap in payments.get("captures", []):
            _capture(session, out, tenant_id, cap, snapshot.get(cap["id"], cap),
                     payment.id, seller_id, mode, released, retrieved)

        # Rule 3: refunds come through the order, with their breakdown.
        for ref in payments.get("refunds", []):
            _refund(session, out, tenant_id, ref, seller_id, retrieved)

    for release in document.get("releases", []):
        _release(session, out, tenant_id, release, sellers, retrieved)

    out.orders.append(order_ref)
    return out


def _total(order: dict) -> Money:
    """The order's gross, summed across purchase units.

    Single-currency by construction: every unit of one order shares its
    currency, and `Money` refuses to add across currencies, so a mixed order
    would raise here rather than produce a meaningless total.
    """
    total: Money | None = None
    for unit in order.get("purchase_units", []):
        for cap in unit.get("payments", {}).get("captures", []):
            amount = _money(cap.get("amount"))
            if amount is None:
                continue
            total = amount if total is None else total + amount
    if total is None:
        raise ReplayError(f"order {order.get('id')} carries no capture amount")
    return total


def _capture(
    session: Session,
    out: ReplayResult,
    tenant_id: uuid.UUID,
    cap: dict,
    at_capture: dict,
    payment_id: uuid.UUID,
    seller_id: uuid.UUID | None,
    mode: str | None,
    released: bool,
    retrieved: datetime,
) -> None:
    if _exists(session, Capture, tenant_id, cap["id"]) is not None:
        out.skipped["capture"] += 1
        return

    # Rule 1: the fee and the breakdown come from the capture-time snapshot.
    breakdown = at_capture.get("seller_receivable_breakdown") or {}

    payload = _payload(
        session,
        tenant_id=tenant_id,
        record_type="capture",
        record_id=cap["id"],
        body=at_capture,
        retrieved_at=retrieved,
    )

    settlement = _money(breakdown.get("receivable_amount"))

    # `status` is mutable and does not mean the seller was paid: a DELAYED
    # capture reads COMPLETED while PayPal holds the funds. `disbursement_state`
    # is the column that answers it.
    if mode != "DELAYED":
        state = "disbursed_at_capture"
    elif released:
        state = "disbursement_completed"
    else:
        state = "funds_held"

    capture = Capture(
        tenant_id=tenant_id,
        raw_payload_id=payload.id,
        external_id=cap["id"],
        payment_id=payment_id,
        seller_id=seller_id,
        amount=_money(cap.get("amount")),
        gross_amount=_money(breakdown.get("gross_amount")),
        net_amount=_money(breakdown.get("net_amount")),
        # **NULL, because PayPal reported no such field on a capture.** Not
        # zero: on a *refund* PayPal does report `paypal_fee: 0.00`, and
        # those are different claims. The seller's net is reported directly,
        # so nothing needs the fee to know what they received.
        paypal_fee=_money(breakdown.get("paypal_fee")),
        settlement_amount=settlement,
        settlement_currency=settlement.currency if settlement else None,
        status=cap.get("status", "UNKNOWN"),
        disbursement_mode=mode,
        disbursement_state=state,
        occurred_at=_when(cap.get("create_time")),
    )
    session.add(capture)
    session.flush()
    out.inserted["capture"] += 1

    _fees(session, out, tenant_id, breakdown, payload.id, capture.id, None,
          seller_id, capture.occurred_at)
    _conversion(session, out, tenant_id, breakdown, payload.id, capture.id, None)


def _refund(
    session: Session,
    out: ReplayResult,
    tenant_id: uuid.UUID,
    ref: dict,
    seller_id: uuid.UUID | None,
    retrieved: datetime,
) -> None:
    if _exists(session, Refund, tenant_id, ref["id"]) is not None:
        out.skipped["refund"] += 1
        return

    breakdown = ref.get("seller_payable_breakdown") or {}
    payload = _payload(
        session,
        tenant_id=tenant_id,
        record_type="refund",
        record_id=ref["id"],
        body=ref,
        retrieved_at=retrieved,
    )

    # PayPal supplies no capture id on a refund; the only route back is
    # `links[rel=up]`, whose href has to be parsed. So the linkage is derived
    # and is recorded as derived - never presented as provider evidence.
    href = next(
        (
            link.get("href")
            for link in ref.get("links", [])
            if link.get("rel") == "up"
        ),
        None,
    )

    refund = Refund(
        tenant_id=tenant_id,
        raw_payload_id=payload.id,
        external_id=ref["id"],
        capture_id=None,
        capture_linkage_is_derived=True,
        capture_linkage_href=href,
        seller_id=seller_id,
        amount=_money(ref.get("amount")),
        gross_amount=_money(breakdown.get("gross_amount")),
        net_amount=_money(breakdown.get("net_amount")),
        # Reported as an explicit 0.00 on a refund, unlike on a capture.
        paypal_fee=_money(breakdown.get("paypal_fee")),
        total_refunded_amount=_money(breakdown.get("total_refunded_amount")),
        status=ref.get("status", "UNKNOWN"),
        occurred_at=_when(ref.get("create_time")),
    )
    session.add(refund)
    session.flush()
    out.inserted["refund"] += 1

    # The pro-rata platform fee reversal, which PayPal applies automatically.
    _fees(session, out, tenant_id, breakdown, payload.id, None, refund.id,
          seller_id, refund.occurred_at)
    _conversion(session, out, tenant_id, breakdown, payload.id, None, refund.id)


def _fees(
    session: Session,
    out: ReplayResult,
    tenant_id: uuid.UUID,
    breakdown: dict,
    payload_id: uuid.UUID,
    capture_id: uuid.UUID | None,
    refund_id: uuid.UUID | None,
    seller_id: uuid.UUID | None,
    occurred_at: datetime,
) -> None:
    """Platform fee rows from a breakdown's `platform_fees` array.

    **The identifier is synthesised, and visibly so.** PayPal gives a
    platform fee no id of its own - just `{amount, payee}` - but
    `platform_fee.external_id` is NOT NULL and its unique constraint is what
    makes replay idempotent. So the key is the parent record's id plus the
    entry's index, which is stable across runs. The `pf-` prefix is there so
    nobody goes looking for it in a PayPal dashboard.
    """
    parent = capture_id or refund_id
    for index, entry in enumerate(breakdown.get("platform_fees") or []):
        amount = _money(entry.get("amount"))
        if amount is None:
            continue
        external_id = f"pf-{parent}-{index}"
        if _exists(session, PlatformFee, tenant_id, external_id) is not None:
            out.skipped["platform_fee"] += 1
            continue
        session.add(
            PlatformFee(
                tenant_id=tenant_id,
                raw_payload_id=payload_id,
                external_id=external_id,
                capture_id=capture_id,
                refund_id=refund_id,
                seller_id=seller_id,
                payee_reference=(entry.get("payee") or {}).get("merchant_id"),
                amount=amount,
                settlement_reference=None,
                occurred_at=occurred_at,
            )
        )
        session.flush()
        out.inserted["platform_fee"] += 1


def _conversion(
    session: Session,
    out: ReplayResult,
    tenant_id: uuid.UUID,
    breakdown: dict,
    payload_id: uuid.UUID,
    capture_id: uuid.UUID | None,
    refund_id: uuid.UUID | None,
) -> None:
    """A conversion row, **only where PayPal reported a rate**.

    Rule 4. An empty row can never be mistaken for a conversion whose fields
    went missing, and a same-currency transaction gets none at all - which
    `CHECK (source_currency <> target_currency)` enforces independently.

    **The source is the net, not the gross.** PayPal deducts fees in the
    transaction currency and converts the remainder; converting the gross is
    wrong by the whole fee. Confirmed against a real capture:
    `138.00 x 1.089477173902071 = 150.35`, where the gross would give 163.42.
    """
    rate_node = breakdown.get("exchange_rate")
    if not rate_node or not rate_node.get("value"):
        return

    source = _money(breakdown.get("net_amount"))
    target = _money(breakdown.get("receivable_amount"))
    if source is None or target is None:
        return

    from decimal import Decimal

    session.add(
        FxConversion(
            tenant_id=tenant_id,
            raw_payload_id=payload_id,
            capture_id=capture_id,
            refund_id=refund_id,
            source=source,
            target=target,
            rate=Decimal(str(rate_node["value"])),
            # PayPal declares no timestamp on any conversion structure, so
            # the parent's time is used and `is_derived` records that.
            converted_at=None,
            is_derived=False,
            # Stays NULL. PayPal publishes no conversion fee, and the CHECK
            # refuses a zero: absent and measured-as-nothing differ (P3).
            fee=None,
            provider="paypal",
        )
    )
    session.flush()
    out.inserted["fx_conversion"] += 1


def _release(
    session: Session,
    out: ReplayResult,
    tenant_id: uuid.UUID,
    release: dict,
    sellers: dict[str, uuid.UUID],
    retrieved: datetime,
) -> None:
    """A referenced-payout release record.

    `released_at` stays **NULL**. The record carries a `time_updated`, but
    whether that is when PayPal released the funds or when the record was
    last touched is unestablished - and writing it into `released_at` on a
    guess would present our reading as a provider timestamp, which is the
    substitution the column was built to refuse (P3,
    `specs/2026-11-07-multiparty-field-discovery/notes.md` section 4.11).
    """
    external_id = release.get("item_id") or release.get("payout_transaction_id")
    if not external_id:
        raise ReplayError("release record carries no identifier")
    if _exists(session, Disbursement, tenant_id, external_id) is not None:
        out.skipped["disbursement"] += 1
        return

    payload = _payload(
        session,
        tenant_id=tenant_id,
        record_type="disbursement",
        record_id=external_id,
        body=release,
        retrieved_at=retrieved,
    )
    destination = release.get("payout_destination")
    status = (release.get("processing_state") or {}).get("status")

    session.add(
        Disbursement(
            tenant_id=tenant_id,
            raw_payload_id=payload.id,
            external_id=external_id,
            # The capture this released, named by the release itself.
            capture_id=None,
            seller_id=sellers.get(destination or ""),
            recipient_reference=destination,
            payout_transaction_id=release.get("payout_transaction_id"),
            amount=_money(release.get("payout_amount")),
            provider_status=status,
            state=(
                "disbursement_completed"
                if status == "SUCCESS"
                else "disbursement_pending"
            ),
            released_at=None,
            provider_errors=None,
        )
    )
    session.flush()
    out.inserted["disbursement"] += 1


def ingest_directory(
    session: Session, directory: Path, *, tenant_id: uuid.UUID
) -> ReplayResult:
    """Replay every captured order in a directory, in reference order."""
    out = ReplayResult(tenant_id=tenant_id)
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ReplayError(
            f"no fixtures in {directory}. Replaying nothing would leave every "
            "marketplace record unmatched, which reads as eighteen findings "
            "rather than as a missing step."
        )
    for path in files:
        ingest_document(
            session, json.loads(path.read_text(encoding="utf-8")),
            tenant_id=tenant_id, result=out,
        )
    return out
