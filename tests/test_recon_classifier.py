"""Classification, on hand-built rows shaped like the real dataset.

Every figure comes from `specs/2026-11-09-paypal-capture/validation.md`
section 2 or from `reckonai.sim.data`, so a test that passes here is checking
the arithmetic the database-backed gate will meet rather than a convenient
invention.

The clean cases matter as much as the faults. Four of the tests below assert
that *nothing* is reported, and those are the ones standing in for the
false-positive gate at unit scale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from reckonai.engine import CaptureNotSupplied
from reckonai.money import Money
from reckonai.recon.classifier import (
    Finding,
    OrderCase,
    classify_order,
    exceptions_only,
)
from reckonai.recon.vocabulary import EvidenceBasis, ExceptionCode

TENANT = uuid.uuid4()
SELLER_A = uuid.uuid4()
SELLER_B = uuid.uuid4()
PLACED = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Hand-built rows, matching the protocols rather than the ORM
# ---------------------------------------------------------------------------


@dataclass
class Order:
    total: Money
    placed_at: datetime = PLACED
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Item:
    seller_id: uuid.UUID
    line_total: Money
    category: str | None = "books"
    external_ref: str = "ITEM-1"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Rule:
    rate: Decimal = Decimal("0.10")
    seller_id: uuid.UUID | None = None
    category: str | None = None
    fixed: Money | None = None
    valid_from: datetime = datetime(2020, 1, 1, tzinfo=UTC)
    valid_to: datetime | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Capture:
    seller_id: uuid.UUID | None
    amount: Money
    net_amount: Money | None = None
    gross_amount: Money | None = None
    paypal_fee: Money | None = None
    settlement_amount: Money | None = None
    status: str = "COMPLETED"
    disbursement_state: str = "disbursed_at_capture"
    external_id: str = "CAP-1"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Fee:
    amount: Money
    seller_id: uuid.UUID | None = None
    capture_id: uuid.UUID | None = None
    refund_id: uuid.UUID | None = None
    external_id: str = "FEE-1"


@dataclass
class Release:
    amount: Money
    seller_id: uuid.UUID | None = None
    capture_id: uuid.UUID | None = None
    state: str = "disbursement_completed"
    external_id: str = "REL-1"


@dataclass
class Refund:
    amount: Money
    seller_id: uuid.UUID | None = None
    capture_id: uuid.UUID | None = None
    capture_linkage_is_derived: bool = False
    external_id: str = "RFND-1"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Entry:
    entry_category: str
    transaction_type: str
    amount: Money
    order_id: uuid.UUID | None
    seller_id: uuid.UUID = SELLER_A
    external_ref: str = "LED-1"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def clean_case(
    *,
    gross: str = "110.00",
    currency: str = "USD",
    commission: str = "11.00",
    entitlement: str = "99.00",
    seller: uuid.UUID = SELLER_B,
    order_ref: str = "ORD-0003",
) -> OrderCase:
    """One clean single-seller order, with its ledger correct.

    The default is ORD-0013's shape without its fault, so each fault test
    below differs from a clean baseline by exactly the fault.
    """
    order = Order(Money(gross, currency))
    capture = Capture(
        seller,
        Money(gross, currency),
        net_amount=Money(entitlement, currency),
        gross_amount=Money(gross, currency),
        external_id=f"CAP-{order_ref}",
    )
    return OrderCase(
        order_ref=order_ref,
        order=order,
        items=(Item(seller, Money(gross, currency)),),
        rules=(Rule(),),
        captures=(capture,),
        platform_fees=(
            Fee(
                Money(commission, currency),
                seller_id=seller,
                capture_id=capture.id,
                external_id=f"FEE-{order_ref}",
            ),
        ),
        ledger_entries=(
            Entry(
                "sale",
                "credit",
                Money(gross, currency),
                order.id,
                seller,
                f"LED-{order_ref}-sale",
            ),
            Entry(
                "commission",
                "debit",
                Money(commission, currency),
                order.id,
                seller,
                f"LED-{order_ref}-comm",
            ),
        ),
    )


def codes(findings: tuple[Finding, ...]) -> list[ExceptionCode]:
    return [finding.code for finding in findings]


# ---------------------------------------------------------------------------
# The clean baseline, at unit scale
# ---------------------------------------------------------------------------


def test_a_clean_order_produces_nothing() -> None:
    """The criterion the whole block is judged on, in miniature."""
    assert classify_order(clean_case()) == ()


def test_a_clean_two_seller_order_produces_nothing() -> None:
    """ORD-0001, the mission worked example: 160.00 -> A 90.00, B 54.00,
    commission 16.00."""
    order = Order(Money("160.00", "USD"))
    cap_a = Capture(
        SELLER_A,
        Money("100.00", "USD"),
        net_amount=Money("90.00", "USD"),
        external_id="CAP-A",
    )
    cap_b = Capture(
        SELLER_B,
        Money("60.00", "USD"),
        net_amount=Money("54.00", "USD"),
        external_id="CAP-B",
    )
    case = OrderCase(
        order_ref="ORD-0001",
        order=order,
        items=(
            Item(SELLER_A, Money("100.00", "USD")),
            Item(SELLER_B, Money("60.00", "USD")),
        ),
        rules=(Rule(),),
        captures=(cap_a, cap_b),
        platform_fees=(
            Fee(Money("10.00", "USD"), SELLER_A, cap_a.id, external_id="FEE-A"),
            Fee(Money("6.00", "USD"), SELLER_B, cap_b.id, external_id="FEE-B"),
        ),
        ledger_entries=(
            Entry("sale", "credit", Money("100.00", "USD"), order.id, SELLER_A, "L1"),
            Entry(
                "commission", "debit", Money("10.00", "USD"), order.id, SELLER_A, "L2"
            ),
            Entry("sale", "credit", Money("60.00", "USD"), order.id, SELLER_B, "L3"),
            Entry(
                "commission", "debit", Money("6.00", "USD"), order.id, SELLER_B, "L4"
            ),
        ),
    )

    assert classify_order(case) == ()


def test_a_clean_refund_reflected_in_the_ledger_produces_nothing() -> None:
    """ORD-0011: a 20.00 refund with PayPal's automatic 2.00 fee reversal.

    This is the case most likely to be reported wrongly, because it differs
    from F1 only by the presence of two ledger rows.
    """
    case = clean_case(
        gross="70.00", commission="7.00", entitlement="63.00", order_ref="ORD-0011"
    )
    refund = Refund(
        Money("20.00", "USD"),
        seller_id=SELLER_B,
        capture_id=case.captures[0].id,
        external_id="RFND-0011-A",
    )
    case = replace(
        case,
        provider_refunds=(refund,),
        platform_fees=(
            *case.platform_fees,
            Fee(
                Money("2.00", "USD"),
                seller_id=SELLER_B,
                refund_id=refund.id,
                external_id="FEE-REV-0011",
            ),
        ),
        ledger_entries=(
            *case.ledger_entries,
            Entry(
                "refund",
                "debit",
                Money("20.00", "USD"),
                case.order.id,
                SELLER_B,
                "LED-0011-refund",
            ),
            Entry(
                "commission_reversal",
                "credit",
                Money("2.00", "USD"),
                case.order.id,
                SELLER_B,
                "LED-0011-rev",
            ),
        ),
    )

    assert classify_order(case) == ()


def test_held_funds_under_delayed_produce_nothing() -> None:
    """ORD-0005, the second named false-positive trap.

    The capture reads COMPLETED while the seller is unpaid, and the ledger
    has not been credited yet. Reporting that makes every DELAYED order an
    exception.
    """
    case = clean_case(
        gross="200.00",
        currency="EUR",
        commission="20.00",
        entitlement="180.00",
        order_ref="ORD-0005",
    )
    held = Capture(
        SELLER_B,
        Money("200.00", "EUR"),
        net_amount=Money("180.00", "EUR"),
        disbursement_state="funds_held",
        external_id="CAP-ORD-0005",
    )
    case = replace(
        case,
        captures=(held,),
        # The sale is on the books; the seller has not been paid.
        ledger_entries=(
            Entry(
                "sale",
                "credit",
                Money("150.00", "EUR"),
                case.order.id,
                SELLER_B,
                "LED-0005-part",
            ),
        ),
    )

    assert classify_order(case) == ()


@pytest.mark.parametrize(
    "state", ["funds_held", "release_requested", "disbursement_pending"]
)
def test_every_healthy_held_state_suppresses_the_residual(state: str) -> None:
    case = clean_case()
    held = Capture(
        SELLER_B,
        Money("110.00", "USD"),
        net_amount=Money("99.00", "USD"),
        disbursement_state=state,
    )
    case = replace(
        case,
        captures=(held,),
        ledger_entries=(
            Entry("sale", "credit", Money("50.00", "USD"), case.order.id, SELLER_B),
        ),
    )

    assert classify_order(case) == ()


# ---------------------------------------------------------------------------
# The six faults
# ---------------------------------------------------------------------------


def test_f1_a_refund_the_ledger_never_recorded() -> None:
    """ORD-0013. Impact is the 30.00 refund less PayPal's 3.00 reversal."""
    case = clean_case(order_ref="ORD-0013")
    refund = Refund(
        Money("30.00", "USD"),
        seller_id=SELLER_B,
        capture_id=case.captures[0].id,
        capture_linkage_is_derived=True,
        external_id="RFND-0013-A",
    )
    case = replace(
        case,
        provider_refunds=(refund,),
        platform_fees=(
            *case.platform_fees,
            Fee(
                Money("3.00", "USD"),
                seller_id=SELLER_B,
                refund_id=refund.id,
                external_id="FEE-REV-0013",
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.MISSING_LEDGER_ADJUSTMENT]
    assert findings[0].impact == Money("27.00", "USD")
    assert "RFND-0013-A" in findings[0].evidence_refs
    assert "FEE-REV-0013" in findings[0].evidence_refs


def test_f1_says_its_linkage_was_derived() -> None:
    """specs/roadmap.md Phase 11: an exception resting on the HATEOAS-parsed
    refund linkage must say so."""
    case = clean_case(order_ref="ORD-0013")
    refund = Refund(
        Money("30.00", "USD"),
        seller_id=SELLER_B,
        capture_id=case.captures[0].id,
        capture_linkage_is_derived=True,
        external_id="RFND-0013-A",
    )
    case = replace(case, provider_refunds=(refund,))

    assert classify_order(case)[0].evidence_basis is EvidenceBasis.DERIVED_LINKAGE


def test_f2_commission_the_marketplace_overstated() -> None:
    """ORD-0014: 30.00 retained where the tenant's 10% gives 20.00."""
    order = Order(Money("200.00", "USD"))
    capture = Capture(
        SELLER_A,
        Money("200.00", "USD"),
        net_amount=Money("180.00", "USD"),
        external_id="CAP-ORD-0014",
    )
    case = OrderCase(
        order_ref="ORD-0014",
        order=order,
        items=(Item(SELLER_A, Money("200.00", "USD")),),
        rules=(Rule(),),
        captures=(capture,),
        platform_fees=(
            Fee(
                Money("20.00", "USD"),
                SELLER_A,
                capture.id,
                external_id="FEE-ORD-0014",
            ),
        ),
        ledger_entries=(
            Entry("sale", "credit", Money("200.00", "USD"), order.id, SELLER_A, "L1"),
            # The wrong figure the marketplace believes.
            Entry(
                "commission", "debit", Money("30.00", "USD"), order.id, SELLER_A, "L2"
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.COMMISSION_MISMATCH]
    assert findings[0].impact == Money("10.00", "USD")
    assert findings[0].expected == Money("20.00", "USD")
    assert findings[0].reported == Money("30.00", "USD")
    assert "FEE-ORD-0014" in findings[0].evidence_refs


def test_f3_the_ledger_recorded_in_another_currency_at_a_stale_rate() -> None:
    """ORD-0015: 262.00 USD recorded against a reported 294.16 USD settlement.

    The comparison is against PayPal's reported `receivable_amount`. A
    recomputation of 270.00 EUR times 1.089477173902071 gives
    294.158... , which is not the figure PayPal reported and must not be the
    figure compared.
    """
    order = Order(Money("300.00", "EUR"))
    capture = Capture(
        SELLER_B,
        Money("300.00", "EUR"),
        net_amount=Money("270.00", "EUR"),
        settlement_amount=Money("294.16", "USD"),
        external_id="CAP-ORD-0015",
    )
    case = OrderCase(
        order_ref="ORD-0015",
        order=order,
        items=(Item(SELLER_B, Money("300.00", "EUR")),),
        rules=(Rule(),),
        captures=(capture,),
        platform_fees=(
            Fee(Money("30.00", "EUR"), SELLER_B, capture.id, external_id="FEE-0015"),
        ),
        ledger_entries=(
            Entry(
                "sale",
                "credit",
                Money("262.00", "USD"),
                order.id,
                SELLER_B,
                "LED-0015-usd",
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.FX_DISCREPANCY]
    assert findings[0].impact == Money("32.16", "USD")
    assert findings[0].expected == Money("294.16", "USD")
    assert "CAP-ORD-0015" in findings[0].evidence_refs


def test_f3_without_a_reported_settlement_reports_without_inventing() -> None:
    """specs/mission.md P3 and P5 together, which pull in different directions.

    With no reported settlement there is no figure in that currency anyone
    measured, so no impact may be stated - P3 forbids closing the gap with a
    quoted rate. But silence is also wrong: the ledger is in a currency the
    order is not, which is a real condition an operator must see. P5's answer
    is to escalate rather than guess.

    So the finding is made, the impact is zero because none can be *stated*,
    the detail says why, and `confirmed` is False. **This test asserted
    silence until a review pointed out that the module docstring promised the
    opposite** - "an unexplained difference is still never silently dropped" -
    and the docstring was right.
    """
    order = Order(Money("300.00", "EUR"))
    capture = Capture(
        SELLER_B,
        Money("300.00", "EUR"),
        net_amount=Money("270.00", "EUR"),
        settlement_amount=None,
        external_id="CAP-ORD-0015",
    )
    case = OrderCase(
        order_ref="ORD-0015",
        order=order,
        items=(Item(SELLER_B, Money("300.00", "EUR")),),
        rules=(Rule(),),
        captures=(capture,),
        ledger_entries=(
            Entry("sale", "credit", Money("262.00", "USD"), order.id, SELLER_B, "L1"),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.MIXED_CURRENCY_ORDER]
    assert findings[0].impact == Money("0.00", "USD")
    assert findings[0].expected is None, "no expected figure may be invented"
    assert findings[0].reported == Money("262.00", "USD")
    assert findings[0].confirmed is False
    assert "L1" in findings[0].evidence_refs


def test_f4_one_sale_credited_twice() -> None:
    """ORD-0016: 90.00 credited under two distinct references."""
    case = clean_case(
        gross="90.00",
        commission="9.00",
        entitlement="81.00",
        seller=SELLER_A,
        order_ref="ORD-0016",
    )
    case = replace(
        case,
        ledger_entries=(
            *case.ledger_entries,
            Entry(
                "sale",
                "credit",
                Money("90.00", "USD"),
                case.order.id,
                SELLER_A,
                "LED-ORD-0016-vendor-aurora-sale-dup",
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.DUPLICATE_LEDGER_ENTRY]
    assert findings[0].impact == Money("90.00", "USD")
    assert "LED-ORD-0016-vendor-aurora-sale-dup" in findings[0].evidence_refs


def test_f5_a_marketplace_refund_paypal_has_not_reported_is_not_an_exception() -> None:
    """ORD-0017, and the single most important non-error case.

    The marketplace refunded 15.00 and recorded it correctly. PayPal's record
    is a day behind. Reporting a discrepancy here is the false positive the
    hardest criterion forbids, and the ledger must not look wrong by the
    refund either.
    """
    case = clean_case(
        gross="60.00",
        commission="6.00",
        entitlement="54.00",
        seller=SELLER_A,
        order_ref="ORD-0017",
    )
    refund = Refund(
        Money("15.00", "USD"),
        seller_id=SELLER_A,
        capture_id=case.captures[0].id,
        external_id="RFND-0017-A",
    )
    case = replace(
        case,
        marketplace_refunds=(refund,),
        ledger_entries=(
            *case.ledger_entries,
            Entry(
                "refund",
                "debit",
                Money("15.00", "USD"),
                case.order.id,
                SELLER_A,
                "LED-0017-refund",
            ),
            Entry(
                "commission_reversal",
                "credit",
                Money("1.50", "USD"),
                case.order.id,
                SELLER_A,
                "LED-0017-rev",
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.AWAITING_EVIDENCE]
    assert findings[0].is_exception is False
    assert findings[0].impact == Money("0.00", "USD")
    assert exceptions_only(findings) == ()
    assert "RFND-0017-A" in findings[0].evidence_refs


def test_f6_a_shortfall_on_a_completed_release() -> None:
    """ORD-0018: PayPal released the full 135.00 EUR, the ledger credits
    120.00."""
    case = clean_case(
        gross="150.00",
        currency="EUR",
        commission="15.00",
        entitlement="135.00",
        order_ref="ORD-0018",
    )
    case = replace(
        case,
        disbursements=(
            Release(
                Money("135.00", "EUR"),
                seller_id=SELLER_B,
                external_id="REL-ORD-0018",
            ),
        ),
        ledger_entries=(
            Entry(
                "sale",
                "credit",
                Money("135.00", "EUR"),
                case.order.id,
                SELLER_B,
                "LED-0018-sale",
            ),
            Entry(
                "adjustment",
                "debit",
                Money("15.00", "EUR"),
                case.order.id,
                SELLER_B,
                "LED-0018-short",
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.DISBURSEMENT_MISMATCH]
    assert findings[0].impact == Money("15.00", "EUR")
    assert "REL-ORD-0018" in findings[0].evidence_refs


# ---------------------------------------------------------------------------
# One fault, one finding
# ---------------------------------------------------------------------------


def test_a_single_fault_produces_a_single_finding() -> None:
    """The reason specific rules explain and the fallback reports a remainder.

    F1's missing ledger rows also move the seller's position, so a generic
    position check would report the same fault twice - once correctly and once
    as a disbursement mismatch. Two findings for one fault is a false positive
    on the second, and it is what the explained-residual design prevents.
    """
    case = clean_case(order_ref="ORD-0013")
    refund = Refund(
        Money("30.00", "USD"),
        seller_id=SELLER_B,
        capture_id=case.captures[0].id,
        external_id="RFND-0013-A",
    )
    case = replace(
        case,
        provider_refunds=(refund,),
        platform_fees=(
            *case.platform_fees,
            Fee(
                Money("3.00", "USD"),
                seller_id=SELLER_B,
                refund_id=refund.id,
                external_id="FEE-REV-0013",
            ),
        ),
    )

    assert len(classify_order(case)) == 1


# ---------------------------------------------------------------------------
# Engine refusals
# ---------------------------------------------------------------------------


def test_no_applicable_rule_becomes_a_named_finding() -> None:
    """The engine raises rather than defaulting to a zero rate, and the
    classification says the rule configuration is what needs fixing."""
    order = Order(Money("100.00", "USD"))
    case = OrderCase(
        order_ref="ORD-0099",
        order=order,
        items=(Item(SELLER_A, Money("100.00", "USD"), category="unmatched"),),
        rules=(Rule(category="books"),),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.UNRESOLVABLE_COMMISSION_RULE]
    assert findings[0].impact == Money("0.00", "USD")
    assert "ORD-0099" in findings[0].evidence_refs


def test_a_mixed_currency_order_is_classified_not_coerced() -> None:
    """specs/mission.md P2. There is no single expected position to compute,
    so the engine refuses and the refusal is the finding."""
    order = Order(Money("100.00", "USD"))
    case = OrderCase(
        order_ref="ORD-0098",
        order=order,
        items=(
            Item(SELLER_A, Money("50.00", "USD")),
            Item(SELLER_B, Money("50.00", "EUR")),
        ),
        rules=(Rule(),),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.MIXED_CURRENCY_ORDER]


def test_a_refund_with_no_capture_fails_the_run_rather_than_reporting() -> None:
    """`CaptureNotSupplied` means this module was called with the wrong
    captures. Reporting it would file a caller bug as a finding about the
    data, so it propagates.
    """
    case = clean_case()
    orphan = Refund(
        Money("10.00", "USD"),
        seller_id=None,
        capture_id=uuid.uuid4(),
        external_id="RFND-ORPHAN",
    )
    case = replace(
        case,
        provider_refunds=(orphan,),
        captures=(),
    )

    with pytest.raises(CaptureNotSupplied):
        classify_order(case)


# ---------------------------------------------------------------------------
# The frozen JSON contract
# ---------------------------------------------------------------------------


def test_the_finding_contract_is_exactly_this_shape() -> None:
    """Frozen for Block F. A field rename fails here rather than silently
    reshaping the dashboard.

    Amounts are strings with their currency, so no client-side arithmetic is
    possible from this payload - which is what Block F's "no arithmetic in the
    dashboard" rule rests on.
    """
    finding = Finding(
        code=ExceptionCode.COMMISSION_MISMATCH,
        order_ref="ORD-0014",
        detail="The marketplace retained more than the rule gives.",
        impact=Money("10.00", "USD"),
        evidence_refs=("ORD-0014", "FEE-ORD-0014"),
        seller_id=SELLER_A,
        expected=Money("20.00", "USD"),
        reported=Money("30.00", "USD"),
    )

    assert finding.as_dict() == {
        "code": "commission_mismatch",
        "order_ref": "ORD-0014",
        "detail": "The marketplace retained more than the rule gives.",
        "is_exception": True,
        "impact": {"amount": "10.00", "currency": "USD"},
        "expected": {"amount": "20.00", "currency": "USD"},
        "reported": {"amount": "30.00", "currency": "USD"},
        "evidence_refs": ["ORD-0014", "FEE-ORD-0014"],
        "evidence_basis": "provider_reported",
        "seller_id": str(SELLER_A),
        "confirmed": True,
    }


def test_the_contract_carries_no_json_numbers() -> None:
    """A reconciliation system's output in binary floating point is the one
    place it can least afford to be."""
    finding = Finding(
        code=ExceptionCode.AWAITING_EVIDENCE,
        order_ref="ORD-0017",
        detail="late, not absent",
        impact=Money("0.00", "USD"),
        evidence_refs=(),
    )

    payload = finding.as_dict()

    assert payload["impact"] == {"amount": "0.00", "currency": "USD"}
    assert payload["is_exception"] is False
    assert payload["expected"] is None


def test_amounts_in_the_contract_are_always_quantized() -> None:
    """`0.5` and `0.50` must not both be able to reach the dashboard."""
    finding = Finding(
        code=ExceptionCode.COMMISSION_MISMATCH,
        order_ref="ORD-1",
        detail="",
        impact=Money("0.5", "USD"),
        evidence_refs=(),
    )

    assert finding.as_dict()["impact"] == {"amount": "0.50", "currency": "USD"}


# ---------------------------------------------------------------------------
# Regressions from the Block C code review
# ---------------------------------------------------------------------------


def test_two_sellers_with_equal_shares_are_not_reported_as_duplicates() -> None:
    """**The review's worst finding, and a false positive on correct data.**

    Two sellers who each sold 50.00 produce two identical `sale` credits and
    two identical `commission` debits. A duplicate key without the seller
    reported this healthy order twice as `duplicate_ledger_entry` and then
    added a self-contradicting residual - "PayPal discharged 45.00 USD and
    the seller ledger records 45.00 USD, a shortfall of 55.00 USD".

    The seeded dataset never splits an order equally - ORD-0001, ORD-0006 and
    ORD-0008 all have unequal shares - so the database gate passed on luck.
    This is the case the gate could not see.
    """
    order = Order(Money("100.00", "USD"))
    cap_a = Capture(
        SELLER_A,
        Money("50.00", "USD"),
        net_amount=Money("45.00", "USD"),
        external_id="CAP-EQ-A",
    )
    cap_b = Capture(
        SELLER_B,
        Money("50.00", "USD"),
        net_amount=Money("45.00", "USD"),
        external_id="CAP-EQ-B",
    )
    case = OrderCase(
        order_ref="ORD-EQUAL",
        order=order,
        items=(
            Item(SELLER_A, Money("50.00", "USD")),
            Item(SELLER_B, Money("50.00", "USD")),
        ),
        rules=(Rule(),),
        captures=(cap_a, cap_b),
        ledger_entries=(
            Entry("sale", "credit", Money("50.00", "USD"), order.id, SELLER_A, "L1"),
            Entry(
                "commission", "debit", Money("5.00", "USD"), order.id, SELLER_A, "L2"
            ),
            Entry("sale", "credit", Money("50.00", "USD"), order.id, SELLER_B, "L3"),
            Entry(
                "commission", "debit", Money("5.00", "USD"), order.id, SELLER_B, "L4"
            ),
        ),
    )

    assert classify_order(case) == ()


def test_one_sellers_genuine_duplicate_is_still_caught() -> None:
    """The fix must not buy its silence by going blind.

    Same amounts as the test above, but both duplicate rows belong to one
    seller, which is a real double-post.
    """
    order = Order(Money("100.00", "USD"))
    cap_a = Capture(
        SELLER_A,
        Money("100.00", "USD"),
        net_amount=Money("90.00", "USD"),
        external_id="CAP-DUP",
    )
    case = OrderCase(
        order_ref="ORD-DUP",
        order=order,
        items=(Item(SELLER_A, Money("100.00", "USD")),),
        rules=(Rule(),),
        captures=(cap_a,),
        ledger_entries=(
            Entry("sale", "credit", Money("100.00", "USD"), order.id, SELLER_A, "L1"),
            Entry("sale", "credit", Money("100.00", "USD"), order.id, SELLER_A, "L1b"),
            Entry(
                "commission", "debit", Money("10.00", "USD"), order.id, SELLER_A, "L2"
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.DUPLICATE_LEDGER_ENTRY]
    assert findings[0].impact == Money("100.00", "USD")


def test_a_duplicated_debit_produces_one_finding_not_three() -> None:
    """The residual credit must carry the ledger direction.

    `position()` makes a credit positive and a debit negative. Crediting the
    unsigned magnitude made a duplicated *debit* offset the residual the wrong
    way, so one fault surfaced as `commission_mismatch`, then
    `duplicate_ledger_entry`, then a spurious `disbursement_mismatch`.
    """
    order = Order(Money("100.00", "USD"))
    capture = Capture(
        SELLER_A,
        Money("100.00", "USD"),
        net_amount=Money("90.00", "USD"),
        external_id="CAP-DD",
    )
    case = OrderCase(
        order_ref="ORD-DEBIT-DUP",
        order=order,
        items=(Item(SELLER_A, Money("100.00", "USD")),),
        rules=(Rule(),),
        captures=(capture,),
        ledger_entries=(
            Entry("sale", "credit", Money("100.00", "USD"), order.id, SELLER_A, "L1"),
            Entry(
                "commission", "debit", Money("10.00", "USD"), order.id, SELLER_A, "L2"
            ),
            Entry(
                "commission", "debit", Money("10.00", "USD"), order.id, SELLER_A, "L2b"
            ),
        ),
    )

    findings = classify_order(case)

    assert codes(findings) == [ExceptionCode.DUPLICATE_LEDGER_ENTRY], (
        f"one fault must give one finding, got {codes(findings)}"
    )
    assert findings[0].impact == Money("10.00", "USD")


def test_a_release_still_in_flight_is_not_a_discrepancy() -> None:
    """T3, through the `disbursement` row rather than the capture.

    `release_requested` and `disbursement_pending` only ever appear on a
    `Disbursement`; the capture's own `disbursement_state` never carries
    either. Consulting the capture alone reported a release in flight as a
    mismatch - the exact trap T3 exists to avoid.
    """
    order = Order(Money("150.00", "EUR"))
    capture = Capture(
        SELLER_B,
        Money("150.00", "EUR"),
        net_amount=Money("135.00", "EUR"),
        disbursement_state="disbursement_completed",
        external_id="CAP-FLIGHT",
    )
    case = OrderCase(
        order_ref="ORD-INFLIGHT",
        order=order,
        items=(Item(SELLER_B, Money("150.00", "EUR")),),
        rules=(Rule(),),
        captures=(capture,),
        disbursements=(
            Release(
                Money("135.00", "EUR"),
                seller_id=SELLER_B,
                state="disbursement_pending",
                external_id="REL-INFLIGHT",
            ),
        ),
        ledger_entries=(
            Entry("sale", "credit", Money("120.00", "EUR"), order.id, SELLER_B, "L1"),
        ),
    )

    assert classify_order(case) == ()


def test_a_mixed_currency_ledger_does_not_abort_the_run() -> None:
    """`explained` is keyed by currency as well as by seller.

    A duplicate in one currency and a residual in another asked `Money` to
    subtract EUR from USD, which raised `ValueError` and killed the whole
    reconciliation run instead of producing a finding.
    """
    order = Order(Money("100.00", "EUR"))
    capture = Capture(
        SELLER_A,
        Money("100.00", "EUR"),
        net_amount=Money("90.00", "EUR"),
        settlement_amount=Money("109.00", "USD"),
        external_id="CAP-MIX",
    )
    case = OrderCase(
        order_ref="ORD-MIX",
        order=order,
        items=(Item(SELLER_A, Money("100.00", "EUR")),),
        rules=(Rule(),),
        captures=(capture,),
        ledger_entries=(
            Entry("sale", "credit", Money("50.00", "USD"), order.id, SELLER_A, "L1"),
            Entry("sale", "credit", Money("50.00", "USD"), order.id, SELLER_A, "L1b"),
        ),
    )

    findings = classify_order(case)

    assert findings, "a mixed-currency ledger must produce something"
    assert all(isinstance(f.impact, Money) for f in findings)


# ---------------------------------------------------------------------------
# D11 - a dispute settled by a refund
#
# `specs/2026-11-09-queue-scheduled-run/requirements.md` D11, and Block C's
# D7 precedent: **encode the rule, seed nothing.** The clean baseline stays at
# 15 purchase units and two non-faults, so the false-positive gate keeps
# measuring the same thing.
#
# Hand-authored, and labelled as such: the sandbox cannot produce this shape.
# Its one dispute is an open inquiry that moves no money, there is no PayPal
# dispute fixture at all, and no refund anywhere settles a dispute. The four
# tests below are the four ways a marketplace can book the loss.
# ---------------------------------------------------------------------------


def _disputed_order(*bookings: Entry, with_provider_refund: bool = True) -> OrderCase:
    """ORD-0011's clean shape, with the refund recast as a dispute settlement.

    The refund is PayPal's: a lost dispute is settled by money going back to
    the buyer, and PayPal reverses the platform fee pro rata on a lost dispute
    exactly as it does on a refund - which is what
    `LEDGER_ENTRY_CATEGORIES`'s own comment records. So the expected
    entitlement absorbs the loss through the refund, and what varies here is
    only how the marketplace wrote it down.
    """
    case = clean_case(
        gross="70.00", commission="7.00", entitlement="63.00", order_ref="ORD-D11"
    )
    refund = Refund(
        Money("20.00", "USD"),
        seller_id=SELLER_B,
        capture_id=case.captures[0].id,
        external_id="RFND-D11-A",
    )

    fees = case.platform_fees
    if with_provider_refund:
        fees = (
            *fees,
            Fee(
                Money("2.00", "USD"),
                seller_id=SELLER_B,
                refund_id=refund.id,
                external_id="FEE-REV-D11",
            ),
        )

    return replace(
        case,
        provider_refunds=(refund,) if with_provider_refund else (),
        platform_fees=fees,
        ledger_entries=(
            *case.ledger_entries,
            Entry(
                "commission_reversal",
                "credit",
                Money("2.00", "USD"),
                case.order.id,
                SELLER_B,
                "LED-D11-rev",
            ),
            *bookings,
        ),
    )


def _booking(category: str, ref: str, amount: str = "20.00") -> Entry:
    return Entry(
        category,
        "debit",
        Money(amount, "USD"),
        None,
        SELLER_B,
        ref,
    )


def _with_order(case: OrderCase, entry: Entry) -> Entry:
    """Ledger entries are grouped by `order_id`, which `_booking` cannot know."""
    entry.order_id = case.order.id
    return entry


def test_a_dispute_loss_booked_as_a_dispute_debit_produces_nothing() -> None:
    """D11's first row, and the false positive the exclusion used to cause.

    Before `dispute` joined `POSITION_CATEGORIES` this debit was invisible to
    the ledger sum while the expected entitlement had already absorbed the
    loss through the refund, so a correct marketplace was reported as not
    having reflected its refund.
    """
    booking = _booking("dispute", "LED-D11-dispute")
    case = _disputed_order()
    booking.order_id = case.order.id
    case = replace(case, ledger_entries=(*case.ledger_entries, booking))

    assert classify_order(case) == ()


def test_a_dispute_loss_booked_as_a_refund_debit_produces_nothing() -> None:
    """D11's second row: unchanged behaviour, asserted so it stays unchanged.

    Widening the categories must not disturb the marketplace that books the
    ordinary way.
    """
    booking = _booking("refund", "LED-D11-refund")
    case = _disputed_order()
    booking.order_id = case.order.id
    case = replace(case, ledger_entries=(*case.ledger_entries, booking))

    assert classify_order(case) == ()


def test_booking_the_loss_twice_counts_it_once() -> None:
    """D11's third row, and the hazard a naive inclusion creates.

    A marketplace that records the same loss under both categories has not
    lost twice as much money. Summing both halves debits the seller 40.00
    against a 20.00 refund and reports a 20.00 shortfall that does not exist -
    a false positive invented by the fix for a false positive.
    """
    case = _disputed_order()
    refund_entry = _booking("refund", "LED-D11-refund")
    dispute_entry = _booking("dispute", "LED-D11-dispute")
    refund_entry.order_id = case.order.id
    dispute_entry.order_id = case.order.id

    case = replace(
        case, ledger_entries=(*case.ledger_entries, refund_entry, dispute_entry)
    )

    assert classify_order(case) == ()


def test_a_dispute_debit_with_no_provider_refund_is_reported() -> None:
    """D11's fourth row. The hold-out must not become a blanket exemption.

    A dispute debit with no PayPal refund behind it is an unevidenced
    movement: the seller's position moved and nothing from the provider
    accounts for it. That is exactly what a reconciliation should surface, so
    this one is a finding rather than a held-out row.
    """
    case = _disputed_order(with_provider_refund=False)
    booking = _booking("dispute", "LED-D11-dispute")
    booking.order_id = case.order.id
    case = replace(
        case,
        ledger_entries=(
            *[
                entry
                for entry in case.ledger_entries
                if entry.entry_category != "commission_reversal"
            ],
            booking,
        ),
    )

    findings = classify_order(case)

    assert findings, (
        "a 20.00 debit with no provider record behind it must not be silently "
        "absorbed by the dispute hold-out"
    )


def test_the_hold_out_is_matched_on_amount() -> None:
    """A dispute debit for a different amount is not the refund restated.

    Without the amount check the rule would swallow any dispute debit on an
    order that happened to have a refund, which would hide a real loss.
    """
    case = _disputed_order()
    refund_entry = _booking("refund", "LED-D11-refund")
    other = _booking("dispute", "LED-D11-dispute", amount="35.00")
    refund_entry.order_id = case.order.id
    other.order_id = case.order.id

    case = replace(
        case, ledger_entries=(*case.ledger_entries, refund_entry, other)
    )

    assert classify_order(case), (
        "35.00 is not the 20.00 refund written twice, so it must still be "
        "accounted for"
    )
