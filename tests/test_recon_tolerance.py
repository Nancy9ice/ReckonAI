"""The four tolerance rules, each passing at its bound and failing outside it.

`validation.md` section 4: "A rule with no failing case is a rule that was
never exercised." The failing direction is the one that matters here - a
tolerance is only a decision if a figure outside it is still reported.

Every figure below is PayPal's own, from
`specs/2026-11-09-paypal-capture/validation.md` section 2 or from Phase 0's
fixtures, rather than invented for the test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from reckonai.models.lifecycle import DISBURSEMENT_STATES
from reckonai.money import Money
from reckonai.recon.tolerance import (
    HEALTHY_HELD_STATES,
    PAYPAL_AUTO_RELEASE_DAYS,
    AmountNotStated,
    compare_converted_total,
    compare_exact,
    is_healthy_disbursement,
    require_stated,
    within_rounding_of_rate,
)

# ---------------------------------------------------------------------------
# T1 - zero tolerance, same currency
# ---------------------------------------------------------------------------


def test_exact_agreement_agrees_with_no_impact() -> None:
    """ORD-0009: 59.97 gross, 6.00 commission, 53.97 net."""
    result = compare_exact(Money("53.97", "USD"), Money("53.97", "USD"))

    assert result.agrees
    assert result.difference == Money("0.00", "USD")
    assert result.impact == Money("0.00", "USD")


def test_one_cent_is_a_finding() -> None:
    """T1's whole point, stated as a failing case.

    Block B measured zero residual on all 21 captures, so a cent here is real.
    An allowance "for safety" is the first step toward one that swallows F2.
    """
    result = compare_exact(Money("53.97", "USD"), Money("53.98", "USD"))

    assert not result.agrees
    assert result.difference == Money("0.01", "USD")
    assert result.impact == Money("0.01", "USD")


def test_f2s_commission_gap_is_reported() -> None:
    """ORD-0014: the marketplace retained 30.00 where the rule gives 20.00."""
    result = compare_exact(Money("20.00", "USD"), Money("30.00", "USD"))

    assert not result.agrees
    assert result.impact == Money("10.00", "USD")


def test_the_difference_keeps_its_sign_while_impact_does_not() -> None:
    """Both are needed: the sign says who is short, the magnitude is the
    impact every exception row must carry."""
    short = compare_exact(Money("100.00", "USD"), Money("90.00", "USD"))

    assert short.difference == Money("-10.00", "USD")
    assert short.impact == Money("10.00", "USD")


def test_comparing_across_currencies_is_refused() -> None:
    """specs/mission.md P2, enforced by `Money` rather than re-implemented."""
    with pytest.raises(ValueError):
        compare_exact(Money("100.00", "USD"), Money("100.00", "EUR"))


# ---------------------------------------------------------------------------
# T2 - one minor unit per conversion leg
# ---------------------------------------------------------------------------


def test_ord_0008s_rounding_artefact_is_not_a_finding() -> None:
    """The non-fault the clean baseline exists to prove is not flagged.

    per capture : 36.02 EUR -> 39.24 USD
                  73.37 EUR -> 79.93 USD   = 119.17 USD
    the 109.39 total, converted once       = 119.18 USD
    """
    result = compare_converted_total(
        Money("119.18", "USD"), Money("119.17", "USD"), legs=2
    )

    assert result.agrees
    assert result.impact == Money("0.00", "USD")


def test_two_cents_over_two_legs_is_at_the_bound() -> None:
    """One minor unit per leg, so two legs allow two cents - and no more."""
    at_bound = compare_converted_total(
        Money("119.19", "USD"), Money("119.17", "USD"), legs=2
    )
    beyond = compare_converted_total(
        Money("119.20", "USD"), Money("119.17", "USD"), legs=2
    )

    assert at_bound.agrees
    assert not beyond.agrees
    assert beyond.impact == Money("0.03", "USD")


def test_f3s_discrepancy_is_two_orders_of_magnitude_clear() -> None:
    """ORD-0015: 5.05 EUR against a one-cent artefact.

    The manifest states this gap exists "so no threshold can confuse the two".
    It would survive a tolerance five hundred times wider than the one T2
    grants, which is what makes the rule safe rather than lucky.
    """
    result = compare_converted_total(
        Money("253.45", "EUR"), Money("248.40", "EUR"), legs=1
    )

    assert not result.agrees
    assert result.impact == Money("5.05", "EUR")


def test_a_single_leg_allows_one_cent_only() -> None:
    assert compare_converted_total(
        Money("104.05", "USD"), Money("104.04", "USD"), legs=1
    ).agrees
    assert not compare_converted_total(
        Money("104.05", "USD"), Money("104.03", "USD"), legs=1
    ).agrees


def test_zero_legs_is_a_caller_error_not_a_lenient_comparison() -> None:
    with pytest.raises(ValueError, match="at least one leg"):
        compare_converted_total(Money("1.00", "USD"), Money("1.00", "USD"), legs=0)


def test_the_exact_comparison_grants_nothing_by_contrast() -> None:
    """T1 is the default and T2 the exception, asserted rather than assumed.

    If `compare_exact` ever grew an allowance, every same-currency figure in
    the product would quietly acquire slack.
    """
    assert compare_exact(
        Money("119.18", "USD"), Money("119.17", "USD")
    ).allowed == Money("0.00", "USD")


# ---------------------------------------------------------------------------
# T3 - held funds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["funds_held", "release_requested", "disbursement_pending"]
)
def test_held_states_are_healthy(state: str) -> None:
    """ORD-0005's shape. Flagging these makes every DELAYED order an
    exception, which Phase 11's hardest criterion forbids by design."""
    assert is_healthy_disbursement(state)


