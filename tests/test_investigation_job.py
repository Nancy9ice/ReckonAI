"""The queued investigation: enqueue once, run off the request, fail legibly."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.api.deps import get_session, get_tenant_id
from reckonai.app import app
from reckonai.jobs import handlers, investigation_job, queue
from reckonai.models import Job, ReconciliationException, Tenant


@pytest.fixture
def client(session: Session, tenant: Tenant, database_url: str) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_tenant_id] = lambda: tenant.id
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _jobs(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Job)) or 0


def test_investigate_returns_202_and_a_queued_job(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    response = client.post(f"/api/exceptions/{seeded_exception.id}/investigate")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert _jobs(session) == 1


def test_a_double_click_is_one_job(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    first = client.post(f"/api/exceptions/{seeded_exception.id}/investigate").json()
    second = client.post(f"/api/exceptions/{seeded_exception.id}/investigate").json()
    assert first["id"] == second["id"]
    assert _jobs(session) == 1


def test_a_finished_job_can_be_requested_again(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    first = client.post(f"/api/exceptions/{seeded_exception.id}/investigate").json()
    job = session.get(Job, first["id"])
    assert job is not None
    queue.complete(session, job)
    session.flush()
    second = client.post(f"/api/exceptions/{seeded_exception.id}/investigate").json()
    assert second["id"] != first["id"]


def test_an_unknown_exception_is_404_and_enqueues_nothing(
    client: TestClient, session: Session
) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/exceptions/{unknown}/investigate").status_code == 404
    assert _jobs(session) == 0


def test_the_handler_investigates_the_exceptions_order_and_code(
    session: Session,
    tenant: Tenant,
    seeded_exception: ReconciliationException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(investigation_job, "provider_for", lambda _s: "provider")

    def fake(_session: Session, tenant_id: object, **kwargs: object) -> None:
        seen.update(kwargs, tenant_id=tenant_id)

    monkeypatch.setattr(investigation_job, "investigate", fake)
    investigation_job.execute(
        session,
        tenant_id=tenant.id,
        payload=investigation_job.payload_for(seeded_exception.id),
    )
    assert seen["order_ref"] == "ORD-TEST"
    assert seen["code"] == "commission_mismatch"
    assert seen["tenant_id"] == tenant.id


def test_a_missing_credential_fails_the_job_with_the_reason_visible(
    client: TestClient,
    session: Session,
    seeded_exception: ReconciliationException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_MODEL", "claude-haiku-5-5")
    from reckonai.config import get_settings

    get_settings.cache_clear()
    try:
        queued = client.post(
            f"/api/exceptions/{seeded_exception.id}/investigate"
        ).json()
        job = session.get(Job, queued["id"])
        assert job is not None
        job.status, job.attempts = "claimed", 1
        session.flush()
        session.commit()  # dispatch requires a committed claim; a savepoint here
        assert handlers.dispatch(session, job) is False
    finally:
        get_settings.cache_clear()

    detail = client.get(f"/api/exceptions/{seeded_exception.id}").json()
    assert "ANTHROPIC_API_KEY" in detail["investigation_job"]["last_error"]
    assert detail["investigation"] is None


def test_the_type_is_registered_and_allowed() -> None:
    from reckonai.models import JOB_TYPES

    assert investigation_job.JOB_TYPE in JOB_TYPES
    assert investigation_job.JOB_TYPE in handlers.HANDLERS
