#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28"]
# ///
"""Capture the demo dataset's PayPal side from the sandbox, into fixtures.

The replan's R16. These fixtures replace Block A's synthetic PayPal rows (its
D12) with PayPal's own responses, so nothing in the demo presents our
arithmetic as a provider figure (specs/mission.md P3).

**Every order captured here is correct.** All six seeded faults are
marketplace-side - a refund the ledger never recorded, a commission the
marketplace computed wrong, a settlement it mis-recorded - so the provider
side is the honest counterpart they are measured against. The single
capture-side choice is F5: `ORD-0017` is deliberately **not** refunded here,
so the marketplace's refund genuinely has no provider counterpart yet, which
is R10's `awaiting_evidence` case rather than a discrepancy.

**Two sellers, both US, both settling USD.** EUR-denominated orders therefore
convert, which is what carries the FX scenario. An earlier plan used a German
seller receiving USD; that account's `PPCP_CUSTOM` vetting is
`NEED_MORE_DATA`, so it cannot process cards at all, and the direction was
flipped rather than adding manual browser approvals.

**Card-funded, so this runs unattended.** A PayPal-wallet order needs a buyer
to approve it in a browser; a card order authorises server to server. The
funding instrument is not recorded by the API anyway
(specs/2026-11-07-multiparty-field-discovery/notes.md section 4.5), and
nothing in the canonical model may branch on it.

Sandbox only - the script refuses a non-sandbox host. Nothing production, and
no real money. **Sandbox records cannot be deleted**, so a re-run skips orders
whose fixture already exists unless --force is given.

Usage:
    uv run scripts/capture_paypal.py --out fixtures/paypal
    uv run scripts/capture_paypal.py --out fixtures/paypal --only ORD-0001
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

#: Sandbox test card. `4032035728516179` is rejected; this one and
#: `5555555555554444` are accepted.
CARD = "4111111111111111"

#: Dataset seller -> the env key holding its merchant id.
SELLERS = {
    "vendor-aurora": "PAYPAL_SELLER_MERCHANT_ID",
    "vendor-bellweather": "PAYPAL_SELLER_2_MERCHANT_ID",
}


@dataclass(frozen=True)
class Line:
    seller: str
    gross: str
    fee: str | None


@dataclass(frozen=True)
class Spec:
    ref: str
    currency: str
    lines: tuple[Line, ...]
    mode: str = "INSTANT"
    refund: str | None = None
    release: bool = False
    note: str = ""


A = "vendor-aurora"
B = "vendor-bellweather"


def one(seller: str, gross: str, fee: str) -> tuple[Line, ...]:
    return (Line(seller, gross, fee),)


#: The eighteen orders, exactly as agreed. Every fee is the rule-correct 10%:
#: the faults are injected by the seed builder, not here.
ORDERS: tuple[Spec, ...] = (
    Spec("ORD-0001", "USD",
         (Line(A, "100.00", "10.00"), Line(B, "60.00", "6.00")),
         note="specs/mission.md worked example"),
    Spec("ORD-0002", "USD", one(A, "50.00", "5.00")),
    Spec("ORD-0003", "USD", one(B, "120.00", "12.00")),
    Spec("ORD-0004", "USD", one(A, "80.00", "8.00")),
    Spec("ORD-0005", "EUR", one(B, "200.00", "20.00"), mode="DELAYED",
         note="held funds, never released - a healthy intermediate state"),
    Spec("ORD-0006", "USD",
         (Line(A, "30.00", "3.00"), Line(B, "45.00", "4.50"))),
    Spec("ORD-0007", "USD", one(A, "33.33", "3.33"), note="rounding"),
    # **Two sellers, and the amounts are chosen, not arbitrary.** PayPal
    # converts each capture separately, so a reconciler that converts the
    # order's total once lands a cent away:
    #
    #   per capture: 36.02 -> 39.24  and  73.37 -> 79.93   = 119.17 USD
    #   total once:  109.39 -> 119.18 USD
    #
    # Neither figure is wrong. This is the legitimate artefact Block C must
    # not flag, and specs/roadmap.md Phase 11's hardest criterion is measured
    # against it. A single-seller order cannot produce it, because there is
    # nothing to convert twice.
    Spec("ORD-0008", "EUR",
         (Line(B, "40.02", "4.00"), Line(A, "81.52", "8.15")),
         note="converts twice; the one-cent rounding guard"),
    Spec("ORD-0009", "USD", one(B, "59.97", "6.00"), note="rounding"),
    Spec("ORD-0010", "USD", one(A, "45.00", "4.50")),
    Spec("ORD-0011", "USD", one(A, "70.00", "7.00"), refund="20.00",
         note="clean refund; PayPal reverses 2.00 of the fee automatically"),
    Spec("ORD-0012", "EUR", one(B, "120.00", "12.00")),
    # --- the six faulty orders. Their PayPal side is correct; the seed
    # builder injects the marketplace's error. ---
    Spec("ORD-0013", "USD", one(B, "110.00", "11.00"), refund="30.00",
         note="F1: the refund is real; the ledger will not record it"),
    Spec("ORD-0014", "USD", one(A, "200.00", "20.00"),
         note="F2: 20.00 is rule-correct; the marketplace will claim 30.00"),
    Spec("ORD-0015", "EUR", one(B, "300.00", "30.00"),
         note="F3: the conversion is real; the marketplace mis-records it"),
    Spec("ORD-0016", "USD", one(A, "90.00", "9.00"),
         note="F4: one capture; the ledger will credit it twice"),
    # **No refund call.** That absence is the fault.
    Spec("ORD-0017", "USD", one(A, "60.00", "6.00"),
         note="F5: the marketplace refunds; PayPal's record has not arrived"),
    Spec("ORD-0018", "EUR", one(B, "150.00", "15.00"), mode="DELAYED",
         release=True,
         note="F6: released in full; the marketplace records less"),
)


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


def assertion(client_id: str, payer_id: str) -> str:
    def seg(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'iss': client_id, 'payer_id': payer_id})}."


@dataclass
class Api:
    base: str
    client_id: str
    secret: str
    bn: str
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=60.0))
    bearer: str = ""

    def auth(self) -> None:
        basic = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode()
        r = self.http.post(
            f"{self.base}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials"},
        )
        if r.status_code != 200:
            sys.exit(f"token request returned {r.status_code}")
        self.bearer = r.json()["access_token"]

    def _headers(self, seller: str | None, *, write: bool) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.bearer}",
            "PayPal-Partner-Attribution-Id": self.bn,
        }
        if write:
            h["Content-Type"] = "application/json"
            # **Unique per request.** PayPal treats this as an idempotency
            # key: a repeated value replays the cached first response, which
            # would make every order here a copy of the first.
            h["PayPal-Request-Id"] = uuid.uuid4().hex
        if seller:
            h["PayPal-Auth-Assertion"] = assertion(self.client_id, seller)
        return h

    def post(self, path: str, body: Any, seller: str | None = None) -> httpx.Response:
        return self.http.post(
            f"{self.base}{path}", headers=self._headers(seller, write=True), json=body
        )

    def get(self, path: str, seller: str | None = None) -> httpx.Response:
        return self.http.get(
            f"{self.base}{path}", headers=self._headers(seller, write=False)
        )


def capture_one(api: Api, spec: Spec, ids: dict[str, str]) -> dict[str, Any]:
    """Create, capture, optionally refund and release one order."""
    units = []
    for index, line in enumerate(spec.lines):
        unit: dict[str, Any] = {
            "reference_id": f"{spec.ref}-{index + 1}",
            "amount": {"currency_code": spec.currency, "value": line.gross},
            "payee": {"merchant_id": ids[line.seller]},
            "payment_instruction": {"disbursement_mode": spec.mode},
        }
        if line.fee:
            unit["payment_instruction"]["platform_fees"] = [
                {"amount": {"currency_code": spec.currency, "value": line.fee}}
            ]
        units.append(unit)

    created = api.post(
        "/v2/checkout/orders",
        {
            "intent": "CAPTURE",
            "purchase_units": units,
            "payment_source": {
                "card": {"number": CARD, "expiry": "2030-01", "name": "Test Buyer"}
            },
        },
    )
    if created.status_code not in (200, 201):
        print(f"  FAILED {created.status_code}: {json.dumps(created.json())[:400]}")
        raise SystemExit(f"{spec.ref} could not be created")

    order = created.json()
    order_id = order["id"]
    first_seller = ids[spec.lines[0].seller]
    print(f"  order {order_id}  status {order['status']}")

    for unit in order.get("purchase_units", []):
        for cap in unit.get("payments", {}).get("captures", []):
            brk = cap.get("seller_receivable_breakdown", {})
            rate = (brk.get("exchange_rate") or {}).get("value")
            extra = f"  rate {rate}" if rate else ""
            print(f"    capture {cap['id']}  {cap['amount']['value']} "
                  f"{cap['amount']['currency_code']}{extra}")

    # **Snapshot the capture before any refund touches it.**
    # A capture's `platform_fees` is not stable across a partial refund:
    # ORD-0013 reported its reversal amount (3.00) where ORD-0011 kept its
    # original (7.00), on two structurally identical operations, and both
    # were stable on re-read. So the fee PayPal took is recorded here, at
    # capture time, and the post-refund readback is kept beside it rather
    # than instead of it. Same family as section 4.1's disputed capture
    # losing its whole breakdown.
    at_capture = order

    if spec.refund:
        cap_id = order["purchase_units"][0]["payments"]["captures"][0]["id"]
        r = api.post(
            f"/v2/payments/captures/{cap_id}/refund",
            {"amount": {"currency_code": spec.currency, "value": spec.refund}},
            seller=first_seller,
        )
        if r.status_code not in (200, 201):
            print(f"  refund FAILED {r.status_code}: {json.dumps(r.json())[:300]}")
            raise SystemExit(f"{spec.ref} refund failed")
        print(f"    refund {r.json().get('id')}  {spec.refund}")

    releases = []
    if spec.release:
        for unit in order.get("purchase_units", []):
            for cap in unit.get("payments", {}).get("captures", []):
                r = api.post(
                    "/v1/payments/referenced-payouts-items",
                    {"reference_id": cap["id"], "reference_type": "TRANSACTION_ID"},
                )
                if r.status_code not in (200, 201, 202):
                    print(f"  release FAILED {r.status_code}")
                    raise SystemExit(f"{spec.ref} release failed")
                releases.append(r.json())
                print(f"    release {r.json().get('payout_transaction_id')}")

    # **Read the order back, as the seller.** The refund endpoint omits
    # `seller_payable_breakdown`; the order carries it. Same read-path rule
    # specs/roadmap.md Phase 7 inherited, confirmed here.
    time.sleep(1.0)
    readback = api.get(f"/v2/checkout/orders/{order_id}", seller=first_seller)
    if readback.status_code != 200:
        print(f"  readback FAILED {readback.status_code}")
        raise SystemExit(f"{spec.ref} readback failed")

    return {
        "order_ref": spec.ref,
        "note": spec.note,
        "disbursement_mode": spec.mode,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": f"GET /v2/checkout/orders/{order_id}",
        # What PayPal returned at capture, before any refund. Authoritative
        # for the platform fee actually taken.
        "order_at_capture": at_capture,
        # The same order read back afterwards: carries the refund and its
        # `seller_payable_breakdown`, which the refund endpoint itself omits.
        "order": readback.json(),
        "releases": releases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture the demo dataset.")
    parser.add_argument("--out", default="fixtures/paypal")
    parser.add_argument("--only", action="append", help="capture only these refs")
    parser.add_argument("--force", action="store_true", help="overwrite fixtures")
    args = parser.parse_args()

    values = read_env()
    base = (
        values.get("PAYPAL_API_BASE") or "https://api-m.sandbox.paypal.com"
    ).rstrip("/")
    if "sandbox" not in base:
        sys.exit(f"refusing a non-sandbox host: {base}")

    ids = {}
    for seller, key in SELLERS.items():
        merchant = values.get(key, "")
        if not merchant:
            sys.exit(f"{key} is not set; see .env.example")
        ids[seller] = merchant

    api = Api(
        base=base,
        client_id=values.get("PAYPAL_CLIENT_ID", ""),
        secret=values.get("PAYPAL_CLIENT_SECRET", ""),
        bn=values.get("PAYPAL_BN_CODE", ""),
    )
    if not api.client_id or not api.secret:
        sys.exit("platform credentials not set; see .env.example")
    api.auth()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    wanted = set(args.only) if args.only else None
    for spec in ORDERS:
        if wanted and spec.ref not in wanted:
            continue
        path = out / f"{spec.ref}.json"
        if path.exists() and not args.force:
            print(f"{spec.ref}: fixture exists, skipping")
            continue
        print(f"{spec.ref} ({spec.currency}, {spec.mode}) {spec.note}")
        record = capture_one(api, spec, ids)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"  -> {path.relative_to(ROOT)}")

    print()
    print(f"fixtures in {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
