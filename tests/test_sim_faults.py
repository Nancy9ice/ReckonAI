"""The six seeded faults, the manifest, and their coexistence with clean records.

specs/roadmap.md Phase 5's exit criteria, and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D4.

**What this module does not test.** Whether the faults are *detected* is Block
C's work, scored against this manifest. Here the obligation is narrower and
has to be met first: that each fault is really present in the data, that the
clean baseline is still clean beside it, and that the manifest describes what
was actually seeded rather than what someone meant to seed.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.engine.ledger import position
from reckonai.models import (
    Order,
    Refund,
    Seller,
    SellerLedgerEntry,
)
from reckonai.money import Money
from reckonai.sim import data, manifest, seed


@pytest.fixture
def full(session: Session) -> seed.SeedResult:
    """Clean and faulty records in one dataset - Phase 5's second criterion."""
    result = seed.build(
        session, data.FULL, tenant_slug=f"sim-{uuid.uuid4().hex[:8]}"
    )
    session.flush()
    return result


def _seller_id(session: Session, tenant_id: uuid.UUID, ref: str) -> uuid.UUID:
    found = session.scalar(
        select(Seller.id).where(
            Seller.tenant_id == tenant_id, Seller.external_ref == ref
        )
    )
    assert found is not None
    return found


def _order_id(session: Session, tenant_id: uuid.UUID, ref: str) -> uuid.UUID:
    found = session.scalar(
        select(Order.id).where(
            Order.tenant_id == tenant_id, Order.external_ref == ref
        )
    )
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# The manifest, against the dataset
# ---------------------------------------------------------------------------


def test_there_are_six_faults_and_five_are_exceptions() -> None:
    assert len(manifest.FAULTS) == 6
    assert len(manifest.EXCEPTIONS) == 5

    non_exception = [f for f in manifest.FAULTS if not f.is_exception]
    assert len(non_exception) == 1
    assert non_exception[0].id == "F5"


def test_the_three_demo_scenarios_are_f1_f2_f3() -> None:
    """specs/mission.md section 6's three, in order, which the demo walks."""
    assert [f.id for f in manifest.SCENARIOS] == ["F1", "F2", "F3"]
    assert [f.scenario for f in manifest.SCENARIOS] == [1, 2, 3]


def test_every_fault_names_an_order_the_builder_seeds() -> None:
    """The manifest cannot describe a fault that is not there."""
    seeded = {order.ref for order in data.FAULTY_ORDERS}
    named = {f.order_ref for f in manifest.FAULTS}

    assert named == seeded, (
        f"manifest names {sorted(named - seeded)} which is not seeded, and "
        f"omits {sorted(seeded - named)} which is"
    )


def test_every_faulty_order_is_excluded_from_verify() -> None:
    """Excluded **by reference**, never by a heuristic.

    requirements.md D6: the builder knows which orders it broke. A heuristic
    that tried to detect them would be a reimplementation of the
    reconciliation engine inside the test fixture.
    """
    assert data.FULL.faulty_order_refs == {f.order_ref for f in manifest.FAULTS}


def test_every_impact_amount_is_a_valid_money_string() -> None:
    """Strings, never floats, and parseable by `Money` without loss."""
    for fault in manifest.FAULTS:
        amount = Money(fault.impact_amount, fault.impact_currency)
        assert amount.is_quantized(), fault.id
        if fault.is_exception:
            assert amount.is_positive(), f"{fault.id} has no impact"
        else:
            # F5: nothing is wrong, so the measured impact is nothing.
            assert amount.is_zero(), fault.id


def test_the_manifest_serializes_amounts_as_strings() -> None:
    """A ground truth stored in binary floating point is the one place a
    reconciliation system can least afford it."""
    payload = manifest.as_dict()

    for entry in payload["faults"]:  # type: ignore[index]
        impact = entry["impact"]  # type: ignore[index]
        assert isinstance(impact["amount"], str)
        assert isinstance(impact["currency"], str)


# ---------------------------------------------------------------------------
# Each fault is really present
# ---------------------------------------------------------------------------


def test_f1_refund_has_no_ledger_entries(
    session: Session, full: seed.SeedResult
) -> None:
    """specs/mission.md scenario 1: the refund exists, the ledger does not know.

    Asserted as a **comparison against the clean counterpart**, not as a bare
    zero. ORD-0011 has the same shape and does carry its entries, so a builder
    that stopped writing refund entries altogether would fail here rather than
    pass twice.
    """
    faulty = _order_id(session, full.tenant_id, "ORD-0013")
    clean = _order_id(session, full.tenant_id, "ORD-0011")

    def refund_entries(order_id: uuid.UUID) -> int:
        return session.scalar(
            select(func.count())
            .select_from(SellerLedgerEntry)
            .where(
                SellerLedgerEntry.tenant_id == full.tenant_id,
                SellerLedgerEntry.order_id == order_id,
                SellerLedgerEntry.entry_category.in_(
                    ["refund", "commission_reversal"]
                ),
            )
        )

    assert refund_entries(faulty) == 0
    assert refund_entries(clean) == 2, (
        "the clean refund lost its entries too, so F1 is not a fault - it is "
        "the builder being broken"
    )

    # And the refund record itself is present, or there is nothing to detect.
    assert session.scalar(
        select(func.count())
        .select_from(Refund)
        .where(
            Refund.tenant_id == full.tenant_id,
            Refund.external_id == "RFND-0013-A",
        )
    ) == 1


