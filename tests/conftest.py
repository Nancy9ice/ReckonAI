"""Shared fixtures.

Two deliberate choices about what happens when there is no database.

A missing `DATABASE_URL` is a **hard failure in CI** and an explanatory **skip**
locally. A silent skip everywhere would mean a green CI run that tested nothing,
which is the failure mode this project can least afford in its deployment gate.

The engine fixture also **asserts the server's major version**, so no test that
touches a database can pass against the wrong one. See the module comment on
that fixture for why that is not paranoia.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from reckonai.config import get_settings
from reckonai.db import create_short_lived_engine, server_major_version

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from reckonai.models import RawPayload, Seller, Tenant

#: The major version compose, CI and the Render instance all pin.
REQUIRED_SERVER_MAJOR = 16

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_NO_URL_MESSAGE = (
    "DATABASE_URL is not set. Start the local database and export it:\n"
    "  docker compose up -d --wait\n"
    '  export DATABASE_URL="postgres://reckonai:devpassword@localhost:5432/reckonai"'
)


def _running_in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


@pytest.fixture(scope="session")
def database_url() -> str:
    """The configured database URL, driver normalised by Settings."""
    if not os.environ.get("DATABASE_URL"):
        if _running_in_ci():
            pytest.fail(_NO_URL_MESSAGE)
        pytest.skip(_NO_URL_MESSAGE, allow_module_level=True)

    get_settings.cache_clear()
    return get_settings().sqlalchemy_url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """An unpooled engine, guarded on the server's major version.

    The guard lives here rather than in an autouse fixture on purpose: a test
    that needs no database - the conventions gate, for instance - must still run
    when there is none, so the check attaches to the engine rather than to the
    session.
    """
    engine = create_short_lived_engine(database_url)

    found = server_major_version(engine)
    if found != REQUIRED_SERVER_MAJOR:
        engine.dispose()
        pytest.fail(
            f"Connected to PostgreSQL major version {found}, expected "
            f"{REQUIRED_SERVER_MAJOR}. Something other than the compose "
            "container is probably bound to this port - check "
            "`brew services list` for a running postgresql@17 or @18. "
            "Testing against the wrong major version would defeat the "
            "local/CI/Render parity that requirements.md D3 depends on."
        )

    yield engine
    engine.dispose()


def committed_tenant_count(engine: Engine) -> int:
    """How many `tenant` rows are committed right now, or 0 if the table is gone.

    Deliberately not a fixture, so `no_committed_marketplace_rows` can be
    tested without committing a row to provoke it.
    """
    from sqlalchemy import inspect, text

    if "tenant" not in inspect(engine).get_table_names():
        # The schema is already at base. Nothing to protect.
        return 0

    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM tenant")).scalar_one()


def assert_no_committed_marketplace_rows(engine: Engine) -> None:
    """Fail loudly if committed rows would break a downgrade to `base`.

    A plain function rather than only a fixture, so
    `tests/test_order_dependence_guard.py` can provoke it **without committing
    a tenant row** - committing one is precisely what would break the
    downgrade for every module collected after it.

    **The message names two causes, and that is the point of it.** It
    originally offered only the collection-order one, because that was the
    only cause when Block B wrote it. Block E added a second: the
    investigation agent is a separate process and can only see *committed*
    rows, so running it by hand requires seeding the database for real. A
    developer who does that and forgets to reset gets 6 failures and 43
    errors, none of which mentions the dataset - and a diagnosis sending them
    to look for a pytest ordering bug that is not there.

    That is `specs/2026-11-10-investigation-agent/validation.md` section 8
    debt 3, and extending this message is what discharges it. A note in a spec
    file would not: the failure happens here, so the explanation belongs here.
    """
    count = committed_tenant_count(engine)
    if count:
        pytest.fail(
            f"{count} committed tenant row(s) exist, so downgrading the schema "
            "to base would fail against ondelete=RESTRICT with an error that "
            "names neither this cause nor its fix.\n\n"
            "TWO THINGS CAUSE THIS.\n\n"
            "1. The database was seeded outside the suite. Most likely "
            "`reckonai.demo_data --ensure`, which commits - and which running "
            "the investigation agent by hand requires, because the agent is a "
            "separate process and can only see committed rows. Check this "
            "first; it is one command to undo:\n\n"
            "       uv run python -m reckonai.demo_data --reset\n\n"
            "2. Collection order has been disturbed. This suite is "
            "order-dependent: tests/test_sim_api.py and "
            "tests/test_connector_ingest.py commit rows and remove them at "
            "module teardown, and it only works because pytest collects "
            "alphabetically. pytest-randomly, -n auto, a -k selection that "
            "runs a committing module first, or an earlier module that failed "
            "before its teardown ran will each break that.\n\n"
            "See specs/2026-11-08-block-a-marketplace-connector/validation.md "
            "section 8 debt 4 for cause 2, and "
            "specs/2026-11-10-investigation-agent/validation.md section 8 "
            "debt 3 for cause 1."
        )


@pytest.fixture
def no_committed_marketplace_rows(engine: Engine) -> None:
    """Refuse to downgrade the schema while committed marketplace rows exist.

    **This suite is order-dependent, and this fixture is what makes that fail
    loudly instead of silently.** See
    `specs/2026-11-08-block-a-marketplace-connector/validation.md` section 8
    debt 4, and `specs/2026-11-09-entitlement-engine/requirements.md` D6.

    `tests/test_sim_api.py` and `tests/test_connector_ingest.py` each **commit**
    real rows and delete them at module teardown, because the sim's endpoints
    open their own `Session` from `app.state.engine` and cannot see a
    transaction a fixture is holding open. `tests/test_migrations.py`
    downgrades to `base`.

    It passes today only because pytest collects alphabetically -
    `test_connector_ingest` < `test_migrations` < `test_sim_api` - so the
    committed rows are gone before the downgrade runs. Add `pytest-randomly`,
    `-n auto`, or any reordering, and the downgrade meets rows it cannot drop
    through `ondelete="RESTRICT"`, failing with a `NotNullViolation` or a
    foreign-key error from deep inside Alembic that names none of this.

    **Function-scoped rather than session-scoped**, which is stronger than the
    debt proposed: the check then runs immediately before each downgrade rather
    than once at session start, so it also catches a module that commits rows
    *during* the session. It is one `count(*)` against a table that holds
    single digits.

    This fixture does not make the suite safe to randomise. It makes the
    failure legible, which is the precondition for randomising it later.
    """
    assert_no_committed_marketplace_rows(engine)


@pytest.fixture(scope="session")
def migrated_database(engine: Engine) -> Engine:
    """The database at `head`, migrated once per session.

    **Database-backed tests run against the schema a migration produced**, not
    against one built by `Base.metadata.create_all`. Two reasons, and the
    second was learned the hard way:

      - It is the schema the deployed system actually has. A `CHECK` constraint
        lost in an autogenerated revision now fails the constraint tests as
        well as the drift check, rather than passing against a `create_all`
        schema that still had it.
      - `create_all` / `drop_all` in a module fixture **fights with Alembic**.
        `tests/test_catalogue_constraints.py` originally dropped the tables at
        module teardown, which left `tests/test_migrations.py` trying to
        `downgrade base` against a schema whose tables were already gone:
        `psycopg.errors.UndefinedObject: index "ix_order_item_tenant_id" does
        not exist`. It passed while the baseline revision was empty, because
        there was no DDL to reverse, and broke the moment Phase 2 added a real
        one.

    So nothing but `tests/test_migrations.py` manages schema, and that module
    restores `head` on teardown.
    """
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return engine


# ---------------------------------------------------------------------------
# Phase 3 shared fixtures
#
# `tests/test_catalogue_constraints.py` established this session pattern and
# keeps its own copy; these are the same thing promoted to conftest, because
# Phase 3 adds five test modules that all need a tenant, a seller and a
# payload to hang a financial record off.
# ---------------------------------------------------------------------------


@pytest.fixture
def session(migrated_database: Engine) -> Iterator[Session]:
    """A session whose work is always rolled back.

    `join_transaction_mode="create_savepoint"` is load-bearing. Most Phase 3
    tests provoke an `IntegrityError`, which rolls the session's transaction
    back - and without a savepoint that also tears down the outer transaction
    this fixture owns, so the next statement runs outside a transaction. With a
    savepoint, a failed flush unwinds only to the savepoint.

    It also means **nothing here needs to clean up after itself**, which
    matters more than it sounds: a `delete()`-based teardown runs after a failed
    flush has already poisoned the session.
    """
    from sqlalchemy.orm import Session

    connection = migrated_database.connect()
    transaction = connection.begin()
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        yield session
    transaction.rollback()
    connection.close()


@pytest.fixture
def tenant(session: Session) -> Tenant:
    """One marketplace operator.

    Flushed rather than left pending: without any `relationship()` between
    these models, SQLAlchemy's unit of work has no mapper dependency to sort
    on, so inserting a tenant and a seller in one flush can order the seller
    first. Table-level foreign keys do not fix that - the flush order between
    unrelated mappers is simply not derived from them.
    """
    from reckonai.models import Tenant

    tenant = Tenant(name="ReckonMarket", slug=f"rm-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    session.flush()
    return tenant


@pytest.fixture
def other_tenant(session: Session) -> Tenant:
    """A second tenant, so a cross-tenant link has somewhere to point."""
    from reckonai.models import Tenant

    tenant = Tenant(name="Other Market", slug=f"om-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    session.flush()
    return tenant


@pytest.fixture
def seller(session: Session, tenant: Tenant) -> Seller:
    """A seller onboarded with both multiparty capabilities."""
    from reckonai.models import Seller

    seller = Seller(
        tenant_id=tenant.id,
        external_ref=f"vendor-{uuid.uuid4().hex[:8]}",
        name="A Seller",
        partner_fee_enabled=True,
        delay_funds_enabled=True,
    )
    session.add(seller)
    session.flush()
    return seller


def make_payload(tenant_id: uuid.UUID, **overrides: object) -> RawPayload:
    """A `RawPayload` with a unique digest, so the unique constraint allows many."""
    from reckonai.models import RawPayload

    digest = uuid.uuid4().hex + uuid.uuid4().hex
    fields: dict[str, object] = {
        "tenant_id": tenant_id,
        "source": "paypal",
        "source_record_type": "capture",
        "source_record_id": f"REC-{digest[:12]}",
        "endpoint": "GET /v2/payments/captures/{id}",
        "retrieved_at": datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        "body": {"id": digest[:12]},
        "body_sha256": digest,
    }
    fields.update(overrides)
    return RawPayload(**fields)


@pytest.fixture
def payload(session: Session, tenant: Tenant) -> RawPayload:
    """The evidence a financial record points at. Every record needs one."""
    row = make_payload(tenant.id)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def other_payload(session: Session, other_tenant: Tenant) -> RawPayload:
    """A payload in the wrong tenant, for the cross-tenant checks."""
    row = make_payload(other_tenant.id)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def seeded_exception(session: Session, tenant: Tenant, seller: Seller):  # type: ignore[no-untyped-def]
    """One persisted run and one exception under it, for Block F's tables.

    The order and its one item exist because every real exception names an
    order the matcher claimed; the API reads its age and seller count there.
    """
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from reckonai.models import (
        Order,
        OrderItem,
        ReconciliationException,
        ReconciliationRun,
    )
    from reckonai.money import Money

    now = datetime.now(UTC)
    order = Order(
        tenant_id=tenant.id,
        external_ref="ORD-TEST",
        total=Money(Decimal("100.00"), "USD"),
        placed_at=now - timedelta(days=6),
    )
    session.add(order)
    session.flush()
    session.add(
        OrderItem(
            tenant_id=tenant.id,
            order_id=order.id,
            seller_id=seller.id,
            external_ref="ITEM-TEST",
            quantity=1,
            unit_price=Money(Decimal("100.00"), "USD"),
            line_total=Money(Decimal("100.00"), "USD"),
        )
    )
    session.flush()
    run = ReconciliationRun(
        tenant_id=tenant.id,
        window_start=now,
        window_end=now + timedelta(days=1),
        status="completed",
    )
    session.add(run)
    session.flush()
    row = ReconciliationException(
        tenant_id=tenant.id,
        run_id=run.id,
        code="commission_mismatch",
        order_external_ref="ORD-TEST",
        detail="test",
        is_exception=True,
        impact=Money(Decimal("10.00"), "USD"),
        evidence_refs=[],
        evidence_basis="provider_reported",
    )
    session.add(row)
    session.flush()
    return row
