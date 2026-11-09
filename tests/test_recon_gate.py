"""The false-positive gate: the hardest criterion in the roadmap.

specs/roadmap.md Phase 11 has four exit criteria and this module proves all of
them against the real seeded tenant rather than hand-built rows:

  1. every fault in the manifest is detected and correctly classified
  2. **the clean baseline produces zero exceptions**
  3. a delayed event is classified as delayed, not as a discrepancy
  4. each exception row carries financial impact and the ids of its evidence

Criterion 2 is why this is database-backed at all. The unit tests in
`test_recon_classifier.py` prove each rule on rows built to exercise it, which
is necessary and not sufficient: the criterion is about the 15 clean purchase
units of the actual dataset, including the two non-faults that exist
specifically to be ignored. Proving it against fixtures would be proving it
against the author's idea of the baseline.

**Nothing here commits.** The shared `session` fixture rolls back, which keeps
this module clear of the suite's order-dependence (Block A debt 4) - it adds no
rows that `test_migrations`' downgrade could meet. The filename sorts after
`test_migrations` in any case.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data, reconcile
from reckonai.models import Tenant
from reckonai.money import Money
from reckonai.recon import ExceptionCode, Finding, exceptions_only
from reckonai.sim import data, manifest


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    """The demo tenant, seeded into the rolled-back session.

    `demo_data.ensure` is the same entry point the deploy runs, so the rows
    classified here are the rows the demo will show - not a second fixture
    that could drift from it.
    """
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None, "the demo tenant was not seeded"
    return tenant_id


@pytest.fixture
def findings(session: Session, seeded: uuid.UUID) -> tuple[Finding, ...]:
    """One reconciliation run over the whole tenant."""
    return reconcile.run(session, seeded)


def by_order(findings: tuple[Finding, ...], order_ref: str) -> list[Finding]:
    return [finding for finding in findings if finding.order_ref == order_ref]


# ---------------------------------------------------------------------------
# Criterion 2 - the gate
# ---------------------------------------------------------------------------


def test_the_clean_baseline_produces_zero_exceptions(
    findings: tuple[Finding, ...],
) -> None:
    """**The hardest criterion.** Twelve clean orders, nothing reported.

    ORD-0005 and ORD-0008 are in here deliberately: held funds under
    `DELAYED`, and the cent of rounding PayPal's own arithmetic produces on a
    twice-converted order. Both are healthy, and a product that flags them
    reports every delayed transaction and every converted transaction as an
    error.
    """
    clean = {order.ref for order in data.CLEAN_ORDERS}

    reported = [
        finding for finding in exceptions_only(findings) if finding.order_ref in clean
    ]

    assert reported == [], "false positives on the clean baseline:\n" + "\n".join(
        f"  {f.order_ref}: {f.code} {f.impact} - {f.detail}" for f in reported
    )


def test_the_clean_baseline_is_not_empty() -> None:
    """A gate over an empty set passes trivially.

    The same guard Phase 2 put on the float gate: if the seed stopped
    producing clean orders, the test above would pass over nothing.
    """
    clean = {order.ref for order in data.CLEAN_ORDERS}

    assert len(clean) >= 12, f"only {len(clean)} clean orders seeded"
    assert "ORD-0005" in clean, "the held-funds non-fault is missing"
    assert "ORD-0008" in clean, "the rounding non-fault is missing"


# ---------------------------------------------------------------------------
# Criterion 1 - detection and classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fault", manifest.FAULTS, ids=lambda f: f.id)
def test_every_fault_is_detected_and_correctly_classified(
    findings: tuple[Finding, ...], fault: manifest.Fault
) -> None:
    """One row per fault, against the manifest's own expectation.

    The manifest is ground truth and is not edited to match the
    implementation. Where the two disagreed during this block the manifest was
    checked against the fixtures and the seed first, and corrected only where
    all three agreed against it - `tests/test_manifest_agrees_with_seed.py`
    now holds that agreement in place.
    """
    reported = by_order(findings, fault.order_ref)

    assert reported, f"{fault.id} on {fault.order_ref} was not detected at all"
    assert [str(f.code) for f in reported] == [fault.expected_classification], (
        f"{fault.id}: expected {fault.expected_classification}, got "
        f"{[str(f.code) for f in reported]}"
    )


@pytest.mark.parametrize("fault", manifest.FAULTS, ids=lambda f: f.id)
def test_every_faults_impact_matches_the_manifest(
    findings: tuple[Finding, ...], fault: manifest.Fault
) -> None:
    """Criterion 4's financial impact, against an independently stated figure.

    The manifest's impact is hand-written and the classifier's is computed, so
    this compares two derivations rather than a value against itself.
    """
    reported = by_order(findings, fault.order_ref)
    assert reported

    expected = Money(fault.impact_amount, fault.impact_currency)

    assert reported[0].impact == expected, (
        f"{fault.id}: manifest says {expected}, classifier says {reported[0].impact}"
    )


@pytest.mark.parametrize("fault", manifest.FAULTS, ids=lambda f: f.id)
def test_every_fault_cites_the_evidence_the_manifest_names(
    findings: tuple[Finding, ...], fault: manifest.Fault
) -> None:
    """Criterion 4's evidence ids - **every one the manifest names**.

    This asserted only that the order itself was cited until a review pointed
    out it was proving almost nothing. Tightened to full coverage, which then
    showed that F6 cited no release at all and that four of the manifest's
    own references named records that exist nowhere - Block A's synthetic
    provider rows, retired by R16 and never followed up.

    The manifest records "the source references a correct investigation should
    cite", so this is what makes Block E's "did it cite the right evidence?"
    scoreable rather than a judgement call.
    """
    reported = by_order(findings, fault.order_ref)
    assert reported

    cited = set(reported[0].evidence_refs)
    missing = set(fault.evidence_refs) - cited

    assert cited, f"{fault.id} cites nothing"
    assert missing == set(), (
        f"{fault.id} does not cite {sorted(missing)}; it cites {sorted(cited)}"
    )


def test_every_reference_the_manifest_names_is_a_real_record(
    session: Session, seeded: uuid.UUID
) -> None:
    """**The guard that was missing**, and the reason four phantoms survived.

    `test_no_finding_cites_a_reference_that_does_not_exist` checks the
    classifier's output; nothing checked the manifest's own input. So
    `FEE-ORD-0014`, `CAP-ORD-0015`, `FX-ORD-0015` and `REL-ORD-0018` sat in
    the ground truth naming records R16's capture had retired, and the
    coverage assertion above could never have passed.
    """
    from reckonai.models import (
        Capture,
        Disbursement,
        Order,
        OrderItem,
        PlatformFee,
        Refund,
        SellerLedgerEntry,
    )

    real: set[str] = set()
    for model, column in (
        (Order, Order.external_ref),
        (OrderItem, OrderItem.external_ref),
        (Capture, Capture.external_id),
        (Refund, Refund.external_id),
        (PlatformFee, PlatformFee.external_id),
        (Disbursement, Disbursement.external_id),
        (SellerLedgerEntry, SellerLedgerEntry.external_ref),
    ):
        real.update(session.scalars(select(column).where(model.tenant_id == seeded)))

    named = {ref for fault in manifest.FAULTS for ref in fault.evidence_refs}
    phantom = named - real

    assert phantom == set(), (
        f"the manifest names records that do not exist: {sorted(phantom)}"
    )


def test_no_finding_cites_a_reference_that_does_not_exist(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    """Block E's exit criterion is that every cited id resolves to a real
    record, and a fabricated reference here would be inherited by every
    investigation built on it.
    """
    from reckonai.models import (
        Capture,
        Disbursement,
        Order,
        OrderItem,
        PlatformFee,
        Refund,
        SellerLedgerEntry,
    )

    real: set[str] = set()
    for model, column in (
        (Order, Order.external_ref),
        (OrderItem, OrderItem.external_ref),
        (Capture, Capture.external_id),
        (SellerLedgerEntry, SellerLedgerEntry.external_ref),
        (Refund, Refund.external_id),
        (PlatformFee, PlatformFee.external_id),
        (Disbursement, Disbursement.external_id),
    ):
        real.update(session.scalars(select(column).where(model.tenant_id == seeded)))

    cited = {ref for finding in findings for ref in finding.evidence_refs}
    fabricated = cited - real

    assert fabricated == set(), f"findings cite non-existent records: {fabricated}"


# ---------------------------------------------------------------------------
# Criterion 3 - a delayed event is delayed, not wrong
# ---------------------------------------------------------------------------


def test_f5_is_awaiting_evidence_and_not_an_exception(
    findings: tuple[Finding, ...],
) -> None:
    """ORD-0017: the marketplace refunded and PayPal's record is a day behind.

    Both halves matter. Classifying it `awaiting_evidence` is right, and
    counting that as an exception would fail the gate while being correct -
    which is why the non-error distinction lives in the vocabulary rather than
    in whoever is counting.
    """
    reported = by_order(findings, "ORD-0017")

    assert [f.code for f in reported] == [ExceptionCode.AWAITING_EVIDENCE]
    assert reported[0].is_exception is False
    assert exceptions_only(tuple(reported)) == ()


def test_held_funds_are_not_reported_at_all(findings: tuple[Finding, ...]) -> None:
    """ORD-0005, by name. The first of the two traps named in advance."""
    assert by_order(findings, "ORD-0005") == []


def test_the_held_funds_order_is_clean_for_the_reason_we_think(
    session: Session, seeded: uuid.UUID
) -> None:
    """**Why ORD-0005 is clean, asserted rather than assumed.**

    Measured during this block: deleting `funds_held` from
    `tolerance.HEALTHY_HELD_STATES` does not fail the gate, because ORD-0005's
    ledger credits the full 180.00 entitlement, so the residual is zero and
    the held-funds guard is never reached on this order at all. The guard is
    genuinely proven - removing the state fails three unit tests in
    `test_recon_classifier.py` and `test_recon_tolerance.py` - but it is
    proven there and not here.

    This test pins the two facts that make that true, so nobody reads the
    assertion above as evidence that the database baseline exercises T3. If
    the seed ever credits ORD-0005 short, this fails and the test above starts
    carrying real weight.
    """
    from reckonai.models import Capture, Order, Payment, SellerLedgerEntry

    order = session.scalar(
        select(Order).where(Order.tenant_id == seeded, Order.external_ref == "ORD-0005")
    )
    assert order is not None

    held = [
        capture.disbursement_state
        for capture in session.scalars(
            select(Capture).where(Capture.tenant_id == seeded)
        )
        if capture.disbursement_state == "funds_held"
    ]
    assert held, "no capture is in funds_held; the trap's data is gone"

    entries = tuple(
        session.scalars(
            select(SellerLedgerEntry).where(SellerLedgerEntry.order_id == order.id)
        )
    )
    categories = {entry.entry_category for entry in entries}

    assert categories == {"sale", "commission"}, (
        "ORD-0005's ledger is no longer the complete-books case this test "
        f"describes: {sorted(categories)}"
    )
    assert (
        session.scalars(select(Payment).where(Payment.tenant_id == seeded)).first()
        is not None
    )


def test_the_rounding_artefact_is_not_reported_at_all(
    findings: tuple[Finding, ...],
) -> None:
    """ORD-0008, by name. The second trap.

    It stays clean because no comparison here recomputes a converted total -
    the figure that disagrees by a cent is not one PayPal reports, so it is
    never produced. See `classifier.py`'s module docstring.
    """
    assert by_order(findings, "ORD-0008") == []


# ---------------------------------------------------------------------------
# The run as a whole
# ---------------------------------------------------------------------------


def test_exactly_the_expected_number_of_exceptions(
    findings: tuple[Finding, ...],
) -> None:
    """Five exceptions and one non-error finding, and nothing else.

    Counted rather than filtered, so an extra finding anywhere in the dataset
    fails here even if it lands on a faulty order where a test above would
    have tolerated it.
    """
    exceptions = exceptions_only(findings)

    assert len(exceptions) == len(manifest.EXCEPTIONS) == 5
    assert len(findings) == len(manifest.FAULTS) == 6


def test_every_order_is_matched_to_paypals_record(
    session: Session, seeded: uuid.UUID
) -> None:
    """The link parsed out of `purchase_units[].reference_id`, end to end.

    If this breaks, every order falls into `marketplace_only` and the run
    reports nothing at all - which would pass the false-positive gate while
    reconciling nothing.
    """
    cases, marketplace_only, provider_only = reconcile.load_order_cases(session, seeded)

    assert len(cases) == len(data.FULL.orders)
    assert marketplace_only == ()
    assert provider_only == ()


def test_the_run_is_deterministic(session: Session, seeded: uuid.UUID) -> None:
    """specs/mission.md P7: a historical run must be re-derivable.

    Two runs over unchanged rows must agree exactly, including order - the
    queries carry no `ORDER BY` on most tables, so this is what keeps the
    output from depending on physical row order.
    """
    first = reconcile.run(session, seeded)
    second = reconcile.run(session, seeded)

    assert [f.as_dict() for f in first] == [f.as_dict() for f in second]