def test_f4_credits_one_sale_twice(
    session: Session, full: seed.SeedResult
) -> None:
    """Two entries describing one sale, under distinct references.

    Distinct because `UNIQUE (tenant_id, external_ref)` refuses a literal
    repeat - and a real double-post carries its own id anyway.
    """
    order_id = _order_id(session, full.tenant_id, "ORD-0016")

    sales = list(
        session.scalars(
            select(SellerLedgerEntry).where(
                SellerLedgerEntry.tenant_id == full.tenant_id,
                SellerLedgerEntry.order_id == order_id,
                SellerLedgerEntry.entry_category == "sale",
            )
        )
    )

    assert len(sales) == 2
    assert len({e.external_ref for e in sales}) == 2
    assert sales[0].amount == sales[1].amount == Money("90.00", "USD")
    assert sales[0].amount == Money(manifest.fault("F4").impact_amount, "USD")


# ---------------------------------------------------------------------------
# Coexistence: the clean baseline survives the faults
# ---------------------------------------------------------------------------


def test_verify_still_passes_over_the_clean_subset(
    session: Session, full: seed.SeedResult
) -> None:
    """Phase 5's second exit criterion, and the one most worth protecting.

    The faults are present in the same dataset and the clean orders' own
    arithmetic still holds. If this failed, the baseline Phase 11's gate is
    measured against would have been contaminated by the faults meant to sit
    beside it.
    """
    seed.verify(session, full.tenant_id, data.FULL)


def test_a_faulty_order_would_fail_verify_if_it_were_not_excluded(
    session: Session, full: seed.SeedResult
) -> None:
    """The exclusion is doing work, which is the direction that can fail silently.

    If the faulty orders happened to be internally consistent, excluding them
    would be a no-op and `test_verify_still_passes_over_the_clean_subset`
    would prove nothing. F2's order is inconsistent with its own commission
    rule, so including it must fail.
    """
    including = data.Dataset(
        orders=data.CLEAN_ORDERS + (data.faulty_order("ORD-0014"),),
        faulty_order_refs=frozenset(),
    )

    with pytest.raises(seed.SeedInconsistency, match="ORD-0014"):
        seed.verify(session, full.tenant_id, including)


def test_the_clean_sellers_positions_are_unchanged_by_the_faults(
    session: Session, full: seed.SeedResult
) -> None:
    """A seller's clean orders still sum to the hand-written figure.

    Aurora carries clean orders and three faults, so this is the strongest
    available check that fault rows did not leak into the baseline.
    """
    seller_id = _seller_id(session, full.tenant_id, "vendor-aurora")
    clean_order_ids = set(
        session.scalars(
            select(Order.id).where(
                Order.tenant_id == full.tenant_id,
                Order.external_ref.in_([o.ref for o in data.CLEAN_ORDERS]),
            )
        )
    )

    entries = [
        entry
        for entry in session.scalars(
            select(SellerLedgerEntry).where(
                SellerLedgerEntry.tenant_id == full.tenant_id,
                SellerLedgerEntry.seller_id == seller_id,
            )
        )
        if entry.order_id in clean_order_ids
    ]

    found = position(entries, "USD").quantize()
    assert found == Money(data.EXPECTED_POSITIONS[("vendor-aurora", "USD")], "USD")


def test_the_two_non_faults_are_not_in_the_manifest() -> None:
    """requirements.md D4: they are not faults, so they are not ground truth.

    `ORD-0005` holds funds under DELAYED with no release requested, and
    `ORD-0008` carries PayPal's cent-rounding. Both belong to the clean
    baseline precisely so Block C proves they are not flagged - and a manifest
    entry for either would instruct the eval to expect an exception.
    """
    named = {f.order_ref for f in manifest.FAULTS}

    assert "ORD-0005" not in named
    assert "ORD-0008" not in named


def test_the_full_dataset_has_eighteen_orders() -> None:
    assert len(data.FULL.orders) == 18
    assert len(data.CLEAN_ORDERS) == 12
    assert len(data.FAULTY_ORDERS) == 6


# ---------------------------------------------------------------------------
# The provider half, asserted against the captured fixtures
#
# These used to query the database, back when the seed invented PayPal's
# records. R16 retired that: the provider side is real now and lives in
# `fixtures/paypal/`, so the evidence a fault is measured against is checked
# where it actually is. Once the replay ingester lands, the same facts are
# worth re-asserting after ingestion - but that would test the ingester, and
# these test the evidence.
# ---------------------------------------------------------------------------

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "paypal"


