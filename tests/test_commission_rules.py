"""Commission rule resolution.

No database: the resolver is a pure function and the fixtures are dataclasses
that satisfy `CommissionRuleLike` structurally. That is the point of the
protocol - the engine never imports the ORM, so these tests exercise exactly
the code a Phase 10 calculation will.

The two tests that matter most are the ones asserting a *raise*:
`test_equal_specificity_is_ambiguous_and_raises` and
`test_no_applicable_rule_raises`. A resolver that silently picks the newest rule
makes a transaction's commission depend on insertion order, and one that
returns a zero rate for a missing rule defaults money - which
specs/tech-stack.md section 9.6 forbids.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from reckonai.engine import (
    CommissionRuleAmbiguity,
    CommissionRuleLike,
    EngineError,
    NoApplicableCommissionRule,
    resolve_commission_rule,
)

SELLER = uuid.uuid4()
OTHER_SELLER = uuid.uuid4()

JANUARY = datetime(2026, 1, 1, tzinfo=UTC)
JUNE = datetime(2026, 6, 1, tzinfo=UTC)
DECEMBER = datetime(2026, 12, 1, tzinfo=UTC)


@dataclass
class Rule:
    """A stand-in for `reckonai.models.CommissionRule`.

    It satisfies `CommissionRuleLike` by having the right attributes, with no
    inheritance and no registration. If this stops working, the protocol and
    the ORM class have diverged and Phase 10 would find out the hard way.
    """

    rate: Decimal
    valid_from: datetime = JANUARY
    valid_to: datetime | None = None
    seller_id: uuid.UUID | None = None
    category: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_the_test_fixture_satisfies_the_protocol() -> None:
    """Asserted rather than assumed: a `Protocol` mismatch would otherwise show
    up as a confusing `AttributeError` inside the resolver."""
    assert isinstance(Rule(rate=Decimal("0.1")), CommissionRuleLike)


def test_the_orm_model_satisfies_the_protocol() -> None:
    """The other half of the same guarantee, and the one that actually matters.

    Imported inside the test so this module does not pull in SQLAlchemy at
    collection time - the rest of the file is pure and should stay runnable
    without it.

    `isinstance`, not `issubclass`: a `Protocol` with non-method members is a
    *data* protocol, and `issubclass` against one raises
    `TypeError: Protocols with non-method members don't support issubclass()`.
    An unflushed instance is enough, since the check is for attribute presence
    and SQLAlchemy instruments the attributes on the class.
    """
    from reckonai.models import CommissionRule

    assert isinstance(CommissionRule(), CommissionRuleLike)


# ---------------------------------------------------------------------------
# Specificity: each level wins in turn
# ---------------------------------------------------------------------------


def test_the_tenant_default_applies_when_nothing_else_does() -> None:
    default = Rule(rate=Decimal("0.100000"))

    chosen = resolve_commission_rule(
        [default], seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert chosen is default


def test_a_category_rule_beats_the_tenant_default() -> None:
    default = Rule(rate=Decimal("0.100000"))
    by_category = Rule(rate=Decimal("0.080000"), category="ceramics")

    chosen = resolve_commission_rule(
        [default, by_category], seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert chosen is by_category


def test_a_seller_rule_beats_a_category_rule() -> None:
    """A negotiated per-seller rate outranks a catalogue-wide one. This is the
    ordering decision in requirements.md D12, and reversing it would change
    every commission for a seller who has both."""
    by_category = Rule(rate=Decimal("0.080000"), category="ceramics")
    by_seller = Rule(rate=Decimal("0.050000"), seller_id=SELLER)

    chosen = resolve_commission_rule(
        [by_category, by_seller], seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert chosen is by_seller


def test_a_seller_and_category_rule_beats_everything() -> None:
    candidates = [
        Rule(rate=Decimal("0.100000")),
        Rule(rate=Decimal("0.080000"), category="ceramics"),
        Rule(rate=Decimal("0.050000"), seller_id=SELLER),
        Rule(rate=Decimal("0.030000"), seller_id=SELLER, category="ceramics"),
    ]

    chosen = resolve_commission_rule(
        candidates, seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert chosen.rate == Decimal("0.030000")


def test_order_of_the_candidate_list_does_not_matter() -> None:
    """Resolution is by specificity, not by position. A resolver that returned
    the first match would pass every test above if the lists happened to be
    ordered helpfully."""
    candidates = [
        Rule(rate=Decimal("0.030000"), seller_id=SELLER, category="ceramics"),
        Rule(rate=Decimal("0.100000")),
    ]

    forwards = resolve_commission_rule(
        candidates, seller_id=SELLER, category="ceramics", at=JUNE
    )
    backwards = resolve_commission_rule(
        list(reversed(candidates)), seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert forwards.rate == backwards.rate == Decimal("0.030000")


def test_another_sellers_rule_is_not_a_candidate() -> None:
    default = Rule(rate=Decimal("0.100000"))
    someone_else = Rule(rate=Decimal("0.010000"), seller_id=OTHER_SELLER)

    chosen = resolve_commission_rule(
        [default, someone_else], seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert chosen is default


def test_another_categorys_rule_is_not_a_candidate() -> None:
    default = Rule(rate=Decimal("0.100000"))
    other_category = Rule(rate=Decimal("0.010000"), category="textiles")

    chosen = resolve_commission_rule(
        [default, other_category], seller_id=SELLER, category="ceramics", at=JUNE
    )

    assert chosen is default


def test_an_uncategorised_item_still_matches_an_all_category_rule() -> None:
    """Not every marketplace categorises its catalogue, so `category=None` on
    the *item* must still resolve - against rules that apply to all
    categories."""
    default = Rule(rate=Decimal("0.100000"))
    by_category = Rule(rate=Decimal("0.080000"), category="ceramics")

    chosen = resolve_commission_rule(
        [default, by_category], seller_id=SELLER, category=None, at=JUNE
    )

    assert chosen is default


# ---------------------------------------------------------------------------
# Effective dating, and the half-open interval
# ---------------------------------------------------------------------------


def test_a_rule_outside_its_validity_period_is_ignored() -> None:
    expired = Rule(rate=Decimal("0.200000"), valid_from=JANUARY, valid_to=JUNE)
    current = Rule(rate=Decimal("0.100000"), valid_from=JUNE)

    chosen = resolve_commission_rule(
        [expired, current], seller_id=SELLER, category=None, at=DECEMBER
    )

    assert chosen is current


def test_valid_from_is_inclusive() -> None:
    rule = Rule(rate=Decimal("0.100000"), valid_from=JUNE)

    chosen = resolve_commission_rule(
        [rule], seller_id=SELLER, category=None, at=JUNE
    )

    assert chosen is rule


def test_valid_to_is_exclusive() -> None:
    """The half-open interval, from the other end.

    Together with the test above, this is what lets two consecutive rules abut
    on the same instant without overlapping. Both ends inclusive would make
    every handover date ambiguous.
    """
    rule = Rule(rate=Decimal("0.100000"), valid_from=JANUARY, valid_to=JUNE)

    with pytest.raises(NoApplicableCommissionRule):
        resolve_commission_rule([rule], seller_id=SELLER, category=None, at=JUNE)


def test_consecutive_rules_abutting_on_one_instant_are_not_ambiguous() -> None:
    """The practical payoff of the half-open interval: a rate change on 1 June
    is expressed as two rules meeting at that instant, and resolving on that
    exact instant picks the new one without ambiguity."""
    old = Rule(rate=Decimal("0.200000"), valid_from=JANUARY, valid_to=JUNE)
    new = Rule(rate=Decimal("0.100000"), valid_from=JUNE, valid_to=DECEMBER)

    before = resolve_commission_rule(
        [old, new], seller_id=SELLER, category=None, at=JUNE - timedelta(seconds=1)
    )
    on_the_day = resolve_commission_rule(
        [old, new], seller_id=SELLER, category=None, at=JUNE
    )

    assert before.rate == Decimal("0.200000")
    assert on_the_day.rate == Decimal("0.100000")


def test_an_open_ended_rule_applies_indefinitely() -> None:
    rule = Rule(rate=Decimal("0.100000"), valid_from=JANUARY, valid_to=None)

    chosen = resolve_commission_rule(
        [rule],
        seller_id=SELLER,
        category=None,
        at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert chosen is rule


def test_a_historical_timestamp_resolves_the_historical_rule() -> None:
    """Why effective dating exists at all (requirements.md D2, mission P7).

    Re-running a reconciliation for January must use January's rate, even
    though the rate has since changed. Without dating, correcting a rate today
    would silently rewrite what January's run meant.
    """
    january_rate = Rule(
        rate=Decimal("0.200000"), valid_from=JANUARY, valid_to=JUNE
    )
    current_rate = Rule(rate=Decimal("0.100000"), valid_from=JUNE)
    rules = [january_rate, current_rate]

    assert resolve_commission_rule(
        rules, seller_id=SELLER, category=None, at=JANUARY
    ).rate == Decimal("0.200000")
    assert resolve_commission_rule(
        rules, seller_id=SELLER, category=None, at=DECEMBER
    ).rate == Decimal("0.100000")


def test_a_more_specific_rule_outside_its_period_does_not_shadow_the_default() -> None:
    """Specificity is only considered among rules that actually apply. A
    seller rule that expired must not prevent the default from being chosen."""
    expired_seller_rule = Rule(
        rate=Decimal("0.050000"),
        seller_id=SELLER,
        valid_from=JANUARY,
        valid_to=JUNE,
    )
    default = Rule(rate=Decimal("0.100000"))

    chosen = resolve_commission_rule(
        [expired_seller_rule, default],
        seller_id=SELLER,
        category=None,
        at=DECEMBER,
    )

    assert chosen is default


# ---------------------------------------------------------------------------
# The two failure modes, which are the point of the module
# ---------------------------------------------------------------------------


def test_equal_specificity_is_ambiguous_and_raises() -> None:
    """requirements.md D12.

    Silently taking the newest or the first would make the commission for a
    transaction depend on insertion order. specs/mission.md P5 is explicit that
    "insufficient evidence" is a correct answer where a guess is not.
    """
    first = Rule(rate=Decimal("0.100000"), seller_id=SELLER)
    second = Rule(rate=Decimal("0.050000"), seller_id=SELLER)

    with pytest.raises(CommissionRuleAmbiguity) as caught:
        resolve_commission_rule(
            [first, second], seller_id=SELLER, category=None, at=JUNE
        )

    message = str(caught.value)
    assert str(first.id) in message
    assert str(second.id) in message
    assert "insertion order" in message


def test_ambiguity_names_every_tied_rule_not_just_two() -> None:
    rules = [Rule(rate=Decimal("0.1"), seller_id=SELLER) for _ in range(3)]

    with pytest.raises(CommissionRuleAmbiguity) as caught:
        resolve_commission_rule(rules, seller_id=SELLER, category=None, at=JUNE)

    message = str(caught.value)
    assert all(str(rule.id) in message for rule in rules)
    assert "3 commission rules" in message


def test_rules_of_different_specificity_are_not_ambiguous() -> None:
    """The ambiguity check must not fire on the ordinary case of a default plus
    an override - which is the whole reason for having scoping."""
    resolve_commission_rule(
        [
            Rule(rate=Decimal("0.100000")),
            Rule(rate=Decimal("0.050000"), seller_id=SELLER),
        ],
        seller_id=SELLER,
        category=None,
        at=JUNE,
    )


def test_no_applicable_rule_raises() -> None:
    """specs/tech-stack.md section 9.6: a monetary value is never defaulted.

    A zero rate returned here would be a wrong financial answer that looks like
    a correct one - the marketplace would appear to have taken no commission.
    """
    with pytest.raises(NoApplicableCommissionRule) as caught:
        resolve_commission_rule(
            [Rule(rate=Decimal("0.1"), seller_id=OTHER_SELLER)],
            seller_id=SELLER,
            category=None,
            at=JUNE,
        )

    assert "no commission rule applies" in str(caught.value)
    assert str(SELLER) in str(caught.value)


def test_an_empty_rule_set_raises() -> None:
    with pytest.raises(NoApplicableCommissionRule):
        resolve_commission_rule([], seller_id=SELLER, category=None, at=JUNE)


def test_a_naive_timestamp_raises_with_a_useful_message() -> None:
    """A naive datetime compared against an aware one raises `TypeError` deep
    inside the comparison. Catching it at the boundary names the problem."""
    with pytest.raises(EngineError, match="must be timezone-aware"):
        resolve_commission_rule(
            [Rule(rate=Decimal("0.1"))],
            seller_id=SELLER,
            category=None,
            at=datetime(2026, 6, 1),  # noqa: DTZ001 - the point of the test
        )


def test_the_engine_errors_are_not_value_errors() -> None:
    """Unlike the money errors, these describe a state of the data that no
    calculation can resolve - so they must not be swallowed by a boundary that
    catches `ValueError` to quarantine a malformed field."""
    assert not issubclass(EngineError, ValueError)
    assert issubclass(CommissionRuleAmbiguity, EngineError)
    assert issubclass(NoApplicableCommissionRule, EngineError)


# ---------------------------------------------------------------------------
# No clock read
# ---------------------------------------------------------------------------


def test_resolution_reads_no_clock() -> None:
    """specs/tech-stack.md section 6. The same inputs must resolve the same way
    whenever the function is called, which is only true if `at` is the only
    source of time.

    Checked by source inspection rather than by monkeypatching, because the
    rule is about the module containing no clock read at all, not about one
    call happening to avoid it. `tests/test_money_purity.py` enforces the
    broader version of this over the whole package.
    """
    import inspect

    from reckonai.engine import rules

    source = inspect.getsource(rules)

    assert "datetime.now" not in source
    assert "utcnow" not in source
    assert "time.time" not in source
