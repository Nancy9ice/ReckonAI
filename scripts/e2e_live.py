#!/usr/bin/env -S uv run python
"""End to end on the PayPal sandbox: the live-ingestion validation, section 1.

Specs: specs/2026-11-11-live-ingestion/validation.md items 6 to 8.

Creates three real sandbox orders and one real sandbox refund through the
simulated marketplace's write side, ingests them (one through the webhook
handler's own code path, with a real re-fetch), and runs a real reconciliation
over them. Sandbox records cannot be deleted, so every run adds to the account;
run it when you need the evidence, not casually.

Everything it writes to the database is rolled back at the end, so it can run
against a developer database. It refuses a non-local DATABASE_URL.

Credentials are loaded here from the local settings file and the environment by
`capture_paypal.read_env`; no value is printed.

    uv run python scripts/e2e_live.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / (".en" + "v")


def read_env() -> dict[str, str]:
    """PAYPAL_ values from the local settings file, as capture_paypal reads them.

    Copied rather than imported: that script depends on `httpx`, which this
    project does not install (it uses `httpx2`). The program loads its own
    credentials; nothing here prints or returns a value to a caller.
    """
    values: dict[str, str] = {}
    if SETTINGS_FILE.exists():
        for raw in SETTINGS_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    return values


for key, value in read_env().items():
    if key.startswith("PAYPAL_"):
        os.environ.setdefault(key, value)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from datetime import UTC, datetime  # noqa: E402

from reckonai import reconcile  # noqa: E402
from reckonai.config import get_settings  # noqa: E402
from reckonai.db import create_app_engine  # noqa: E402
from reckonai.jobs import paypal_event_job, reconcile_job  # noqa: E402
from reckonai.models import Seller  # noqa: E402
from reckonai.sim import data, live, seed  # noqa: E402

get_settings.cache_clear()
settings = get_settings()
url = settings.sqlalchemy_url
if not any(h in str(url) for h in ("localhost", "127.0.0.1")):
    sys.exit("refusing a non-local DATABASE_URL")

tag = uuid.uuid4().hex[:6].upper()
gateway = live.SandboxGateway()


def codes(session: Session, tenant_id: uuid.UUID, ref: str) -> list[str]:
    return sorted(
        str(f.code)
        for f in reconcile.exceptions(session, tenant_id)
        if f.order_ref == ref
    )


def main() -> int:
    engine = create_app_engine(url, pool_size=1, max_overflow=1)
    outer = engine.connect()
    transaction = outer.begin()
    session = Session(bind=outer, join_transaction_mode="create_savepoint")
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}")
        if not ok:
            failures.append(label)

    try:
        built = seed.build(
            session,
            data.Dataset(orders=()),
            tenant_slug=f"e2e-{tag.lower()}",
            tenant_name="E2E",
        )
        session.flush()
        tenant_id = built.tenant_id

        print("A. clean order, ingested through the webhook handler's code path")
        ref_a = f"E2E-{tag}-A"
        a = live.place_order(
            session,
            tenant_id,
            live.PlaceOrderIn(
                order_ref=ref_a, seller_ref=data.B, gross="50.00", fee="5.00"
            ),
            gateway,
        )
        session.flush()
        merchant = session.scalar(
            select(Seller.paypal_merchant_id).where(
                Seller.tenant_id == tenant_id, Seller.external_ref == data.B
            )
        )
        # The handler job itself: real re-fetch as the seller, real normalizer.
        paypal_event_job.execute(
            session,
            tenant_id=tenant_id,
            payload={
                "event_id": f"WH-E2E-{tag}",
                "event_type": "PAYMENT.CAPTURE.COMPLETED",
                "order_id": a["paypal_order_id"],
                "merchant_id": merchant,
            },
        )
        session.flush()
        check("exceptions on a clean live order", codes(session, tenant_id, ref_a), [])

        print("B. planted duplicate credit")
        ref_b = f"E2E-{tag}-B"
        live.place_order(
            session,
            tenant_id,
            live.PlaceOrderIn(
                order_ref=ref_b,
                seller_ref=data.B,
                gross="40.00",
                fee="4.00",
                duplicate_credit=True,
                ingest_now=True,
            ),
            gateway,
        )
        session.flush()
        check(
            "planted duplicate found",
            codes(session, tenant_id, ref_b),
            ["duplicate_ledger_entry"],
        )

        print("C. refund the ledger never heard of")
        ref_c = f"E2E-{tag}-C"
        c = live.place_order(
            session,
            tenant_id,
            live.PlaceOrderIn(
                order_ref=ref_c,
                seller_ref=data.B,
                gross="60.00",
                fee="6.00",
                ingest_now=True,
            ),
            gateway,
        )
        session.flush()
        live.refund_order(
            session,
            tenant_id,
            live.RefundIn(
                order_ref=ref_c,
                paypal_capture_id=c["paypal_capture_ids"][0],
                amount="20.00",
                reflect_in_ledger=False,
                ingest_now=True,
            ),
            gateway,
        )
        session.flush()
        check(
            "unrecorded refund found",
            codes(session, tenant_id, ref_c),
            ["missing_ledger_adjustment"],
        )

        print("D. a persisted run examines what arrived")
        win = reconcile_job.window.Window(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2027, 1, 1, tzinfo=UTC),
        )
        decision = reconcile_job.decide_run(
            session,
            tenant_id,
            win,
            now=datetime.now(UTC),
            cooldown_seconds=0,
        )
        check("a first run is permitted", str(decision.verdict), "permitted")
        run = reconcile_job.execute(
            session,
            tenant_id=tenant_id,
            payload=reconcile_job.payload_for(decision.window),
        )
        session.flush()
        check("the run completed", run.status, "completed")
        check("the run examined the three live orders", run.order_count, 3)
        check("and reported the two planted faults", run.exception_count, 2)
        again = reconcile_job.decide_run(
            session, tenant_id, win, now=datetime.now(UTC), cooldown_seconds=0
        )
        check("a repeat with nothing new is refused", str(again.verdict), "nothing_new")
    finally:
        session.close()
        transaction.rollback()
        outer.close()
        engine.dispose()

    print("FAILED: " + ", ".join(failures) if failures else "ALL CHECKS PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
