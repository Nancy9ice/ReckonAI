"""The same-day re-run rule.

`specs/2026-11-11-live-ingestion/requirements.md` section 4. A run over dates
that already have one is permitted only when evidence has been retrieved since
the last completed run started; dates with no run are always permitted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from conftest import make_payload
from reckonai.jobs import queue, reconcile_job, window
from reckonai.jobs.reconcile_job import RunVerdict
from reckonai.models import Tenant

DAY = window.Window(
    start=datetime(2026, 11, 9, tzinfo=UTC),
    end=datetime(2026, 11, 11, tzinfo=UTC),
)
NEXT_DAY = window.Window(
    start=datetime(2026, 11, 10, tzinfo=UTC),
    end=datetime(2026, 11, 12, tzinfo=UTC),
)


def decide(
    session: Session, tenant: Tenant, win: window.Window, *, cooldown: int = 0
) -> reconcile_job.RunDecision:
    return reconcile_job.decide_run(
        session,
        tenant.id,
        win,
        now=datetime.now(UTC),
        cooldown_seconds=cooldown,
    )


def run(session: Session, tenant: Tenant, win: window.Window) -> None:
    decision = decide(session, tenant, win)
    assert decision.verdict is RunVerdict.PERMITTED
    reconcile_job.execute(
        session, tenant_id=tenant.id, payload=reconcile_job.payload_for(decision.window)
    )
    session.flush()


def arrive(session: Session, tenant: Tenant) -> None:
    """Evidence retrieved now, which is after any run that has already started."""
    session.add(make_payload(tenant.id, retrieved_at=datetime.now(UTC)))
    session.flush()


def test_the_first_run_over_a_date_is_permitted(
    session: Session, tenant: Tenant
) -> None:
    assert decide(session, tenant, DAY).verdict is RunVerdict.PERMITTED


def test_a_repeat_with_nothing_new_is_refused(
    session: Session, tenant: Tenant
) -> None:
    run(session, tenant, DAY)
    assert decide(session, tenant, DAY).verdict is RunVerdict.NOTHING_NEW


def test_new_evidence_permits_a_same_day_run_and_then_stops(
    session: Session, tenant: Tenant
) -> None:
    """The gate, not the key, stops a chain: every completed run moves the
    watermark, so the key alone would allow a run after every run."""
    run(session, tenant, DAY)
    arrive(session, tenant)

    assert decide(session, tenant, DAY).verdict is RunVerdict.PERMITTED
    run(session, tenant, DAY)

    assert decide(session, tenant, DAY).verdict is RunVerdict.NOTHING_NEW


def test_a_new_day_is_permitted_with_no_new_evidence(
    session: Session, tenant: Tenant
) -> None:
    """The nightly re-evaluation of `awaiting_evidence` does not depend on
    anything arriving."""
    run(session, tenant, DAY)
    assert decide(session, tenant, NEXT_DAY).verdict is RunVerdict.PERMITTED


def test_the_cooldown_holds_a_run_back_and_zero_disables_it(
    session: Session, tenant: Tenant
) -> None:
    run(session, tenant, DAY)
    arrive(session, tenant)

    assert decide(session, tenant, DAY, cooldown=300).verdict is RunVerdict.COOLING_DOWN
    assert decide(session, tenant, DAY, cooldown=0).verdict is RunVerdict.PERMITTED


def test_the_cooldown_does_not_apply_to_a_first_run(
    session: Session, tenant: Tenant
) -> None:
    run(session, tenant, DAY)
    assert (
        decide(session, tenant, NEXT_DAY, cooldown=300).verdict
        is RunVerdict.PERMITTED
    )


def test_two_jobs_queued_against_one_state_run_once(
    session: Session, tenant: Tenant
) -> None:
    """Both see the same watermark and are stamped with it, so the second is
    refused at the constraint even after the first has moved the watermark."""
    first = decide(session, tenant, DAY)
    second = decide(session, tenant, DAY)
    assert first.verdict is second.verdict is RunVerdict.PERMITTED
    assert reconcile_job.idempotency_key(
        tenant.id, first.window
    ) == reconcile_job.idempotency_key(tenant.id, second.window)

    reconcile_job.execute(
        session, tenant_id=tenant.id, payload=reconcile_job.payload_for(first.window)
    )
    with pytest.raises(reconcile_job.AlreadyReconciled):
        reconcile_job.execute(
            session,
            tenant_id=tenant.id,
            payload=reconcile_job.payload_for(second.window),
        )


def test_a_key_in_the_old_format_counts_as_a_run_over_those_dates(
    session: Session, tenant: Tenant
) -> None:
    queue.mark_processed(
        session,
        tenant_id=tenant.id,
        event_id=(
            f"recon:{tenant.id}:{DAY.start.date().isoformat()}"
            f":{DAY.end.date().isoformat()}"
        ),
        job_type=reconcile_job.JOB_TYPE,
    )
    assert decide(session, tenant, DAY).verdict is RunVerdict.NOTHING_NEW


def test_the_key_names_the_watermark(session: Session, tenant: Tenant) -> None:
    before = decide(session, tenant, DAY).window
    assert reconcile_job.idempotency_key(tenant.id, before).endswith(":none")

    run(session, tenant, DAY)
    arrive(session, tenant)
    after = decide(session, tenant, DAY).window
    assert after.evidence_since is not None
    assert reconcile_job.idempotency_key(tenant.id, after) != (
        reconcile_job.idempotency_key(tenant.id, before)
    )


def test_has_new_evidence_is_clause_twos_test(
    session: Session, tenant: Tenant
) -> None:
    now = datetime.now(UTC)
    assert window.has_new_evidence(session, tenant.id, None) is False

    session.add(make_payload(tenant.id, retrieved_at=now))
    session.flush()

    assert window.has_new_evidence(session, tenant.id, None) is True
    assert window.has_new_evidence(session, tenant.id, now - timedelta(seconds=1))
    assert not window.has_new_evidence(session, tenant.id, now)


def test_an_explicit_null_watermark_is_not_recomputed(
    session: Session, tenant: Tenant
) -> None:
    """Null means no run had completed; an absent key means an older payload."""
    payload = reconcile_job.payload_for(DAY)
    assert "evidence_since" in payload and payload["evidence_since"] is None


def test_evidence_dated_in_the_future_has_not_arrived(
    session: Session, tenant: Tenant
) -> None:
    """The seeded dataset is dated up to a week ahead; counting those payloads
    as new made every same-day run look permitted (found running it locally)."""
    run(session, tenant, DAY)
    session.add(
        make_payload(tenant.id, retrieved_at=datetime.now(UTC) + timedelta(days=3))
    )
    session.flush()

    assert decide(session, tenant, DAY).verdict is RunVerdict.NOTHING_NEW
