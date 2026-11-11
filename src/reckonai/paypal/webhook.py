"""PayPal webhook events: verification, routing, and queueing.

`specs/2026-11-11-live-ingestion/requirements.md` F1 to F5.

**An event is a notification, never the record** (D1). What this module does
with a verified event is store it, work out whose it is, and queue a job; the
job re-fetches the order and the existing normalizer writes the records.

**Verification fails closed.** `Verifier.verify` answers `True` only when
PayPal's own verification call says `SUCCESS`. A missing webhook id, a refused
call or any other answer is not acceptance. Note that PayPal answers `200` with
`FAILURE` for a bad signature, so the body is read, never the status.

**Routing is a cross-tenant lookup, and is the one place that must be.** An
event names a merchant, not a tenant. The lookup `seller.paypal_merchant_id ->
tenant` is therefore unscoped by design, and it refuses to guess: no match or
more than one tenant is `unrouted`. An unrouted event cannot be stored as a
row, because every row needs a tenant; it is logged and acknowledged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx2 as httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.jobs import queue
from reckonai.logging import event as log_event
from reckonai.models import ProcessedEvent, RawPayload, Seller
from reckonai.paypal import live

logger = logging.getLogger("reckonai.paypal.webhook")

JOB_TYPE = "paypal_event"

#: Event types whose handling is an order re-fetch. A capture's lifecycle is
#: all visible on the order, including refunds (read through the order, rule 3).
#: Dispute events are announced but not handled: no dispute normalizer exists on
#: the provider side, so claiming to handle them would be a false claim.
HANDLED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "PAYMENT.CAPTURE.COMPLETED",
        "PAYMENT.CAPTURE.PENDING",
        "PAYMENT.CAPTURE.DENIED",
        "PAYMENT.CAPTURE.DECLINED",
        "PAYMENT.CAPTURE.REFUNDED",
        "PAYMENT.CAPTURE.REVERSED",
    }
)

#: The headers PayPal signs a delivery with.
SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


class VerifierUnavailable(RuntimeError):
    """PayPal could not be asked, so the event is neither accepted nor refused."""


class Outcome(StrEnum):
    QUEUED = "queued"
    DUPLICATE = "duplicate"
    IGNORED_TYPE = "ignored_type"
    UNROUTED = "unrouted"
    NO_ORDER = "no_order"


class Verifier:
    """Asks PayPal whether a delivery is genuine."""

    def __init__(
        self,
        creds: live.Credentials,
        webhook_id: str | None,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self._creds = creds
        self._webhook_id = webhook_id
        self._owns_http = http is None
        self._http = http or httpx.Client(timeout=live.DEFAULT_TIMEOUT)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def verify(
        self,
        headers: Mapping[str, str],
        event: dict[str, Any],
        raw: bytes | None = None,
    ) -> bool:
        """Whether PayPal says this delivery is genuine.

        **`raw` is the body exactly as received, and is spliced into the request
        unparsed.** PayPal checks the signature against the bytes it sent;
        parsing and re-serializing can change key order, spacing, escapes or
        number formatting, and a genuine event would then fail verification and
        be refused for good (PayPal does not retry a `401`). `event` is the
        parsed form, used only when no raw body exists.
        """
        if not self._webhook_id:
            return False
        lowered = {k.lower(): v for k, v in headers.items()}
        if any(name not in lowered for name in SIGNATURE_HEADERS.values()):
            return False

        basic = base64.b64encode(
            f"{self._creds.client_id}:{self._creds.secret}".encode()
        ).decode()
        try:
            token = self._http.post(
                f"{self._creds.base}/v1/oauth2/token",
                headers={"Authorization": f"Basic {basic}"},
                data={"grant_type": "client_credentials"},
            )
            if token.status_code != 200:
                raise VerifierUnavailable(f"token request returned {token.status_code}")
            fields = {
                **{key: lowered[name] for key, name in SIGNATURE_HEADERS.items()},
                "webhook_id": self._webhook_id,
            }
            request_headers = {
                "Authorization": f"Bearer {token.json()['access_token']}",
                "Content-Type": "application/json",
            }
            url = f"{self._creds.base}/v1/notifications/verify-webhook-signature"
            if raw is not None:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return False
                body = f'{json.dumps(fields)[:-1]}, "webhook_event": {text}}}'
                response = self._http.post(
                    url, headers=request_headers, content=body.encode("utf-8")
                )
            else:
                response = self._http.post(
                    url,
                    headers=request_headers,
                    json={**fields, "webhook_event": event},
                )
        except httpx.HTTPError as exc:
            raise VerifierUnavailable(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise VerifierUnavailable(f"verify returned {response.status_code}")
        if response.status_code != 200:
            return False
        return bool(response.json().get("verification_status") == "SUCCESS")


def _dig(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def route_tenant(session: Session, merchant_id: str) -> uuid.UUID | None:
    """The one tenant that has a seller with this merchant id, or `None`."""
    tenants = set(
        session.scalars(
            select(Seller.tenant_id).where(Seller.paypal_merchant_id == merchant_id)
        )
    )
    return next(iter(tenants)) if len(tenants) == 1 else None


def event_key(event_id: str) -> str:
    return f"paypal:{event_id}"


def ingest_event(session: Session, event: dict[str, Any]) -> Outcome:
    """Store a verified event and queue its handler. The caller commits.

    Never writes a financial record: that is the job's work, after a re-fetch.
    """
    event_id = str(event["id"])
    event_type = str(event["event_type"])

    if event_type not in HANDLED_EVENT_TYPES:
        logger.info(log_event("reckonai.webhook.ignored", event_type=event_type))
        return Outcome.IGNORED_TYPE

    resource = event.get("resource")
    merchant_id = _dig(resource, "payee", "merchant_id")
    order_id = _dig(resource, "supplementary_data", "related_ids", "order_id")
    if not order_id:
        logger.warning(log_event("reckonai.webhook.no_order", event_id=event_id))
        return Outcome.NO_ORDER

    tenant_id = route_tenant(session, str(merchant_id)) if merchant_id else None
    if tenant_id is None:
        logger.warning(
            log_event(
                "reckonai.webhook.unrouted",
                event_id=event_id,
                reason="no_payee" if not merchant_id else "no_unique_tenant",
            )
        )
        return Outcome.UNROUTED

    done = session.scalars(
        select(ProcessedEvent.id).where(
            ProcessedEvent.tenant_id == tenant_id,
            ProcessedEvent.event_id == event_key(event_id),
        )
    ).first()
    if done is not None:
        return Outcome.DUPLICATE

    _store(session, tenant_id, event_id, event)
    queue.enqueue(
        session,
        tenant_id=tenant_id,
        job_type=JOB_TYPE,
        payload={
            "event_id": event_id,
            "event_type": event_type,
            "order_id": str(order_id),
            "merchant_id": str(merchant_id),
        },
    )
    return Outcome.QUEUED


def _store(
    session: Session, tenant_id: uuid.UUID, event_id: str, event: dict[str, Any]
) -> RawPayload:
    """Retain the event as delivered (P7), reusing an identical earlier row."""
    digest = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = session.scalars(
        select(RawPayload).where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source == "paypal",
            RawPayload.source_record_type == "webhook_event",
            RawPayload.body_sha256 == digest,
        )
    ).first()
    if existing is not None:
        return existing
    row = RawPayload(
        tenant_id=tenant_id,
        source="paypal",
        source_record_type="webhook_event",
        source_record_id=event_id,
        endpoint="WEBHOOK",
        retrieved_at=datetime.now(UTC),
        body=event,
        body_sha256=digest,
    )
    session.add(row)
    session.flush()
    return row
