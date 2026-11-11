"""Live retrieval: read an order from the PayPal sandbox, as a seller.

`specs/2026-11-11-live-ingestion/requirements.md` F6 and F7.

**It produces the document `replay.ingest_document` already reads**, so a live
order and a replayed fixture go through one normalizer and the four ingester
rules apply to both. There is no second normalization path.

Five client rules from `specs/tech-stack.md` section 8 are held here, each by a
named behaviour a test pins:

  * Refunds come through the order (rule 3), which is the only thing fetched.
  * **A `403` is never recorded as an absent field.** A seller who may not read
    an order is "not this seller", tried against the next candidate; if every
    candidate refuses, `LiveDenied` is raised and the job fails with the error
    recorded. Nothing is ingested on the strength of a refusal.
  * **A `5xx` is not retried here** ("not reliably transient"). `LiveUnavailable`
    is raised and the queue's bounded retry decides.
  * The fee is read from a capture-time snapshot (rule 1). On a live order the
    snapshot is the earliest order payload we stored. An order first seen after
    a refund has no trustworthy snapshot, and `SnapshotUnavailable` is raised
    rather than recording a fee PayPal's later read may have replaced with the
    reversal amount (P3: never present a figure we cannot stand behind).
  * Every raw payload is retained, by `replay`.

**Sandbox only**: `credentials` refuses a non-sandbox host.

`retrieved_at` of the resulting rows is when *we* fetched, as everywhere
(`window.payload_times`); it is never a PayPal timestamp.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import RawPayload
from reckonai.paypal.capture import _assertion, _settings_or_none

DEFAULT_TIMEOUT = 20.0


class LiveFetchError(RuntimeError):
    """A live read did not produce a usable document."""


class LiveUnavailable(LiveFetchError):
    """PayPal could not be reached or answered `5xx`. Not retried here."""


class LiveDenied(LiveFetchError):
    """No candidate seller was permitted to read the order.

    A permissions fact about *us*, never a fact about the order.
    """


class SnapshotUnavailable(LiveFetchError):
    """The order was first seen after a refund, so no capture-time fee exists."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    secret: str
    base: str
    bn: str


def credentials() -> Credentials:
    """The sandbox credentials from settings, or `LiveFetchError`.

    `capture._settings_or_none` already refuses a non-sandbox host.
    """
    resolved = _settings_or_none()
    if resolved is None:
        raise LiveFetchError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
    client_id, secret, base, bn = resolved
    return Credentials(client_id=client_id, secret=secret, base=base, bn=bn)


