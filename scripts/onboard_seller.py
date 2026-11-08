#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28"]
# ///
"""Mint a seller onboarding referral, and verify what it granted.

Added 2026-11-08 for the replan's R16, which moved the PayPal capture ahead of
Block B. The capture needs a multi-seller order, and PayPal puts exactly one
payee on a purchase unit - so it needs more than one seller onboarded under the
platform.

**A plain sandbox business account will not do.** Capabilities are granted per
seller at onboarding, and the simpler onboarding flow excludes Platform Fee and
Delayed Disbursement outright (specs/multiparty-direction.md section 8.1). An
account that was never onboarded under the platform produces
`422 PLATFORM_FEES_NOT_SUPPORTED` on an order carrying a platform fee.

**There is no static onboarding URL.** `action_url` comes back from the
referral, is built per seller, and is **single use** (section 8.4).

Three things section 8.4 recorded that this script encodes so they are not
rediscovered:

  - `products` must be `["PPCP"]`. A referral requesting `EXPRESS_CHECKOUT`
    produced an `action_url` that dead-ends at
    `Cannot GET /unifiedonboarding/entry`.
  - Products cannot be combined: both returns
    `400 INVALID_PARAMETER_SYNTAX`, "Requested multiple products are not
    supported together."
  - **Grants are reported as scope URIs**, under
    `oauth_integrations[].oauth_third_party[].scopes`, not as the feature names
    used in the request. Reading the `capabilities` array alone suggests
    wrongly that nothing was granted - which is why `verify` exists and reads
    the former.

**Onboard a second seller in a private window.** Observed 2026-11-08: the
first onboarding ran in a clean browser and completed; the second, attempted
while already signed in as the first seller, dead-ended at
`Cannot GET /unifiedonboarding/after-login` and created nothing - the
tracking-id lookup returned `404 RESOURCE_NOT_FOUND_ERROR`. Retried in a
private window, the same referral body completed normally.

That is a different failure from section 8.4's `Cannot GET
/unifiedonboarding/entry`, which came from requesting `EXPRESS_CHECKOUT`
instead of `PPCP`. Same shape of error, different cause, and both cost a
single-use token to discover - so: one seller per clean browser session.

This is a flat disposable script, like `probe_paypal.py` beside it, per
`specs/2026-11-05-paypal-field-discovery/requirements.md` section 4 and D2. It
uses `httpx` rather than the `httpx2` that tech-stack section 1 locks, because
these standalone scripts resolve their own dependencies and matching the
sibling probe matters more here than matching the package.

**No credential value is printed, logged or written.** Sandbox only; the script
refuses a non-sandbox host.

Usage:
    uv run scripts/onboard_seller.py create
    uv run scripts/onboard_seller.py create --email seller@example.com
    uv run scripts/onboard_seller.py verify --seller SELLERMERCHANTID
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

#: The six features Phase 0b requested and had granted (section 8.4). Kept as
#: the default rather than widened: `PAYPAL_SELLER_MERCHANT_ID`'s account was
#: onboarded with exactly these, so a new seller onboarded with the same set is
#: comparable to it.
#:
#: **Dispute scopes are deliberately absent.** Section 4.7 records that the
#: platform cannot read a seller's disputes without them and that the seller
#: must be re-onboarded to add them - but the correct feature name is not
#: recorded anywhere observed, and guessing an API constant risks a 400 that
#: blocks onboarding entirely. Add one with --feature once it is known.
DEFAULT_FEATURES = (
    "PAYMENT",
    "REFUND",
    "PARTNER_FEE",
    "DELAY_FUNDS_DISBURSEMENT",
    "ACCESS_MERCHANT_INFORMATION",
    "ADVANCED_TRANSACTIONS_SEARCH",
)

#: Request feature -> granted scope, from section 8.4's table. Used to report
#: `verify` in the caller's own vocabulary rather than PayPal's.
FEATURE_SCOPES = {
    "PARTNER_FEE": "payments/partnerfee",
    "DELAY_FUNDS_DISBURSEMENT": "payments/delay-funds-disbursement",
    "ADVANCED_TRANSACTIONS_SEARCH": "reporting/search/read",
    "ACCESS_MERCHANT_INFORMATION": "customer/merchant-integrations/read",
    "REFUND": "payments/refund",
    "PAYMENT": "payments/payment/authcapture",
}


def read_env_file() -> dict[str, str]:
    """Every PAYPAL_ value from the local file, environment taking precedence.

    The same loader `probe_paypal.py` uses, duplicated rather than imported:
    these are standalone scripts with their own dependency sets, and the
    phase's D2 prefers a flat script to a shared layer.
    """
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


def load_platform() -> dict[str, str]:
    """The platform app's credentials, plus the BN code the referral needs."""
    values = read_env_file()

    client_id = values.get("PAYPAL_CLIENT_ID", "")
    secret = values.get("PAYPAL_CLIENT_SECRET", "")
    bn_code = values.get("PAYPAL_BN_CODE", "")

    missing = [
        name
        for name, value in (
            ("PAYPAL_CLIENT_ID", client_id),
            ("PAYPAL_CLIENT_SECRET", secret),
            # The partner-referrals and referenced-payouts APIs require it
            # (specs/multiparty-direction.md section 8.1).
            ("PAYPAL_BN_CODE", bn_code),
        )
        if not value
    ]
    if missing:
        sys.exit(
            f"error: {', '.join(missing)} not set.\n"
            "Set them in the environment or the local env file (see "
            ".env.example). These must be the PLATFORM app's credentials: a "
            "plain merchant app carries no partner scopes and gets 403 on "
            "partner-referrals."
        )
    if client_id.startswith("replace-with"):
        sys.exit("error: PAYPAL_CLIENT_ID is still the .env.example placeholder.")

    base = (
        values.get("PAYPAL_API_BASE") or "https://api-m.sandbox.paypal.com"
    ).rstrip("/")
    if "sandbox" not in base:
        sys.exit(
            f"error: refusing to run against a non-sandbox host: {base}\n"
            "Onboarding a real seller is not something this project does."
        )

    return {
        "client_id": client_id,
        "secret": secret,
        "bn_code": bn_code,
        "base": base,
        "platform_merchant_id": values.get("PAYPAL_PLATFORM_MERCHANT_ID", ""),
        # Slot 1 is the original pair; PAYPAL_SELLER_1_MERCHANT_ID overrides it
        # if set. See .env.example for why slot 1 keeps the unnumbered names.
        "seller_1": (
            values.get("PAYPAL_SELLER_1_MERCHANT_ID")
            or values.get("PAYPAL_SELLER_MERCHANT_ID", "")
        ),
        "seller_2": values.get("PAYPAL_SELLER_2_MERCHANT_ID", ""),
        "seller_3": values.get("PAYPAL_SELLER_3_MERCHANT_ID", ""),
    }


