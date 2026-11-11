"""Create one live sandbox order, in the shape the replay ingester reads.

The replan's R15, built toward the live-order variant the operator chose: the
demo's opening call is a real PayPal order that then flows through ingestion
to the dashboard, with the captured fixtures as the fallback when it fails.

**Nothing may depend on this succeeding.** R6's reasoning stands - sandbox
availability, the reporting lag, and the `5xx` that specs/tech-stack.md
section 8 calls "not reliably transient" - so `try_live_order` returns `None`
on any failure rather than raising, and the caller carries on with the seeded
data. A demo that dies because PayPal hiccupped is worse than one that never
called it.

**Sandbox only.** The client refuses a non-sandbox base URL, and
specs/mission.md P6 is unaffected: creating a sandbox order moves no real
money, and nothing here releases funds, refunds, or alters a balance.

The document returned matches `scripts/capture_paypal.py`'s fixture shape
exactly, so `reckonai.paypal.replay.ingest_document` reads a live order and a
replayed one through one code path rather than two.
"""

from __future__ import annotations

import base64
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx

from reckonai.config import get_settings

logger = logging.getLogger("reckonai.paypal.capture")

#: PayPal's sandbox test card. A card-funded order authorises server to
#: server, where a wallet order needs a buyer to approve it in a browser -
#: which is what makes an unattended live call possible at all.
TEST_CARD = "4111111111111111"

DEFAULT_TIMEOUT = 20.0

#: The two modes PayPal's Complete Payments Platform offers. `DELAYED` holds the
#: seller's funds until a release; the capture still reads COMPLETED.
DISBURSEMENT_MODES = ("INSTANT", "DELAYED")


class CaptureUnavailable(RuntimeError):
    """The live call could not be made or did not complete."""


@dataclass(frozen=True)
class LiveOrder:
    """One live order, plus the document to hand the replay ingester."""

    order_id: str
    captures: tuple[str, ...]
    document: dict[str, Any]


def _settings_or_none() -> tuple[str, str, str, str] | None:
    """Credentials and base URL, or `None` when the demo call is not set up.

    Absent credentials are an ordinary condition, not an error: the deployed
    service does not need PayPal to serve the dashboard, and R6 means the
    reconciliation never calls it. So this returns `None` and the caller
    skips the live call.
    """
    settings = get_settings()
    client_id = getattr(settings, "paypal_client_id", None)
    secret = getattr(settings, "paypal_client_secret", None)
    if not client_id or not secret:
        return None
    base = (getattr(settings, "paypal_api_base", None)
            or "https://api-m.sandbox.paypal.com").rstrip("/")
    if "sandbox" not in base:
        raise CaptureUnavailable(
            f"refusing a non-sandbox host: {base}. Nothing in this project "
            "touches a live PayPal account."
        )
    bn = getattr(settings, "paypal_bn_code", None) or ""
    return (
        client_id.get_secret_value() if hasattr(client_id, "get_secret_value")
        else str(client_id),
        secret.get_secret_value() if hasattr(secret, "get_secret_value")
        else str(secret),
        base,
        bn,
    )


