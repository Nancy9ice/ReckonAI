#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28"]
# ///
"""Disposable PayPal sandbox probe harness - Phase 0, task group 1.

Captures what the PayPal sandbox ACTUALLY returns, so the observed column of
specs/paypal-fields.md is machine-produced and re-runnable rather than
hand-transcribed.

This is deliberately a flat script and NOT the PayPal client of Phase 7. There
is no client class, no retry layer and no pagination abstraction, by
requirements.md section 4 and D2. It imports no PayPal SDK: the probe records
what the wire carried, and a deserializer between the response and the artifact
would be an interpreter standing between us and the evidence.

Money: every amount PayPal sends is a JSON string. Parsing uses
parse_float=Decimal so that no float can exist anywhere in the parsed payload
even if PayPal sends a bare number, and the output is checked for floats before
it is written. See specs/tech-stack.md section 2.

Credentials are read from the environment, falling back to a local .env file.
No credential value is ever printed, logged, or written to the output.

Output is UNREDACTED raw payload. Task group 3 redacts identifiers and buyer
PII before committing; amounts, currencies, rates, fees and timestamps are
evidence and are kept.

Usage:
    uv run scripts/probe_paypal.py --help
    uv run scripts/probe_paypal.py --record-type balances --out probe.raw.json
    uv run scripts/probe_paypal.py --record-type captures --id 3C679366HH908993F \\
        --out probe.raw.json
    uv run scripts/probe_paypal.py --record-type all --out probe.raw.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
TOKEN_PATH = "/v1/oauth2/token"

# Record type -> (path template, needs an id, a note for the artifact).
# Paths carry their own API version because PayPal versions per resource and
# returns no API version header. See notes.md, "Group 0 step 4".
RECORD_TYPES: dict[str, tuple[str, bool, str]] = {
    "orders": ("/v2/checkout/orders/{id}", True, "purchase units, payer, links"),
    "captures": (
        "/v2/payments/captures/{id}",
        True,
        "seller_receivable_breakdown - the FX and settlement evidence",
    ),
    "refunds": ("/v2/payments/refunds/{id}", True, "refund linkage and partial shape"),
    "authorizations": ("/v2/payments/authorizations/{id}", True, "pre-capture state"),
    "disputes": ("/v1/customer/disputes", False, "dispute list, lifecycle states"),
    "dispute": ("/v1/customer/disputes/{id}", True, "single dispute detail"),
    "payouts": ("/v1/payments/payouts/{id}", True, "batch header and items"),
    "transactions": (
        "/v1/reporting/transactions",
        False,
        "transaction search, fee breakdown",
    ),
    "balances": ("/v1/reporting/balances", False, "settlement currency and balances"),
}

# Record types that take no id and so can be probed without task group 2's
# fixtures existing yet.
LISTABLE = [name for name, (_, needs_id, _n) in RECORD_TYPES.items() if not needs_id]


class MoneyError(Exception):
    """A monetary value PayPal sent could not be parsed as a Decimal."""


# Two credential pairs now exist, because the product moved to PayPal's
# multiparty model (specs/multiparty-direction.md) while Phase 0's fixtures
# stayed in the original merchant account.
#
#   platform  the partner app. Carries the partner scopes, and is what every
#             multiparty call needs. Reads another account's records only with
#             a PayPal-Auth-Assertion for that seller.
#   merchant  the original app, whose account owns Phase 0's fixtures. Without
#             it, validation.md C2 cannot re-probe them at all: they answer
#             401 to the platform.
PROFILES = {
    "platform": ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"),
    "merchant": ("PAYPAL_MERCHANT_CLIENT_ID", "PAYPAL_MERCHANT_CLIENT_SECRET"),
}


def read_env_file() -> dict[str, str]:
    """Every PAYPAL_ value from the local file, environment taking precedence."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[len("export ") :]
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


