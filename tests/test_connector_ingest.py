"""Ingestion: zero data loss, idempotence, and quarantine instead of defaults.

specs/roadmap.md Phase 6's first and third exit criteria, and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D2, D9 and
D11.

**The whole path runs over real HTTP**, through `httpx2.ASGITransport` against
the in-process app - real request parsing, real JSON serialization, real
response, no socket. That matters because the deliberately non-canonical field
names and money-as-strings only exist on the wire, so an in-process shortcut
would test the mapping against data that never crossed a boundary.

**The ingest target is a different tenant from the one the sim serves** (D2).
Reading and writing the same tenant would make a row-count comparison compare
a number against itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx2 as httpx
import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from reckonai.app import app
from reckonai.connector.client import (
    MarketplaceClient,
    MarketplaceNotConfigured,
    MarketplaceUnavailable,
    Page,
)
from reckonai.connector.ingest import ensure_tenant, ingest
from reckonai.connector.sim_mapping import SIM_MAPPING
from reckonai.models import (
    ConnectorMapping,
    Order,
    OrderItem,
    Payment,
    QuarantinedRecord,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
    Tenant,
)
from reckonai.money import Money
from reckonai.sim import data, seed

MAPPING_VERSION = 1


@pytest.fixture(scope="module")
def environment(request: pytest.FixtureRequest) -> Iterator[Engine]:
    """A seeded sim tenant and an empty ingest tenant, both committed.

    Committed rather than rolled back because the sim's endpoints open their
    own `Session` from `app.state.engine` and cannot see a transaction a test
    fixture is holding open.
    """
    engine: Engine = request.getfixturevalue("migrated_database")

    with Session(bind=engine) as session:
        _drop(session, data.TENANT_SLUG)
        _drop(session, data.INGEST_TENANT_SLUG)
        session.commit()
        seed.build(session, data.FULL)
        session.commit()

    yield engine

    with Session(bind=engine) as session:
        _drop(session, data.TENANT_SLUG)
        _drop(session, data.INGEST_TENANT_SLUG)
        session.commit()


def _drop(session: Session, slug: str) -> None:
    """Delete a tenant and its rows, children first.

    Every foreign key here is `ondelete="RESTRICT"` - deleting evidence under a
    financial record is never silent - so there is no cascade and the order is
    explicit.
    """
    from reckonai.models import (
        Capture,
        CommissionRule,
        Disbursement,
        Dispute,
        FxConversion,
        PlatformFee,
    )

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    if tenant_id is None:
        return

    for model in (
        FxConversion,
        PlatformFee,
        Disbursement,
        QuarantinedRecord,
        SellerLedgerEntry,
        Refund,
        Dispute,
        Capture,
        Payment,
        OrderItem,
        CommissionRule,
        Order,
        ConnectorMapping,
        Seller,
        RawPayload,
    ):
        session.execute(delete(model).where(model.tenant_id == tenant_id))
    session.execute(delete(Tenant).where(Tenant.id == tenant_id))


@pytest.fixture
def client(running_app: object) -> Iterator[MarketplaceClient]:
    """The connector, pointed at the in-process app.

    **Bridged through Starlette's `TestClient` rather than
    `httpx2.ASGITransport`**, which is async-only - a synchronous
    `httpx2.Client` cannot drive it, and the connector is synchronous because
    everything calling it is. `TestClient` already owns the sync-to-ASGI
    translation, so the request is still built, serialized, routed through
    FastAPI and parsed for real; only the socket is absent.
    """
    with MarketplaceClient(
        base_url="http://sim.test/sim",
        transport=httpx.MockTransport(_forward_to(running_app)),
    ) as marketplace:
        yield marketplace


def _forward_to(test_client: object):
    """A MockTransport handler that replays the request through `TestClient`."""

    def handler(request: httpx.Request) -> httpx.Response:
        replayed = test_client.request(  # type: ignore[attr-defined]
            request.method,
            request.url.path,
            params=dict(request.url.params),
        )
        return httpx.Response(
            replayed.status_code,
            content=replayed.content,
            headers={"content-type": replayed.headers.get("content-type", "")},
        )

    return handler


@pytest.fixture
def ingest_tenant(environment: Engine) -> Iterator[uuid.UUID]:
    """A fresh ingest tenant per test, rolled back afterwards."""
    with Session(bind=environment) as session:
        tenant_id = ensure_tenant(
            session,
            slug=f"ingest-{uuid.uuid4().hex[:8]}",
            name="Ingest Target",
        )
        session.commit()
        yield tenant_id
        _drop_by_id(session, tenant_id)
        session.commit()


def _drop_by_id(session: Session, tenant_id: uuid.UUID) -> None:
    slug = session.scalar(select(Tenant.slug).where(Tenant.id == tenant_id))
    if slug:
        _drop(session, slug)


@pytest.fixture
def running_app(environment: Engine) -> Iterator[object]:
    """A `TestClient` inside its lifespan, so the sim's endpoints have an engine.

    The sim's `_session` dependency reads `app.state.engine`, which the
    lifespan builds. Without entering it the endpoints answer 503 - which is
    deliberate: those endpoints read a database and a test that wants them has
    to say so.
    """
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


def test_the_client_refuses_to_run_without_a_base_url() -> None:
    """D10: no default, because a default would be a plausible-looking host.

    A connector that fell back to one would reconcile a tenant's books against
    the wrong marketplace and report every difference as a discrepancy - a
    confident wrong answer, which P3 forbids.
    """
    from reckonai.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(MarketplaceNotConfigured, match="no default"):
            MarketplaceClient()
    finally:
        get_settings.cache_clear()


def test_a_truncated_page_is_refused_rather_than_accepted() -> None:
    """The stated count is the only independent figure available.

    `len(records)` compared against itself proves nothing; compared against
    what the source claimed it catches a page that lost rows in transit - which
    every later count would otherwise agree on.
    """
    with pytest.raises(MarketplaceUnavailable, match="silently accepted is data loss"):
        Page(
            endpoint="GET /x",
            records=[{"a": 1}],
            stated_count=2,
            schema_sha256="x",
        )


def test_the_client_reads_the_sim_over_real_http(
    client: MarketplaceClient,
) -> None:
    page = client.fetch("/marketplace/orders")

    assert page.stated_count == len(data.FULL.orders)
    assert page.records[0]["order_ref"] == "ORD-0001"
    # Money crossed as a string, which is the thing an in-process call would
    # never have exercised.
    assert page.records[0]["order_gross"] == "160.00"
    assert isinstance(page.records[0]["order_gross"], str)


def test_the_client_hashes_the_observed_source_schema(
    client: MarketplaceClient,
) -> None:
    """Section 9.6: schema drift is detected, not absorbed."""
    first = client.fetch("/marketplace/orders")
    again = client.fetch("/marketplace/orders")

    assert first.schema_sha256 == again.schema_sha256
    assert first.schema_sha256 != client.fetch(
        "/marketplace/sellers"
    ).schema_sha256


# ---------------------------------------------------------------------------
# Phase 6's exit criteria
# ---------------------------------------------------------------------------


def _run(
    session: Session, client: MarketplaceClient, tenant_id: uuid.UUID
) -> object:
    return ingest(
        session,
        client,
        tenant_id=tenant_id,
        document=SIM_MAPPING,
        mapping_version=MAPPING_VERSION,
        retrieved_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
    )


def test_the_full_dataset_ingests_with_zero_data_loss(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """Phase 6's first exit criterion, as the identity that makes it checkable.

    Every retrieved record is either written or quarantined. A record that was
    skipped would be absent from the canonical tables *and* from any count,
    which is indistinguishable from one that was never sent - so the identity
    is the check, not the row counts alone.
    """
    with Session(bind=environment) as session:
        result = _run(session, client, ingest_tenant)
        session.commit()

        assert result.accounts_for_everything(), (
            f"retrieved {result.total_retrieved}, wrote {result.total_written}, "
            f"quarantined {result.total_quarantined}"
        )
        assert result.total_retrieved > 0
        assert result.total_quarantined == 0, (
            f"the sim's own data should map cleanly: "
            f"{dict(result.quarantined)}"
        )


def test_the_accounting_identity_can_actually_fail() -> None:
    """**This test exists because the one above could not fail.**

    Mutation testing found it: replacing `accounts_for_everything` with
    `return True` left the whole suite green. Every test that calls it does so
    where the identity genuinely holds, so the method was both the claim and
    its own witness - the same shape as the Phase 3 gate that passed with its
    subject commented out.

    So the method is exercised directly, on numbers that do not add up.
    """
    from reckonai.connector.ingest import IngestResult

    honest = IngestResult(tenant_id=uuid.uuid4(), mapping_version=1)
    honest.retrieved["order"] = 10
    honest.inserted["order"] = 7
    honest.quarantined["order"] = 3
    assert honest.accounts_for_everything()

    # Three records retrieved, neither written nor quarantined: the silent
    # skip this identity exists to catch.
    lossy = IngestResult(tenant_id=uuid.uuid4(), mapping_version=1)
    lossy.retrieved["order"] = 10
    lossy.inserted["order"] = 7
    assert not lossy.accounts_for_everything(), (
        "the identity accepted a run that lost three records"
    )

    # And double-counting, which is the opposite failure.
    doubled = IngestResult(tenant_id=uuid.uuid4(), mapping_version=1)
    doubled.retrieved["order"] = 10
    doubled.inserted["order"] = 7
    doubled.updated["order"] = 7
    assert not doubled.accounts_for_everything()


def test_zero_data_loss_measured_from_rows_not_from_counters(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """The same claim, independent of the ingester's own bookkeeping.

    The test above makes the counters trustworthy; this one does not use them.
    It counts what the sim **served** over HTTP against what landed in the
    database, per table, so a result object that lied about everything could
    not make this pass.
    """
    served = {
        "seller": len(client.fetch("/marketplace/sellers").records),
        "order": len(client.fetch("/marketplace/orders").records),
        "order_item": len(client.fetch("/marketplace/order-items").records),
        "payment": len(client.fetch("/marketplace/payments").records),
        "refund": len(client.fetch("/marketplace/refunds").records),
        "seller_ledger_entry": len(
            client.fetch("/marketplace/ledger-entries").records
        ),
    }
    assert all(count > 0 for count in served.values()), served

    with Session(bind=environment) as session:
        _run(session, client, ingest_tenant)
        session.commit()

        models = {
            "seller": Seller,
            "order": Order,
            "order_item": OrderItem,
            "payment": Payment,
            "refund": Refund,
            "seller_ledger_entry": SellerLedgerEntry,
        }

        quarantined = session.scalar(
            select(func.count())
            .select_from(QuarantinedRecord)
            .where(QuarantinedRecord.tenant_id == ingest_tenant)
        )

        for table, expected in served.items():
            landed = session.scalar(
                select(func.count())
                .select_from(models[table])
                .where(models[table].tenant_id == ingest_tenant)
            )
            assert landed == expected, (
                f"{table}: the sim served {expected} records and {landed} "
                f"landed. Quarantined overall: {quarantined}."
            )

        assert quarantined == 0


def test_the_ingested_counts_match_the_sim_tenant(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """The row-count comparison D2's separate tenant makes meaningful.

    Reading and writing one tenant would compare a number against itself.
    """
    with Session(bind=environment) as session:
        _run(session, client, ingest_tenant)
        session.commit()

        sim_id = session.scalar(
            select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
        )
        assert sim_id is not None and sim_id != ingest_tenant

        def count(model: object, tenant: uuid.UUID) -> int:
            return session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == tenant)
            )

        for model in (Seller, Order, OrderItem, Payment, SellerLedgerEntry):
            assert count(model, ingest_tenant) == count(model, sim_id), (
                f"{model.__tablename__} differs between the two tenants"
            )

        # Refunds are the one table where the counts legitimately differ: the
        # sim tenant holds both a marketplace and a PayPal row per refund, and
        # the connector reads only the marketplace endpoint.
        assert count(Refund, ingest_tenant) == len(
            [r for o in data.FULL.orders for r in o.refunds]
        )


def test_the_ingested_amounts_match_exactly(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """Counts agreeing while amounts drift is the failure a count misses.

    Compared as `Decimal` through `Money`, per currency, so a value that
    round-tripped through a float would fail here.
    """
    with Session(bind=environment) as session:
        _run(session, client, ingest_tenant)
        session.commit()

        sim_id = session.scalar(
            select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
        )

        for currency in ("USD", "EUR"):
            sums = []
            for tenant in (sim_id, ingest_tenant):
                rows = session.scalars(
                    select(Order).where(Order.tenant_id == tenant)
                )
                total = Money("0", currency)
                for row in rows:
                    if row.total.currency == currency:
                        total = total + row.total
                sums.append(total)
            assert sums[0] == sums[1], f"{currency} order totals differ"


def test_re_running_ingestion_is_idempotent(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """Phase 6's third exit criterion.

    Asserted on **rows**, not on the result counters: a second run reports its
    records as written, which is correct - the row is there - and what must not
    change is the number of rows.
    """
    with Session(bind=environment) as session:
        first = _run(session, client, ingest_tenant)
        session.commit()

        before = {
            model.__tablename__: session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == ingest_tenant)
            )
            for model in (
                Seller, Order, OrderItem, Payment, Refund,
                SellerLedgerEntry, RawPayload,
            )
        }

        second = _run(session, client, ingest_tenant)
        session.commit()

        after = {
            model.__tablename__: session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == ingest_tenant)
            )
            for model in (
                Seller, Order, OrderItem, Payment, Refund,
                SellerLedgerEntry, RawPayload,
            )
        }

        assert before == after, (
            f"the second run changed row counts: {before} -> {after}"
        )
        assert first.inserted, "the first run inserted nothing, so this proves nothing"
        assert not second.inserted, (
            f"the second run inserted {dict(second.inserted)}"
        )


def test_an_unchanged_re_read_writes_no_second_payload(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """Half of what makes ingestion idempotent.

    `UNIQUE (tenant_id, source, source_record_type, body_sha256)` means an
    identical body is the same row. A *changed* body writes a second row
    deliberately, which is the mechanism Block D's payload diff needs.
    """
    with Session(bind=environment) as session:
        _run(session, client, ingest_tenant)
        session.commit()
        first = session.scalar(
            select(func.count())
            .select_from(RawPayload)
            .where(RawPayload.tenant_id == ingest_tenant)
        )

        _run(session, client, ingest_tenant)
        session.commit()
        second = session.scalar(
            select(func.count())
            .select_from(RawPayload)
            .where(RawPayload.tenant_id == ingest_tenant)
        )

        assert first == second


def test_every_ingested_payload_records_its_endpoint_and_mapping_version(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """D13 and Phase 3's reason for the `endpoint` column.

    Field availability depends on the read path, so a payload whose path is
    unrecorded cannot be interpreted later. And section 9.1 requires every
    canonical record to carry the mapping version that produced it, which it
    reaches through this row.
    """
    with Session(bind=environment) as session:
        _run(session, client, ingest_tenant)
        session.commit()

        rows = list(
            session.scalars(
                select(RawPayload).where(RawPayload.tenant_id == ingest_tenant)
            )
        )
        assert rows

        for row in rows:
            assert row.endpoint is not None, row.source_record_id
            assert "/sim/marketplace/" in row.endpoint
            assert row.mapping_version == MAPPING_VERSION
            assert row.source == "marketplace"


def test_the_seeded_side_still_records_no_endpoint(
    environment: Engine, client: MarketplaceClient, ingest_tenant: uuid.UUID
) -> None:
    """D2's second separator, asserted as a contrast rather than in isolation.

    The seeded rows have no read path and the ingested ones do, which is what
    tells the two apart now that `raw_payload.source` cannot.
    """
    with Session(bind=environment) as session:
        _run(session, client, ingest_tenant)
        session.commit()

        sim_id = session.scalar(
            select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
        )

        seeded_endpoints = set(
            session.scalars(
                select(RawPayload.endpoint).where(RawPayload.tenant_id == sim_id)
            )
        )
        ingested_endpoints = set(
            session.scalars(
                select(RawPayload.endpoint).where(
                    RawPayload.tenant_id == ingest_tenant
                )
            )
        )

        assert seeded_endpoints == {None}
        assert None not in ingested_endpoints


# ---------------------------------------------------------------------------
# Quarantine (D11)
# ---------------------------------------------------------------------------


def test_a_record_missing_a_required_field_is_quarantined_not_defaulted(
    environment: Engine, ingest_tenant: uuid.UUID
) -> None:
    """Section 9.6: visible, countable, not skipped and not defaulted.

    Driven through a stub transport rather than the sim, because the sim's own
    data maps cleanly - which is the point of it. A fault injected at the
    transport is the only way to reach this branch without corrupting the
    baseline.
    """
    incomplete = {
        "count": 1,
        "records": [
            {
                "vendor_id": "vendor-broken",
                # `display_name` is required and absent.
                "payout_account": "M9",
                "trading_ccy": "USD",
                "fee_capable": True,
                "hold_capable": False,
            }
        ],
    }

    with Session(bind=environment) as session:
        with _stub_client({"/marketplace/sellers": incomplete}) as stub:
            result = ingest(
                session,
                stub,
                tenant_id=ingest_tenant,
                document=_sellers_only(),
                mapping_version=MAPPING_VERSION,
            )
        session.commit()

        assert result.total_quarantined == 1
        assert result.total_written == 0
        assert result.accounts_for_everything()

        row = session.scalar(
            select(QuarantinedRecord).where(
                QuarantinedRecord.tenant_id == ingest_tenant
            )
        )
        assert row is not None
        assert row.reason == "missing_required_field"
        assert row.field_name == "name"
        assert row.source_reference == "vendor-broken"
        assert row.mapping_version == MAPPING_VERSION

        # No seller row was written, and nothing was defaulted.
        assert session.scalar(
            select(func.count())
            .select_from(Seller)
            .where(Seller.tenant_id == ingest_tenant)
        ) == 0

        # The evidence survives, so the gap is recoverable once the mapping is
        # fixed - section 9.6: "an unmapped source field is not lost."
        assert row.raw_payload_id is not None


def test_an_amount_without_a_currency_is_quarantined_never_zeroed(
    environment: Engine, ingest_tenant: uuid.UUID
) -> None:
    """The failure section 9.3 calls the most likely quiet mapping error.

    "Defaulting money is how a reconciliation system produces a confident wrong
    answer" - so the record is quarantined and no order row appears with a
    zero total.
    """
    orders = {
        "count": 1,
        "records": [
            {
                "order_ref": "ORD-BROKEN",
                "order_gross": "99.00",
                # `ccy` absent.
                "booked_on": "2026-11-08T12:00:00+00:00",
                "payout_timing": "INSTANT",
            }
        ],
    }

    with Session(bind=environment) as session:
        with _stub_client({"/marketplace/orders": orders}) as stub:
            result = ingest(
                session,
                stub,
                tenant_id=ingest_tenant,
                document=_orders_only(),
                mapping_version=MAPPING_VERSION,
            )
        session.commit()

        assert result.total_quarantined == 1

        row = session.scalar(
            select(QuarantinedRecord).where(
                QuarantinedRecord.tenant_id == ingest_tenant
            )
        )
        assert row is not None
        assert row.reason == "incomplete_money"
        assert row.field_name == "total"
        assert "never a default" in row.detail

        assert session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.tenant_id == ingest_tenant)
        ) == 0


def test_an_unresolved_reference_is_quarantined(
    environment: Engine, ingest_tenant: uuid.UUID
) -> None:
    """Section 9.5: an unresolved link may not silently drive a conclusion."""
    sellers = {"count": 0, "records": []}
    orders = {"count": 0, "records": []}
    items = {
        "count": 1,
        "records": [
            {
                "line_ref": "ITEM-ORPHAN",
                "order_ref": "ORD-NOWHERE",
                "vendor_id": "vendor-nowhere",
                "product_class": "audio",
                "units": 1,
                "unit_cost": "5.00",
                "item_total": "5.00",
                "ccy": "USD",
            }
        ],
    }

    with Session(bind=environment) as session:
        with _stub_client(
            {
                "/marketplace/sellers": sellers,
                "/marketplace/orders": orders,
                "/marketplace/order-items": items,
            }
        ) as stub:
            from reckonai.connector.mapping import MappingDocument

            result = ingest(
                session,
                stub,
                tenant_id=ingest_tenant,
                document=MappingDocument(
                    connector="t",
                    resources=(
                        SIM_MAPPING.resource("seller"),
                        SIM_MAPPING.resource("order"),
                        SIM_MAPPING.resource("order_item"),
                    ),
                ),
                mapping_version=MAPPING_VERSION,
            )
        session.commit()

        assert result.total_quarantined == 1
        row = session.scalar(
            select(QuarantinedRecord).where(
                QuarantinedRecord.tenant_id == ingest_tenant
            )
        )
        assert row is not None
        assert row.reason == "unresolved_reference"


# ---------------------------------------------------------------------------
# Stub transport helpers
# ---------------------------------------------------------------------------


def _stub_client(bodies: dict[str, dict]) -> MarketplaceClient:
    """A client whose transport returns canned bodies.

    The seam is a real `httpx2` transport, so the request is still built,
    serialized and parsed by the library - only the network is replaced.
    """
    import json as _json

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for key, body in bodies.items():
            if path.endswith(key):
                return httpx.Response(
                    200,
                    content=_json.dumps(body).encode(),
                    headers={"content-type": "application/json"},
                )
        return httpx.Response(404)

    return MarketplaceClient(
        base_url="http://stub.test/sim",
        transport=httpx.MockTransport(handler),
    )


def _sellers_only():
    from reckonai.connector.mapping import MappingDocument

    return MappingDocument(
        connector="t", resources=(SIM_MAPPING.resource("seller"),)
    )


def _orders_only():
    from reckonai.connector.mapping import MappingDocument

    return MappingDocument(
        connector="t", resources=(SIM_MAPPING.resource("order"),)
    )


def test_every_target_is_tenant_scoped_with_its_reference_column() -> None:
    """`TARGETS` is runtime dispatch, so mypy cannot check its entries.

    `_ingest_one` looks a model up from a string and then filters on
    `model.tenant_id` and the named reference column. Neither is statically
    verifiable at that point - the annotation is `type[Any]` because the class
    genuinely is not known - so this asserts at runtime what the type cannot.

    It is the stronger check of the two: it proves the columns exist, where a
    narrower annotation would only have said they might. The error it replaces
    was one Block B flagged as real and assigned to "Block C or D"; neither
    took it. See `specs/2026-11-10-investigation-agent/validation.md` section 9.
    """
    from reckonai.connector.ingest import TARGETS

    assert TARGETS, "the dispatch table is empty, so this guard proves nothing"

    for table, (model, key_column) in TARGETS.items():
        columns = {column.name for column in model.__table__.columns}

        assert "tenant_id" in columns, (
            f"{table} -> {model.__name__} is not tenant-scoped, so "
            "_ingest_one would filter on a column that does not exist"
        )
        assert key_column in columns, (
            f"{table} -> {model.__name__} has no {key_column!r} column, so "
            "idempotent ingestion would match on nothing"
        )
