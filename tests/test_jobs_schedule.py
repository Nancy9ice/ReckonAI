"""The scheduler entry point, and the Blueprint that invokes it.

`specs/2026-11-09-queue-scheduled-run/validation.md` section 4. The cron job's
*firing* is deferred to Block F's deploy; what is proven here is that the start
command names a real entry point which enqueues the right work and writes no
financial row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.jobs import reconcile_job, schedule
from reckonai.models import Job, ReconciliationException, ReconciliationRun, Tenant

RENDER_YAML = Path(__file__).resolve().parents[1] / "render.yaml"


# ---------------------------------------------------------------------------
# The window the cron job asks for
# ---------------------------------------------------------------------------


def test_the_window_is_snapped_to_midnight() -> None:
    """Two invocations on one day must produce the same window.

    This is what makes the dedupe marker work at all: an unsnapped `now` would
    give every hourly tick its own window, the composed key would never match
    an existing one, and the hourly schedule would reconcile the same days
    over and over under different keys.
    """
    morning = schedule.window_ending(
        datetime(2026, 11, 10, 6, 30, tzinfo=UTC), days=2
    )
    evening = schedule.window_ending(
        datetime(2026, 11, 10, 23, 59, tzinfo=UTC), days=2
    )

    assert morning == evening
    assert morning.end == datetime(2026, 11, 11, tzinfo=UTC)
    assert morning.start == datetime(2026, 11, 9, tzinfo=UTC)


def test_the_window_covers_today() -> None:
    """A window ending at midnight *after* today, not at midnight today.

    Ending at the start of today would exclude every order placed today,
    which for a run scheduled hourly through the day is most of them.
    """
    win = schedule.window_ending(datetime(2026, 11, 10, 9, tzinfo=UTC), days=2)

    assert win.start <= datetime(2026, 11, 10, 9, tzinfo=UTC) < win.end


def test_the_default_window_is_two_days() -> None:
    """One day would close before the evidence for its last hours arrives:
    specs/tech-stack.md section 8 records the reporting APIs as roughly a day
    behind."""
    assert schedule.DEFAULT_WINDOW_DAYS == 2


# ---------------------------------------------------------------------------
# What it enqueues
# ---------------------------------------------------------------------------


def test_one_job_is_enqueued_per_tenant(
    session: Session, tenant: Tenant, other_tenant: Tenant
) -> None:
    """D9: one job per tenant, not one job that loops over tenants.

    A tenant whose reconciliation fails must not stop another tenant's, and
    the retry and the error belong to the tenant they happened to.
    """
    enqueued = schedule.enqueue_runs(session, days=2)

    assert len(enqueued) == 2

    jobs = session.scalars(select(Job)).all()
    assert {job.tenant_id for job in jobs} == {tenant.id, other_tenant.id}
    assert {job.job_type for job in jobs} == {reconcile_job.JOB_TYPE}


def test_the_job_payload_carries_the_window_and_one_flag_and_no_money(
    session: Session, tenant: Tenant
) -> None:
    """A job carries identifiers and dates. **No monetary value**: a `Decimal`
    does not survive a JSON round trip as itself (specs/tech-stack.md
    section 2)."""
    schedule.enqueue_runs(session, days=2)

    job = session.scalars(select(Job)).one()

    # The window, and `auto_investigate` (Block F, D17): a boolean asking the
    # worker to queue investigations afterwards, and `evidence_since`, the
    # watermark the run will read (a timestamp or null; live-ingestion section
    # 4). Still nothing monetary.
    assert set(job.payload) == {
        "window_start",
        "window_end",
        "evidence_since",
        "auto_investigate",
    }
    assert job.payload["auto_investigate"] is True
    parsed = reconcile_job._window_from(job.payload)
    assert parsed.end - parsed.start == timedelta(days=2)


def test_scheduling_writes_no_financial_row(
    session: Session, tenant: Tenant
) -> None:
    """D9's split, as a property. The scheduler enqueues; the worker executes.

    If this ever stopped holding, the cron job would be a third writer racing
    the worker, outside the `SKIP LOCKED` claim that keeps the two safe.
    """
    schedule.enqueue_runs(session, days=2)

    assert session.scalar(select(func.count()).select_from(ReconciliationRun)) == 0
    assert (
        session.scalar(select(func.count()).select_from(ReconciliationException))
        == 0
    )


def test_an_already_reconciled_window_is_skipped(
    session: Session, tenant: Tenant
) -> None:
    """A queue full of expected failures hides a real one.

    Enqueueing regardless would be *correct* - the handler's marker insert
    would refuse the duplicate - but every hourly tick would add a job that
    fails on the constraint. The read here keeps the queue readable; the
    constraint is what keeps it right.
    """
    win = schedule.window_ending(datetime.now(UTC), days=2)
    reconcile_job.execute(
        session,
        tenant_id=tenant.id,
        payload={
            "window_start": win.start.isoformat(),
            "window_end": win.end.isoformat(),
        },
    )

    assert schedule.enqueue_runs(session, days=2) == []


def test_a_different_window_is_still_enqueued(
    session: Session, tenant: Tenant
) -> None:
    """The skip is per window, not per tenant - otherwise one reconciled day
    would stop every later run."""
    win = schedule.window_ending(datetime.now(UTC), days=2)
    reconcile_job.execute(
        session,
        tenant_id=tenant.id,
        payload={
            "window_start": win.start.isoformat(),
            "window_end": win.end.isoformat(),
        },
    )

    assert len(schedule.enqueue_runs(session, days=5)) == 1


def test_the_window_argument_is_validated() -> None:
    """A zero or negative window would make `window_end > window_start` fail
    at the database, which is a legible error in an illegible place."""
    with pytest.raises(SystemExit):
        schedule.main(["--window-days", "0"])


# ---------------------------------------------------------------------------
# Section 4 - the Blueprint declares what this module provides
# ---------------------------------------------------------------------------


def test_the_blueprint_declares_the_cron_job() -> None:
    """The start command must name an entry point that exists.

    Asserted against the file rather than a parsed Blueprint: Render is the
    only thing that parses this, and what matters here is that the module path
    and flags in it are the ones this package actually provides.
    """
    blueprint = RENDER_YAML.read_text()

    assert "type: cron" in blueprint
    assert "reckonai.jobs.schedule" in blueprint
    assert "--no-sync" in blueprint, (
        "without it `uv run` re-syncs and undoes the build's --no-dev"
    )
    assert "--window-days 2" in blueprint


def test_the_blueprint_states_exactly_one_status_for_the_cron_job() -> None:
    """D8, written where someone reading the Blueprint will see it.

    The point is that a reader cannot mistake a declared service for a running
    one, or the reverse, and conclude Phase 8's fourth criterion is met or
    unmet on a guess. So the file must carry an explicit status marker.

    **This used to assert the literal text "DECLARED BUT NOT APPLIED"**, and
    that was the wrong thing to pin: it asserted a *value* of the status, so it
    went stale the moment the status changed. It did change - the Blueprint
    sync that PR #22's merge triggered created the service on 2026-11-10, and
    this test would have kept demanding the file say otherwise. A test that
    enforces a falsehood is worse than none, because it makes correcting the
    comment look like breaking the build.

    It now pins the property that matters: one marker, never both, so the file
    cannot be left contradicting itself half-edited.
    """
    blueprint = RENDER_YAML.read_text()

    not_applied = "DECLARED BUT NOT APPLIED" in blueprint
    applied = "**APPLIED " in blueprint

    assert not_applied != applied, (
        "render.yaml must state whether the cron job is applied, and only one "
        f"way (found not-applied={not_applied}, applied={applied})"
    )


def test_the_scheduler_module_is_runnable_as_a_main() -> None:
    """`python -m reckonai.jobs.schedule` is what the Blueprint invokes."""
    assert hasattr(schedule, "main")
    assert callable(schedule.main)
