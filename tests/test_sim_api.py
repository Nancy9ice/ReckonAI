"""The simulated marketplace's endpoints, over real HTTP.

specs/roadmap.md Phase 4's first and third exit criteria, and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D1 and D3.

**This module also carries the property the removed money probe used to hold.**
`GET /money/example` was the only place proving that FastAPI's own response
encoding does not undo `Money`'s string serialization; Block A deleted it as
Phase 4 required, and `tests/strict_json.py` moved the decoder hook here, where
it runs against endpoints that carry money in nearly every response rather than
against one synthetic route. The coverage is broader than what it replaced.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from reckonai.app import app
from reckonai.sim import data, fields, seed

# Bare module name, matching `expected_schema`'s established import in this
# suite: `tests/` is on the path but is not a package.
from strict_json import decode_strictly

#: Every list endpoint, with the field map whose names its records must use.
LIST_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("/sim/marketplace/sellers", "sellers"),
    ("/sim/marketplace/orders", "orders"),
    ("/sim/marketplace/order-items", "order_items"),
    ("/sim/marketplace/commission-rules", "commission_rules"),
    ("/sim/marketplace/payments", "payments"),
    ("/sim/marketplace/refunds", "refunds"),
    ("/sim/marketplace/ledger-entries", "ledger_entries"),
    ("/sim/marketplace/disputes", "disputes"),
    ("/sim/marketplace/entitlements", "entitlements"),
    ("/sim/marketplace/releases", "releases"),
)


@pytest.fixture(scope="module")
def seeded_client(
    request: pytest.FixtureRequest,
) -> Iterator[TestClient]:
    """A client over a database seeded with the sim's own tenant slug.

    **Module-scoped and committed, not rolled back.** The endpoints open their
    own `Session` from `app.state.engine`, so a transaction held open by a test
    fixture would be invisible to them - the rows have to be really there. The
    fixture therefore commits and deletes afterwards, which is the one place in
    this suite that does so.
    """
    engine: Engine = request.getfixturevalue("migrated_database")

    with Session(bind=engine) as session:
        # Clear any leftover from an interrupted run, so a re-run is not
        # blocked by its own previous rows.
        _drop_tenant(session, data.TENANT_SLUG)
        session.commit()

        seed.build(session)
        session.commit()

    with TestClient(app) as client:
        yield client

    with Session(bind=engine) as session:
        _drop_tenant(session, data.TENANT_SLUG)
        session.commit()


def _drop_tenant(session: Session, slug: str) -> None:
    """Delete a tenant and everything under it, children first.

    Every foreign key in this schema is `ondelete="RESTRICT"` - deleting
    evidence under a financial record is never something to do silently - so a
    cascade is not available and the order is explicit.
    """
    from sqlalchemy import delete, select

    from reckonai.models import (
        Capture,
        CommissionRule,
        Disbursement,
        Dispute,
        FxConversion,
        Order,
        OrderItem,
        Payment,
        PlatformFee,
        RawPayload,
        Refund,
        Seller,
        SellerLedgerEntry,
        Tenant,
    )

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    if tenant_id is None:
        return

    for model in (
        FxConversion,
        PlatformFee,
        Disbursement,
        SellerLedgerEntry,
        Refund,
        Dispute,
        Capture,
        Payment,
        OrderItem,
        CommissionRule,
        Order,
        Seller,
        RawPayload,
    ):
        session.execute(delete(model).where(model.tenant_id == tenant_id))
    session.execute(delete(Tenant).where(Tenant.id == tenant_id))


# ---------------------------------------------------------------------------
# The index, which needs no database
# ---------------------------------------------------------------------------


def test_the_marketplace_index_needs_no_database(database_url: str) -> None:
    """It is what `/` advertises, and `/`'s own test asserts a 200.

    Without the lifespan there is no engine at all, which is the strongest form
    of "no database" available here.
    """
    with TestClient(app) as client:
        body = client.get("/sim/marketplace").json()

    assert body["tenant_slug"] == data.TENANT_SLUG
    assert "/sim/marketplace/orders" in body["resources"]


def test_every_advertised_resource_exists(seeded_client: TestClient) -> None:
    """So the index cannot drift from the routes it lists."""
    resources = seeded_client.get("/sim/marketplace").json()["resources"]

    for path in resources:
        assert seeded_client.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# Phase 4's first exit criterion: deliberately non-canonical field names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "resource"), LIST_ENDPOINTS)
def test_a_record_uses_exactly_the_mapped_outward_names(
    seeded_client: TestClient, path: str, resource: str
) -> None:
    """The outward key set is asserted, not sampled.

    An endpoint that emitted one extra key - a canonical name leaking through -
    would pass a check that only looked for the names it expected. This
    compares the whole set.
    """
    body = seeded_client.get(path).json()
    assert body["count"] > 0, f"{path} returned nothing, so this proves nothing"

    expected = set(fields.ALL_FIELD_MAPS.get(resource)
                   or fields.NON_CANONICAL_FIELD_MAPS[resource].values())
    if resource in fields.ALL_FIELD_MAPS:
        expected = set(fields.ALL_FIELD_MAPS[resource].values())

    for record in body["records"]:
        assert set(record) == expected, (
            f"{path} record keys {sorted(record)} != {sorted(expected)}"
        )


def test_no_response_carries_a_canonical_field_name(
    seeded_client: TestClient,
) -> None:
    """Phase 4's criterion as one assertion over every endpoint.

    This is the check that would fail if someone added an endpoint that serves
    the ORM's own attribute names - the failure mode R3 names, where mapping
    looks exercised and is not.
    """
    canonical = {
        column.name
        for table in _metadata().tables.values()
        for column in table.columns
    }

    for path, _ in LIST_ENDPOINTS:
        for record in seeded_client.get(path).json()["records"]:
            leaked = set(record) & canonical
            assert not leaked, f"{path} leaked canonical names {sorted(leaked)}"


def _metadata():
    from reckonai.models import Base

    return Base.metadata


# ---------------------------------------------------------------------------
# Money over JSON - the property the removed probe used to carry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "_resource"), LIST_ENDPOINTS)
def test_no_amount_crosses_as_a_json_number(
    seeded_client: TestClient, path: str, _resource: str
) -> None:
    """The criterion itself, enforced by refusing to decode a JSON number.

    `strict_json.decode_strictly` hooks `parse_float`, so a bare `160.00` fails
    during decoding rather than after something has already looked at the
    wrong object.
    """
    response = seeded_client.get(path)

    decoded = decode_strictly(response.content)

    assert decoded["count"] > 0


def test_every_amount_is_a_string_with_a_currency_beside_it(
    seeded_client: TestClient,
) -> None:
    """The integer case `parse_float` cannot see, plus the currency rule.

    A quantized JPY amount is a legal JSON integer, so a serializer emitting it
    as a number would be parsed by `parse_int` and never reach `parse_float`.
    And specs/tech-stack.md section 9.3 treats an amount without a resolved
    currency as a hard failure: an amount key present with no currency key is
    that failure at the source.
    """
    amount_keys = {
        "order_gross",
        "unit_cost",
        "item_total",
        "paid_amount",
        "refund_amount",
        "entry_amount",
        "disputed_amount",
        "owed_amount",
        "retained_amount",
        "released_amount",
    }

    for path, _ in LIST_ENDPOINTS:
        for record in seeded_client.get(path).json()["records"]:
            present = amount_keys & set(record)
            for key in present:
                value = record[key]
                if value is None:
                    continue
                assert isinstance(value, str), f"{path}.{key} is {type(value)}"
            if present:
                assert fields.CURRENCY_KEY in record, (
                    f"{path} carries {sorted(present)} with no "
                    f"{fields.CURRENCY_KEY!r} key - an amount without its "
                    "currency is section 9.3's hard failure"
                )


# ---------------------------------------------------------------------------
# References, and what the dataset actually contains
# ---------------------------------------------------------------------------


def test_references_cross_as_marketplace_references_not_uuids(
    seeded_client: TestClient,
) -> None:
    """An item names `ORD-0001` and `vendor-aurora`, not primary keys.

    This is what forces A3 to resolve relationships rather than carry UUIDs
    across the boundary (specs/tech-stack.md section 9.5), and it is how a real
    marketplace API behaves.
    """
    records = seeded_client.get("/sim/marketplace/order-items").json()["records"]
    refs = {r["order_ref"] for r in records}
    vendors = {r["vendor_id"] for r in records}

    assert "ORD-0001" in refs
    assert "vendor-aurora" in vendors

    for value in refs | vendors:
        with pytest.raises(ValueError):
            uuid.UUID(value)


def test_the_worked_example_is_served_as_specified(
    seeded_client: TestClient,
) -> None:
    """specs/mission.md's example, over HTTP, as Block B will read it."""
    order = seeded_client.get("/sim/marketplace/orders/ORD-0001").json()

    assert order["order_ref"] == "ORD-0001"
    assert order["order_gross"] == "160.00"
    assert order["ccy"] == "USD"
    assert order["payout_timing"] == "INSTANT"

    items = [
        record
        for record in seeded_client.get(
            "/sim/marketplace/order-items"
        ).json()["records"]
        if record["order_ref"] == "ORD-0001"
    ]
    assert {r["item_total"] for r in items} == {"100.00", "60.00"}