@pytest.mark.parametrize(
    "state", ["disbursement_failed", "disbursed_at_capture", "disbursement_completed"]
)
def test_terminal_and_failed_states_are_not_held(state: str) -> None:
    """`disbursement_failed` is the one Phase 3 split out deliberately: a
    failed release needs a cause, so it must not be absorbed as healthy."""
    assert not is_healthy_disbursement(state)


def test_an_unknown_state_is_not_assumed_healthy() -> None:
    """A state outside the vocabulary is something to look at."""
    assert not is_healthy_disbursement("something_new")
    assert not is_healthy_disbursement("")


def test_every_healthy_state_is_a_real_disbursement_state() -> None:
    """The constant cannot drift from `models.lifecycle`.

    A typo here would silently stop treating a held state as healthy, and the
    only symptom would be every delayed order appearing as an exception.
    """
    assert HEALTHY_HELD_STATES < set(DISBURSEMENT_STATES)


def test_the_auto_release_window_is_recorded() -> None:
    """Phase 0's observation, kept as a named constant.

    It is the reason an absent release instruction is not evidence that funds
    are stuck, so a future ageing rule must not treat day 29 as a finding.
    """
    assert PAYPAL_AUTO_RELEASE_DAYS == 28


# ---------------------------------------------------------------------------
# T4 - absent is not zero
# ---------------------------------------------------------------------------


def test_an_absent_amount_raises_rather_than_comparing_as_zero() -> None:
    """No capture states a `paypal_fee` at all. Reading that absence as zero
    would produce a confident figure from no evidence."""
    with pytest.raises(AmountNotStated, match="paypal_fee"):
        require_stated(None, field="paypal_fee", record="CAP-ORD-0009")


def test_the_refusal_names_the_record_and_the_principle() -> None:
    """An operator reading this has to know which record and why."""
    with pytest.raises(AmountNotStated) as caught:
        require_stated(None, field="fee", record="FX-ORD-0015")

    message = str(caught.value)
    assert "FX-ORD-0015" in message
    assert "different claims" in message


def test_a_measured_zero_passes_through() -> None:
    """A refund states an explicit 0.00, and that is a real measurement.

    The two must stay different claims in both directions: refusing a stated
    zero would be as wrong as inventing one.
    """
    assert require_stated(
        Money("0.00", "USD"), field="paypal_fee", record="RFND-0011-A"
    ) == Money("0.00", "USD")


def test_a_stated_amount_passes_through_unchanged() -> None:
    assert require_stated(
        Money("6.00", "USD"), field="platform_fee", record="FEE-ORD-0009"
    ) == Money("6.00", "USD")


# ---------------------------------------------------------------------------
# The explanatory rate check, which must not become a comparison
# ---------------------------------------------------------------------------


def test_the_rate_check_explains_phase_0s_fixture() -> None:
    """95.50 * 1.089477173902071 = 104.04507010764778050 against 104.05.

    The residue is -0.00492989235221950, which is why the roadmap says to
    compare against the reported settlement and never against this product.
    """
    assert within_rounding_of_rate(
        Money("95.50", "EUR"),
        Money("104.05", "USD"),
        Decimal("1.089477173902071"),
    )


def test_the_rate_check_rejects_a_stale_rate() -> None:
    """F3: a rate near 0.90 reported as 0.9183 on a 276.00 share."""
    assert not within_rounding_of_rate(
        Money("276.00", "USD"), Money("248.40", "EUR"), Decimal("0.9183")
    )


def test_the_rate_check_refuses_a_same_currency_pair() -> None:
    """There is no conversion to explain, so there is no answer to give."""
    assert not within_rounding_of_rate(
        Money("100.00", "USD"), Money("100.00", "USD"), Decimal("1")
    )


def test_the_rate_check_uses_full_precision() -> None:
    """A 15-decimal rate, which is what PayPal returns.

    Truncating it would make the check disagree with the figures it is meant
    to explain, and the rate is stored whole precisely so it does not have to
    be.
    """
    rate = Decimal("1.089477173902071")

    assert rate == Decimal(str(rate))
    assert within_rounding_of_rate(Money("95.50", "EUR"), Money("104.05", "USD"), rate)
