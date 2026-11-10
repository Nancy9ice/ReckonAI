"""Run-now, openable evidence, coverage and CSV export."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data
from reckonai.api import export
from reckonai.api.deps import get_session, get_tenant_id
from reckonai.app import app
from reckonai.jobs import reconcile_job, window
from reckonai.models import (
    Capture,
    ExceptionDecision,
    Job,
    PlatformFee,
    ReconciliationException,
    Tenant,
)
from reckonai.sim import data


@pytest.fixture
def client(session: Session, tenant: Tenant, database_url: str) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_tenant_id] = lambda: tenant.id
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def demo(session: Session) -> uuid.UUID:
    """The real seeded dataset, inside the rolled-back session, reconciled."""
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()
    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None
    reconcile_job.run_window(
        session,
        tenant_id=tenant_id,
        win=window.Window(
            start=datetime(2026, 10, 12, tzinfo=UTC),
            end=datetime(2026, 11, 21, tzinfo=UTC),
        ),
    )
    session.flush()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_tenant_id] = lambda: tenant_id
    with TestClient(app) as test_client:
        yield test_client  # type: ignore[misc]
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_an_order_reference_opens_the_order(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    body = client.get("/api/evidence/ORD-TEST").json()
    assert body["kind"] == "order"
    total = next(f for f in body["fields"] if f["label"] == "Total")
    assert total["money"] == {"amount": "100.00", "currency": "USD"}


def test_a_reference_that_names_nothing_is_404(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    assert client.get("/api/evidence/GHOST-1").status_code == 404
    assert client.get("/api/evidence/GHOST-1/raw").status_code == 404


def test_another_tenants_record_is_not_resolvable(
    client: TestClient, other_tenant: Tenant, seeded_exception: ReconciliationException
) -> None:
    app.dependency_overrides[get_tenant_id] = lambda: other_tenant.id
    assert client.get("/api/evidence/ORD-TEST").status_code == 404


def test_a_capture_opens_with_its_absent_fee_shown_as_absent(
    demo: TestClient, session: Session
) -> None:
    capture = session.scalars(select(Capture)).first()
    assert capture is not None
    body = demo.get(f"/api/evidence/{capture.external_id}").json()
    assert body["kind"] == "capture"
    fee = next(f for f in body["fields"] if f["label"] == "Paypal fee")
    # No seeded capture states a PayPal fee: absent, not zero (P3).
    assert fee["money"] is None
    assert fee["value"] is None
    gross = next(f for f in body["fields"] if f["label"] == "Gross amount")
    assert gross["money"]["currency"]


def test_the_retained_provider_payload_is_reachable(
    demo: TestClient, session: Session
) -> None:
    capture = session.scalars(select(Capture)).first()
    assert capture is not None
    opened = demo.get(f"/api/evidence/{capture.external_id}").json()
    assert opened["raw"]["source"] == "paypal"
    raw = demo.get(f"/api/evidence/{capture.external_id}/raw")
    assert raw.status_code == 200
    assert isinstance(raw.json(), dict)


def test_a_platform_fee_id_resolves_but_is_marked_unstable(
    demo: TestClient, session: Session
) -> None:
    fee = session.scalars(select(PlatformFee)).first()
    assert fee is not None
    body = demo.get(f"/api/evidence/{fee.external_id}").json()
    assert body["kind"] == "platform_fee"
    assert body["stable"] is False


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_coverage_accounts_for_every_order(demo: TestClient) -> None:
    cov = demo.get("/api/coverage").json()
    assert cov["orders_total"] == 18
    assert cov["with_exceptions"] == 5
    flagged = {e["order_ref"] for e in demo.get("/api/exceptions").json()}
    parked = {p["order_ref"] for p in cov["parked_items"]}
    clean = {c["order_ref"] for c in cov["clean_orders"]}
    assert not clean & (flagged | parked)
    assert cov["clean"] == 18 - len(flagged | parked)
    assert "not which" in cov["basis"]


def test_a_parked_finding_is_listed_and_is_not_an_exception(demo: TestClient) -> None:
    cov = demo.get("/api/coverage").json()
    assert cov["parked"] == len(cov["parked_items"]) >= 1
    queue_orders = {e["order_ref"] for e in demo.get("/api/exceptions").json()}
    # The manifest's parked order is a lifecycle state, never a queue row.
    assert all(
        p["order_ref"] not in queue_orders or p["detail"] for p in cov["parked_items"]
    )


# ---------------------------------------------------------------------------
# Run now
# ---------------------------------------------------------------------------


def test_no_orders_means_no_run(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    assert client.post("/api/runs").json()["status"] == "no_orders"
    assert session.scalar(select(Job.id).where(Job.tenant_id == tenant.id)) is None


def test_a_run_is_queued_then_reported_in_flight(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    first = client.post("/api/runs")
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    second = client.post("/api/runs").json()
    assert second["status"] == "in_flight"
    assert second["job"]["id"] == first.json()["job"]["id"]


def test_the_queued_window_covers_the_whole_dataset(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    body = client.post("/api/runs").json()
    start = datetime.fromisoformat(body["window_start"])
    end = datetime.fromisoformat(body["window_end"])
    assert start <= datetime.now(UTC) <= end


def test_once_done_today_a_second_request_is_answered_not_queued(
    client: TestClient,
    session: Session,
    tenant: Tenant,
    seeded_exception: ReconciliationException,
) -> None:
    queued = client.post("/api/runs").json()
    job = session.get(Job, queued["job"]["id"])
    assert job is not None
    reconcile_job.execute(session, tenant_id=tenant.id, payload=dict(job.payload))
    job.status = "done"
    session.flush()
    again = client.post("/api/runs").json()
    assert again["status"] == "already_done"
    assert again["job"] is None


def test_a_job_can_be_polled_and_only_by_its_tenant(
    client: TestClient, seeded_exception: ReconciliationException, other_tenant: Tenant
) -> None:
    job_id = client.post("/api/runs").json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    app.dependency_overrides[get_tenant_id] = lambda: other_tenant.id
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["=SUM(A1)", "@x", "\tcmd", "-1+2", "+cmd|x", "-SUM(A1)", "-12.50 USD"]
)
def test_a_cell_that_would_execute_is_neutralised(value: str) -> None:
    assert export.safe(value).startswith("'")


@pytest.mark.parametrize("value", ["-12.50", "+1", "0.00", "90.00", "-0.01"])
def test_a_plain_number_is_left_alone_so_a_negative_amount_stays_a_number(
    value: str,
) -> None:
    assert export.safe(value) == value


def test_ordinary_cells_are_untouched() -> None:
    assert export.safe("Aurora Audio") == "Aurora Audio"
    assert export.safe(None) == ""
    assert export.safe("90.00") == "90.00"


def test_the_queue_exports_amounts_as_strings_with_their_currency(
    client: TestClient, seeded_exception: ReconciliationException
) -> None:
    response = client.get("/api/export/exceptions.csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().split("\r\n")
    header, first = lines[0].split(","), lines[1].split(",")
    assert header[:2] == ["priority", "status"]
    row = dict(zip(header, first, strict=False))
    assert row["impact_amount"] == "10.00"
    assert row["impact_currency"] == "USD"
    assert row["decision"] == "undecided"


def test_the_decision_log_keeps_superseded_decisions_and_neutralises_notes(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    for outcome, note in (("rejected", "=HYPERLINK(1)"), ("approved", "fine")):
        client.post(
            f"/api/exceptions/{seeded_exception.id}/decision",
            json={"outcome": outcome, "approver": "Nancy", "note": note},
        )
    text = client.get("/api/export/decisions.csv").text
    assert text.count("Nancy") == 2
    assert "'=HYPERLINK(1)" in text
    assert session.query(ExceptionDecision).count() == 2


# ---------------------------------------------------------------------------
# Found by the second code review
# ---------------------------------------------------------------------------


def test_an_order_with_an_exception_and_a_parked_finding_is_counted_once(
    client: TestClient, session: Session, seeded_exception: ReconciliationException
) -> None:
    session.add(
        ReconciliationException(
            tenant_id=seeded_exception.tenant_id,
            run_id=seeded_exception.run_id,
            code="awaiting_evidence",
            order_external_ref=seeded_exception.order_external_ref,
            detail="late",
            is_exception=False,
            impact=seeded_exception.impact,
            evidence_refs=[],
            evidence_basis="provider_reported",
        )
    )
    session.flush()
    cov = client.get("/api/coverage").json()
    assert cov["with_exceptions"] == 1
    assert cov["parked"] == 0
    assert (
        cov["with_exceptions"] + cov["parked"] + cov["clean"] + cov["not_examined"]
        == cov["orders_total"]
    )


def test_an_order_no_run_has_examined_is_not_called_clean(
    demo: TestClient, session: Session
) -> None:
    from datetime import timedelta

    from reckonai.models import Order
    from reckonai.money import Money

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    before = demo.get("/api/coverage").json()
    # Ingested after the run finished, inside its window.
    session.add(
        Order(
            tenant_id=tenant_id,
            external_ref="ORD-LATE",
            total=Money("10.00", "USD"),
            placed_at=datetime(2026, 11, 5, tzinfo=UTC),
            created_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.flush()
    cov = demo.get("/api/coverage").json()
    assert "ORD-LATE" in cov["not_examined_orders"]
    assert cov["clean"] == before["clean"]
    assert cov["not_examined"] == before["not_examined"] + 1
    assert (
        cov["with_exceptions"] + cov["parked"] + cov["clean"] + cov["not_examined"]
        == cov["orders_total"]
    )


def test_with_no_run_at_all_nothing_is_clean(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    from reckonai.models import Order
    from reckonai.money import Money

    session.add(
        Order(
            tenant_id=tenant.id,
            external_ref="ORD-ALONE",
            total=Money("10.00", "USD"),
            placed_at=datetime(2026, 11, 5, tzinfo=UTC),
        )
    )
    session.flush()
    cov = client.get("/api/coverage").json()
    assert cov["clean"] == 0
    assert cov["not_examined"] == cov["orders_total"] == 1
    assert cov["not_examined_orders"] == ["ORD-ALONE"]
