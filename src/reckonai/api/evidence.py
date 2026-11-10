"""Opening a cited reference: the record it names, as the product holds it.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D13.

A finding or an investigation cites ids (`RFND-0013-A`, a capture id). This
turns one into the record behind it, so a person can check the evidence
instead of trusting a chip. **It resolves through the agent's own
`CitationResolver`**, so "what this id names" has one definition: the answer
here and the answer behind "does not resolve" cannot disagree.

Read-only and tenant-scoped. An absent value is returned as `None` and shown
as "not stated", never as zero (`mission.md` P3): a capture that states no
PayPal fee is a different claim from one that states a fee of nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.agent import citations
from reckonai.api import schemas
from reckonai.models import (
    Capture,
    Disbursement,
    Order,
    OrderItem,
    Payment,
    PlatformFee,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
)
from reckonai.money import Money
from reckonai.schemas.money import MoneyOut

#: kind -> (model, the column holding the external reference).
_SOURCES: dict[str, tuple[type[Any], str]] = {
    citations.ORDER: (Order, "external_ref"),
    citations.ORDER_ITEM: (OrderItem, "external_ref"),
    citations.PAYMENT: (Payment, "external_id"),
    citations.CAPTURE: (Capture, "external_id"),
    citations.REFUND: (Refund, "external_id"),
    citations.LEDGER_ENTRY: (SellerLedgerEntry, "external_ref"),
    citations.DISBURSEMENT: (Disbursement, "external_id"),
    citations.PLATFORM_FEE: (PlatformFee, "external_id"),
}

#: Columns that are plumbing, not evidence.
_HIDDEN = {
    "id",
    "tenant_id",
    "created_at",
    "raw_payload_id",
    "external_id",
    "external_ref",
}


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _fields(
    session: Session, tenant_id: uuid.UUID, row: Any
) -> list[schemas.EvidenceField]:
    mapper = sa_inspect(type(row))
    composite_keys = {c.key for c in mapper.composites}
    # The raw columns behind a money composite are shown once, as the money.
    behind = {f"{k}_amount" for k in composite_keys} | {
        f"{k}_currency" for k in composite_keys
    }

    sellers: dict[uuid.UUID, str] = {}
    out: list[schemas.EvidenceField] = []

    for key in composite_keys:
        value = getattr(row, key)
        if isinstance(value, Money):
            out.append(
                schemas.EvidenceField(
                    label=_label(key), value=None, money=MoneyOut.from_money(value)
                )
            )
        else:
            out.append(schemas.EvidenceField(label=_label(key), value=None, money=None))

    for attr in mapper.column_attrs:
        key = attr.key
        if key in _HIDDEN or key in behind or key in composite_keys:
            continue
        value = getattr(row, key)
        if key == "seller_id" and value is not None:
            if value not in sellers:
                sellers[value] = session.scalar(
                    select(Seller.name).where(
                        Seller.tenant_id == tenant_id, Seller.id == value
                    )
                ) or str(value)
            out.append(schemas.EvidenceField(label="Seller", value=sellers[value]))
            continue
        if key.endswith("_id") and isinstance(value, uuid.UUID):
            continue  # an internal link, not something a person can look up
        if isinstance(value, datetime):
            text: str | None = value.isoformat()
        elif value is None:
            text = None
        else:
            text = str(value)
        out.append(schemas.EvidenceField(label=_label(key), value=text))

    out.sort(key=lambda f: (f.money is None, f.label))
    return out


def describe(
    session: Session, tenant_id: uuid.UUID, ref: str
) -> schemas.EvidenceOut | None:
    """The record `ref` names, or `None` if it names nothing for this tenant."""
    citation = citations.CitationResolver(session, tenant_id).resolve(ref)
    if citation.kind is None or citation.kind not in _SOURCES:
        return None

    model, column = _SOURCES[citation.kind]
    row = session.scalar(
        select(model).where(
            model.tenant_id == tenant_id,
            getattr(model, column) == ref.strip(),
        )
    )
    if row is None:
        return None

    raw = None
    payload_id = getattr(row, "raw_payload_id", None)
    if payload_id is not None:
        payload = session.scalar(
            select(RawPayload).where(
                RawPayload.tenant_id == tenant_id, RawPayload.id == payload_id
            )
        )
        if payload is not None:
            raw = schemas.RawPayloadSummary(
                source=payload.source,
                record_type=payload.source_record_type,
                retrieved_at=payload.retrieved_at,
                endpoint=payload.endpoint,
                sha256=payload.body_sha256,
            )

    return schemas.EvidenceOut(
        ref=ref.strip(),
        kind=citation.kind,
        stable=citation.stable,
        fields=_fields(session, tenant_id, row),
        raw=raw,
    )


def raw_body(session: Session, tenant_id: uuid.UUID, ref: str) -> dict[str, Any] | None:
    """The provider's own response behind a record, exactly as retained."""
    citation = citations.CitationResolver(session, tenant_id).resolve(ref)
    if citation.kind is None or citation.kind not in _SOURCES:
        return None
    model, column = _SOURCES[citation.kind]
    payload_id = (
        session.scalar(
            select(model.raw_payload_id).where(
                model.tenant_id == tenant_id, getattr(model, column) == ref.strip()
            )
        )
        if hasattr(model, "raw_payload_id")
        else None
    )
    if payload_id is None:
        return None
    body = session.scalar(
        select(RawPayload.body).where(
            RawPayload.tenant_id == tenant_id, RawPayload.id == payload_id
        )
    )
    return dict(body) if body is not None else None
