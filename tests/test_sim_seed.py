"""The seed builder and its consistency check.

specs/roadmap.md Phase 4's second exit criterion - "the seed dataset reconciles
perfectly by hand" - and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D6 and D5a.

**Every guard here is verified in both directions.** Phase 3 shipped a gate
whose own setup performed the action it checked, so it passed with its subject
commented out. A check that cannot fail is not a check, so each invariant below
has a companion test that perturbs the dataset and asserts the failure.
"""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.models import (
    CommissionRule,
    Order,
    OrderItem,
    Payment,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
)
from reckonai.sim import data, seed

# Every financial table, which is to say every table carrying a NOT NULL
# `raw_payload_id`. The orphan check below iterates this.
# **Only what the marketplace owns.** The provider tables - capture,
# platform_fee, fx_conversion, disbursement - are written by
# `reckonai.paypal.replay` from captured fixtures, not by the seed builder,
# since the replan's R16 retired Block A's synthetic provider side.
FINANCIAL_MODELS = (Payment, Refund, SellerLedgerEntry)


@pytest.fixture
def seeded(session: Session) -> seed.SeedResult:
    """The clean dataset, in a transaction that is rolled back."""
    result = seed.build(session, tenant_slug=f"sim-{uuid.uuid4().hex[:8]}")
    session.flush()
    return result


# ---------------------------------------------------------------------------
# The dataset's own arithmetic, with no database involved
# ---------------------------------------------------------------------------


def test_the_dataset_has_the_shape_d5_specifies() -> None:
    # Two sellers, because each must map one-to-one onto a real onboarded
    # PayPal account: the connector resolves seller_id from
    # payee.merchant_id, so a shared account misattributes every order.
    assert len(data.SELLERS) == 2
    assert len(data.CLEAN_ORDERS) == 12
    assert sum(len(order.items) for order in data.CLEAN_ORDERS) == 15

    assert {o.currency for o in data.CLEAN_ORDERS} == {"USD", "EUR"}

    delayed = [o for o in data.CLEAN_ORDERS if o.disbursement_mode == "DELAYED"]
    assert len(delayed) == 1, "exactly one DELAYED order, so funds_held is reachable"

    # Every seller maps to a distinct real sandbox account.
    merchants = {s.merchant_id for s in data.SELLERS}
    assert len(merchants) == len(data.SELLERS)
    assert all(m and not m.startswith("MERCH-") for m in merchants), merchants


def test_the_mission_worked_example_is_seeded_exactly() -> None:
    """specs/mission.md's example, which Block B asserts against.

    $160 across two sellers at 10% -> A $90, B $54, commission $16.
    """
    order = data.clean_order("ORD-0001")

    assert order.total == "160.00"
    assert order.currency == "USD"
    assert order.expected_commission == "16.00"
    assert order.expected_entitlements == {
        "vendor-aurora": "90.00",
        "vendor-bellweather": "54.00",
    }


def test_the_delayed_order_belongs_to_a_seller_who_can_hold_funds() -> None:
    """`DELAY_FUNDS_DISBURSEMENT` is granted per seller, not account-wide.

    A `DELAYED` order for a seller without the capability would be a dataset
    that cannot exist, which is a worse baseline than one that is merely wrong.
    """
    delayed = next(o for o in data.CLEAN_ORDERS if o.disbursement_mode == "DELAYED")
    sellers = {item.seller_ref for item in delayed.items}

    for ref in sellers:
        assert data.seller(ref).delay_funds_enabled, ref


# ---------------------------------------------------------------------------
# verify(), in both directions
# ---------------------------------------------------------------------------


def test_verify_passes_on_the_clean_dataset(
    session: Session, seeded: seed.SeedResult
) -> None:
    """The criterion itself. No exception is the whole assertion."""
    seed.verify(session, seeded.tenant_id)