def token(client: httpx.Client, creds: dict[str, str]) -> str:
    """Mint a client-credentials access token.

    Minted per run rather than read from `PAYPAL_SANDBOX_ACCESS_TOKEN`: that
    variable is for the MCP server and expires within hours, and a stale token
    reports a false negative because a scope added after issue does not appear
    in it (.env.example, "Sandbox app credentials").
    """
    basic = base64.b64encode(
        f"{creds['client_id']}:{creds['secret']}".encode()
    ).decode()
    response = client.post(
        f"{creds['base']}/v1/oauth2/token",
        headers={"Authorization": f"Basic {basic}"},
        data={"grant_type": "client_credentials"},
    )
    if response.status_code != 200:
        sys.exit(
            f"error: token request returned {response.status_code}. "
            "Check the platform credentials; no value is printed here."
        )
    return response.json()["access_token"]


def create(args: argparse.Namespace) -> int:
    """Mint a referral and print its single-use `action_url`."""
    creds = load_platform()
    features = list(args.feature) if args.feature else list(DEFAULT_FEATURES)

    tracking_id = f"reckonai-{uuid.uuid4().hex[:12]}"

    body: dict = {
        "tracking_id": tracking_id,
        "operations": [
            {
                "operation": "API_INTEGRATION",
                "api_integration_preference": {
                    "rest_api_integration": {
                        "integration_method": "PAYPAL",
                        "integration_type": "THIRD_PARTY",
                        "third_party_details": {"features": features},
                    }
                },
            }
        ],
        # Must be PPCP, and must be alone. See the module docstring.
        "products": ["PPCP"],
        "legal_consents": [{"type": "SHARE_DATA_CONSENT", "granted": True}],
    }
    if args.email:
        # Optional: pre-fills the onboarding form. The seller still signs in.
        body["email"] = args.email

    with httpx.Client(timeout=30.0) as client:
        bearer = token(client, creds)
        response = client.post(
            f"{creds['base']}/v2/customer/partner-referrals",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "PayPal-Partner-Attribution-Id": creds["bn_code"],
            },
            json=body,
        )

    if response.status_code not in (200, 201):
        print(f"referral failed: {response.status_code}", file=sys.stderr)
        print(json.dumps(response.json(), indent=2)[:2000], file=sys.stderr)
        return 1

    payload = response.json()
    action = next(
        (
            link["href"]
            for link in payload.get("links", [])
            if link.get("rel") == "action_url"
        ),
        None,
    )
    if not action:
        print("no action_url in the response:", file=sys.stderr)
        print(json.dumps(payload, indent=2)[:2000], file=sys.stderr)
        return 1

    print(f"tracking_id  {tracking_id}")
    print(f"features     {', '.join(features)}")
    print()
    print("action_url (SINGLE USE - a second attempt needs a fresh referral):")
    print(action)
    return 0


