#!/usr/bin/env python3
"""Create your own set of live test transactions on a ReckonAI deployment.

    python3 scripts/judge_scenarios.py --name yourname

Needs only Python 3 (standard library). It asks the app to place six real PayPal
sandbox orders under your name, each shaped to one reconciliation outcome, then
waits for PayPal's side of each to arrive by webhook. Anything that has not
arrived after --wait seconds is fetched directly, and the table says which path
each order took.

Running it again with the same name creates nothing. A different name gets a
new set. Then, in the app: filter the queue by JUDGE-<NAME>, click Run
reconciliation, and Investigate any exception.

    --late-refund   deliver PayPal's side of the S4 refund, so a further run
                    reassesses it.

specs/2026-11-11-judge-scenarios/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://reckonai-api.onrender.com"


def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "reckonai-judge/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        if exc.code == 404:
            sys.exit(
                "The judge scenarios are not switched on for this deployment "
                "(404). Ask the operator to enable them."
            )
        if exc.code == 429:
            sys.exit(f"Limit reached: {detail}")
        sys.exit(f"{method} {path} failed ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {base}: {exc.reason}")


def table(state: dict) -> None:
    rows = state["scenarios"]
    width = max(len(r["ref"]) for r in rows)
    for r in rows:
        if not r["placed"]:
            where = "not placed"
        elif not r["paypal_side_ingested"]:
            where = "waiting for PayPal"
        else:
            where = f"in ({r['path']})"
        print(f"  {r['ref']:<{width}}  {where:<22} {r['case']}")
        print(f"  {'':<{width}}  {'':<22} expect: {r['expected']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", required=True, help="1 to 12 letters or digits")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for webhooks")
    parser.add_argument("--late-refund", action="store_true")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    name = args.name

    if args.late_refund:
        out = call(base, "POST", f"/sim/judge/scenarios/{name}/late-refund")
        print(f"S4 PayPal refund: {out['late_refund']}.")
        print("Click Run reconciliation again; S4 should now clear.")
        return 0

    print(f"Placing orders for {name.upper()} (a few seconds each)...")
    state = call(base, "POST", "/sim/judge/scenarios", {"name": name})
    deadline = time.monotonic() + args.wait
    while True:
        if state.get("error"):
            print(f"PayPal reported a problem: {state['error']}")
            print("Progress is kept. Run the command again to resume.")
            break
        if state["complete"]:
            break
        if time.monotonic() >= deadline:
            print(f"Not everything arrived by webhook in {args.wait}s; fetching it directly.")
            call(base, "POST", f"/sim/judge/scenarios/{name}/ingest-missing")
            state = call(base, "POST", "/sim/judge/scenarios", {"name": name})
            break
        time.sleep(5)
        # Idempotent: re-asking also makes the refund steps that wait for an order.
        state = call(base, "POST", "/sim/judge/scenarios", {"name": name})

    print()
    table(state)
    print()
    if state["complete"]:
        print("Ready. In the app: filter the queue by", f"JUDGE-{name.upper()}")
        print("then click Run reconciliation. If it says nothing is new or a run is")
        print("cooling down, another run already covers your orders. Open the findings")
        print("and Investigate any exception.")
        print("Optional: run again with --late-refund after your run.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
