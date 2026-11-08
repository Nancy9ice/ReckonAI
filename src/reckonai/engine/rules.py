"""Which commission rule applies to a sale.

A tenant may hold several rules at once - a default for the whole marketplace, a
better rate for one seller, a different rate for one category - each valid over
a period. Picking the right one is a small amount of logic with a large
consequence, since it decides the commission figure Phase 11 compares against
PayPal's recorded platform fee.

**Specificity order**, most specific first:

    1. seller and category both match
    2. seller matches, rule applies to all categories
    3. category matches, rule applies to all sellers
    4. the tenant default - all sellers, all categories

A rule is a candidate only if its validity period contains the timestamp. The
interval is **half-open**: `valid_from` is inclusive, `valid_to` is exclusive,
so two consecutive rules can abut on the same instant without overlapping. The
alternative - both ends inclusive - makes every handover date ambiguous, which
is the one thing this module refuses to be.

**Two rules of equal specificity raise** rather than one being chosen. See
`CommissionRuleAmbiguity`; the reasoning is in
specs/2026-11-07-canonical-core/requirements.md D12.

**No applicable rule raises** rather than returning a zero rate.
specs/tech-stack.md section 9.6: a monetary value is never defaulted. A zero
commission silently applied is a wrong financial answer that looks like a
correct one.

The timestamp is a parameter, never a clock read, per specs/tech-stack.md
section 6.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class EngineError(Exception):
    """Base for errors raised by the reconciliation engine.

    Not a `ValueError`: unlike the money errors, these do not describe a
    malformed value. They describe a state of the *data* that no calculation can
    resolve - and the correct response is a quarantine or an escalation
    (specs/mission.md P5), not a coercion.
    """


class CommissionRuleAmbiguity(EngineError):
    """Two or more rules of equal specificity apply at the same instant."""


class NoApplicableCommissionRule(EngineError):
    """No rule covers this seller, category and instant."""


@runtime_checkable
class CommissionRuleLike(Protocol):
    """The fields this module reads from a commission rule.

    A `Protocol` rather than `reckonai.models.CommissionRule`, so this package
    does not import the ORM - which is what keeps the purity gate in
    `tests/test_money_purity.py` satisfiable. The ORM class satisfies it
    structurally, and so does a plain dataclass in a test, with no registration
    and no inheritance.

    Nothing here is read lazily: every attribute is a plain column on the ORM
    class, so passing a detached instance cannot trigger a database round trip
    inside the calculation.
    """

    id: uuid.UUID
    seller_id: uuid.UUID | None
    category: str | None
    rate: Decimal
    valid_from: datetime
    valid_to: datetime | None


def _applies_at(rule: CommissionRuleLike, at: datetime) -> bool:
    """Whether `rule`'s half-open validity period contains `at`."""
    if at < rule.valid_from:
        return False
    return rule.valid_to is None or at < rule.valid_to


def _specificity(
    rule: CommissionRuleLike, seller_id: uuid.UUID, category: str | None
) -> int | None:
    """Score how specifically `rule` matches, or `None` if it does not match.

    The score is only ever compared against another score, so the absolute
    values carry no meaning beyond their order.
    """
    if rule.seller_id is not None and rule.seller_id != seller_id:
        return None
    if rule.category is not None and rule.category != category:
        return None

    return (2 if rule.seller_id is not None else 0) + (
        1 if rule.category is not None else 0
    )


def resolve_commission_rule[RuleT: CommissionRuleLike](
    rules: Iterable[RuleT],
    *,
    seller_id: uuid.UUID,
    category: str | None,
    at: datetime,
) -> RuleT:
    """Return the single commission rule that applies.

    `rules` are the candidates - normally every rule for one tenant. Filtering
    by tenant is the caller's job, because this function performs no I/O and
    cannot know which tenant is in scope; `tenant_id` is not even on the
    protocol.

    Raises `NoApplicableCommissionRule` if none applies, and
    `CommissionRuleAmbiguity` if two of equal specificity do.

        >>> resolve_commission_rule(
        ...     rules, seller_id=seller.id, category="ceramics", at=order.placed_at
        ... )

    Note the `at` that a caller normally passes: the time the *order* was
    placed, not the time of the reconciliation run. That is what makes a
    historical run re-derivable (specs/mission.md P7).
    """
    if at.tzinfo is None:
        # A naive timestamp compared against timezone-aware column values
        # raises TypeError deep inside the comparison below. Catching it here
        # names the problem instead.
        raise EngineError(
            "`at` must be timezone-aware: a naive timestamp cannot be compared "
            "against a rule's validity period"
        )

    scored: list[tuple[int, RuleT]] = []
    for rule in rules:
        if not _applies_at(rule, at):
            continue
        score = _specificity(rule, seller_id, category)
        if score is not None:
            scored.append((score, rule))

    if not scored:
        raise NoApplicableCommissionRule(
            f"no commission rule applies to seller {seller_id} "
            f"(category {category!r}) at {at.isoformat()}. This is a "
            "quarantine, not a zero rate: see specs/tech-stack.md section 9.6."
        )

    best = max(score for score, _ in scored)
    winners: Sequence[RuleT] = [rule for score, rule in scored if score == best]

    if len(winners) > 1:
        ids = ", ".join(str(rule.id) for rule in winners)
        raise CommissionRuleAmbiguity(
            f"{len(winners)} commission rules of equal specificity apply to "
            f"seller {seller_id} (category {category!r}) at {at.isoformat()}: "
            f"{ids}. Resolving this by picking one would make the commission "
            "depend on insertion order; correct the overlapping validity "
            "periods instead."
        )

    return winners[0]
