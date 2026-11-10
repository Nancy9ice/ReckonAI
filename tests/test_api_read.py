"""The read API: money as strings, latest-per-key reads, tenant scoping.

The app's own engine cannot see a rolled-back test transaction, so the session
dependency is overridden to hand routes the test session. Everything else - the
routing, the response models, the queries - is the deployed code.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from reckonai.api.deps import get_session, get_tenant_id
from reckonai.app import app
from reckonai.models import (
    ExceptionDecision,
    Investigation,
    InvestigationCause,
    ReconciliationException,
    Tenant,
)


@pytest.fixture
def client(session: Session, tenant: Tenant, database_url: str) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_tenant_id] = lambda: tenant.id
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _investigation(
    session: Session,
    exc: ReconciliationException,
    verdict: str,
    confidence: str | None,
    minutes_ago: int = 0,
) -> Investigation:
    inv = Investigation(
        tenant_id=exc.tenant_id,
        order_external_ref=exc.order_external_ref,
        code=exc.code,
        provider="openai",
        model_requested="gpt-4.1",
        verdict=verdict,
        # now() is constant inside one transaction, so order explicitly.
        started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    session.add(inv)
    session.flush()
    if confidence is not None:
        session.add(
            InvestigationCause(
                tenant_id=exc.tenant_id,
                investigation_id=inv.id,
                rank=1,
                description="The refund was never booked.",
                confidence=Decimal(confidence),
                cited_refs=["ORD-TEST"],
                unresolved_refs=["GHOST-1"],
            )
        )
        session.flush()
    return inv


def test_the_queue_lists_the_exception_with_money_as_strings(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    body = client.get("/api/exceptions").json()
    assert len(body) == 1
    row = body[0]
    assert row["impact"] == {"amount": "10.00", "currency": "USD"}
    assert row["certainty"] == "confirmed"
    assert row["order_ref"] == "ORD-TEST"
    assert row["seller_count"] == 1
    assert isinstance(row["priority"]["score"], str)


def test_an_uninvestigated_exception_has_no_confidence_not_zero(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    row = client.get("/api/exceptions").json()[0]
    assert row["investigation"] is None
    assert row["priority"]["factors"]["confidence"] is None


def test_suspected_is_distinct_from_confirmed(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    seeded_exception.confirmed = False
    session.flush()
    assert client.get("/api/exceptions").json()[0]["certainty"] == "suspected"


def test_the_newest_investigation_is_the_one_shown(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    _investigation(session, seeded_exception, "failed", None, minutes_ago=5)
    newer = _investigation(session, seeded_exception, "reported", "0.800")
    session.flush()
    row = client.get("/api/exceptions").json()[0]
    assert row["investigation"]["id"] == str(newer.id)
    assert row["investigation"]["top_confidence"] == "0.8000"
    assert row["priority"]["factors"]["confidence"] == "0.8000"


def test_detail_marks_unresolved_citations_and_withheld_kinds(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    inv = _investigation(session, seeded_exception, "reported", "0.800")
    inv.withheld = ["ledger"]
    session.flush()
    detail = client.get(f"/api/exceptions/{seeded_exception.id}").json()
    cause = detail["investigation_detail"]["causes"][0]
    assert cause["unresolved_refs"] == ["GHOST-1"]
    assert detail["investigation_detail"]["withheld"] == ["ledger"]


def test_the_effective_decision_is_the_newest(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    for outcome in ("rejected", "approved"):
        session.add(
            ExceptionDecision(
                tenant_id=seeded_exception.tenant_id,
                exception_id=seeded_exception.id,
                outcome=outcome,
                approver="Nancy",
            )
        )
        session.flush()
    from sqlalchemy import text

    # now() is constant inside one transaction, so order the two explicitly.
    session.execute(
        text(
            "UPDATE exception_decision SET decided_at = now() - interval '1 hour' "
            "WHERE outcome = 'rejected'"
        )
    )
    detail = client.get(f"/api/exceptions/{seeded_exception.id}").json()
    assert detail["decision"]["outcome"] == "approved"
    assert [d["outcome"] for d in detail["decisions"]] == ["approved", "rejected"]


def test_runs_are_listed_and_filter_the_queue(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    runs = client.get("/api/runs").json()
    assert [r["id"] for r in runs] == [str(seeded_exception.run_id)]
    assert (
        len(client.get(f"/api/exceptions?run_id={seeded_exception.run_id}").json()) == 1
    )
    other = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/exceptions?run_id={other}").json() == []


def test_an_unknown_exception_is_404(client: TestClient) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/exceptions/{unknown}").status_code == 404


def test_another_tenants_exception_is_invisible(
    client: TestClient,
    session: Session,
    other_tenant: Tenant,
    seeded_exception: ReconciliationException,
) -> None:
    app.dependency_overrides[get_tenant_id] = lambda: other_tenant.id
    assert client.get("/api/exceptions").json() == []
    assert client.get(f"/api/exceptions/{seeded_exception.id}").status_code == 404
