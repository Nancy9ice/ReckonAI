"""Missed-delivery recovery.

`specs/2026-11-11-live-ingestion/requirements.md` F9.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.jobs import queue, recover, schedule
from reckonai.models import Job, Seller
from reckonai.paypal import live, webhook
from reckonai.sim import data, seed

NOW = datetime(2026, 11, 11, 1, tzinfo=UTC)


class StubClient:
    def __init__(self, events: list[dict] | Exception) -> None:
        self.events_ = events

    def events(self, since: datetime, until: datetime) -> list[dict]:
        if isinstance(self.events_, Exception):
            raise self.events_
        return self.events_


def _setup(session: Session) -> tuple[uuid.UUID, str]:
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"rc-{uuid.uuid4().hex[:8]}",
        tenant_name="Recover",
    )
    session.flush()
    merchant = f"M-{uuid.uuid4().hex[:10]}"
    seller = session.scalars(
        select(Seller).where(Seller.tenant_id == built.tenant_id)
    ).first()
    assert seller is not None
    seller.paypal_merchant_id = merchant
    session.flush()
    return built.tenant_id, merchant


def _event(merchant: str, event_id: str) -> dict:
    return {
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "payee": {"merchant_id": merchant},
            "supplementary_data": {"related_ids": {"order_id": f"O-{event_id}"}},
        },
    }


def _jobs(session: Session, tenant_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(Job).where(
            Job.tenant_id == tenant_id, Job.job_type == "paypal_event"
        )
    ) or 0


def test_missed_events_are_queued(session: Session) -> None:
    tenant_id, merchant = _setup(session)
    client = StubClient([_event(merchant, "WH-A"), _event(merchant, "WH-B")])

    out = recover.recover(
        session,
        client,  # type: ignore[arg-type]
        since=NOW - timedelta(hours=72),
        until=NOW,
    )

    assert out["queued"] == 2
    assert _jobs(session, tenant_id) == 2


def test_an_event_already_handled_is_not_queued_again(session: Session) -> None:
    tenant_id, merchant = _setup(session)
    queue.mark_processed(
        session,
        tenant_id=tenant_id,
        event_id=webhook.event_key("WH-DONE"),
        job_type=webhook.JOB_TYPE,
    )
    session.flush()

    out = recover.recover(
        session,
        StubClient([_event(merchant, "WH-DONE")]),  # type: ignore[arg-type]
        since=NOW - timedelta(hours=72),
        until=NOW,
    )

    assert out["duplicate"] == 1
    assert _jobs(session, tenant_id) == 0


def test_a_malformed_event_does_not_stop_the_rest(session: Session) -> None:
    tenant_id, merchant = _setup(session)
    out = recover.recover(
        session,
        StubClient([{"nope": 1}, _event(merchant, "WH-OK")]),  # type: ignore[arg-type]
        since=NOW - timedelta(hours=72),
        until=NOW,
    )
    assert out["malformed"] == 1 and out["queued"] == 1


def test_a_refused_list_propagates_and_is_not_a_clean_sweep(session: Session) -> None:
    with pytest.raises(live.LiveDenied):
        recover.recover(
            session,
            StubClient(live.LiveDenied("403")),  # type: ignore[arg-type]
            since=NOW - timedelta(hours=72),
            until=NOW,
        )


def test_recovery_failing_never_stops_the_nightly_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> live.Credentials:
        raise live.LiveFetchError("not configured")

    monkeypatch.setattr(live, "credentials", boom)

    # Logs a warning and returns. (`main` goes on to enqueue the run; the
    # rollback inside would undo this test's own fixtures, so it is not chained.)
    schedule._recover(session, hours=72)
