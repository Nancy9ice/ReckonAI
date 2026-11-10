"""Investigation after a scheduled run: capped, deduplicated, credential-aware."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai import demo_data
from reckonai.config import Settings
from reckonai.jobs import investigation_job, reconcile_job, schedule
from reckonai.models import Investigation, Job, ReconciliationRun, Tenant
from reckonai.sim import data

WINDOW = {
    "window_start": datetime(2026, 10, 12, tzinfo=UTC).isoformat(),
    "window_end": datetime(2026, 11, 21, tzinfo=UTC).isoformat(),
}


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()
    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None
    return tenant_id


def _investigation_jobs(session: Session, tenant_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.tenant_id == tenant_id, Job.job_type == "investigation")
        )
        or 0
    )


def _run(
    session: Session,
    tenant_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    *,
    flag: bool,
    **env: str,
) -> ReconciliationRun:
    settings = _settings(monkeypatch, **env)
    monkeypatch.setattr(investigation_job, "get_settings", lambda: settings)
    payload: dict[str, object] = dict(WINDOW)
    if flag:
        payload["auto_investigate"] = True
    return reconcile_job.execute(session, tenant_id=tenant_id, payload=payload)


def test_a_scheduled_run_queues_one_investigation_per_exception(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(session, seeded, monkeypatch, flag=True)
    # Five exceptions; the parked `awaiting_evidence` finding is not one.
    assert run.exception_count == 5
    assert _investigation_jobs(session, seeded) == 5


def test_a_run_nobody_scheduled_investigates_nothing(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(session, seeded, monkeypatch, flag=False)
    assert _investigation_jobs(session, seeded) == 0


def test_the_schedule_asks_for_it(session: Session, seeded: uuid.UUID) -> None:
    schedule.enqueue_runs(session, days=2)
    payload = session.scalars(
        select(Job.payload).where(
            Job.tenant_id == seeded, Job.job_type == reconcile_job.JOB_TYPE
        )
    ).first()
    assert payload is not None
    assert payload["auto_investigate"] is True


def test_asking_twice_does_not_queue_twice(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(session, seeded, monkeypatch, flag=True)
    again = investigation_job.auto_investigate(session, seeded)
    assert again.queued == 0
    assert again.in_flight == 5
    assert _investigation_jobs(session, seeded) == 5


def _add_investigation(session: Session, run: ReconciliationRun, verdict: str) -> None:
    from reckonai.models import ReconciliationException

    row = session.scalars(
        select(ReconciliationException).where(
            ReconciliationException.run_id == run.id,
            ReconciliationException.is_exception.is_(True),
        )
    ).first()
    assert row is not None
    session.add(
        Investigation(
            tenant_id=row.tenant_id,
            order_external_ref=row.order_external_ref,
            code=row.code,
            provider="anthropic",
            model_requested="claude-haiku-5-5",
            verdict=verdict,
        )
    )
    session.flush()


def test_an_exception_with_a_conclusion_is_not_investigated_again(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(monkeypatch)
    monkeypatch.setattr(investigation_job, "get_settings", lambda: settings)
    run = reconcile_job.run_window(
        session,
        tenant_id=seeded,
        win=reconcile_job._window_from(WINDOW),
    )
    _add_investigation(session, run, "insufficient_evidence")
    result = investigation_job.auto_investigate(session, seeded)
    assert result.already_settled == 1
    assert result.queued == 4


def test_a_failed_investigation_is_retried(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(monkeypatch)
    run = reconcile_job.run_window(
        session, tenant_id=seeded, win=reconcile_job._window_from(WINDOW)
    )
    _add_investigation(session, run, "failed")
    result = investigation_job.auto_investigate(session, seeded, settings=settings)
    assert result.already_settled == 0
    assert result.queued == 5


def test_the_cap_limits_the_spend(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(monkeypatch, AUTO_INVESTIGATE_MAX="2")
    reconcile_job.run_window(
        session, tenant_id=seeded, win=reconcile_job._window_from(WINDOW)
    )
    result = investigation_job.auto_investigate(session, seeded, settings=settings)
    assert (result.queued, result.over_limit) == (2, 3)
    assert _investigation_jobs(session, seeded) == 2


def test_zero_turns_it_off(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(session, seeded, monkeypatch, flag=True, AUTO_INVESTIGATE_MAX="0")
    assert _investigation_jobs(session, seeded) == 0


def test_without_a_credential_nothing_is_queued_to_fail(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_MODEL", "claude-haiku-5-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings()  # type: ignore[call-arg]
    reconcile_job.run_window(
        session, tenant_id=seeded, win=reconcile_job._window_from(WINDOW)
    )
    result = investigation_job.auto_investigate(session, seeded, settings=settings)
    assert result.skipped_reason == "no credential"
    assert _investigation_jobs(session, seeded) == 0


def test_the_nightly_sweep_reaches_exceptions_outside_tonights_window(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reconciliation is windowed; the investigation sweep is not.

    An earlier run concluded the five exceptions. A later run, standing for a
    night whose window reaches no order, holds none of its own. Tied to that
    later run's own exceptions the sweep would queue nothing; it must still
    queue the five, because they are open and uninvestigated.
    """
    from datetime import timedelta

    settings = _settings(monkeypatch)
    first = reconcile_job.run_window(
        session, tenant_id=seeded, win=reconcile_job._window_from(WINDOW)
    )
    assert first.exception_count == 5

    later = datetime.now(UTC) + timedelta(hours=1)
    session.add(
        ReconciliationRun(
            tenant_id=seeded,
            window_start=datetime(2026, 1, 1, tzinfo=UTC),
            window_end=datetime(2026, 1, 2, tzinfo=UTC),
            started_at=later,
            finished_at=later,
            status="completed",
        )
    )
    session.flush()

    result = investigation_job.auto_investigate(session, seeded, settings=settings)
    assert result.queued == 5
    assert _investigation_jobs(session, seeded) == 5


def test_an_exception_a_later_run_re_concluded_is_swept_once(
    session: Session, seeded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs both concluded the same five faults: five investigations."""
    settings = _settings(monkeypatch)
    for end_day in (21, 22):
        reconcile_job.run_window(
            session,
            tenant_id=seeded,
            win=reconcile_job.window.Window(
                start=datetime(2026, 10, 12, tzinfo=UTC),
                end=datetime(2026, 11, end_day, tzinfo=UTC),
            ),
        )
    result = investigation_job.auto_investigate(session, seeded, settings=settings)
    assert result.queued == 5
    assert _investigation_jobs(session, seeded) == 5
