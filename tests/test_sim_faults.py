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

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.engine.ledger import position
from reckonai.models import (
    Capture,
    Disbursement,
    FxConversion,
    Order,
    PlatformFee,
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


def test_every_evidence_reference_resolves(
    session: Session, full: seed.SeedResult
) -> None:
    """Block E's agent must cite real ids, so the manifest's must be real.

    An expected-evidence list naming a reference nothing resolves would score
    a correct investigation as wrong.
    """
    refs: set[str] = set()
    for table, column in (
        (Order, Order.external_ref),
        (SellerLedgerEntry, SellerLedgerEntry.external_ref),
        (Refund, Refund.external_id),
        (PlatformFee, PlatformFee.external_id),
        (Disbursement, Disbursement.external_id),
        (Capture, Capture.external_id),
    ):
        refs.update(
            session.scalars(
                select(column).where(table.tenant_id == full.tenant_id)
            )
        )
    # Order items are referenced too, and live on their own table.
    from reckonai.models import OrderItem

    refs.update(
        session.scalars(
            select(OrderItem.external_ref).where(
                OrderItem.tenant_id == full.tenant_id
            )
        )
    )
    # FX conversions carry no external reference of their own - the table has
    # none, deliberately, since a conversion is a property of its parent. The
    # manifest names them by the payload's record id instead.
    from reckonai.models import RawPayload

    refs.update(
        r
        for r in session.scalars(
            select(RawPayload.source_record_id).where(
                RawPayload.tenant_id == full.tenant_id
            )
        )
        if r is not None
    )

    for fault in manifest.FAULTS:
        missing = set(fault.evidence_refs) - refs
        assert not missing, f"{fault.id} cites unresolvable refs {sorted(missing)}"


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


def test_f2_platform_fee_disagrees_with_the_retained_commission(
    session: Session, full: seed.SeedResult
) -> None:
    """scenario 2, and the reason `paypal_platform_fee` exists.

    With the provider's fee derived from the marketplace's figure, the two
    would agree and the fault would be invisible.
    """
    order_id = _order_id(session, full.tenant_id, "ORD-0014")

    retained = session.scalar(
        select(SellerLedgerEntry).where(
            SellerLedgerEntry.tenant_id == full.tenant_id,
            SellerLedgerEntry.order_id == order_id,
            SellerLedgerEntry.entry_category == "commission",
        )
    )
    assert retained is not None
    assert retained.amount == Money("30.00", "USD")

    fee = session.scalar(
        select(PlatformFee).where(
            PlatformFee.tenant_id == full.tenant_id,
            PlatformFee.external_id == "FEE-ORD-0014",
        )
    )
    assert fee is not None
    assert fee.amount == Money("20.00", "USD"), "PayPal took the rule-correct fee"

    assert (retained.amount - fee.amount) == Money(
        manifest.fault("F2").impact_amount, "USD"
    )


def test_f3_settlement_does_not_follow_from_its_rate(
    session: Session, full: seed.SeedResult
) -> None:
    """scenario 3, and clear of ORD-0008's legitimate rounding artefact.

    The assertion that matters is the **ratio** between the two discrepancies:
    if the fault were the same size as the artefact, no classifier could tell
    them apart and the dataset would be unusable for Phase 11's gate.
    """
    faulty = data.faulty_order("ORD-0015")
    assert faulty.fx is not None

    implied = Money(faulty.fx.naive_recomputation, "EUR")
    reported = Money(faulty.fx.settlement_amount, "EUR")
    gap = abs((implied - reported).amount)

    assert gap == Money(manifest.fault("F3").impact_amount, "EUR").amount

    artefact = Money("0.01", "EUR").amount
    assert gap > artefact * 100, (
        f"the F3 gap is {gap} against a legitimate artefact of {artefact}; a "
        "fault within an order of magnitude of the noise is not detectable "
        "without a threshold, and Phase 11 forbids introducing one"
    )

    # The conversion row is present, and still carries no fee (P3).
    conversion = session.scalar(
        select(FxConversion).where(
            FxConversion.tenant_id == full.tenant_id,
            FxConversion.target_amount == reported.amount,
        )
    )
    assert conversion is not None
    assert conversion.fee is None


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


def test_f5_marketplace_side_is_correct_and_paypal_side_is_absent(
    session: Session, full: seed.SeedResult
) -> None:
    """R10's cross-source case, and the fault that must not become an exception.

    Both halves are asserted. The marketplace's ledger is complete - so there
    is genuinely nothing wrong on the side we can see - and PayPal's refund
    record is missing, which is what makes "missing from PayPal" the tempting
    and wrong conclusion.
    """
    order_id = _order_id(session, full.tenant_id, "ORD-0017")

    entries = list(
        session.scalars(
            select(SellerLedgerEntry).where(
                SellerLedgerEntry.tenant_id == full.tenant_id,
                SellerLedgerEntry.order_id == order_id,
                SellerLedgerEntry.entry_category.in_(
                    ["refund", "commission_reversal"]
                ),
            )
        )
    )
    assert len(entries) == 2, "the marketplace side must be complete and correct"

    assert session.scalar(
        select(func.count())
        .select_from(Refund)
        .where(
            Refund.tenant_id == full.tenant_id,
            Refund.external_id == "PPRF-RFND-0017-A",
        )
    ) == 0, "PayPal's record must be absent"

    # The clean refund's provider record *is* present, which is what makes the
    # absence above a distinguishable state rather than the universal one.
    assert session.scalar(
        select(func.count())
        .select_from(Refund)
        .where(
            Refund.tenant_id == full.tenant_id,
            Refund.external_id == "PPRF-RFND-0011-A",
        )
    ) == 1

    assert manifest.fault("F5").expected_classification == "awaiting_evidence"
    assert manifest.fault("F5").is_exception is False


def test_f6_release_falls_short_of_the_obligation(
    session: Session, full: seed.SeedResult
) -> None:
    """A completed release that disagrees, so nothing in flight explains it."""
    release = session.scalar(
        select(Disbursement).where(
            Disbursement.tenant_id == full.tenant_id,
            Disbursement.external_id == "REL-ORD-0018",
        )
    )
    assert release is not None
    assert release.state == "disbursement_completed"
    assert release.amount == Money("120.00", "EUR")

    owed = Money(
        data.faulty_order("ORD-0018").expected_entitlements["vendor-citrine"],
        "EUR",
    )
    assert (owed - release.amount) == Money(
        manifest.fault("F6").impact_amount, "EUR"
    )

    # Still no provider release timestamp, fault or not (P3).
    assert release.released_at is None


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
