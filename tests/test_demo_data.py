"""The demo loader: deterministic on deploy, self-correcting when stale.

`specs/2026-11-09-paypal-capture/requirements.md` D11 and D13.

**The fingerprint exists because the early return alone was wrong.**
Returning early whenever the demo tenant existed stopped a redeploy
duplicating anything - and also meant production would never pick up a
dataset change. No error, no warning, just a demo running on figures the
repository no longer states, which is worse than duplication because
duplication is visible.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai import demo_data
from reckonai.models import (
    DemoSeed,
    Investigation,
    InvestigationCause,
    Job,
    Order,
    ReconciliationRun,
    Tenant,
)
from reckonai.models.base import Base
from reckonai.sim import data


@pytest.fixture
def clean(session: Session):
    """The demo tenant absent, and removed again afterwards.

    These tests use the shared rolled-back session, so nothing commits -
    which is also why they cannot exercise the real deploy path, only the
    logic it runs.
    """
    demo_data.reset(session)
    session.flush()
    yield session
    demo_data.reset(session)
    session.flush()


def _count(session: Session, model: object) -> int:
    return session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------------------
# The fingerprint itself
# ---------------------------------------------------------------------------


def test_the_fingerprint_is_stable_across_calls() -> None:
    """**The failure that made the whole mechanism useless.**

    `Dataset.faulty_order_refs` is a `frozenset`, and serialising it through
    `str()` renders it in an order that varies with Python's hash
    randomisation. Two identical runs produced different fingerprints, so
    `--ensure` re-seeded on every deploy. Only running it twice shows this.
    """
    assert demo_data.fingerprint() == demo_data.fingerprint()


def test_the_fingerprint_survives_a_reordered_set() -> None:
    """Directly, rather than by hoping repeated calls happen to agree.

    `_stable` sorts sets, so two frozensets with the same members hash
    identically whatever order they iterate in.
    """
    left = demo_data._stable({"refs": frozenset({"a", "b", "c"})})
    right = demo_data._stable({"refs": frozenset({"c", "a", "b"})})

    assert left == right
    assert left["refs"] == ["a", "b", "c"]


def test_a_changed_amount_changes_the_fingerprint(monkeypatch) -> None:
    """The point of hashing the dataset at all."""
    before = demo_data.fingerprint()

    import dataclasses

    altered = dataclasses.replace(
        data.FULL,
        orders=(
            dataclasses.replace(data.CLEAN_ORDERS[1], total="51.00"),
            *data.CLEAN_ORDERS[2:],
        ),
    )
    monkeypatch.setattr(data, "FULL", altered)

    assert demo_data.fingerprint() != before


def test_a_changed_fixture_changes_the_fingerprint(tmp_path) -> None:
    """**Both inputs are hashed, and both must be.**

    A re-captured fixture with an unchanged `data.py` leaves production
    exactly as stale as the reverse.
    """
    one = tmp_path / "ORD-0001.json"
    one.write_text(json.dumps({"order_ref": "ORD-0001"}))
    before = demo_data.fingerprint(fixtures=tmp_path)

    one.write_text(json.dumps({"order_ref": "ORD-0001", "note": "recaptured"}))

    assert demo_data.fingerprint(fixtures=tmp_path) != before


# ---------------------------------------------------------------------------
# ensure()
# ---------------------------------------------------------------------------


def test_ensure_seeds_then_no_ops(clean: Session) -> None:
    """A redeploy with an unchanged dataset must change nothing."""
    first = demo_data.ensure(clean)
    clean.flush()
    assert first["orders"] == len(data.FULL.orders)

    orders = _count(clean, Order)
    second = demo_data.ensure(clean)
    clean.flush()

    assert second == {"seeded": 0}
    assert _count(clean, Order) == orders


def test_ensure_records_what_it_loaded(clean: Session) -> None:
    demo_data.ensure(clean)
    clean.flush()

    tenant_id = clean.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    loaded = clean.scalar(
        select(DemoSeed).where(DemoSeed.tenant_id == tenant_id)
    )

    assert loaded is not None
    assert loaded.fingerprint == demo_data.fingerprint()
    assert loaded.order_count == len(data.FULL.orders)


def test_a_stale_fingerprint_reseeds_rather_than_leaving_old_data(
    clean: Session,
) -> None:
    """The behaviour the whole table exists for.

    Simulated by corrupting the stored fingerprint, which is equivalent to
    the dataset having moved underneath it - and avoids mutating module
    state that other tests read.
    """
    demo_data.ensure(clean)
    clean.flush()
    tenant_id = clean.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )

    loaded = clean.scalar(
        select(DemoSeed).where(DemoSeed.tenant_id == tenant_id)
    )
    loaded.fingerprint = "0" * 64
    clean.flush()

    result = demo_data.ensure(clean)
    clean.flush()

    assert result["orders"] == len(data.FULL.orders), "it must re-seed"

    # A *new* tenant, because reset deleted the old one - so no duplicates.
    fresh = clean.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    assert fresh != tenant_id
    assert _count(clean, Tenant) >= 1
    assert clean.scalar(
        select(func.count()).select_from(Order).where(Order.tenant_id == fresh)
    ) == len(data.FULL.orders)


def test_a_tenant_with_no_recorded_fingerprint_is_treated_as_stale(
    clean: Session,
) -> None:
    """A database seeded before this table existed.

    Leaving it alone would be the same silent staleness, so the unrecorded
    case re-seeds rather than assuming it is current.
    """
    demo_data.ensure(clean)
    clean.flush()
    tenant_id = clean.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    clean.delete(
        clean.scalar(select(DemoSeed).where(DemoSeed.tenant_id == tenant_id))
    )
    clean.flush()

    result = demo_data.ensure(clean)
    clean.flush()

    assert result["orders"] == len(data.FULL.orders)


def test_ensure_verifies_the_seed_it_wrote(clean: Session) -> None:
    """`seed.verify()` runs inside `ensure`, so a deploy cannot load an
    inconsistent baseline.

    Caught a real perturbation during development: a leftover edit left
    ORD-0002's total at 51.00 against items summing to 50.00, and the load
    refused rather than seeding it.
    """
    demo_data.ensure(clean)
    clean.flush()

    from reckonai.sim import seed

    tenant_id = clean.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    seed.verify(clean, tenant_id, data.FULL)


def test_live_is_skipped_when_paypal_is_not_configured(clean: Session) -> None:
    """R15: the demo proceeds "whether or not that call succeeded".

    With no credentials the call cannot be made, and `live` must report 0
    rather than raise - a sandbox outage costs a flourish, not the demo.
    """
    demo_data.ensure(clean)
    clean.flush()

    assert demo_data.live(clean) == 0


def test_live_without_a_seeded_tenant_is_also_skipped(clean: Session) -> None:
    assert demo_data.live(clean) == 0


def test_reset_removes_the_tenant_and_its_fingerprint(clean: Session) -> None:
    demo_data.ensure(clean)
    clean.flush()
    assert _count(clean, DemoSeed) >= 1

    demo_data.reset(clean)
    clean.flush()

    assert clean.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    ) is None
    assert _count(clean, DemoSeed) == 0


def test_the_fixture_directory_the_loader_defaults_to_exists() -> None:
    """A wrong default would make `--ensure` raise on deploy rather than
    seed, and the path is resolved from this file's location."""
    assert demo_data.FIXTURES.is_dir()
    assert len(list(demo_data.FIXTURES.glob("*.json"))) == len(data.FULL.orders)