def test_the_held_release_record_states_no_release_timestamp(
    seeded_client: TestClient,
) -> None:
    """Non-fault 1, over the wire. P3: no provider timestamp, so none is sent."""
    records = seeded_client.get("/sim/marketplace/releases").json()["records"]

    assert len(records) == 1
    assert records[0]["release_state"] == "funds_held"
    assert records[0]["released_on"] is None


def test_an_unseeded_marketplace_is_a_404_not_an_empty_list(
    migrated_database: Engine,
) -> None:
    """An unseeded database and a marketplace with no orders are different.

    Collapsing the two would let A3 report a successful ingestion of nothing,
    which is the quietest way for "the full seeded dataset ingests with zero
    data loss" to pass while being false.

    **Asserted against `_tenant_id` with a slug that cannot exist**, rather
    than against the live endpoint with the real slug. The endpoint's answer
    depends on whether this module's own fixture has seeded yet, so testing it
    that way needs a conditional skip - and a test that skips itself when the
    condition it wants is absent is the silent-green pattern this project
    refuses elsewhere. This one always runs.
    """
    from fastapi import HTTPException

    from reckonai.sim.router import _tenant_id

    with Session(bind=migrated_database) as session:
        with pytest.raises(HTTPException) as caught:
            _tenant_id(session, slug=f"absent-{uuid.uuid4().hex}")

    assert caught.value.status_code == 404
    assert "run the seed builder first" in caught.value.detail


def test_a_missing_order_is_a_404(seeded_client: TestClient) -> None:
    assert (
        seeded_client.get("/sim/marketplace/orders/ORD-9999").status_code == 404
    )