def test_verify_fails_when_an_order_total_is_perturbed(session: Session) -> None:
    """D6's reason for existing, demonstrated.

    Phase 2's D13 ties no constraint between an order's total and the sum of
    its items, so the database accepts this write happily. If `verify()` did
    not catch it, Block C would measure the inconsistency as a false positive
    forever.
    """
    order = data.clean_order("ORD-0001")
    broken = dataclasses.replace(order, total="160.01")
    dataset = data.Dataset(
        orders=tuple(
            broken if o.ref == "ORD-0001" else o for o in data.CLEAN_ORDERS
        )
    )

    result = seed.build(
        session, dataset, tenant_slug=f"sim-{uuid.uuid4().hex[:8]}"
    )
    session.flush()

    with pytest.raises(seed.SeedInconsistency, match="ORD-0001"):
        seed.verify(session, result.tenant_id, dataset)


def test_verify_fails_when_a_line_total_contradicts_its_unit_price(
    session: Session,
) -> None:
    order = data.clean_order("ORD-0002")
    item = order.items[0]
    broken_item = dataclasses.replace(item, line_total="49.00")
    broken = dataclasses.replace(order, items=(broken_item,), total="49.00")
    dataset = data.Dataset(
        orders=tuple(
            broken if o.ref == "ORD-0002" else o for o in data.CLEAN_ORDERS
        )
    )

    result = seed.build(
        session, dataset, tenant_slug=f"sim-{uuid.uuid4().hex[:8]}"
    )
    session.flush()

    with pytest.raises(seed.SeedInconsistency, match="line_total"):
        seed.verify(session, result.tenant_id, dataset)


def test_verify_fails_when_a_stated_entitlement_is_wrong(session: Session) -> None:
    """The invariant that catches a wrong commission, which is fault F2's shape.

    Stated 91.00 against a 10% rule on a 100.00 share, which leaves 90.00.
    """
    order = data.clean_order("ORD-0001")
    broken = dataclasses.replace(
        order,
        expected_entitlements={
            "vendor-aurora": "91.00",
            "vendor-bellweather": "54.00",
        },
    )
    dataset = data.Dataset(
        orders=tuple(
            broken if o.ref == "ORD-0001" else o for o in data.CLEAN_ORDERS
        )
    )

    result = seed.build(
        session, dataset, tenant_slug=f"sim-{uuid.uuid4().hex[:8]}"
    )
    session.flush()

    with pytest.raises(seed.SeedInconsistency, match="entitlement"):
        seed.verify(session, result.tenant_id, dataset)


def test_verify_recomputes_through_the_real_resolver(
    session: Session, seeded: seed.SeedResult
) -> None:
    """D5a: the two derivations are independent.

    The literals come from `data.py`; the recomputation runs
    `reckonai.engine.rules.resolve_commission_rule` against the rules
    actually in the database. Changing the stored rate must therefore break
    `verify()` - which is only possible if it really is consulting the
    database rather than re-reading the same literals twice.

    The perturbation is the rate rather than deleting a seller-scoped rule,
    because there is now exactly one rule: Phase 10's exit criterion needs
    both sellers on ORD-0001 at 10%, so no seller-scoped rate can exist.
    """
    rule = session.scalar(
        select(CommissionRule).where(CommissionRule.tenant_id == seeded.tenant_id)
    )
    assert rule is not None
    rule.rate = Decimal("0.150000")
    session.flush()

    with pytest.raises(seed.SeedInconsistency, match="entitlement"):
        seed.verify(session, seeded.tenant_id)


def test_verify_fails_when_a_ledger_direction_is_flipped(
    session: Session, seeded: seed.SeedResult
) -> None:
    """The position invariant, which invariant 3 cannot see.

    A commission written as a credit rather than a debit leaves every order's
    own arithmetic intact and the seller's position wrong by twice the
    commission. Only reading the ledger back catches it.
    """
    entry = session.scalar(
        select(SellerLedgerEntry).where(
            SellerLedgerEntry.tenant_id == seeded.tenant_id,
            SellerLedgerEntry.entry_category == "commission",
        )
    )
    assert entry is not None
    entry.transaction_type = "credit"
    session.flush()

    with pytest.raises(seed.SeedInconsistency, match="position"):
        seed.verify(session, seeded.tenant_id)


