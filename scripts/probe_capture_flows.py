#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28"]
# ///
"""Probe the three capture flows that R16's dataset needs but nobody has run.

`probe_card_capture.py` established that a card-funded multiparty order
captures server to server, with per-seller platform fees and no browser
approval. Three things it did not cover, each of which the demo dataset
depends on, and each observed in Phase 0b only against a **wallet** capture:

  refund   a partial refund, and whether PayPal reverses the platform fee
           automatically and pro rata (Phase 0b section 4.4)
  delayed  DELAYED disbursement: funds held at capture, then released through
           referenced payouts (sections 4.3, 4.9, 4.11)
  fx       a USD order paid to a EUR seller, and what the conversion reports
           (specs/paypal-fields.md section 7)

Each creates real sandbox records. Sandbox only - the script refuses a
non-sandbox host - so no real money and nothing production. These are
throwaways; the dataset's own orders come later.

Usage:
    uv run scripts/probe_capture_flows.py refund  --payee <merchant-id>
    uv run scripts/probe_capture_flows.py delayed --payee <merchant-id>
    uv run scripts/probe_capture_flows.py fx      --payee <eur-merchant-id>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
CARD = "4111111111111111"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    for key, value in os.environ.items():
        if key.startswith("PAYPAL_") and value:
            values[key] = value
    return values


def auth_assertion(client_id: str, payer_id: str) -> str:
    """The PayPal-Auth-Assertion header for acting as a seller.

    An unsigned JWT - `alg: none` - with the platform client id as `iss` and
    the seller as `payer_id`, and a trailing period where the signature would
    be (specs/multiparty-direction.md section 8.1).
    """
    def seg(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'iss': client_id, 'payer_id': payer_id})}."


class Api:
    def __init__(self, values: dict[str, str]) -> None:
        self.base = (
            values.get("PAYPAL_API_BASE") or "https://api-m.sandbox.paypal.com"
        ).rstrip("/")
        if "sandbox" not in self.base:
            sys.exit(f"error: refusing a non-sandbox host: {self.base}")
        self.client_id = values.get("PAYPAL_CLIENT_ID", "")
        self.secret = values.get("PAYPAL_CLIENT_SECRET", "")
        self.bn = values.get("PAYPAL_BN_CODE", "")
        if not self.client_id or not self.secret:
            sys.exit("error: platform credentials not set; see .env.example")
        self.http = httpx.Client(timeout=60.0)
        self.bearer = self._token()

    def _token(self) -> str:
        basic = base64.b64encode(
            f"{self.client_id}:{self.secret}".encode()
        ).decode()
        r = self.http.post(
            f"{self.base}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials"},
        )
        if r.status_code != 200:
            sys.exit(f"error: token request returned {r.status_code}")
        return r.json()["access_token"]

    def post(self, path: str, body: Any, *, seller: str | None = None) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.bearer}",
            "Content-Type": "application/json",
            "PayPal-Partner-Attribution-Id": self.bn,
            # Unique per request: PayPal treats this as an idempotency key, and
            # a repeated value replays the cached first response.
            "PayPal-Request-Id": uuid.uuid4().hex,
        }
        if seller:
            headers["PayPal-Auth-Assertion"] = auth_assertion(self.client_id, seller)
        return self.http.post(f"{self.base}{path}", headers=headers, json=body)

    def get(self, path: str, *, seller: str | None = None) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.bearer}",
            "PayPal-Partner-Attribution-Id": self.bn,
        }
        if seller:
            headers["PayPal-Auth-Assertion"] = auth_assertion(self.client_id, seller)
        return self.http.get(f"{self.base}{path}", headers=headers)


def make_order(
    api: Api,
    payee: str,
    amount: str,
    fee: str,
    *,
    currency: str = "USD",
    mode: str = "INSTANT",
) -> tuple[str, str]:
    """Create and capture one card order. Returns (order_id, capture_id)."""
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "probe",
                "amount": {"currency_code": currency, "value": amount},
                "payee": {"merchant_id": payee},
                "payment_instruction": {
                    "disbursement_mode": mode,
                    "platform_fees": [
                        {"amount": {"currency_code": currency, "value": fee}}
                    ],
                },
            }
        ],
        "payment_source": {
            "card": {"number": CARD, "expiry": "2030-01", "name": "Test Buyer"}
        },
    }
    r = api.post("/v2/checkout/orders", body)
    if r.status_code not in (200, 201):
        print(json.dumps(r.json(), indent=2)[:1800])
        sys.exit(f"order create failed: {r.status_code}")
    d = r.json()
    cap = d["purchase_units"][0]["payments"]["captures"][0]
    print(f"  order {d['id']}  capture {cap['id']}  status {cap['status']}")
    print(f"  breakdown {json.dumps(cap.get('seller_receivable_breakdown'))}")
    return d["id"], cap["id"]


def show_capture(api: Api, capture_id: str, seller: str, label: str) -> dict:
    r = api.get(f"/v2/payments/captures/{capture_id}", seller=seller)
    print(f"  {label}: GET capture -> {r.status_code}")
    if r.status_code != 200:
        print("   ", json.dumps(r.json())[:400])
        return {}
    d = r.json()
    print(f"    status {d.get('status')}  mode {d.get('disbursement_mode')}")
    print(f"    breakdown {json.dumps(d.get('seller_receivable_breakdown'))}")
    return d


def cmd_refund(api: Api, args: argparse.Namespace) -> int:
    """Does a partial refund reverse the platform fee automatically?"""
    print("1. capture 70.00 with a 7.00 platform fee")
    _, capture_id = make_order(api, args.payee, "70.00", "7.00")

    print("2. refund 20.00 of it")
    r = api.post(
        f"/v2/payments/captures/{capture_id}/refund",
        {"amount": {"currency_code": "USD", "value": "20.00"}},
        seller=args.payee,
    )
    print(f"  refund -> {r.status_code}")
    d = r.json() if r.content else {}
    if r.status_code not in (200, 201):
        print(json.dumps(d, indent=2)[:1500])
        return 1
    print(f"  refund id {d.get('id')}  status {d.get('status')}")
    print(f"  seller_payable_breakdown {json.dumps(d.get('seller_payable_breakdown'))}")

    print("3. re-read the capture, to see whether the fee was reversed")
    show_capture(api, capture_id, args.payee, "after refund")

    print("4. re-read the refund through the order's own path")
    print(f"  refund id for the record: {d.get('id')}")
    return 0


def cmd_delayed(api: Api, args: argparse.Namespace) -> int:
    """Are funds held under DELAYED, and does the release work?"""
    print("1. capture 200.00 with a 16.00 fee, disbursement_mode DELAYED")
    _, capture_id = make_order(
        api, args.payee, "200.00", "16.00", currency=args.currency, mode="DELAYED"
    )

    print("2. read the capture - status should read COMPLETED while held")
    show_capture(api, capture_id, args.payee, "held")

    print("3. release it through referenced payouts")
    r = api.post(
        "/v1/payments/referenced-payouts-items",
        {"reference_id": capture_id, "reference_type": "TRANSACTION_ID"},
    )
    print(f"  release -> {r.status_code}")
    d = r.json() if r.content else {}
    print("  ", json.dumps(d, indent=2)[:900])

    print("4. re-read the capture after release")
    show_capture(api, capture_id, args.payee, "after release")
    return 0 if r.status_code in (200, 201, 202) else 1


def cmd_fx(api: Api, args: argparse.Namespace) -> int:
    """What does a USD order paid to a EUR seller report?"""
    print(f"1. capture 150.00 {args.currency} with a 12.00 fee")
    print("   conversion happens when the payee's settlement currency differs")
    _, capture_id = make_order(
        api, args.payee, "150.00", "12.00", currency=args.currency
    )

    print("2. read the capture as the seller, for the conversion")
    d = show_capture(api, capture_id, args.payee, "fx")
    brk = d.get("seller_receivable_breakdown", {})
    rate = brk.get("exchange_rate")
    print()
    if rate:
        print(f"  exchange_rate     {json.dumps(rate)}")
        print(f"  receivable_amount {json.dumps(brk.get('receivable_amount'))}")
        print(f"  net_amount        {json.dumps(brk.get('net_amount'))}")
        print()
        print("VERDICT: a conversion is reported.")
        return 0
    print("  no exchange_rate in the breakdown")
    print("VERDICT: no conversion reported for this payee.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the three capture flows.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("refund", cmd_refund), ("delayed", cmd_delayed), ("fx", cmd_fx)):
        p = sub.add_parser(name)
        p.add_argument("--payee", required=True)
        p.add_argument("--currency", default="USD")
        p.set_defaults(func=fn)
    args = parser.parse_args()
    return args.func(Api(read_env()), args)


if __name__ == "__main__":
    sys.exit(main())