def load_credentials(profile: str = "platform") -> dict[str, str]:
    """Read one credential profile, falling back to the local file.

    Values are returned for use and never logged. The file fallback exists
    because requirements.md section 5 prerequisite 3 wants the credentials
    reachable by a local script; whether they arrive via the shell or a
    gitignored file is immaterial to that.
    """
    if profile not in PROFILES:
        sys.exit(f"error: unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    id_key, secret_key = PROFILES[profile]
    values = read_env_file()

    client_id = values.get(id_key, "")
    secret = values.get(secret_key, "")
    missing = [k for k, v in ((id_key, client_id), (secret_key, secret)) if not v]
    if missing:
        sys.exit(
            f"error: {', '.join(missing)} not set, needed for the {profile} profile.\n"
            f"Set them in the environment or in the local env file (see .env.example)."
        )
    if client_id.startswith("replace-with"):
        sys.exit(f"error: {id_key} is still the .env.example placeholder.")

    base = (values.get("PAYPAL_API_BASE") or "https://api-m.sandbox.paypal.com").rstrip("/")
    if "sandbox" not in base:
        sys.exit(
            f"error: refusing to run against a non-sandbox host: {base}\n"
            "This phase is sandbox only (requirements.md section 3)."
        )
    return {
        "PAYPAL_CLIENT_ID": client_id,
        "PAYPAL_CLIENT_SECRET": secret,
        "PAYPAL_API_BASE": base,
        "PAYPAL_BN_CODE": values.get("PAYPAL_BN_CODE", ""),
        "PAYPAL_SELLER_MERCHANT_ID": values.get("PAYPAL_SELLER_MERCHANT_ID", ""),
        "profile": profile,
    }


def auth_assertion(client_id: str, payer_id: str) -> str:
    """The PayPal-Auth-Assertion header value for acting as a seller.

    An unsigned JWT: alg none, the platform client id as iss, the seller's
    payer id in the payload, and a trailing period. Observed 2026-11-07 to be
    enforced rather than decorative - a bogus payer id answers 401.
    """

    def segment(obj: dict[str, str]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{segment({'alg': 'none'})}.{segment({'iss': client_id, 'payer_id': payer_id})}."


def get_token(client: httpx.Client, base: str, cid: str, secret: str) -> str:
    """Acquire a bearer token. The token is returned, never logged."""
    resp = client.post(
        base + TOKEN_PATH,
        auth=(cid, secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
    )
    if resp.status_code != 200:
        # resp.text may name the failure but never contains our secret.
        sys.exit(f"error: token request returned HTTP {resp.status_code}: {resp.text[:200]}")
    payload = json.loads(resp.text, parse_float=Decimal)
    token = payload.get("access_token")
    if not token:
        sys.exit("error: token response contained no access_token")
    return token


def default_params(record_type: str) -> dict[str, str]:
    """Query parameters the listable endpoints require."""
    if record_type == "transactions":
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=28)  # PayPal caps the window at 31 days
        return {
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%S-0000"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M:%S-0000"),
            "fields": "all",
            "page_size": "100",
        }
    if record_type == "disputes":
        return {"page_size": "50"}
    return {}


# PayPal is not consistent about the money envelope between its APIs:
#   Orders v2 and Payments v2 use {"currency_code": str, "value": str}
#   Payouts v1 uses            {"currency": str,      "value": str}
# Both must be recognised or the observed column silently under-reports. This
# was found by probing a real payout batch whose amounts were missed entirely.
CURRENCY_KEYS = ("currency_code", "currency")


def walk_money(node: Any, path: str = "$") -> list[dict[str, str]]:
    """Find every PayPal money object and parse its value as a Decimal.

    Parsing each one proves the amount is Decimal-representable and surfaces
    anything that is not, rather than discovering it in Phase 7. Every amount
    is recorded with its currency, never alone.
    """
    out: list[dict[str, str]] = []
    if isinstance(node, dict):
        currency_key = next((k for k in CURRENCY_KEYS if k in node), None)
        if "value" in node and currency_key:
            raw = node["value"]
            try:
                amount = Decimal(str(raw))
            except (InvalidOperation, ValueError) as exc:
                raise MoneyError(f"{path}: cannot parse {raw!r} as Decimal") from exc
            out.append(
                {
                    "path": path,
                    "currency": str(node[currency_key]),
                    "currency_key": currency_key,
                    # str() of a Decimal, so the artifact carries no float.
                    "amount": str(amount),
                    "raw_json_type": type(raw).__name__,
                }
            )
        for key, value in node.items():
            out.extend(walk_money(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(walk_money(value, f"{path}[{index}]"))
    return out


def find_floats(node: Any, path: str = "$") -> list[str]:
    """Every path holding a float. Must be empty before anything is written."""
    hits: list[str] = []
    if isinstance(node, float):
        hits.append(path)
    elif isinstance(node, dict):
        for key, value in node.items():
            hits.extend(find_floats(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_floats(value, f"{path}[{index}]"))
    return hits


def probe(
    client: httpx.Client,
    base: str,
    token: str,
    record_type: str,
    record_id: str | None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch one record type and return the raw payload plus provenance."""
    template, needs_id, note = RECORD_TYPES[record_type]
    if needs_id and not record_id:
        return {
            "record_type": record_type,
            "status": "skipped",
            "reason": "needs --id; task group 2 creates the fixture",
            "note": note,
        }

    path = template.format(id=record_id) if needs_id else template
    supplied = dict(extra_headers or {})
    # Keys prefixed with _ are provenance for the artifact, not HTTP headers.
    read_as = supplied.pop("_read_as", "self")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    headers.update({k: v for k, v in supplied.items() if not k.startswith("_")})
    resp = client.get(base + path, headers=headers, params=default_params(record_type))

    result: dict[str, Any] = {
        "record_type": record_type,
        "note": note,
        "request_path": path,
        "http_status": resp.status_code,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        # Which identity made the call. A 401 or 403 means nothing without it:
        # the platform reading a seller's record without an assertion is a
        # permissions fact, not a fact about the field.
        "read_as": read_as,
    }

    if resp.status_code == 403:
        # notes.md: a 403 is never evidence about the API, only about our app.
        result["status"] = "not_authorized"
        result["warning"] = (
            "HTTP 403. This says our app lacks a permission, NOT that the "
            "field is absent. Do not derive a verdict from this response."
        )
        return result

    try:
        body = json.loads(resp.text, parse_float=Decimal)
    except json.JSONDecodeError:
        result["status"] = "unparseable"
        result["body_prefix"] = resp.text[:200]
        return result

    result["status"] = "ok" if resp.is_success else "http_error"
    result["body"] = body
    try:
        result["money_values"] = walk_money(body, f"${record_type}")
    except MoneyError as exc:
        result["status"] = "money_parse_error"
        result["money_error"] = str(exc)
    return result


class DecimalEncoder(json.JSONEncoder):
    """Serialize Decimal as a JSON string, never as a float."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="probe_paypal.py",
        description=(
            "Capture what the PayPal sandbox actually returns, for the observed "
            "column of specs/paypal-fields.md. Sandbox only. Reads nothing that "
            "moves money."
        ),
        epilog=(
            "Record types taking no --id and probeable now: "
            + ", ".join(LISTABLE)
            + ". The rest need a fixture id from task group 2."
        ),
    )
    parser.add_argument(
        "--record-type",
        action="append",
        choices=sorted(RECORD_TYPES) + ["all"],
        help="Record type to probe. Repeatable. 'all' probes every type.",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        metavar="RECORD_ID",
        help="Fixture id for an id-bearing record type. Repeatable, paired "
        "positionally with --record-type.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help="Write the JSON artifact here. Without it, a summary goes to stdout.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="platform",
        help="Which credential pair to authenticate with. 'platform' is the "
        "partner app; 'merchant' is the account that owns Phase 0's fixtures, "
        "which answer 401 to the platform without an assertion.",
    )
    parser.add_argument(
        "--as-seller",
        metavar="PAYER_ID",
        help="Read on a seller's behalf via PayPal-Auth-Assertion. Pass the "
        "seller's payer id, or 'env' to use PAYPAL_SELLER_MERCHANT_ID. Only "
        "meaningful with the platform profile.",
    )
    args = parser.parse_args()

    selected = args.record_type or ["all"]
    if "all" in selected:
        selected = sorted(RECORD_TYPES)

    ids: list[str | None] = list(args.id) + [None] * (len(selected) - len(args.id))

    creds = load_credentials(args.profile)
    base = creds["PAYPAL_API_BASE"]

    seller = args.as_seller
    if seller == "env":
        seller = creds["PAYPAL_SELLER_MERCHANT_ID"]
        if not seller:
            sys.exit("error: --as-seller env, but PAYPAL_SELLER_MERCHANT_ID is not set.")
    if seller and args.profile != "platform":
        sys.exit(
            "error: --as-seller needs the platform profile; only a partner app "
            "may assert a seller's identity."
        )

    extra: dict[str, str] = {}
    if creds["PAYPAL_BN_CODE"]:
        extra["PayPal-Partner-Attribution-Id"] = creds["PAYPAL_BN_CODE"]
    if seller:
        extra["PayPal-Auth-Assertion"] = auth_assertion(creds["PAYPAL_CLIENT_ID"], seller)
        extra["_read_as"] = f"seller:{seller}"

    with httpx.Client(timeout=60.0) as client:
        token = get_token(
            client, base, creds["PAYPAL_CLIENT_ID"], creds["PAYPAL_CLIENT_SECRET"]
        )
        headers = {k: v for k, v in extra.items() if not k.startswith("_")}
        results = [
            probe(client, base, token, record_type, record_id, {**headers, **{
                k: v for k, v in extra.items() if k.startswith("_")}})
            for record_type, record_id in zip(selected, ids, strict=False)
        ]

    artifact: dict[str, Any] = {
        "probe": {
            "script": "scripts/probe_paypal.py",
            "phase": "0",
            "task_group": "1",
            "run_at": datetime.now(timezone.utc).isoformat(),
            "api_base": base,
            "profile": args.profile,
            "read_as": f"seller:{seller}" if seller else "self",
            "redacted": False,
            "redaction_note": (
                "Raw payload. Task group 3 redacts identifiers and buyer PII "
                "before committing. Amounts, currencies, rates, fees and "
                "timestamps are evidence and must be kept."
            ),
        },
        "results": results,
    }

    floats = find_floats(artifact)
    if floats:
        print(
            "error: float found in the artifact, which specs/tech-stack.md "
            f"section 2 forbids: {floats[:5]}",
            file=sys.stderr,
        )
        return 2

    text = json.dumps(artifact, indent=2, cls=DecimalEncoder, sort_keys=False)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        for item in results:
            money = len(item.get("money_values") or [])
            print(
                f"{item['record_type']:<16} {item.get('status'):<18} "
                f"http={item.get('http_status', '-')} money_values={money}"
            )

    failures = [r for r in results if r.get("status") == "money_parse_error"]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
