"""The four comparison rules, and every tolerance decision in the product.

specs/roadmap.md Phase 11 is explicit about why this module exists as its own
file: "record the rounding tolerance as an explicit decision rather than a
constant found while chasing noise". A tolerance invented at the point of use,
inside a classifier branch, is indistinguishable from one widened until the
tests passed. Keeping all four in one module with their reasons means widening
one is a visible edit to a documented decision.

`requirements.md` D4 is the table these implement:

  - T1 `compare_exact` - zero tolerance on same-currency comparisons
  - T2 `compare_converted_total` - one minor unit per conversion leg, and
    only for the sum-versus-total shape
  - T3 `is_healthy_disbursement` - held funds are never an exception
  - T4 `require_stated` - an absent amount is never compared as zero

**T1 is the default and T2 is the exception.** That direction matters: a
module whose general comparison carried slack would apply it everywhere,
including to the same-currency figures that PayPal reports exactly, and the
faults this product exists to find are as small as 10.00 on a 200.00 order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from reckonai.money import Money

#: Disbursement states in which the seller does not yet have the money and
#: nothing is wrong. specs/roadmap.md Phase 11's second named false-positive
#: trap, and `reckonai.models.lifecycle` says the same thing at
#: `DISBURSEMENT_STATES`: states 2 through 5 of
#: specs/multiparty-direction.md section 4 are non-error states.
#:
#: `disbursement_failed` is deliberately **absent**: a failed release needs a
#: cause, which is why Phase 3 split it from `disbursement_pending`.
HEALTHY_HELD_STATES: frozenset[str] = frozenset(
    {
        "funds_held",
        "release_requested",
        "disbursement_pending",
    }
)

#: Days after which PayPal releases held funds without being asked. Observed in
#: Phase 0 and recorded in specs/roadmap.md Phase 11: "the absence of a
#: marketplace instruction does not mean the funds are stuck". Carried as a
#: named constant because it is the reason `release_requested` is not a
#: required predecessor of completion, and a future ageing rule must not treat
#: day 29 of a held transaction as a discrepancy either.
PAYPAL_AUTO_RELEASE_DAYS = 28


class AmountNotStated(Exception):
    """A comparison was attempted against an amount no source stated.

    Not a finding. The caller asked the wrong question, and the right answer is
    to record the gap as a limitation - specs/mission.md P3, and the reason
    `capture.paypal_fee` and `fx_conversion.fee` are nullable with a `CHECK`
    refusing zero.
    """


@dataclass(frozen=True, slots=True)
class Comparison:
    """The outcome of comparing an expected amount against a reported one.

    Carries the figures as well as the verdict, because a finding has to cite
    what it compared. Reporting "commission mismatch" without both sides makes
    an exception unauditable, and specs/roadmap.md Phase 11 requires every
    exception row to carry its financial impact.
    """

    expected: Money
    reported: Money

    #: `reported - expected`, signed. Zero when they agree exactly.
    difference: Money

    #: The slack the comparison allowed, in the compared currency. Zero for
    #: T1, which is almost every comparison.
    allowed: Money

    @property
    def agrees(self) -> bool:
        """Whether the difference is within the allowance."""
        return abs(self.difference) <= self.allowed

    @property
    def impact(self) -> Money:
        """The financial impact of the disagreement, as a magnitude.

        Zero when the comparison agrees, so a finding's impact and its verdict
        cannot contradict each other.
        """
        if self.agrees:
            return Money.zero(self.difference.currency)
        return abs(self.difference)


def compare_exact(expected: Money, reported: Money) -> Comparison:
    """T1. Same-currency comparison with zero tolerance.

    Two measurements justify zero rather than caution, both from Block B and
    neither to be re-derived:

      - `net_amount == gross_amount - platform_fees` **exactly** on all 21
        captured captures, zero residual
      - the expected commission equals `platform_fee.amount` on all 15 clean
        purchase units

    So any difference here is real. A tolerance would only hide one: F2's
    whole fault is 10.00 of commission, and a "safe" one-cent allowance is the
    first step toward an allowance that swallows it.

    Raises:
        ValueError: on a currency mismatch, from `Money` itself. Comparing
            across currencies is specs/mission.md P2's exact hazard, and
            `Money` refusing it is the guard.
    """
    difference = reported - expected
    return Comparison(
        expected=expected,
        reported=reported,
        difference=difference,
        allowed=Money.zero(expected.currency),
    )


def compare_converted_total(
    expected: Money, reported: Money, *, legs: int
) -> Comparison:
    """T2. A sum of converted parts against a single converted total.

    **This is the only tolerance in the product, and its shape is narrow.** It
    applies when several amounts were each converted and rounded
    independently, and their sum is compared against one figure. It does not
    apply to a single reported settlement, which is compared exactly against
    the figure PayPal reported - `compare_exact` - because there is nothing for
    the residue to accumulate across.

    ORD-0008 is why it exists, and the arithmetic is PayPal's own:

        per capture : 36.02 EUR -> 39.24 USD
                      73.37 EUR -> 79.93 USD   = 119.17 USD
        the 109.39 total, converted once       = 119.18 USD

    PayPal rounds each converted settlement to the cent and leaves the rate
    unrounded, so each leg carries up to half a minor unit of rounding and
    `n` legs can accumulate against the single rounding of the total. One
    minor unit per leg is the smallest bound expressible in minor units that
    covers that, and it stays two orders of magnitude clear of F3's 5.05 EUR -
    the manifest says so explicitly, "so no threshold can confuse the two".

    **Never recompute the amount this is compared against.** On our own Phase 0
    fixture, `95.50 * 1.089477173902071` is `104.04507010764778050` against a
    reported `104.05`. The reported figure is the evidence; the rate is for
    explaining it, not for re-deriving it.

    Raises:
        ValueError: for `legs` below 1. Zero legs is not a lenient comparison,
            it is a caller that does not know what it is comparing.
    """
    if legs < 1:
        raise ValueError(
            f"a converted comparison needs at least one leg, got {legs}. "
            "Use compare_exact for a single reported settlement."
        )

    difference = reported - expected
    return Comparison(
        expected=expected,
        reported=reported,
        difference=difference,
        allowed=Money.from_minor_units(legs, expected.currency),
    )


def is_healthy_disbursement(state: str) -> bool:
    """T3. Whether a disbursement state is a healthy intermediate condition.

    specs/roadmap.md Phase 11's second false-positive trap, and the one that
    would be most expensive to get wrong: "Classifying them as discrepancies
    makes every delayed transaction an exception." Under `DELAYED`, funds held
    with no release requested is what a healthy transaction looks like before
    the money moves.

    `release_requested` is included and is **not** a required predecessor of
    completion - PayPal auto-releases after `PAYPAL_AUTO_RELEASE_DAYS`, so an
    order sitting in `funds_held` with no marketplace instruction behind it is
    not stuck.

    An unknown state returns `False`: a state outside the vocabulary is
    something to look at, not something to assume is fine.
    """
    return state in HEALTHY_HELD_STATES


def require_stated(amount: Money | None, *, field: str, record: str) -> Money:
    """T4. An absent amount is never compared as zero.

    The project's sharpest rule, from specs/mission.md P3 and restated in the
    schema: `fx_conversion.fee` is NULL and never zero, with a `CHECK`
    refusing the zero, because **an absent fee and a measured fee of nothing
    are different claims**.

    It bites here specifically. Across all 21 captures, **no capture states a
    `paypal_fee` at all** - the key is simply absent - while a refund states
    an explicit `0.00`. A comparison that read the absence as zero would
    produce a confident figure from no evidence, and it would look right.

    Raises:
        AmountNotStated: always, when `amount` is None. The caller records a
            limitation or classifies the gap; it does not substitute a number.
    """
    if amount is None:
        raise AmountNotStated(
            f"{record} states no {field}. An absent amount and a measured zero "
            "are different claims (mission.md P3); record the gap as a "
            "limitation rather than comparing against zero."
        )
    return amount


def within_rounding_of_rate(
    source: Money, target: Money, rate: Decimal, *, legs: int = 1
) -> bool:
    """Whether a reported settlement is consistent with its reported rate.

    **Explanatory only, and deliberately not a comparison that can produce a
    finding.** `compare_exact` against PayPal's reported `target` is what
    decides whether a settlement is right; this answers the different question
    of whether the reported rate explains the reported amount, which is what
    an investigation needs in order to say *why*.

    Kept separate because the two must not be confused: specs/roadmap.md
    Phase 11 warns that a reported rate may not be the capture-time rate at
    all - `payment_instruction.payee_receivable_fx_rate_id` lets a caller pin
    an earlier quote - so a disagreement here is a reason to look, never by
    itself a discrepancy. The rate is used at full precision and the result is
    never stored as a money figure.
    """
    if source.currency == target.currency:
        return False

    # Compared at full precision rather than quantized first. Quantizing would
    # impose a rounding mode on PayPal's arithmetic in order to check PayPal's
    # arithmetic, and the residue being measured is smaller than the
    # quantization itself: on the Phase 0 fixture the implied figure is
    # 104.04507010764778050 against a reported 104.05.
    implied = source.amount * rate
    slack = Decimal(legs) / Decimal(100)

    return abs(implied - target.amount) <= slack
