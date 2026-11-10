"""Persisting an investigation, with its audit trail and its citations checked.

Phase 12's sixth exit criterion lands here: the summarized reasoning is
persisted with the report, and the model id from the response is stored and
compared against the model requested.

**This module owns one rule the database cannot express.** A `reported` verdict
requires at least one cause, and that cannot be a `CHECK`: the causes live in
another table and Postgres forbids a subquery in a `CHECK`. The options were a
deferred constraint trigger or enforcing it where both tables are written. It is
enforced here, because this function writes both in one transaction and a
trigger would put a second financial rule in a second language for a condition
one function already owns.

**Citations are resolved on the way in, and the failures are kept.** Phase 12's
second exit criterion is that no cited id is fabricated. The honest way to hold
that is to measure it, so an unresolvable reference is stored in
`unresolved_refs` rather than silently repaired or dropped - quietly fixing one
would hide exactly the defect the criterion exists to catch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from reckonai.agent.citations import CitationResolver
from reckonai.agent.providers.base import ProviderResult
from reckonai.agent.report import ModelVerdict
from reckonai.models import Investigation, InvestigationCause

#: Three decimal places, matching `investigation_cause.confidence`'s
#: `NUMERIC(4, 3)`. Quantized once, here, at the boundary: the report model
#: carries the plain `float` the providers' JSON returns, and the column is
#: exact so that Block G's comparison across providers measures the models
#: rather than storage.
_CONFIDENCE_PLACES = Decimal("0.001")

#: The verdict assigned when no report exists. Never returned by a model -
#: `reckonai.agent.report.ModelVerdict` has no such member, because a model
#: cannot declare its own failure.
FAILED = "failed"


class InconsistentReport(ValueError):
    """A `reported` verdict carrying no cause.

    "I found nothing" filed as a conclusion. Refused rather than downgraded to
    `insufficient_evidence`, because the two mean different things to whoever
    reads the queue: one is the agent declining to guess, the other is a bug
    in whatever produced the report. Silently rewriting the first into the
    second would lose that distinction in the audit trail.
    """


def store_investigation(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    order_ref: str,
    code: str,
    result: ProviderResult,
    withheld: tuple[str, ...] = (),
    run_id: uuid.UUID | None = None,
    started_at: datetime | None = None,
) -> Investigation:
    """Write one investigation and its causes, in one transaction.

    Flushes but does not commit: the caller owns the transaction boundary, the
    same division `reckonai.jobs` uses. A CLI commits; a test's rolled-back
    session does not.

    Raises:
        InconsistentReport: for a `reported` verdict with no causes.
    """
    report = result.report
    verdict = FAILED if report is None else str(report.verdict)

    if report is not None and not report.is_consistent():
        raise InconsistentReport(
            "a 'reported' verdict must carry at least one candidate cause; "
            "an investigation that found none is 'insufficient_evidence'"
        )

    investigation = Investigation(
        tenant_id=tenant_id,
        run_id=run_id,
        order_external_ref=order_ref,
        code=code,
        provider=result.provider,
        model_requested=result.model_requested,
        model_returned=result.model_returned,
        # Computed from the two ids rather than passed in, so the flag is
        # *proved* by the comparison instead of asserted by a caller.
        downgraded=result.downgraded,
        stop_reason=result.stop_reason,
        verdict=verdict,
        summarized_reasoning=result.summarized_reasoning,
        withheld=list(withheld),
        finished_at=datetime.now(UTC),
    )
    if started_at is not None:
        investigation.started_at = started_at

    session.add(investigation)
    session.flush()

    if report is None:
        return investigation

    resolver = CitationResolver(session, tenant_id)

    for rank, cause in enumerate(report.ranked(), start=1):
        cited = tuple(cause.cited_refs)
        session.add(
            InvestigationCause(
                tenant_id=tenant_id,
                investigation_id=investigation.id,
                # 1-based, in the order the model returned them. Not re-sorted
                # by confidence: the ranking is the model's judgment, and
                # re-deriving it would silently reorder ties and make the
                # stored rank disagree with the report produced.
                rank=rank,
                description=cause.description,
                confidence=_quantized(cause.confidence),
                cited_refs=list(cited),
                unresolved_refs=list(resolver.unresolved(cited)),
            )
        )

    session.flush()
    return investigation


def failed_investigation(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    order_ref: str,
    code: str,
    provider: str,
    model_requested: str,
    reason: str,
    withheld: tuple[str, ...] = (),
    run_id: uuid.UUID | None = None,
) -> Investigation:
    """Record an investigation that never reached the model.

    A `ProviderError` - a reset connection, an absent credential - is not a
    refusal: no response was observed. So `model_returned` and `stop_reason`
    both stay NULL rather than being filled with the request's own values,
    which would assert something that did not happen (specs/mission.md P3,
    applied to provenance rather than to money).

    The reason is kept in `summarized_reasoning`, which is the only free-text
    column on the row. That is a slight stretch of the column's name and the
    alternative was a `last_error` column - which Block D added, found it
    could never be written under a one-transaction design, and removed. One
    legible row beats a column that is NULL on every row but this one.
    """
    investigation = Investigation(
        tenant_id=tenant_id,
        run_id=run_id,
        order_external_ref=order_ref,
        code=code,
        provider=provider,
        model_requested=model_requested,
        model_returned=None,
        downgraded=False,
        stop_reason=None,
        verdict=FAILED,
        summarized_reasoning=reason,
        withheld=list(withheld),
        finished_at=datetime.now(UTC),
    )
    session.add(investigation)
    session.flush()
    return investigation


def _quantized(confidence: float) -> Decimal:
    """One confidence as an exact three-place decimal.

    Converted through `str` rather than `Decimal(float)`, which would carry
    the float's binary expansion into the decimal and store 0.85 as
    0.84999999999999997779553950749686919152736663818359375 before rounding.
    """
    return Decimal(str(confidence)).quantize(
        _CONFIDENCE_PLACES, rounding=ROUND_HALF_UP
    )


def verdict_of(result: ProviderResult) -> str:
    """What verdict this result will be stored under.

    Exposed so a caller can report the outcome without re-deriving the rule -
    and so a test can assert the mapping without writing a row.
    """
    return FAILED if result.report is None else str(result.report.verdict)


def is_insufficient_evidence(result: ProviderResult) -> bool:
    """Whether the agent declined to guess.

    A named predicate because this is the branch Block G scores specifically,
    and because `verdict == "insufficient_evidence"` written at each call site
    is a string comparison waiting to be mistyped.
    """
    return (
        result.report is not None
        and result.report.verdict is ModelVerdict.INSUFFICIENT_EVIDENCE
    )
