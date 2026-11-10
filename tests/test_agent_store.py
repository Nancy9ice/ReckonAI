"""Persisting an investigation: the audit trail, and the citations kept honest.

Block E, Group 5. Phase 12's sixth exit criterion is proven here - the
reasoning is persisted, the model id is stored and compared - along with the
one rule the database cannot express, and the citation measurement that makes
the second exit criterion honest rather than asserted.

No test here calls an API. Every `ProviderResult` is built directly, which is
what makes the refusal and downgrade paths testable at all: neither can be
provoked on demand from a real provider.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data
from reckonai.agent.providers.base import ProviderResult
from reckonai.agent.report import (
    CandidateCause,
    InvestigationReport,
    ModelVerdict,
)
from reckonai.agent.store import (
    FAILED,
    InconsistentReport,
    failed_investigation,
    is_insufficient_evidence,
    store_investigation,
    verdict_of,
)
from reckonai.models import Investigation, InvestigationCause, Tenant
from reckonai.recon import ExceptionCode
from reckonai.sim import data, manifest

ORDER_REF = manifest.fault("F1").order_ref
CODE = str(ExceptionCode.MISSING_LEDGER_ADJUSTMENT)


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None
    return tenant_id


def _result(
    *,
    report: InvestigationReport | None,
    model_returned: str | None = "claude-haiku-5-5",
    stop_reason: str | None = "end_turn",
    reasoning: str | None = "Checked the ledger; no refund debit.",
) -> ProviderResult:
    return ProviderResult(
        report=report,
        provider="anthropic",
        model_requested="claude-haiku-5-5",
        model_returned=model_returned,
        stop_reason=stop_reason,
        summarized_reasoning=reasoning,
    )


def _report(
    *,
    verdict: ModelVerdict = ModelVerdict.REPORTED,
    causes: list[CandidateCause] | None = None,
) -> InvestigationReport:
    if causes is None:
        causes = [
            CandidateCause(
                description="The ledger is missing the refund debit.",
                confidence=0.85,
                cited_refs=[ORDER_REF],
            )
        ]
    return InvestigationReport(
        verdict=verdict, summary="A refund was never booked.", causes=causes
    )


def _store(
    session: Session, tenant_id: uuid.UUID, result: ProviderResult, **extra: object
) -> Investigation:
    return store_investigation(
        session,
        tenant_id,
        order_ref=ORDER_REF,
        code=CODE,
        result=result,
        **extra,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Exit criterion 6 - the audit trail
# ---------------------------------------------------------------------------


def test_a_report_round_trips_losing_no_field(
    session: Session, seeded: uuid.UUID
) -> None:
    """Every column the audit constraints name is written and readable."""
    stored = _store(session, seeded, _result(report=_report()))
    session.expunge_all()

    row = session.scalars(select(Investigation)).one()

    assert row.order_external_ref == ORDER_REF
    assert row.code == CODE
    assert row.provider == "anthropic"
    assert row.model_requested == "claude-haiku-5-5"
    assert row.model_returned == "claude-haiku-5-5"
    assert row.downgraded is False
    assert row.stop_reason == "end_turn"
    assert row.verdict == "reported"
    assert row.summarized_reasoning == "Checked the ledger; no refund debit."
    assert row.finished_at is not None
    assert row.id == stored.id

    cause = session.scalars(select(InvestigationCause)).one()
    assert cause.rank == 1
    assert cause.description == "The ledger is missing the refund debit."
    assert cause.confidence == Decimal("0.850")
    assert cause.cited_refs == [ORDER_REF]
    assert cause.unresolved_refs == []


def test_the_reasoning_is_persisted(session: Session, seeded: uuid.UUID) -> None:
    """specs/tech-stack.md section 5.2: reasoning must be recorded.

    Stored as supporting context, never as the authoritative cause - the
    cited records are the authority. This asserts it is kept; nothing in the
    product presents it as the conclusion.
    """
    _store(session, seeded, _result(report=_report(), reasoning="Step by step."))

    assert (
        session.scalars(select(Investigation)).one().summarized_reasoning
        == "Step by step."
    )


def test_a_mismatched_model_is_stored_as_downgraded(
    session: Session, seeded: uuid.UUID
) -> None:
    """Nothing in the MVP can produce this, which is exactly why it is tested.

    The stored comparison is what *proves* nothing downgrades silently. An
    unexercised flag proves nothing, and section 5.2 calls an unflagged
    downgrade worse than a failed investigation.
    """
    _store(
        session,
        seeded,
        _result(report=_report(), model_returned="claude-sonnet-5-5"),
    )

    row = session.scalars(select(Investigation)).one()
    assert row.downgraded is True
    assert row.model_returned == "claude-sonnet-5-5"


def test_the_downgrade_flag_is_not_taken_from_the_caller(
    session: Session, seeded: uuid.UUID
) -> None:
    """It is derived from the two ids, so a caller cannot assert it wrongly."""
    _store(
        session,
        seeded,
        _result(report=_report(), model_returned="claude-haiku-5-5"),
    )

    assert session.scalars(select(Investigation)).one().downgraded is False


# ---------------------------------------------------------------------------
# Refusals and failures
# ---------------------------------------------------------------------------


def test_a_refusal_is_stored_as_failed_with_no_causes(
    session: Session, seeded: uuid.UUID
) -> None:
    """Haiku 5.5 has no refusal fallback, so a refusal ends the investigation.

    No report, no cause list, nothing presented as a conclusion - which is
    section 5's decision stated as a row.
    """
    _store(
        session,
        seeded,
        _result(report=None, stop_reason="refusal", reasoning=None),
    )

    row = session.scalars(select(Investigation)).one()
    assert row.verdict == FAILED
    assert row.stop_reason == "refusal"
    assert session.scalars(select(InvestigationCause)).all() == []


def test_a_failure_before_any_response_records_no_stop_reason(
    session: Session, seeded: uuid.UUID
) -> None:
    """A reset connection is not a refusal: we observed no response.

    Filling `model_returned` or `stop_reason` from the request would assert
    something that did not happen.
    """
    failed_investigation(
        session,
        seeded,
        order_ref=ORDER_REF,
        code=CODE,
        provider="anthropic",
        model_requested="claude-haiku-5-5",
        reason="the Anthropic call failed: connection reset",
    )

    row = session.scalars(select(Investigation)).one()
    assert row.verdict == FAILED
    assert row.model_returned is None
    assert row.stop_reason is None
    assert row.downgraded is False
    assert "connection reset" in (row.summarized_reasoning or "")


def test_a_failed_attempt_is_recorded_rather_than_discarded(
    session: Session, seeded: uuid.UUID
) -> None:
    """An audit trail that kept only successful reports would overstate.

    One row per attempt, whatever the outcome.
    """
    _store(session, seeded, _result(report=None, stop_reason="refusal"))
    _store(session, seeded, _result(report=_report()))

    rows = session.scalars(select(Investigation)).all()
    assert len(rows) == 2
    assert {row.verdict for row in rows} == {FAILED, "reported"}


# ---------------------------------------------------------------------------
# The rule the database cannot express
# ---------------------------------------------------------------------------


def test_a_reported_verdict_with_no_causes_is_refused(
    session: Session, seeded: uuid.UUID
) -> None:
    """"I found nothing" cannot be filed as a conclusion.

    Not expressible as a `CHECK` - the causes are in another table and
    Postgres forbids a subquery there - so it is enforced where both tables
    are written.
    """
    report = InvestigationReport.model_construct(
        verdict=ModelVerdict.REPORTED, summary="Nothing found.", causes=[]
    )

    with pytest.raises(InconsistentReport):
        _store(session, seeded, _result(report=report))


def test_that_refusal_is_not_quietly_rewritten_as_insufficient_evidence(
    session: Session, seeded: uuid.UUID
) -> None:
    """The two mean different things to whoever reads the queue.

    One is the agent declining to guess; the other is a bug in whatever
    produced the report. Downgrading the second into the first would lose that
    distinction in the audit trail, so nothing is written at all.
    """
    report = InvestigationReport.model_construct(
        verdict=ModelVerdict.REPORTED, summary="Nothing found.", causes=[]
    )

    with pytest.raises(InconsistentReport):
        _store(session, seeded, _result(report=report))

    session.rollback()
    assert session.scalars(select(Investigation)).all() == []


def test_insufficient_evidence_may_carry_no_causes(
    session: Session, seeded: uuid.UUID
) -> None:
    """The branch specs/mission.md P5 calls a correct and expected answer."""
    report = InvestigationReport(
        verdict=ModelVerdict.INSUFFICIENT_EVIDENCE,
        summary="The ledger records were not available.",
        causes=[],
    )

    _store(session, seeded, _result(report=report), withheld=("ledger",))

    row = session.scalars(select(Investigation)).one()
    assert row.verdict == "insufficient_evidence"
    assert row.withheld == ["ledger"]


def test_insufficient_evidence_may_also_carry_rejected_causes(
    session: Session, seeded: uuid.UUID
) -> None:
    """A model may show what it considered and set aside.

    Constrained in one direction only: `reported` needs a cause,
    `insufficient_evidence` is free either way.
    """
    report = InvestigationReport(
        verdict=ModelVerdict.INSUFFICIENT_EVIDENCE,
        summary="Considered and rejected.",
        causes=[
            CandidateCause(
                description="Possibly a duplicate booking, but no record shows it.",
                confidence=0.2,
                cited_refs=[ORDER_REF],
            )
        ],
    )

    _store(session, seeded, _result(report=report))

    assert session.scalars(select(Investigation)).one().verdict == (
        "insufficient_evidence"
    )
    assert len(session.scalars(select(InvestigationCause)).all()) == 1


def test_the_withheld_set_is_recorded_with_the_verdict(
    session: Session, seeded: uuid.UUID
) -> None:
    """An `insufficient_evidence` verdict is only readable alongside this.

    Without it, the deliberate evidence gap that proves exit criterion 3 is
    indistinguishable from a real one.
    """
    _store(
        session,
        seeded,
        _result(report=_report()),
        withheld=("ledger", "refunds"),
    )

    assert session.scalars(select(Investigation)).one().withheld == [
        "ledger",
        "refunds",
    ]


# ---------------------------------------------------------------------------
# Exit criterion 2 - citations are measured, not assumed
# ---------------------------------------------------------------------------


def test_an_unresolvable_citation_is_persisted_as_unresolved(
    session: Session, seeded: uuid.UUID
) -> None:
    """Recorded rather than repaired or dropped.

    The criterion is that no cited id is fabricated. Quietly fixing one would
    hide exactly the defect the criterion exists to catch, and discarding the
    row would hide that the agent cited anything at all.
    """
    report = _report(
        causes=[
            CandidateCause(
                description="A cause citing one real and one invented record.",
                confidence=0.5,
                cited_refs=[ORDER_REF, "ORD-9999-INVENTED"],
            )
        ]
    )

    _store(session, seeded, _result(report=report))

    cause = session.scalars(select(InvestigationCause)).one()
    assert cause.cited_refs == [ORDER_REF, "ORD-9999-INVENTED"]
    assert cause.unresolved_refs == ["ORD-9999-INVENTED"]


def test_a_clean_report_stores_no_unresolved_citations(
    session: Session, seeded: uuid.UUID
) -> None:
    """The passing case for exit criterion 2 is an empty array."""
    _store(session, seeded, _result(report=_report()))

    assert session.scalars(select(InvestigationCause)).one().unresolved_refs == []


# ---------------------------------------------------------------------------
# Ranking and confidence
# ---------------------------------------------------------------------------


def test_causes_keep_the_order_the_model_returned(
    session: Session, seeded: uuid.UUID
) -> None:
    """Not re-sorted by confidence.

    The ranking is the model's judgment; re-deriving it would silently reorder
    ties and make the stored rank disagree with the report produced. The two
    causes here are deliberately tied.
    """
    report = _report(
        causes=[
            CandidateCause(
                description="First.", confidence=0.5, cited_refs=[ORDER_REF]
            ),
            CandidateCause(
                description="Second.", confidence=0.5, cited_refs=[ORDER_REF]
            ),
        ]
    )

    _store(session, seeded, _result(report=report))

    causes = session.scalars(
        select(InvestigationCause).order_by(InvestigationCause.rank)
    ).all()
    assert [cause.description for cause in causes] == ["First.", "Second."]
    assert [cause.rank for cause in causes] == [1, 2]


def test_confidence_is_quantized_without_binary_float_drift(
    session: Session, seeded: uuid.UUID
) -> None:
    """Converted through `str`, not `Decimal(float)`.

    `Decimal(0.85)` carries the float's binary expansion into the decimal.
    Asserted exactly, because Block G compares these values across providers.
    """
    report = _report(
        causes=[
            CandidateCause(description="A.", confidence=0.85, cited_refs=[ORDER_REF]),
            CandidateCause(description="B.", confidence=0.3333, cited_refs=[ORDER_REF]),
            CandidateCause(description="C.", confidence=1.0, cited_refs=[ORDER_REF]),
        ]
    )

    _store(session, seeded, _result(report=report))

    stored = [
        cause.confidence
        for cause in session.scalars(
            select(InvestigationCause).order_by(InvestigationCause.rank)
        )
    ]
    assert stored == [Decimal("0.850"), Decimal("0.333"), Decimal("1.000")]


# ---------------------------------------------------------------------------
# The named predicates
# ---------------------------------------------------------------------------


def test_verdict_of_maps_every_outcome() -> None:
    assert verdict_of(_result(report=_report())) == "reported"
    assert verdict_of(_result(report=None)) == FAILED
    assert (
        verdict_of(
            _result(
                report=InvestigationReport(
                    verdict=ModelVerdict.INSUFFICIENT_EVIDENCE,
                    summary="Cannot tell.",
                    causes=[],
                )
            )
        )
        == "insufficient_evidence"
    )


def test_is_insufficient_evidence_is_false_for_a_failure() -> None:
    """A refusal is not the agent declining to guess.

    Block G scores the `insufficient_evidence` branch specifically, so
    conflating it with a refusal would inflate that score with investigations
    the model never completed.
    """
    refusal = _result(report=None, stop_reason="refusal")
    assert is_insufficient_evidence(refusal) is False
    assert is_insufficient_evidence(_result(report=_report())) is False
    assert (
        is_insufficient_evidence(
            _result(
                report=InvestigationReport(
                    verdict=ModelVerdict.INSUFFICIENT_EVIDENCE,
                    summary="Cannot tell.",
                    causes=[],
                )
            )
        )
        is True
    )
