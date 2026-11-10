"""Dispatch and the worker drain.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D7, and criterion 2's
other half: a handler that raises has to leave a job that retries rather than
a job that looks untried.

**This module commits**, for the same reason `test_jobs_queue.py` does: the
claim has to be committed before the handler runs, or a failing handler's
rollback would also undo the attempt increment. That boundary is the thing
under test, so it cannot be tested inside a transaction that rolls back.

Rows are removed at module teardown, and `test_jobs_dispatch` sorts before
`test_migrations` alphabetically, so the downgrade to `base` meets none of
them. **Do not rename this module to sort after it.**
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from reckonai import worker
from reckonai.jobs import handlers, queue
from reckonai.models import Job, ProcessedEvent, Tenant

JOB_TYPE = "reconciliation_run"


@pytest.fixture(scope="module")
def committed_tenant(migrated_database: Engine) -> Iterator[uuid.UUID]:
    """One committed tenant, removed at module teardown.

    Children before parents: every foreign key here is `ondelete="RESTRICT"`
    with no cascade, so the tenant goes last.
    """
    tenant_id = uuid.uuid4()

    with Session(migrated_database) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="DispatchTest",
                slug=f"dispatch-{tenant_id.hex[:8]}",
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


@pytest.fixture
def registered() -> Iterator[list[str]]:
    """A throwaway job type, restored afterwards.

    The registry is module-level state, so a test that adds to it must put it
    back - otherwise a later test sees a handler nothing registered.
    """
    original = dict(handlers.HANDLERS)
    calls: list[str] = []
    yield calls
    handlers.HANDLERS.clear()
    handlers.HANDLERS.update(original)


def _enqueue(engine: Engine, tenant_id: uuid.UUID, **kwargs: object) -> uuid.UUID:
    with Session(engine) as session:
        job = queue.enqueue(
            session,
            tenant_id=tenant_id,
            job_type=JOB_TYPE,
            payload={"spec": "dispatch"},
            **kwargs,  # type: ignore[arg-type]
        )
        session.commit()
        return job.id


def _reload(engine: Engine, job_id: uuid.UUID) -> Job:
    with Session(engine) as session:
        return session.scalars(select(Job).where(Job.id == job_id)).one()


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_the_reconciliation_handler_is_registered() -> None:
    """D7: a job type nothing registers would be claimed and silently dropped."""
    assert JOB_TYPE in handlers.HANDLERS


def test_every_declared_job_type_has_a_handler() -> None:
    """The `CHECK` vocabulary and the registry must agree.

    A type added to one and not the other is a job that enqueues fine and then
    fails at dispatch - which is a deploy-time discovery, not a test-time one.
    """
    from reckonai.models.jobs import JOB_TYPES

    assert set(JOB_TYPES) == set(handlers.HANDLERS)


# ---------------------------------------------------------------------------
# Success and failure paths
# ---------------------------------------------------------------------------


def test_a_successful_job_is_completed_and_committed(
    migrated_database: Engine, committed_tenant: uuid.UUID, registered: list[str]
) -> None:
    """The handler's rows and the job's terminal status land together."""

    def handler(session: Session, tenant_id: uuid.UUID, payload: dict) -> None:
        registered.append("ran")

    handlers.HANDLERS[JOB_TYPE] = handler

    job_id = _enqueue(migrated_database, committed_tenant)

    assert worker.drain(migrated_database) is True
    assert registered == ["ran"]
    assert _reload(migrated_database, job_id).status == "done"


def test_a_raising_handler_leaves_a_job_that_retries(
    migrated_database: Engine, committed_tenant: uuid.UUID, registered: list[str]
) -> None:
    """Criterion 2's other half, through the real dispatch path.

    **The attempt must survive the failure it is counting.** If the claim and
    the handler shared one transaction, the rollback would undo the increment
    too and the job would retry forever looking untried - which is the bug
    this boundary exists to prevent.
    """

    def handler(session: Session, tenant_id: uuid.UUID, payload: dict) -> None:
        registered.append("tried")
        raise RuntimeError("seller ledger unreachable")

    handlers.HANDLERS[JOB_TYPE] = handler

    job_id = _enqueue(migrated_database, committed_tenant)

    assert worker.drain(migrated_database) is True

    job = _reload(migrated_database, job_id)
    assert registered == ["tried"]
    assert job.attempts == 1, "the attempt was rolled back with the handler's work"
    assert job.status == "queued", "under its cap, the job stays eligible"
    assert job.last_error == "RuntimeError: seller ledger unreachable"


def test_a_handler_that_raises_writes_none_of_its_rows(
    migrated_database: Engine, committed_tenant: uuid.UUID, registered: list[str]
) -> None:
    """The handler's work is one transaction with the job's status.

    A partially-written run is the failure mode the single `COMMIT` exists to
    prevent: rows that look like a reconciliation happened, under a job that
    says it did not.
    """
    marker = f"recon:partial:{uuid.uuid4()}"

    def handler(session: Session, tenant_id: uuid.UUID, payload: dict) -> None:
        queue.mark_processed(
            session, tenant_id=tenant_id, event_id=marker, job_type=JOB_TYPE
        )
        raise RuntimeError("died after writing")

    handlers.HANDLERS[JOB_TYPE] = handler

    _enqueue(migrated_database, committed_tenant)
    worker.drain(migrated_database)

    with Session(migrated_database) as session:
        survived = session.scalars(
            select(ProcessedEvent).where(ProcessedEvent.event_id == marker)
        ).all()

    assert not survived, (
        "the marker outlived the transaction that failed, so a later run would "
        "skip work whose effect was rolled back"
    )


def test_an_unregistered_job_type_fails_loudly(
    migrated_database: Engine, committed_tenant: uuid.UUID, registered: list[str]
) -> None:
    """A job no handler claims must fail rather than sit in the queue.

    Unreachable through the `CHECK` on `job_type`, which is the point of
    having one - but a type added to the vocabulary and left unregistered
    would land here.
    """
    handlers.HANDLERS.pop(JOB_TYPE)

    job_id = _enqueue(migrated_database, committed_tenant, max_attempts=1)

    assert worker.drain(migrated_database) is True

    job = _reload(migrated_database, job_id)
    assert job.status == "failed"
    assert job.last_error is not None and "no handler" in job.last_error


def test_dispatch_refuses_a_job_whose_claim_is_not_committed(
    session: Session, tenant: Tenant
) -> None:
    """The guard on the boundary, so a future caller cannot skip the commit.

    Without it the mistake is silent: everything works until a handler raises,
    and then the attempt count quietly stops incrementing.
    """
    job = queue.enqueue(
        session, tenant_id=tenant.id, job_type=JOB_TYPE, payload={}
    )

    with pytest.raises(RuntimeError, match="not claimed"):
        handlers.dispatch(session, job)


# ---------------------------------------------------------------------------
# The drain loop
# ---------------------------------------------------------------------------


def test_an_empty_queue_reports_no_work(migrated_database: Engine) -> None:
    """What the worker sleeps on. A drain that claimed nothing must say so, or
    the loop spins at full speed on an empty queue."""
    assert worker.drain(migrated_database) is False


def test_a_job_due_later_is_not_drained(
    migrated_database: Engine, committed_tenant: uuid.UUID, registered: list[str]
) -> None:
    """`run_after` is what makes a retry a delay rather than a spin."""
    from datetime import UTC, datetime, timedelta

    def handler(session: Session, tenant_id: uuid.UUID, payload: dict) -> None:
        registered.append("should not run")

    handlers.HANDLERS[JOB_TYPE] = handler

    _enqueue(
        migrated_database,
        committed_tenant,
        run_after=datetime.now(UTC) + timedelta(hours=1),
    )

    assert worker.drain(migrated_database) is False
    assert registered == []


# ---------------------------------------------------------------------------
# Code review finding 8 - a duplicate window is done, not failed
# ---------------------------------------------------------------------------


def test_an_already_reconciled_window_completes_rather_than_retrying(
    migrated_database: Engine, committed_tenant: uuid.UUID, registered: list[str]
) -> None:
    """`AlreadyReconciled` is work already done, not a retryable failure.

    Review found the `processed_event` violation treated as an ordinary
    failure: rescheduled, retried to the cap, and left as a terminal `failed`
    row carrying an `IntegrityError` - the "queue full of expected failures
    hides a real one" outcome the design claims to avoid, reached by another
    route. Two overlapping cron invocations are the realistic cause.
    """
    from reckonai.jobs.reconcile_job import AlreadyReconciled

    def handler(session: Session, tenant_id: uuid.UUID, payload: dict) -> None:
        registered.append("tried")
        raise AlreadyReconciled("recon:test:2026-11-09:2026-11-11")

    handlers.HANDLERS[JOB_TYPE] = handler

    job_id = _enqueue(migrated_database, committed_tenant)

    assert worker.drain(migrated_database) is True

    job = _reload(migrated_database, job_id)
    assert job.status == "done", "an already-reconciled window needs no retry"
    assert job.last_error is None, "nothing failed, so nothing to record"
    assert registered == ["tried"]