def test_the_stated_positions_match_the_ledger(
    session: Session, seeded: seed.SeedResult
) -> None:
    """`data.EXPECTED_POSITIONS` as a third independent derivation.

    Hand-written there, recomputed by `verify()` from the rows, and asserted
    here against the figures in `validation.md` section 2.
    """
    seed.verify(session, seeded.tenant_id)

    assert data.EXPECTED_POSITIONS[("vendor-aurora", "USD")] == "349.50"
    assert data.EXPECTED_POSITIONS[("vendor-aurora", "EUR")] == "73.37"
    assert data.EXPECTED_POSITIONS[("vendor-bellweather", "USD")] == "256.47"
    assert data.EXPECTED_POSITIONS[("vendor-bellweather", "EUR")] == "324.02"


# ---------------------------------------------------------------------------
# Principle checks on what was written
# ---------------------------------------------------------------------------


def test_every_financial_row_links_to_its_raw_payload(
    session: Session, seeded: seed.SeedResult
) -> None:
    """specs/mission.md P7, across all seven tables the builder writes.

    The link is NOT NULL, so this cannot find a null - it finds a link pointing
    at nothing, which a composite foreign key should also prevent. Both belts
    are worth having: the assertion costs one query and the alternative is
    trusting that a constraint was not dropped in a migration.
    """
    payload_ids = set(
        session.scalars(
            select(RawPayload.id).where(RawPayload.tenant_id == seeded.tenant_id)
        )
    )
    assert payload_ids, "nothing was seeded"

    for model in FINANCIAL_MODELS:
        rows = list(
            session.scalars(
                select(model).where(model.tenant_id == seeded.tenant_id)
            )
        )
        assert rows, f"{model.__tablename__} got no rows, so this proves nothing"
        for row in rows:
            assert row.raw_payload_id in payload_ids, (
                f"{model.__tablename__} {row.id} points at a payload that is "
                "not in this tenant"
            )


def test_every_ledger_amount_is_positive(
    session: Session, seeded: seed.SeedResult
) -> None:
    """The unsigned-amount invariant, which the direction column exists for."""
    entries = list(
        session.scalars(
            select(SellerLedgerEntry).where(
                SellerLedgerEntry.tenant_id == seeded.tenant_id
            )
        )
    )

    assert entries
    for entry in entries:
        assert entry.amount.is_positive(), entry.external_ref
        assert entry.transaction_type in {"debit", "credit"}


def test_a_seeded_payload_records_no_endpoint(
    session: Session, seeded: seed.SeedResult
) -> None:
    """requirements.md D2's second separator.

    `endpoint` is `NULL` because there was no read path, which is a true answer
    and is what tells a seeded row from one the connector fetched.
    """
    payloads = list(
        session.scalars(
            select(RawPayload).where(RawPayload.tenant_id == seeded.tenant_id)
        )
    )

    assert payloads
    for row in payloads:
        assert row.endpoint is None


def test_counts_are_what_the_dataset_implies(
    session: Session, seeded: seed.SeedResult
) -> None:
    """A row-count floor, so a silently-skipped stage fails rather than passes."""
    def count(model: object) -> int:
        return session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.tenant_id == seeded.tenant_id)
        )

    assert count(Seller) == len(data.SELLERS)
    assert count(Order) == len(data.CLEAN_ORDERS)
    assert count(OrderItem) == sum(len(o.items) for o in data.CLEAN_ORDERS)
    assert count(CommissionRule) == len(data.COMMISSION_RULES)
    assert count(Payment) == len(data.CLEAN_ORDERS)

    # **One refund row per marketplace refund, and no provider rows at all.**
    # The seed writes only what the marketplace owns; PayPal's captures,
    # refunds, fees, conversions and disbursements come from
    # `fixtures/paypal/` through the replay ingester (R16).
    assert count(Refund) == sum(len(o.refunds) for o in data.CLEAN_ORDERS)