def capture_live_order(
    *,
    payees: list[tuple[str, str, str]],
    currency: str = "USD",
    order_ref: str | None = None,
    disbursement_mode: str = "INSTANT",
    timeout: float = DEFAULT_TIMEOUT,
) -> LiveOrder:
    """Create and capture one order. Raises on any failure.

    `payees` is `(merchant_id, gross, platform_fee)` per purchase unit - more
    than one makes it a genuine multi-seller order, which is the shape the
    whole product is about.

    Use `try_live_order` unless you want the exception.
    """
    if disbursement_mode not in DISBURSEMENT_MODES:
        raise CaptureUnavailable(f"unknown disbursement mode {disbursement_mode!r}")
    resolved = _settings_or_none()
    if resolved is None:
        raise CaptureUnavailable(
            "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set"
        )
    client_id, secret, base, bn = resolved
    ref = order_ref or f"LIVE-{uuid.uuid4().hex[:8].upper()}"
    # Random when the caller names no order; fixed per named order. For a named
    # order, a retry after a timeout must return the order already created, not
    # make a second one matching the same marketplace reference.
    request_id = f"reckonai-{ref}"[:100] if order_ref else uuid.uuid4().hex

    with httpx.Client(timeout=timeout) as http:
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        auth = http.post(
            f"{base}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials"},
        )
        if auth.status_code != 200:
            raise CaptureUnavailable(f"token request returned {auth.status_code}")
        bearer = auth.json()["access_token"]

        units = [
            {
                "reference_id": f"{ref}-{index + 1}",
                "amount": {"currency_code": currency, "value": gross},
                "payee": {"merchant_id": merchant},
                "payment_instruction": {
                    "disbursement_mode": disbursement_mode,
                    "platform_fees": [
                        {"amount": {"currency_code": currency, "value": fee}}
                    ],
                },
            }
            for index, (merchant, gross, fee) in enumerate(payees)
        ]

        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "PayPal-Partner-Attribution-Id": bn,
            # **Unique per request.** PayPal treats this as an idempotency
            # key, and a repeated value replays the cached first response -
            # which during a demo would silently show the same order twice.
            # (A caller-named order is the exception: see `request_id`.)
            "PayPal-Request-Id": request_id,
        }
        created = http.post(
            f"{base}/v2/checkout/orders",
            headers=headers,
            json={
                "intent": "CAPTURE",
                "purchase_units": units,
                "payment_source": {
                    "card": {
                        "number": TEST_CARD,
                        "expiry": "2030-01",
                        "name": "Test Buyer",
                    }
                },
            },
        )
        if created.status_code not in (200, 201):
            raise CaptureUnavailable(
                f"order create returned {created.status_code}"
            )

        order = created.json()
        if order.get("status") != "COMPLETED":
            raise CaptureUnavailable(
                f"order did not capture outright: {order.get('status')}"
            )

        order_id = order["id"]
        first = payees[0][0]
        # The order exists now, so a slow read must not abandon it: try the
        # readback three times before giving up, and name the order if it fails.
        readback = None
        for attempt in range(3):
            try:
                readback = http.get(
                    f"{base}/v2/checkout/orders/{order_id}",
                    headers={
                        "Authorization": f"Bearer {bearer}",
                        "PayPal-Partner-Attribution-Id": bn,
                        "PayPal-Auth-Assertion": _assertion(client_id, first),
                    },
                )
                break
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise CaptureUnavailable(
                        f"order {order_id} was created but could not be read "
                        f"back: {type(exc).__name__}"
                    ) from exc
                time.sleep(1.0)
        assert readback is not None
        # The readback carries the payee, which the creation response omits -
        # and the payee is the join the reconciliation rests on.
        final = readback.json() if readback.status_code == 200 else order

    captures = tuple(
        cap["id"]
        for unit in final.get("purchase_units", [])
        for cap in unit.get("payments", {}).get("captures", [])
    )
    return LiveOrder(
        order_id=order_id,
        captures=captures,
        document={
            "order_ref": ref,
            "note": "created live during the demo",
            "disbursement_mode": disbursement_mode,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "endpoint": f"GET /v2/checkout/orders/{order_id}",
            # Authoritative for the platform fee: a later read is unreliable
            # once a refund touches the capture.
            "order_at_capture": order,
            "order": final,
            "releases": [],
        },
    )


def refund_capture(
    *,
    capture_id: str,
    merchant_id: str,
    currency: str,
    amount: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Refund part of a sandbox capture, as the seller. Raises on any failure.

    Sandbox only (the credentials resolver refuses any other host). Creating a
    sandbox refund moves no real money, and this is a test-data generator for
    the simulated marketplace's write side, not a path ReckonAI uses on its own
    authority (P6).
    """
    resolved = _settings_or_none()
    if resolved is None:
        raise CaptureUnavailable("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
    client_id, secret, base, bn = resolved

    with httpx.Client(timeout=timeout) as http:
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        auth = http.post(
            f"{base}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials"},
        )
        if auth.status_code != 200:
            raise CaptureUnavailable(f"token request returned {auth.status_code}")
        response = http.post(
            f"{base}/v2/payments/captures/{capture_id}/refund",
            headers={
                "Authorization": f"Bearer {auth.json()['access_token']}",
                "Content-Type": "application/json",
                "PayPal-Partner-Attribution-Id": bn,
                "PayPal-Request-Id": uuid.uuid4().hex,
                "PayPal-Auth-Assertion": _assertion(client_id, merchant_id),
            },
            json={"amount": {"currency_code": currency, "value": amount}},
        )
        if response.status_code not in (200, 201):
            raise CaptureUnavailable(f"refund returned {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise CaptureUnavailable("refund returned a non-object body")
        return body


def _assertion(client_id: str, payer_id: str) -> str:
    """The PayPal-Auth-Assertion header for reading as a seller.

    An unsigned JWT - `alg: none` - with the platform client id as `iss` and
    the seller as `payer_id` (specs/multiparty-direction.md section 8.1).
    """
    import json

    def seg(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'iss': client_id, 'payer_id': payer_id})}."


def try_live_order(**kwargs: Any) -> LiveOrder | None:
    """`capture_live_order`, swallowing every failure.

    **This is the function the demo calls**, and the swallowing is the point:
    R15 says the demo proceeds "whether or not that call succeeded". A
    failure is logged and the caller falls back to the seeded fixtures, so a
    sandbox outage costs a fifteen-second flourish rather than the demo.
    """
    try:
        return capture_live_order(**kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberate: nothing may escape
        logger.warning(
            "live PayPal call failed, continuing from fixtures: %s: %s",
            type(exc).__name__,
            exc,
        )
        return None
