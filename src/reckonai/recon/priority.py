"""Queue prioritisation: one score over the four `readme.md` 9.3 factors.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D6.

Pure and deterministic. The score orders a queue and is never a financial
conclusion; the language model contributes only the stored confidence of its
top cause (`mission.md` P1).

**Impact is ranked inside its own currency and never converted.** Comparing a
USD discrepancy with a EUR one needs a rate, and `mission.md` P3 forbids
substituting one. So each factor-1 value is a share of the largest impact *in
the same currency*; two currencies are therefore each scaled to their own
largest, and the result says nothing about which is bigger in absolute terms.
That is a limitation, recorded in the requirements, not hidden here.

**An absent confidence is not zero.** An exception no one has investigated has
no confidence to score. The factor is `None`, and the weights of the factors
that are present are renormalised, so the missing one neither rewards nor
punishes. The breakdown returns `None` so a reader sees the gap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from reckonai.money import Money

#: Fixed weights, summing to one. Impact leads because the product is about
#: money; age and confidence break ties between comparable impacts.
WEIGHTS: dict[str, Decimal] = {
    "impact": Decimal("0.40"),
    "sellers": Decimal("0.15"),
    "age": Decimal("0.20"),
    "confidence": Decimal("0.25"),
}

#: Affected sellers beyond this add nothing: the factor saturates at one.
SELLER_CAP = 3

#: An exception this old or older has the full age factor.
AGE_CAP_DAYS = Decimal(30)

_SCORE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class PriorityInput:
    """What the score needs to know about one exception."""

    key: str
    impact: Money
    seller_count: int
    age_days: Decimal
    confidence: Decimal | None


@dataclass(frozen=True)
class Priority:
    """A score and the factors that produced it, in zero-to-one terms."""

    key: str
    score: Decimal
    factors: dict[str, Decimal | None]


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal(0), min(Decimal(1), value))


def prioritise(items: Sequence[PriorityInput]) -> list[Priority]:
    """Score every item, returned in the order given.

    Ordering is the caller's job (`sorted(..., key=score, reverse=True)`), so a
    test can state the score without also asserting a sort.
    """
    largest: dict[str, Decimal] = {}
    for item in items:
        current = largest.get(item.impact.currency, Decimal(0))
        largest[item.impact.currency] = max(current, item.impact.amount)

    out: list[Priority] = []
    for item in items:
        biggest = largest[item.impact.currency]
        factors: dict[str, Decimal | None] = {
            "impact": (
                _clamp(item.impact.amount / biggest) if biggest > 0 else Decimal(0)
            ),
            "sellers": _clamp(Decimal(item.seller_count) / Decimal(SELLER_CAP)),
            "age": _clamp(item.age_days / AGE_CAP_DAYS),
            "confidence": (
                None if item.confidence is None else _clamp(item.confidence)
            ),
        }
        present = {k: v for k, v in factors.items() if v is not None}
        weight_total = sum((WEIGHTS[k] for k in present), Decimal(0))
        weighted = sum((WEIGHTS[k] * v for k, v in present.items()), Decimal(0))
        score = (weighted / weight_total).quantize(_SCORE_PLACES)
        out.append(Priority(key=item.key, score=score, factors=factors))
    return out