def _fixture(ref: str) -> dict:
    return json.loads((FIXTURES / f"{ref}.json").read_text())


def _units(doc: dict, which: str = "order") -> list[dict]:
    return doc[which]["purchase_units"]


def test_every_order_has_a_captured_fixture() -> None:
    """A dataset order with no provider record would be flagged as unmatched
    on every run - eighteen silent false positives."""
    captured = {f.stem for f in FIXTURES.glob("ORD-*.json")}
    expected = {o.ref for o in data.FULL.orders}

    assert expected == captured, (
        f"missing fixtures {sorted(expected - captured)}; "
        f"orphan fixtures {sorted(captured - expected)}"
    )


def test_f2_paypal_took_the_rule_correct_fee() -> None:
    """scenario 2: the marketplace claims 30.00 where the rule gives 20.00.

    The disagreement only exists because PayPal's record is correct, so that
    is what is asserted here.
    """
    doc = _fixture("ORD-0014")
    brk = _units(doc, "order")[0]["payments"]["captures"][0][
        "seller_receivable_breakdown"
    ]
    took = Money(brk["platform_fees"][0]["amount"]["value"], "USD")

    assert took == Money("20.00", "USD")
    assert Money(data.faulty_order("ORD-0014").expected_commission, "USD") == Money(
        "30.00", "USD"
    ), "the marketplace must believe something different, or there is no fault"


def test_f3_the_real_settlement_is_far_from_what_the_marketplace_recorded() -> None:
    """scenario 3, and it must stay clear of ORD-0008's legitimate artefact.

    If the fault were the size of the rounding noise, no classifier could
    tell them apart and the dataset would be unusable for Phase 11's gate.
    """
    doc = _fixture("ORD-0015")
    brk = _units(doc)[0]["payments"]["captures"][0]["seller_receivable_breakdown"]
    real = Money(brk["receivable_amount"]["value"], "USD")

    recorded = Money(
        data.faulty_order("ORD-0015").recorded_in_currency["vendor-bellweather"][1],
        "USD",
    )
    gap = abs((real - recorded).amount)

    assert real == Money("294.16", "USD")
    assert gap > Money("0.01", "USD").amount * 100, (
        f"the F3 gap is {gap} against a one-cent artefact; a fault within an "
        "order of magnitude of the noise needs a threshold, and Phase 11 "
        "forbids introducing one"
    )


def test_f5_has_no_captured_refund_while_its_clean_counterpart_does() -> None:
    """R10's cross-source case, asserted as a contrast.

    If no refund anywhere had a provider record, "the counterpart is missing"
    would describe them all rather than the one that earns
    `awaiting_evidence`.
    """
    def refunds(ref: str) -> list:
        out = []
        for unit in _units(_fixture(ref)):
            out.extend(unit.get("payments", {}).get("refunds", []))
        return out

    assert refunds("ORD-0017") == [], "F5's provider record must be absent"
    assert len(refunds("ORD-0011")) == 1, "the clean refund's must be present"
    assert len(refunds("ORD-0013")) == 1, "F1's refund is real; its ledger is not"


def test_f6_paypal_released_in_full_while_the_ledger_understates_it() -> None:
    """The disbursement disagrees with the marketplace's own record."""
    doc = _fixture("ORD-0018")
    assert doc["releases"], "F6 needs a real release to disagree with"
    payout = Money(doc["releases"][0]["payout_amount"]["value"], "USD")

    spec = data.faulty_order("ORD-0018")
    owed = Money(spec.expected_entitlements["vendor-bellweather"], "EUR")
    credited = Money(spec.understated_credit["vendor-bellweather"], "EUR")

    assert payout == Money("147.08", "USD"), "PayPal released the full net"
    assert credited < owed
    assert (owed - credited) == Money("15.00", "EUR")


def test_the_rounding_artefact_is_real_not_constructed() -> None:
    """ORD-0008's one cent comes from PayPal's own arithmetic.

    Converting each capture separately and converting the order total once
    differ by a cent, and **neither is wrong**. A single-seller order cannot
    produce this, which is why ORD-0008 has two.
    """
    doc = _fixture("ORD-0008")
    units = _units(doc)
    assert len(units) == 2, "one purchase unit cannot be converted twice"

    per_capture = Decimal("0")
    net_total = Decimal("0")
    rate = None
    for unit in units:
        brk = unit["payments"]["captures"][0]["seller_receivable_breakdown"]
        per_capture += Decimal(brk["receivable_amount"]["value"])
        net_total += Decimal(brk["net_amount"]["value"])
        rate = Decimal(brk["exchange_rate"]["value"])

    once = (net_total * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    assert per_capture == Decimal("119.17")
    assert once == Decimal("119.18")
    assert abs(per_capture - once) == Decimal("0.01")