class LiveClient:
    """Reads orders as a connected seller. Holds one bearer token."""

    def __init__(
        self, creds: Credentials, *, http: httpx.Client | None = None
    ) -> None:
        self._creds = creds
        self._owns_http = http is None
        self._http = http or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._bearer: str | None = None

    def close(self) -> None:
        """Release the connection pool, if this client made it."""
        if self._owns_http:
            self._http.close()

    def _token(self) -> str:
        if self._bearer is not None:
            return self._bearer
        import base64

        basic = base64.b64encode(
            f"{self._creds.client_id}:{self._creds.secret}".encode()
        ).decode()
        try:
            response = self._http.post(
                f"{self._creds.base}/v1/oauth2/token",
                headers={"Authorization": f"Basic {basic}"},
                data={"grant_type": "client_credentials"},
            )
        except httpx.HTTPError as exc:
            raise LiveUnavailable(
                f"token request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 500:
            raise LiveUnavailable(f"token request returned {response.status_code}")
        if response.status_code != 200:
            raise LiveFetchError(f"token request returned {response.status_code}")
        self._bearer = str(response.json()["access_token"])
        return self._bearer

    def order_as(self, order_id: str, merchant_id: str) -> dict[str, Any] | None:
        """The order as `merchant_id` reads it, or `None` if that seller may not.

        `None` means only that this seller was refused (`403` or `404`); the
        caller decides what a refusal by every candidate means.
        """
        try:
            response = self._http.get(
                f"{self._creds.base}/v2/checkout/orders/{order_id}",
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "PayPal-Partner-Attribution-Id": self._creds.bn,
                    "PayPal-Auth-Assertion": _assertion(
                        self._creds.client_id, merchant_id
                    ),
                },
            )
        except httpx.HTTPError as exc:
            raise LiveUnavailable(f"order read failed: {type(exc).__name__}") from exc
        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict):
                raise LiveFetchError("order read returned a non-object body")
            return body
        if response.status_code in (403, 404):
            return None
        if response.status_code >= 500:
            raise LiveUnavailable(f"order read returned {response.status_code}")
        raise LiveFetchError(f"order read returned {response.status_code}")

    def events(
        self, since: datetime, until: datetime, *, max_pages: int = 20
    ) -> list[dict[str, Any]]:
        """Webhook events PayPal recorded in a window, for missed-delivery recovery.

        Read with the platform credential. A refusal is `LiveDenied` and never
        an empty list: "we may not list events" and "there were none" are
        different claims, and recovery treating the first as the second would
        report a clean sweep it did not make.
        """
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        url: str | None = (
            f"{self._creds.base}/v1/notifications/webhooks-events?page_size=100"
            f"&start_time={since.strftime(fmt)}&end_time={until.strftime(fmt)}"
        )
        found: list[dict[str, Any]] = []
        pages = 0
        while url is not None and pages < max_pages:
            pages += 1
            try:
                response = self._http.get(
                    url, headers={"Authorization": f"Bearer {self._token()}"}
                )
            except httpx.HTTPError as exc:
                raise LiveUnavailable(
                    f"events read failed: {type(exc).__name__}"
                ) from exc
            if response.status_code in (401, 403):
                raise LiveDenied("the platform credential may not list webhook events")
            if response.status_code >= 500:
                raise LiveUnavailable(f"events read returned {response.status_code}")
            if response.status_code != 200:
                raise LiveFetchError(f"events read returned {response.status_code}")
            body = response.json()
            found.extend(e for e in body.get("events", []) if isinstance(e, dict))
            url = next(
                (
                    link["href"]
                    for link in body.get("links", [])
                    if link.get("rel") == "next"
                    # Only ever follow a link on the host we were already talking to.
                    and str(link.get("href", "")).startswith(self._creds.base)
                ),
                None,
            )
        return found

    def fetch_order(
        self, order_id: str, merchant_ids: list[str]
    ) -> tuple[dict[str, Any], str]:
        """Try each candidate seller; return the order and who could read it."""
        for merchant_id in merchant_ids:
            order = self.order_as(order_id, merchant_id)
            if order is not None:
                return order, merchant_id
        raise LiveDenied(
            f"none of {len(merchant_ids)} candidate sellers could read order "
            f"{order_id}; this describes our permissions, not the order"
        )


def _has_refunds(order: dict[str, Any]) -> bool:
    return any(
        unit.get("payments", {}).get("refunds")
        for unit in order.get("purchase_units", [])
    )


def _stored_snapshot(
    session: Session, tenant_id: uuid.UUID, order_id: str
) -> dict[str, Any] | None:
    """The earliest order payload we stored: the nearest thing to capture time."""
    row = session.scalars(
        select(RawPayload)
        .where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source == "paypal",
            RawPayload.source_record_type == "order",
            RawPayload.source_record_id == order_id,
        )
        .order_by(RawPayload.retrieved_at.asc())
        .limit(1)
    ).first()
    return dict(row.body) if row is not None else None


def build_document(
    session: Session,
    tenant_id: uuid.UUID,
    order: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The document `replay.ingest_document` reads, for a freshly fetched order.

    Raises `SnapshotUnavailable` when the only snapshot available is one taken
    after a refund (rule 1).
    """
    snapshot = _stored_snapshot(session, tenant_id, str(order["id"])) or order
    if _has_refunds(snapshot):
        raise SnapshotUnavailable(
            f"order {order['id']} was first seen after a refund; its capture-time "
            "platform fee cannot be established, so it is not ingested"
        )

    units = order.get("purchase_units") or [{}]
    mode = (units[0].get("payment_instruction") or {}).get("disbursement_mode")
    fetched = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "order_ref": order["id"],
        "note": "retrieved live",
        "disbursement_mode": mode,
        "captured_at": fetched,
        "endpoint": f"GET /v2/checkout/orders/{order['id']}",
        "order_at_capture": snapshot,
        "order": order,
        # No event announces a DELAYED release (see validation.md section 3),
        # so none is fetched. A released live DELAYED order would read as held.
        "releases": [],
    }
