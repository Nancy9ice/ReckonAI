"""The manifest's figures, checked against the seed that produces them.

Two of the manifest's six impact figures were **stale** when Block C started,
and nothing caught it: F3 claimed 5.05 EUR against a 276.00 USD share at a
rate of 0.9183, and F6 claimed an 18.00 EUR shortfall against an obligation of
138.00. Neither describes any record in the dataset. They were Block A's
synthetic provider figures, and R16's capture re-pointed the seed at real ones
without these rows following.

The manifest is ground truth for the eval, so a stale figure there is worse
than a bug in the classifier: the gate would have failed against a correct
implementation, and the natural reaction would have been to change the
implementation to match.

This module is the guard that was missing. It derives each fault's impact from
`reckonai.sim.data` - the seed the rows actually come from - and asserts the
manifest agrees. The derivations are written out per fault rather than
computed by a shared helper, because a helper would be a second
implementation of the engine and the two could agree while both being wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from reckonai.sim import data, manifest

#: The tenant's one commission rule. Asserted below rather than imported as a
#: constant, so a rule change breaks this file loudly.
RATE = Decimal("0.10")


def spec(ref: str) -> data.OrderSpec:
    for order in data.FAULTY_ORDERS:
        if order.ref == ref:
            return order
    raise KeyError(ref)


def test_there_is_one_commission_rule_at_ten_percent() -> None:
    """Every derivation below rests on this.

    Block A's handoff listed more rules and more sellers; both are out of
    date, and the derivations here would be wrong if the rule changed.
    """
    for order in data.FAULTY_ORDERS:
        rule_correct = (Decimal(order.total) * RATE).quantize(Decimal("0.01"))

        if order.ref == "ORD-0014":
            # F2's `expected_commission` is deliberately the **wrong** figure
            # the marketplace believes, which is the fault itself. Asserting it
            # equals the rule would assert the fault away.
            assert Decimal(order.expected_commission) != rule_correct
            continue

        assert Decimal(order.expected_commission) == rule_correct, (
            f"{order.ref} commission is not the tenant's 10% default"
        )


def test_every_fault_in_the_manifest_has_a_seeded_order() -> None:
    """A fault describing an order that is not seeded can never be detected."""
    for fault in manifest.FAULTS:
        assert fault.order_ref in data.FAULTY_ORDER_REFS, (
            f"{fault.id} names {fault.order_ref}, which the seed does not build"
        )


def test_every_seeded_fault_has_a_manifest_entry() -> None:
    """And the reverse: a seeded fault with no manifest row is an exception
    the eval would count as a false positive."""
    described = {fault.order_ref for fault in manifest.FAULTS}

    assert described == set(data.FAULTY_ORDER_REFS)


def test_f1s_impact_is_the_refund_less_the_reversal() -> None:
    """ORD-0013: a 30.00 refund with PayPal's automatic 3.00 fee reversal.

    The seller's position is overstated by what the ledger failed to take off,
    which is the refund less the commission PayPal gave back.
    """
    order = spec("ORD-0013")
    refund = order.refunds[0]
    impact = Decimal(refund.amount) - Decimal(refund.expected_commission_reversal)

    assert impact == Decimal("27.00")
    assert Decimal(manifest.fault("F1").impact_amount) == impact
    assert manifest.fault("F1").impact_currency == order.currency


def test_f2s_impact_is_the_commission_overstatement() -> None:
    """ORD-0014: 30.00 retained where the 10% rule gives 20.00."""
    order = spec("ORD-0014")
    rule_correct = (Decimal(order.total) * RATE).quantize(Decimal("0.01"))
    impact = Decimal(order.expected_commission) - rule_correct

    assert impact == Decimal("10.00")
    assert Decimal(manifest.fault("F2").impact_amount) == impact
    assert manifest.fault("F2").impact_currency == order.currency


def test_f3s_impact_is_the_gap_against_the_reported_settlement() -> None:
    """ORD-0015, and the figure that was stale.

    The marketplace records 262.00 USD. PayPal's reported receivable is
    294.16 USD - the figure in `fixtures/paypal/ORD-0015.json`, compared
    against directly and never recomputed from 270.00 EUR times the rate.
    """
    order = spec("ORD-0015")
    currency, recorded = order.recorded_in_currency[data.B]
    reported = Decimal("294.16")
    impact = reported - Decimal(recorded)

    assert impact == Decimal("32.16")
    assert currency == "USD"
    assert Decimal(manifest.fault("F3").impact_amount) == impact
    assert manifest.fault("F3").impact_currency == currency


def test_f3s_reported_settlement_is_what_paypal_actually_returned() -> None:
    """The one figure above that is not in `data.py`, pinned to the fixture.

    Without this the corrected manifest would rest on a number typed into a
    test, which is how the stale one got there.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    document = json.loads(
        (root / "fixtures" / "paypal" / "ORD-0015.json").read_text(encoding="utf-8")
    )
    breakdown = document["order"]["purchase_units"][0]["payments"]["captures"][0][
        "seller_receivable_breakdown"
    ]

    assert breakdown["receivable_amount"] == {
        "currency_code": "USD",
        "value": "294.16",
    }
    assert breakdown["net_amount"] == {"currency_code": "EUR", "value": "270.00"}


def test_f4s_impact_is_the_repeated_credit() -> None:
    """ORD-0016: one 90.00 sale credited twice, so 90.00 of overstatement."""
    order = spec("ORD-0016")

    assert order.duplicate_ledger_entry is True
    assert Decimal(manifest.fault("F4").impact_amount) == Decimal(order.total)
    assert manifest.fault("F4").impact_currency == order.currency


def test_f5_has_no_impact_because_nothing_is_wrong() -> None:
    """ORD-0017. Zero is the measured answer, not a default: the
    marketplace's arithmetic holds completely and only PayPal's record is
    late."""
    order = spec("ORD-0017")
    refund = order.refunds[0]

    assert refund.reflected_in_ledger is True
    assert refund.paypal_record_present is False
    assert Decimal(manifest.fault("F5").impact_amount) == Decimal("0.00")
    assert manifest.fault("F5").is_exception is False


def test_f6s_impact_is_the_understated_credit() -> None:
    """ORD-0018, the other stale figure.

    Entitlement 135.00 EUR against a ledger credit of 120.00, so 15.00 - not
    the 18.00 the manifest claimed, which implied a 12.00 commission on a
    150.00 order and therefore a rule this tenant does not have.
    """
    order = spec("ORD-0018")
    entitlement = Decimal(order.expected_entitlements[data.B])
    credited = Decimal(order.understated_credit[data.B])
    impact = entitlement - credited

    assert entitlement == Decimal("135.00")
    assert impact == Decimal("15.00")
    assert Decimal(manifest.fault("F6").impact_amount) == impact
    assert manifest.fault("F6").impact_currency == order.currency


@pytest.mark.parametrize("fault", manifest.FAULTS, ids=lambda f: f.id)
def test_every_impact_is_a_quantized_string(fault: manifest.Fault) -> None:
    """Amounts stay strings with two places. A manifest carrying `18` or
    `18.0` would compare unequal to a `Money` the classifier produced."""
    assert Decimal(fault.impact_amount) == Decimal(fault.impact_amount).quantize(
        Decimal("0.01")
    )
    assert len(fault.impact_amount.split(".")[-1]) == 2
    assert len(fault.impact_currency) == 3
