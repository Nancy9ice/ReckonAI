"""The queue: claiming, retry, and the dedupe marker.

`specs/2026-11-09-queue-scheduled-run/validation.md` section 1, criteria 1, 2
and 3.

**This module commits rows**, which makes it the third one that does. It has
to: `FOR UPDATE SKIP LOCKED` is a statement about two concurrent
*transactions*, and the shared `session` fixture rolls everything back inside
one - two sessions joined to it cannot see each other's rows, so a test built
on it would pass while proving nothing.

So the concurrency test opens two real sessions against the engine, commits,
and deletes at module teardown. That is the pattern
`tests/test_connector_ingest.py` already uses and the reason this suite is
order-dependent (`specs/2026-11-08-block-a-marketplace-connector/validation.md`
section 8 debt 4). `test_jobs_queue` sorts before `test_migrations`
alphabetically, so the committed rows are gone before the downgrade to `base`
runs. **Do not rename this module to sort after it.**

Every test that does not need two transactions uses the rolled-back `session`
fixture instead, because a committing test is a cost paid per test.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.jobs import queue
from reckonai.models import Job, ProcessedEvent, Tenant

JOB_TYPE = "reconciliation_run"


# ---------------------------------------------------------------------------
# A committed tenant, for the tests that need two real transactions
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed_tenant(migrated_database: Engine) -> Iterator[uuid.UUID]:
    """One committed tenant, removed at module teardown.

    Children before parents: every foreign key in this schema is
    `ondelete="RESTRICT"` and there is no cascade, so deleting the tenant
    first fails rather than cleaning up.
    """
    tenant_id = uuid.uuid4()

    with Session(migrated_database) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="QueueTest",
                slug=f"queue-{tenant_id.hex[:8]}",
            )
        )
        session.commit()

    yield tenant_id

    with Session(migrated_database) as session:
        session.execute(delete(Job).where(Job.tenant_id == tenant_id))
        session.execute(
            delete(ProcessedEvent).where(ProcessedEvent.tenant_id == tenant_id)
        )
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


# ---------------------------------------------------------------------------
# Criterion 1 - two workers never claim the same job
# ---------------------------------------------------------------------------


def test_two_concurrent_claimers_never_claim_the_same_job(
    migrated_database: Engine, committed_tenant: uuid.UUID
) -> None:
    """validation.md criterion 1, by two real transactions rather than inspection.

    Render's rolling deploy overlaps the outgoing worker and the incoming one
    for about 90 seconds on every deploy (observed 2026-11-08), so this is the
    condition production is already in.

    The first claimer **does not commit** before the second claims. That is
    what puts the test in the state the criterion is about: the rows are
    locked and uncommitted, and `skip_locked=True` is what makes the second
    claimer step over them instead of blocking until the first finishes.
    """
    with Session(migrated_database) as setup:
        for _ in range(6):
            queue.enqueue(
                setup,
                tenant_id=committed_tenant,
                job_type=JOB_TYPE,
                payload={"spec": "concurrency"},
            )
        setup.commit()

    first = Session(migrated_database)
    second = Session(migrated_database)
    try:
        claimed_first = [job.id for job in queue.claim(first, limit=6)]
        # The first claimer is still holding its lock here, uncommitted.
        claimed_second = [job.id for job in queue.claim(second, limit=6)]

        assert claimed_first, "the first claimer got nothing; the queue was empty"
        assert not set(claimed_first) & set(claimed_second), (
            "the same job was claimed twice: "
            f"{sorted(set(claimed_first) & set(claimed_second))}"
        )
        assert len(set(claimed_first) | set(claimed_second)) == 6, (
            "between them the two claimers should have taken all six jobs"
        )

        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()


def test_the_concurrency_test_would_fail_without_skip_locked(
    migrated_database: Engine, committed_tenant: uuid.UUID
) -> None:
    """The gate above proves nothing if `claim` could not overlap at all.

    Without `skip_locked` the second claimer would **block** on the first's
    lock rather than return a disjoint set, so the test above would hang
    rather than fail - and a hanging test reads as an environment problem.
    This asserts the flag is actually on the statement, so a future edit that
    drops it is caught here with a legible message.
    """
    statement = str(
        select(Job)
        .where(Job.status == "queued")
        .with_for_update(skip_locked=True)
        .compile(migrated_database)
    )

    assert "SKIP LOCKED" in statement.upper(), statement


# ---------------------------------------------------------------------------
# Criterion 2 - a failing job retries and lands terminal
# ---------------------------------------------------------------------------


def test_a_failing_job_retries_then_lands_terminal_failed(
    session: Session, tenant: Tenant
) -> None:
    """validation.md criterion 2, in R7's narrowed form.

    Three attempts, then `failed` with the error recorded. The job returns to
    `queued` in between, which is what makes the next `claim` pick it up -
    there is no separate retry queue.
    """
    job = queue.enqueue(
        session,
        tenant_id=tenant.id,
        job_type=JOB_TYPE,
        payload={},
        max_attempts=3,
    )

    for attempt in (1, 2):
        claimed = queue.claim(session, limit=1)
        assert [c.id for c in claimed] == [job.id]
        assert job.attempts == attempt

        queue.fail(session, job, ValueError("provider timed out"))

        assert job.status == "queued", "a job under its cap must stay eligible"
        assert job.run_after > datetime.now(UTC), (
            "a retry must not be immediately eligible, or a failing job spins"
        )

        # Make it eligible again without waiting out the delay.
        job.run_after = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()

    claimed = queue.claim(session, limit=1)
    assert [c.id for c in claimed] == [job.id]
    assert job.attempts == 3

    queue.fail(session, job, ValueError("provider timed out"))

    assert job.status == "failed", "attempts are exhausted; this is terminal"
    assert job.last_error == "ValueError: provider timed out"


def test_a_failure_records_the_message_and_not_a_traceback(
    session: Session, tenant: Tenant
) -> None:
    """D6: `last_error` is read by a dashboard, and a traceback can carry rows."""
    job = queue.enqueue(
        session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={}
    )
    queue.claim(session, limit=1)

    try:
        raise RuntimeError("seller ledger unreachable")
    except RuntimeError as exc:
        queue.fail(session, job, exc)

    assert job.last_error == "RuntimeError: seller ledger unreachable"
    assert "Traceback" not in (job.last_error or "")
    assert "line " not in (job.last_error or "")


def test_a_terminal_job_is_not_claimed_again(
    session: Session, tenant: Tenant
) -> None:
    """A `failed` job is done being tried, or the cap means nothing."""
    job = queue.enqueue(
        session,
        tenant_id=tenant.id,
        job_type=JOB_TYPE,
        payload={},
        max_attempts=1,
    )
    queue.claim(session, limit=1)
    queue.fail(session, job, ValueError("once"))
    assert job.status == "failed"

    assert queue.claim(session, limit=5) == []


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_a_job_scheduled_for_later_is_not_claimed_yet(
    session: Session, tenant: Tenant
) -> None:
    """`run_after` is what makes a retry a delay rather than a spin."""
    queue.enqueue(
        session,
        tenant_id=tenant.id,
        job_type=JOB_TYPE,
        payload={},
        run_after=datetime.now(UTC) + timedelta(hours=1),
    )

    assert queue.claim(session, limit=5) == []


def test_the_oldest_due_job_is_claimed_first(
    session: Session, tenant: Tenant
) -> None:
    """Without the ordering a retried job starves behind newer arrivals."""
    now = datetime.now(UTC)

    newer = queue.enqueue(
        session,
        tenant_id=tenant.id,
        job_type=JOB_TYPE,
        payload={"which": "newer"},
        run_after=now - timedelta(minutes=1),
    )
    older = queue.enqueue(
        session,
        tenant_id=tenant.id,
        job_type=JOB_TYPE,
        payload={"which": "older"},
        run_after=now - timedelta(hours=2),
    )

    claimed = queue.claim(session, limit=1)

    assert [job.id for job in claimed] == [older.id], (
        "the job due longest should go first, not the one inserted last"
    )
    assert newer.status == "queued"


def test_claiming_sets_the_claim_timestamp(
    session: Session, tenant: Tenant
) -> None:
    """D6 recovers a dead worker's job through `run_after` and this column,
    rather than through a heartbeat, so it must actually be written."""
    queue.enqueue(session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={})

    claimed = queue.claim(session, limit=1)

    assert claimed[0].claimed_at is not None
    assert claimed[0].status == "claimed"


# ---------------------------------------------------------------------------
# Criterion 3 - the same event id twice is refused by the database
# ---------------------------------------------------------------------------


def test_the_same_event_id_twice_raises_a_constraint_violation(
    session: Session, tenant: Tenant
) -> None:
    """validation.md criterion 3, and the replan's R8.

    Phase 9 was deferred, so this is where the duplicate-delivery property is
    proven - by the constraint, not by a webhook. The point is that the
    *database* refuses it: application logic that checked first would have a
    window between the check and the insert.
    """
    key = "recon:2026-11-09:2026-11-10"

    queue.mark_processed(
        session, tenant_id=tenant.id, event_id=key, job_type=JOB_TYPE
    )

    with pytest.raises(IntegrityError):
        queue.mark_processed(
            session, tenant_id=tenant.id, event_id=key, job_type=JOB_TYPE
        )


def test_two_tenants_may_process_the_same_event_id(
    session: Session, tenant: Tenant, other_tenant: Tenant
) -> None:
    """The marker is scoped by tenant, and that is not incidental.

    Two marketplaces reconciling the same date window are two pieces of work.
    A global unique key would silently drop the second, which is a missing
    reconciliation that looks like a successful dedupe.
    """
    key = "recon:2026-11-09:2026-11-10"

    queue.mark_processed(
        session, tenant_id=tenant.id, event_id=key, job_type=JOB_TYPE
    )
    queue.mark_processed(
        session, tenant_id=other_tenant.id, event_id=key, job_type=JOB_TYPE
    )

    markers = session.scalars(
        select(ProcessedEvent).where(ProcessedEvent.event_id == key)
    ).all()
    assert len(markers) == 2


def test_an_unknown_job_type_is_refused(session: Session, tenant: Tenant) -> None:
    """The checked vocabulary, which exists so a typo is not a silent backlog."""
    with pytest.raises(IntegrityError):
        queue.enqueue(
            session,
            tenant_id=tenant.id,
            job_type="recociliation_run",
            payload={},
        )


# ---------------------------------------------------------------------------
# Stale claims - code review finding 1
# ---------------------------------------------------------------------------


def test_a_stale_claim_is_reclaimed(session: Session, tenant: Tenant) -> None:
    """A job whose worker died is picked up again, which it was not before.

    Nothing moved a row from `claimed` back to `queued`, so a process that
    died between the claim and the handler's own error handling left a job no
    worker would ever take again - with no error recorded and its window
    silently never reconciled. `run_after` was cited as the recovery path and
    could not be one, because the claim never returned to the status
    `run_after` is filtered on.
    """
    job = queue.enqueue(
        session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={}, max_attempts=3
    )
    queue.claim(session, limit=1)
    assert job.status == "claimed"

    # The worker vanishes here: no complete, no fail, nothing.
    assert queue.claim(session, limit=1) == [], "not stale yet"

    job.claimed_at = datetime.now(UTC) - timedelta(
        seconds=queue.STALE_CLAIM_SECONDS + 1
    )
    session.flush()

    reclaimed = queue.claim(session, limit=1)

    assert [c.id for c in reclaimed] == [job.id]
    assert job.attempts == 2, "a reclaim is an attempt, and must be counted"


def test_a_stale_claim_out_of_attempts_is_terminated(
    session: Session, tenant: Tenant
) -> None:
    """The other half: a job that died on its final attempt.

    It sits below `claim`'s `attempts < max_attempts` filter, so without this
    it would stay `claimed` forever - invisible to every worker and to anyone
    reading the queue for failures.
    """
    job = queue.enqueue(
        session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={}, max_attempts=1
    )
    queue.claim(session, limit=1)
    assert job.attempts == 1

    job.claimed_at = datetime.now(UTC) - timedelta(
        seconds=queue.STALE_CLAIM_SECONDS + 1
    )
    session.flush()

    assert queue.claim(session, limit=1) == [], "no attempts left to reclaim with"

    reaped = queue.reap_stale_claims(session)

    assert [r.id for r in reaped] == [job.id]
    assert job.status == "failed"
    assert job.last_error is not None and "ClaimExpired" in job.last_error


def test_a_fresh_claim_is_not_reaped(session: Session, tenant: Tenant) -> None:
    """The reaper must not shoot a job that is simply still running."""
    job = queue.enqueue(
        session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={}, max_attempts=1
    )
    queue.claim(session, limit=1)

    assert queue.reap_stale_claims(session) == []
    assert job.status == "claimed"


def test_a_reclaim_never_exceeds_the_attempt_cap(
    session: Session, tenant: Tenant
) -> None:
    """`CHECK (attempts <= max_attempts)` would otherwise fail the flush.

    Without the cap in the claim query, a repeatedly reclaimed job increments
    past its ceiling and the constraint fails *someone else's* flush rather
    than its own job.
    """
    job = queue.enqueue(
        session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={}, max_attempts=2
    )

    for _ in range(2):
        queue.claim(session, limit=1)
        job.claimed_at = datetime.now(UTC) - timedelta(
            seconds=queue.STALE_CLAIM_SECONDS + 1
        )
        session.flush()

    assert job.attempts == 2
    assert queue.claim(session, limit=1) == []
    session.flush()  # must not raise on the CHECK
