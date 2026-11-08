#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28"]
# ///
"""Can a multiparty order be captured without a browser approval?

A throwaway feasibility probe, not part of the dataset. It answers one
question and the answer decides how the capture for R16 is built.

**The problem.** A PayPal-wallet order needs the buyer to approve it in a
browser - `specs/2026-11-07-multiparty-field-discovery/fixtures.md` lists the
buyer as "none; approves in a browser", and Phase 0 recorded the same blocker.
Capturing eighteen orders that way means eighteen manual approvals.

**The hypothesis.** An order created with `payment_source.card` is authorised
server to server, with no buyer redirect. Seller `E4FUU2CX86ALC` reports
`CUSTOM_CARD_PROCESSING` and `CARD_PROCESSING_VIRTUAL_TERMINAL` as active
PPCP_CUSTOM capabilities, so the seller side may support it. Whether the
*platform* app may do this on a seller's behalf, with a platform fee attached,
is what this probe settles.

**This creates one real sandbox order.** Sandbox only - the script refuses a
non-sandbox host - so no real money is involved and nothing production is
touched. It is a throwaway: its id is printed, not recorded as a fixture.

**On the card number.** The default is a PayPal sandbox test number. If it is
rejected, pass one generated in your own developer dashboard with --card;
nothing real belongs here and the script refuses a number it can see is not a
test one only insofar as it never runs outside sandbox.

Usage:
    uv run scripts/probe_card_capture.py --payee E4FUU2CX86ALC
    uv run scripts/probe_card_capture.py --payee E4FUU2CX86ALC --card 4032035728516179
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

#: A PayPal sandbox test card. Replace with one from your developer dashboard
#: if this is rejected - test instruments vary by account.
DEFAULT_CARD = "4032035728516179"


def read_env_file() -> dict[str, str]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payee", required=True, help="seller merchant id")
    parser.add_argument(
        "--payee2",
        help=(
            "a second seller, to test a multi-payee order - two purchase "
            "units, one payee each, which is the shape the whole demo rests "
            "on and the one card processing may not support"
        ),
    )
    parser.add_argument("--amount2", default="60.00")
    parser.add_argument("--fee2", default="6.00")
    parser.add_argument("--card", default=DEFAULT_CARD)
    parser.add_argument("--amount", default="10.00")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--fee", default="1.00", help="platform fee")
    args = parser.parse_args()

    values = read_env_file()
    client_id = values.get("PAYPAL_CLIENT_ID", "")
    secret = values.get("PAYPAL_CLIENT_SECRET", "")
    bn_code = values.get("PAYPAL_BN_CODE", "")
    base = (
        values.get("PAYPAL_API_BASE") or "https://api-m.sandbox.paypal.com"
    ).rstrip("/")

    if not client_id or not secret:
        sys.exit("error: platform credentials not set; see .env.example")
    if "sandbox" not in base:
        sys.exit(f"error: refusing a non-sandbox host: {base}")

    with httpx.Client(timeout=45.0) as client:
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        auth = client.post(
            f"{base}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials"},
        )
        if auth.status_code != 200:
            sys.exit(f"error: token request returned {auth.status_code}")
        bearer = auth.json()["access_token"]

        def unit(ref: str, payee: str, amount: str, fee: str) -> dict:
            return {
                "reference_id": ref,
                "amount": {"currency_code": args.currency, "value": amount},
                # The payee is the seller: under multiparty this is the join
                # the whole product rests on.
                "payee": {"merchant_id": payee},
                "payment_instruction": {
                    "disbursement_mode": "INSTANT",
                    "platform_fees": [
                        {
                            "amount": {
                                "currency_code": args.currency,
                                "value": fee,
                            }
                        }
                    ],
                },
            }

        units = [unit("probe-1", args.payee, args.amount, args.fee)]
        if args.payee2:
            units.append(unit("probe-2", args.payee2, args.amount2, args.fee2))

        body = {
            "intent": "CAPTURE",
            "purchase_units": units,
            # The hypothesis: a card source authorises server to server, with
            # no buyer redirect to approve.
            "payment_source": {
                "card": {
                    "number": args.card,
                    "expiry": "2030-01",
                    "name": "Test Buyer",
                }
            },
        }

        created = client.post(
            f"{base}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "PayPal-Partner-Attribution-Id": bn_code,
                # **Unique per order, and this bit matters.** PayPal treats
                # PayPal-Request-Id as an idempotency key: a repeated value
                # replays the cached first response. With it hardcoded, a
                # capture run would silently produce eighteen copies of its
                # first order - observed 2026-11-08, when an order asking for
                # 100.00 came back as the earlier 10.00.
                "PayPal-Request-Id": f"probe-{uuid.uuid4().hex[:16]}",
            },
            json=body,
        )

        print(f"create order: {created.status_code}")
        payload = created.json() if created.content else {}
        if created.status_code not in (200, 201):
            print(json.dumps(payload, indent=2)[:2500])
            print()
            print(
                "VERDICT: the card path is not available as configured. The "
                "capture needs browser approvals, or a smaller dataset."
            )
            return 1

        order_id = payload.get("id")
        status = payload.get("status")
        print(f"  id     {order_id}")
        print(f"  status {status}")

        # A status of COMPLETED here means it captured outright. APPROVED
        # means it is ready to capture with no redirect. Either answers the
        # question affirmatively; PAYER_ACTION_REQUIRED does not.
        if status == "COMPLETED":
            print()
            print("VERDICT: captured outright, no approval needed.")
            print(json.dumps(payload, indent=2))
            return 0

        if status == "PAYER_ACTION_REQUIRED":
            print()
            print(
                "VERDICT: this card requires buyer action (3-D Secure or "
                "similar), so it is not an unattended path as configured."
            )
            return 1

        captured = client.post(
            f"{base}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "PayPal-Partner-Attribution-Id": bn_code,
                "PayPal-Request-Id": f"probe-cap-{uuid.uuid4().hex[:16]}",
            },
            json={},
        )
        print(f"capture: {captured.status_code}")
        cap_payload = captured.json() if captured.content else {}
        print(json.dumps(cap_payload, indent=2)[:2500])
        print()
        if captured.status_code in (200, 201):
            print("VERDICT: captured with no browser approval. Scriptable.")
            return 0
        print("VERDICT: capture failed; see the error above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
