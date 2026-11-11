#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28"]
# ///
"""Read-only sandbox probe for the live-ingestion verification spike.

specs/2026-11-11-live-ingestion/plan.md task 0. Answers, from the wire and not
from documentation, what the platform credential can see of PayPal's webhook
surface: the event types it can subscribe to, the webhooks already registered,
whether the events-list endpoint is readable (the basis of missed-delivery
recovery), and how the signature-verification endpoint answers.

Read-only by construction: GETs, plus one POST to the verification endpoint
with a deliberately invalid body, which changes no state. It registers no
webhook and simulates no event; both are outward-facing and held for the
operator.

Credentials are loaded by `capture_paypal.read_env` from the local settings
file and the environment. No value is printed or written. Sandbox only.

Usage:
    uv run scripts/probe_webhooks.py --out probe_webhooks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_paypal import Api, read_env  # noqa: E402

SANDBOX = "https://api-m.sandbox.paypal.com"


def shape(body: Any) -> Any:
    """The structure of a response without its values, for evidence."""
    if isinstance(body, dict):
        return {k: shape(v) for k, v in list(body.items())[:25]}
    if isinstance(body, list):
        return [shape(body[0])] if body else []
    return type(body).__name__


def call(api: Api, label: str, method: str, path: str, body: Any = None) -> dict:
    if method == "GET":
        r = api.get(path)
    else:
        r = api.post(path, body)
    try:
        parsed = r.json()
    except ValueError:
        parsed = None
    return {
        "label": label,
        "request": f"{method} {path}",
        "status": r.status_code,
        "shape": shape(parsed),
        "body": parsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    values = read_env()
    base = values.get("PAYPAL_API_BASE") or SANDBOX
    if base != SANDBOX:
        sys.exit("refusing a non-sandbox base URL")

    api = Api(
        base=base,
        client_id=values.get("PAYPAL_CLIENT_ID", ""),
        secret=values.get("PAYPAL_CLIENT_SECRET", ""),
        bn=values.get("PAYPAL_BN_CODE", ""),
    )
    if not api.client_id or not api.secret:
        sys.exit("sandbox credentials are not configured")
    api.auth()

    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    results = [
        call(api, "event types", "GET", "/v1/notifications/webhooks-event-types"),
        call(api, "registered webhooks", "GET", "/v1/notifications/webhooks"),
        call(
            api,
            "events list, 30 days",
            "GET",
            f"/v1/notifications/webhooks-events?page_size=10"
            f"&start_time={start.strftime(fmt)}&end_time={end.strftime(fmt)}",
        ),
        call(
            api,
            "verify, invalid body",
            "POST",
            "/v1/notifications/verify-webhook-signature",
            {
                "auth_algo": "SHA256withRSA",
                "cert_url": "https://api-m.sandbox.paypal.com/v1/notifications/certs/x",
                "transmission_id": "00000000-0000-0000-0000-000000000000",
                "transmission_sig": "invalid",
                "transmission_time": end.strftime(fmt),
                "webhook_id": "0AA00000AA000000A",
                "webhook_event": {"id": "WH-PROBE", "event_type": "PROBE"},
            },
        ),
    ]

    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    for r in results:
        print(f"{r['label']:24} {r['request'][:60]:60} -> {r['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