def verify(args: argparse.Namespace) -> int:
    """Report what a seller was actually granted.

    Reads `oauth_integrations[].oauth_third_party[].scopes`, **not**
    `capabilities`. Section 8.4: all six features were granted and reported as
    scope URIs, so the `capabilities` array alone suggests wrongly that nothing
    was granted. A verifier reading the wrong field would call a successful
    onboarding a failure.
    """
    creds = load_platform()
    platform = args.platform or creds["platform_merchant_id"]
    if not platform:
        sys.exit(
            "error: the platform's own merchant id is needed to read a "
            "seller's integration status. Pass --platform, or set "
            "PAYPAL_PLATFORM_MERCHANT_ID."
        )

    seller = args.seller
    if args.slot:
        seller = creds.get(f"seller_{args.slot}", "")
        if not seller:
            sys.exit(
                f"error: slot {args.slot} has no merchant id set. Slot 1 reads "
                "PAYPAL_SELLER_MERCHANT_ID (or PAYPAL_SELLER_1_MERCHANT_ID); "
                "slots 2 and 3 read PAYPAL_SELLER_N_MERCHANT_ID."
            )

    if not seller and not args.tracking_id:
        sys.exit("error: pass --seller, --slot or --tracking-id.")

    with httpx.Client(timeout=30.0) as client:
        bearer = token(client, creds)
        headers = {
            "Authorization": f"Bearer {bearer}",
            "PayPal-Partner-Attribution-Id": creds["bn_code"],
        }

        if seller:
            url = (
                f"{creds['base']}/v1/customer/partners/{platform}"
                f"/merchant-integrations/{seller}"
            )
        else:
            # **Lookup by our own tracking_id**, which is how a just-completed
            # onboarding is resolved to a merchant id: the `isuDone` page does
            # not show one, and the sandbox banner claims nothing is saved -
            # so the only trustworthy confirmation that the seller exists is
            # the API finding it.
            url = (
                f"{creds['base']}/v1/customer/partners/{platform}"
                f"/merchant-integrations?tracking_id={args.tracking_id}"
            )

        response = client.get(url, headers=headers)

    if response.status_code != 200:
        print(f"lookup failed: {response.status_code}", file=sys.stderr)
        print(json.dumps(response.json(), indent=2)[:2000], file=sys.stderr)
        return 1

    payload = response.json()

    # **The tracking_id form returns a summary, and scoring it is wrong.**
    # Observed twice on 2026-11-08: it carries `merchant_id` and little else,
    # with no `oauth_integrations` at all - so scoring the grant from it
    # reported all six features as NOT granted on sellers that had all six.
    #
    # A first attempt at this guessed the response wrapped results in a
    # `merchants` collection. It does not, so that branch never ran and the
    # false negative recurred. Hence the unconditional form: when the lookup
    # was by tracking id, **always** re-fetch by the resolved merchant id,
    # whatever shape the summary turns out to have.
    if args.tracking_id and not seller:
        resolved = payload.get("merchant_id")
        if not resolved:
            for item in payload.get("merchants") or []:
                resolved = item.get("merchant_id")
                break
        if not resolved:
            print(
                "no merchant for that tracking_id. The onboarding did not "
                "persist, or it completed under a different referral.",
                file=sys.stderr,
            )
            return 1

        print(f"resolved tracking_id -> merchant_id {resolved}")
        with httpx.Client(timeout=30.0) as client:
            bearer = token(client, creds)
            response = client.get(
                f"{creds['base']}/v1/customer/partners/{platform}"
                f"/merchant-integrations/{resolved}",
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "PayPal-Partner-Attribution-Id": creds["bn_code"],
                },
            )
        if response.status_code != 200:
            print(f"lookup failed: {response.status_code}", file=sys.stderr)
            return 1
        payload = response.json()

    granted: set[str] = set()
    for integration in payload.get("oauth_integrations", []):
        for third_party in integration.get("oauth_third_party", []):
            granted.update(third_party.get("scopes", []))

    print(f"merchant_id             {payload.get('merchant_id', '?')}")
    print(f"payments_receivable     {payload.get('payments_receivable')}")
    print(f"primary_email_confirmed {payload.get('primary_email_confirmed')}")
    # The account's own country and currency decide which slot this seller can
    # fill: a cross-currency order needs a payee whose settlement currency
    # differs from the buyer's, and guessing it would mean a conversion that
    # silently never happens.
    for key in ("country", "primary_currency", "currency_code", "legal_name"):
        if key in payload:
            print(f"{key:<23} {payload[key]}")
    if args.raw:
        print()
        print("full record:")
        print(json.dumps(payload, indent=2)[:4000])
    print()
    # **A record carrying no scope data at all is not a record of six
    # refusals.** Treating it as one is how this script twice called a fully
    # granted seller a failure. A verifier that cannot distinguish "not
    # granted" from "did not ask properly" is worse than no verifier, because
    # it produces a confident wrong answer - which is the same failure mode
    # specs/mission.md P3 forbids of the product itself.
    if not granted:
        print(
            "this record carries no oauth_integrations scopes at all, so it "
            "says nothing about what was granted. Re-query by merchant id, "
            "with --seller or --slot."
        )
        return 1

    print("requested feature             granted scope                        ok")
    missing = []
    for feature, scope in FEATURE_SCOPES.items():
        ok = any(scope in s for s in granted)
        if not ok:
            missing.append(feature)
        print(f"  {feature:<28} {scope:<36} {'yes' if ok else 'NO'}")

    if missing:
        print()
        print(f"NOT granted: {', '.join(missing)}")
        print(
            "A referral that requests a feature is not proof it was granted. "
            "Re-onboard with a fresh referral; the action_url is single use."
        )
        return 1

    print()
    print("all six features granted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mint a PayPal sandbox seller onboarding referral.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("create", help="mint a single-use action_url")
    make.add_argument("--email", help="optional: pre-fill the seller's email")
    make.add_argument(
        "--feature",
        action="append",
        help="override the requested features; repeatable",
    )
    make.set_defaults(func=create)

    check = sub.add_parser("verify", help="report what a seller was granted")
    check.add_argument("--seller", help="the seller's merchant id")
    check.add_argument(
        "--slot",
        type=int,
        choices=(1, 2, 3),
        help=(
            "read the merchant id from the env file instead of passing it, "
            "so the value stays out of shell history and transcripts"
        ),
    )
    check.add_argument(
        "--tracking-id",
        help="the referral's tracking_id, to resolve a just-onboarded seller",
    )
    check.add_argument("--platform", help="the platform's own merchant id")
    check.add_argument(
        "--raw", action="store_true", help="print the whole integration record"
    )
    check.set_defaults(func=verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
