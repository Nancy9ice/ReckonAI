"""Load the demo dataset, and optionally add one live order.

Two commands, deliberately separate, because one is safe to run on every
deploy and the other is not.

**`--ensure` is deterministic and makes no outbound call.** It seeds the
marketplace and replays the captured PayPal fixtures, skipping whatever is
already there. That is what `render.yaml`'s `preDeployCommand` runs beside
`alembic upgrade head`, and it is why the deployed dashboard has rows: a
static site renders what the API returns, so "deployed and reachable" with an
empty database demonstrates nothing (specs/roadmap.md Phase 14).

**`--live` makes one real sandbox call**, and nothing depends on it. The
replan's R15: the demo opens with a live order so a judge sees real PayPal,
and "the reconciliation then proceeds from fixtures whether or not that call
succeeded". A failure is logged and the command still exits 0.

**`--ensure` commits, so it is not for a developer's test database.** It
writes the `reckonmarket` tenant and leaves it there, and
`tests/test_catalogue_constraints.py` then fails on a conflicting external
reference - observed 2026-11-09, immediately after the first local run. Use
`--reset` afterwards, or point `DATABASE_URL` somewhere disposable. This is
`specs/2026-11-08-block-a-marketplace-connector/validation.md` section 8
debt 4 in a new form: rows that outlive a transaction interfere with tests
that assume they own the schema.

**The capture script is not this.** `scripts/capture_paypal.py` builds the
dataset by creating eighteen sandbox orders and writing fixtures, and must
never run during a demo or a deploy: it would overwrite the committed data
the reconciliation is about to run on. That separation is what "made
demo-safe" in R15 actually means.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from reckonai.config import get_settings
from reckonai.db import create_short_lived_engine
from reckonai.logging import configure_logging, event
from reckonai.models import DemoSeed, Tenant
from reckonai.paypal import capture, replay
from reckonai.sim import data, seed

logger = logging.getLogger("reckonai.demo")

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "fixtures" / "paypal"


def fingerprint(*, fixtures: Path = FIXTURES) -> str:
    """Hex SHA-256 over the marketplace dataset **and** the captured fixtures.

    Both, because either changing alone makes what is loaded wrong: a
    re-captured fixture with an unchanged `data.py` is as stale as the
    reverse.

    The dataset is hashed through `dataclasses.asdict`, so a changed amount,
    a renamed seller or an added order all move the hash. Comments and
    formatting do not, which is deliberate - re-seeding production because
    somebody rewrapped a docstring would be noise.
    """
    payload = {
        "dataset": dataclasses.asdict(data.FULL),
        "sellers": [dataclasses.asdict(s) for s in data.SELLERS],
        # Tuple keys do not survive JSON, so they are flattened rather
        # than stringified - a stable shape the hash can depend on.
        "positions": sorted(
            [seller, currency, value]
            for (seller, currency), value in data.EXPECTED_POSITIONS.items()
        ),
        "commission": data.EXPECTED_COMMISSION_TOTALS,
        "fixtures": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(fixtures.glob("*.json"))
        },
    }
    canonical = json.dumps(
        _stable(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stable(value: object) -> object:
    """Make a structure hashable to the *same* digest on every run.

    **Sets must become sorted lists.** `Dataset.faulty_order_refs` is a
    `frozenset`, and falling back to `str()` renders it in an order that
    varies with Python's hash randomisation - so two identical runs produced
    different fingerprints and `--ensure` re-seeded on every deploy,
    defeating the whole mechanism. Caught by running it twice, which is the
    only way this kind of instability shows up.
    """
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=str)}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def ensure(session: Session, *, fixtures: Path = FIXTURES) -> dict[str, int]:
    """Seed the marketplace and replay PayPal's side, if absent or stale.

    Idempotent: a redeploy with an unchanged dataset does nothing, and the
    replay ingester keys every write on `UNIQUE (tenant_id, external_id)`
    anyway.

    **And self-correcting, which the early return alone was not.** Returning
    early whenever the tenant existed meant production never picked up a
    dataset change: alter an amount, deploy, and the check stops with no
    error while the demo runs on figures the repository no longer states.
    So the fingerprint of what was loaded is compared, and a mismatch resets
    and re-seeds rather than leaving stale data in place.
    """
    current = fingerprint(fixtures=fixtures)
    existing = session.scalar(
        select(Tenant).where(Tenant.slug == data.TENANT_SLUG)
    )

    if existing is not None:
        loaded = session.scalar(
            select(DemoSeed).where(DemoSeed.tenant_id == existing.id)
        )
        if loaded is not None and loaded.fingerprint == current:
            logger.info(
                event("reckonai.demo.ensure", status="already_seeded",
                      fingerprint=current[:12])
            )
            return {"seeded": 0}

        logger.info(
            event(
                "reckonai.demo.ensure",
                status="stale",
                was=(loaded.fingerprint[:12] if loaded else "unrecorded"),
                now=current[:12],
            )
        )
        reset(session)

    built = seed.build(session, data.FULL)
    session.flush()
    seed.verify(session, built.tenant_id, data.FULL)

    result = replay.ingest_directory(
        session, fixtures, tenant_id=built.tenant_id
    )
    session.add(
        DemoSeed(
            tenant_id=built.tenant_id,
            fingerprint=current,
            order_count=len(data.FULL.orders),
        )
    )
    session.flush()

    counts = dict(result.inserted)
    counts["orders"] = len(data.FULL.orders)
    logger.info(
        event("reckonai.demo.ensure", status="seeded",
              fingerprint=current[:12], **counts)
    )
    return counts


def reset(session: Session) -> None:
    """Delete the demo tenant and everything under it, children first.

    Every foreign key here is `ondelete="RESTRICT"` - deleting evidence
    under a financial record is never silent - so there is no cascade and
    the order is explicit.
    """
    from reckonai.models import (
        Capture,
        CommissionRule,
        Disbursement,
        Dispute,
        FxConversion,
        Order,
        OrderItem,
        Payment,
        PlatformFee,
        QuarantinedRecord,
        RawPayload,
        Refund,
        Seller,
        SellerLedgerEntry,
    )

    tenant_id = session.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    if tenant_id is None:
        return

    for model in (
        FxConversion, PlatformFee, Disbursement, QuarantinedRecord,
        SellerLedgerEntry, Refund, Dispute, Capture, Payment,
        OrderItem, CommissionRule, Order, Seller, RawPayload, DemoSeed,
    ):
        session.execute(delete(model).where(model.tenant_id == tenant_id))
    session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    logger.info(event("reckonai.demo.reset", status="deleted"))


def live(session: Session) -> int:
    """Create one live sandbox order and ingest it. Never fails the caller.

    Returns the number of orders ingested: 1 on success, 0 when the call
    could not be made. Both are acceptable outcomes - that is R15.
    """
    sellers = [
        (spec.merchant_id, gross, fee)
        for spec, gross, fee in zip(
            data.SELLERS, ("100.00", "60.00"), ("10.00", "6.00"), strict=False
        )
    ]
    order = capture.try_live_order(payees=sellers)
    if order is None:
        logger.warning(
            event("reckonai.demo.live", status="skipped",
                  reason="paypal_unavailable_or_unconfigured")
        )
        return 0

    tenant_id = session.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    if tenant_id is None:
        logger.warning(
            event("reckonai.demo.live", status="skipped", reason="not_seeded")
        )
        return 0

    replay.ingest_document(session, order.document, tenant_id=tenant_id)
    logger.info(
        event("reckonai.demo.live", status="ingested",
              order=order.order_id, captures=len(order.captures))
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ensure", action="store_true",
        help="seed the demo dataset if absent; makes no outbound call",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="create one live sandbox order and ingest it; never fails",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="delete the demo tenant first",
    )
    args = parser.parse_args(argv)

    if not (args.ensure or args.live or args.reset):
        parser.error("pass --ensure, --live or --reset")

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_short_lived_engine(settings.sqlalchemy_url)

    with Session(bind=engine) as session:
        if args.reset:
            reset(session)
            session.commit()
        if args.ensure:
            counts = ensure(session)
            session.commit()
            print(f"ensure: {counts}")
        if args.live:
            added = live(session)
            session.commit()
            print(f"live: {added} order(s) ingested")

    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