def test_an_unknown_uuid_is_not_mistaken_for_the_demo_tenant(
    clean: Session,
) -> None:
    """`reset` on an absent tenant is a no-op, not an error - a deploy may
    run it before anything exists."""
    demo_data.reset(clean)
    assert uuid.UUID(int=0)


def test_reset_empties_every_tenant_scoped_table(session: Session) -> None:
    """`reset`'s delete list is explicit, so it can fall behind the schema.

    It did. A real `--reset` failed on `fk_investigation_tenant_id_tenant`
    once Block E's agent committed an investigation from outside the test
    suite - and Block D's four tables had the same gap, never fired because
    nothing had committed a run row outside the suite either.

    **The expected set is derived from `Base.metadata`, not listed**, so a
    model added later is covered by this gate whether or not anyone remembers
    `reset`. That is the same reasoning `tests/test_model_registry.py` uses
    for the evidence gate: a hand-written list is a thing to forget.
    """
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()

    tenant_id = session.scalar(
        select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG)
    )
    assert tenant_id is not None

    # One row in each of Block D's and Block E's tables, which `ensure` does
    # not create - they are produced by a run and an investigation. Without
    # these the gate would pass on a `reset` that cannot delete them.
    run = ReconciliationRun(
        tenant_id=tenant_id,
        window_start=datetime(2026, 11, 1, tzinfo=UTC),
        window_end=datetime(2026, 11, 2, tzinfo=UTC),
        status="completed",
    )
    session.add(run)
    session.flush()
    investigation = Investigation(
        tenant_id=tenant_id,
        order_external_ref="ORD-0013",
        code="missing_ledger_adjustment",
        provider="anthropic",
        model_requested="claude-haiku-5-5",
        verdict="failed",
    )
    session.add(investigation)
    session.flush()
    session.add(
        InvestigationCause(
            tenant_id=tenant_id,
            investigation_id=investigation.id,
            rank=1,
            description="A cause.",
            confidence=Decimal("0.500"),
            cited_refs=["ORD-0013"],
        )
    )
    session.add(
        Job(
            tenant_id=tenant_id,
            job_type="reconciliation_run",
            payload={},
            status="queued",
        )
    )
    session.flush()

    demo_data.reset(session)
    session.flush()

    scoped = sorted(
        name
        for name, table in Base.metadata.tables.items()
        if "tenant_id" in table.columns and name != "tenant"
    )
    assert scoped, "no tenant-scoped tables found, so this gate proves nothing"

    remaining = {
        name: count
        for name in scoped
        if (
            count := session.scalar(
                select(func.count()).select_from(Base.metadata.tables[name])
            )
        )
    }
    assert not remaining, (
        f"reset left rows in {remaining}. Add the model to "
        "`reckonai.demo_data.reset`'s delete list, children first."
    )
