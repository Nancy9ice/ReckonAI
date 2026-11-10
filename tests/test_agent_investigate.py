"""Orchestration: selecting the exception, running a provider, persisting.

Block E, Group 6. The provider is a stub throughout - these tests prove the
wiring, not the model. What they pin:

  - the exception is selected through `exceptions_only()`, so a lifecycle
    state is never handed to the agent as a discrepancy
  - `--fault F1` resolves to Scenario 1's order through the manifest
  - a provider failure is recorded rather than raised
  - the withheld set reaches both the tools and the persisted row
  - a fabricated citation is surfaced, not swallowed
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data
from reckonai.agent.investigate import (
    NoSuchException,
    investigate,
    main,
)
from reckonai.agent.providers.base import ProviderError, ProviderResult
from reckonai.agent.registry import ToolSpec
from reckonai.agent.report import (
    CandidateCause,
    InvestigationReport,
    ModelVerdict,
)
from reckonai.models import Tenant
from reckonai.recon import ExceptionCode, exceptions_only
from reckonai.reconcile import run
from reckonai.sim import data, manifest

SCENARIO_1 = manifest.fault("F1")
ORDER_REF = SCENARIO_1.order_ref


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None
    return tenant_id


class _StubProvider:
    """Returns a prepared result, recording what it was asked.

    It also *runs* the tools it was given, so these tests exercise the real
    tool surface rather than only the orchestration around it.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        report: InvestigationReport | None,
        error: str | None = None,
        stop_reason: str | None = "end_turn",
        call_tools: bool = True,
    ) -> None:
        self._report = report
        self._error = error
        self._stop_reason = stop_reason
        self._call_tools = call_tools
        self.system: str | None = None
        self.user: str | None = None
        self.tool_results: dict[str, object] = {}

    def investigate(
        self,
        *,
        system: str,
        user: str,
        tools: tuple[ToolSpec, ...],
        model: str,
    ) -> ProviderResult:
        self.system = system
        self.user = user

        if self._call_tools:
            for spec in tools:
                if not spec.parameters:
                    self.tool_results[spec.name] = spec.call()
                elif "order_ref" in spec.parameters:
                    self.tool_results[spec.name] = spec.call(order_ref=ORDER_REF)

        if self._error is not None:
            raise ProviderError(self._error)

        return ProviderResult(
            report=self._report,
            provider=self.name,
            model_requested=model,
            model_returned=model,
            stop_reason=self._stop_reason,
            summarized_reasoning="Checked the ledger.",
        )


def _report(cited: list[str] | None = None) -> InvestigationReport:
    return InvestigationReport(
        verdict=ModelVerdict.REPORTED,
        summary="A refund was never booked to the seller ledger.",
        causes=[
            CandidateCause(
                description="The refund debit is missing from the ledger.",
                confidence=0.9,
                cited_refs=cited if cited is not None else [ORDER_REF],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Selecting the exception
# ---------------------------------------------------------------------------


def test_scenario_1_is_investigated_end_to_end(
    session: Session, seeded: uuid.UUID
) -> None:
    """The wiring for Phase 12's first exit criterion.

    Not a claim about the model: this asserts the exception is found, the
    tools are offered, the report is persisted and the citation resolves. The
    live run in validation.md section 3 is what proves the model names the
    cause.
    """
    provider = _StubProvider(report=_report())

    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=provider,
        model="claude-haiku-5-5",
    )

    assert outcome.investigation.code == str(ExceptionCode.MISSING_LEDGER_ADJUSTMENT)
    assert outcome.investigation.verdict == "reported"
    assert outcome.unresolved == ()
    assert outcome.unstable == ()


def test_the_exception_reaches_the_prompt(
    session: Session, seeded: uuid.UUID
) -> None:
    """The order, the code and the engine's own detail are all stated."""
    provider = _StubProvider(report=_report())

    investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=provider,
        model="claude-haiku-5-5",
    )

    assert provider.user is not None
    assert ORDER_REF in provider.user
    assert str(ExceptionCode.MISSING_LEDGER_ADJUSTMENT) in provider.user


def test_the_system_prompt_carries_the_no_arithmetic_rule(
    session: Session, seeded: uuid.UUID
) -> None:
    """specs/mission.md P1 is instructed as well as enforced structurally.

    The tools make it impossible to pass a figure *in*; the prompt is what
    stops the model computing one for its own summary.
    """
    provider = _StubProvider(report=_report())

    investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=provider,
        model="claude-haiku-5-5",
    )

    assert provider.system is not None
    assert "NEVER CALCULATE A MONETARY AMOUNT" in provider.system
    assert "NEVER INVENT A RECORD REFERENCE" in provider.system
    assert "insufficient_evidence" in provider.system


def test_an_order_with_no_exception_is_refused(
    session: Session, seeded: uuid.UUID
) -> None:
    """An agent asked to explain nothing would return a cause for nothing.

    The clean baseline is the right place to prove this: Phase 11's gate says
    those 15 purchase units produce zero exceptions, so any of them is an
    order with nothing to investigate.
    """
    findings = run(session, seeded)
    exceptional = {finding.order_ref for finding in exceptions_only(findings)}
    clean = sorted({finding.order_ref for finding in findings} - exceptional)
    assert clean, "the seeded dataset has no clean order to check"

    with pytest.raises(NoSuchException):
        investigate(
            session,
            seeded,
            order_ref=clean[0],
            provider=_StubProvider(report=_report()),
            model="claude-haiku-5-5",
        )


def test_an_awaiting_evidence_finding_is_not_investigable(
    session: Session, seeded: uuid.UUID
) -> None:
    """Selected through `exceptions_only()`, never `len(findings)`.

    F5's refund is late, not wrong. Handing it to the agent would ask it to
    explain a healthy lifecycle state as a discrepancy - and whatever it
    returned would be a false positive wearing a report.
    """
    parked = [
        finding
        for finding in run(session, seeded)
        if finding.code is ExceptionCode.AWAITING_EVIDENCE
    ]
    assert parked, "the seeded dataset has no awaiting_evidence finding"

    with pytest.raises(NoSuchException):
        investigate(
            session,
            seeded,
            order_ref=parked[0].order_ref,
            provider=_StubProvider(report=_report()),
            model="claude-haiku-5-5",
            code=str(ExceptionCode.AWAITING_EVIDENCE),
        )


def test_the_fault_id_resolves_through_the_manifest() -> None:
    """`--fault F1` and Block G's ground truth name the same order."""
    from reckonai.agent.investigate import _order_for_fault

    assert _order_for_fault("F1") == ORDER_REF
    assert SCENARIO_1.expected_classification == str(
        ExceptionCode.MISSING_LEDGER_ADJUSTMENT
    )


# ---------------------------------------------------------------------------
# The withhold set
# ---------------------------------------------------------------------------


def test_withholding_reaches_the_tools_and_the_row(
    session: Session, seeded: uuid.UUID
) -> None:
    """Exit criterion 3's instrument, end to end.

    The ledger tool comes back empty to the agent, and the row records that it
    was withheld - without which an `insufficient_evidence` verdict would be
    indistinguishable from a real evidence gap.
    """
    provider = _StubProvider(
        report=InvestigationReport(
            verdict=ModelVerdict.INSUFFICIENT_EVIDENCE,
            summary="The ledger records were not available.",
            causes=[],
        )
    )

    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=provider,
        model="claude-haiku-5-5",
        withhold=("ledger",),
    )

    assert provider.tool_results["fetch_seller_ledger_entries"] == []
    assert outcome.investigation.withheld == ["ledger"]
    assert outcome.investigation.verdict == "insufficient_evidence"


def test_the_recompute_tool_still_works_when_everything_is_withheld(
    session: Session, seeded: uuid.UUID
) -> None:
    """It returns verdicts, not evidence, so it is never suppressed."""
    from reckonai.agent.tools import WITHHOLDABLE

    provider = _StubProvider(report=_report())

    investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=provider,
        model="claude-haiku-5-5",
        withhold=tuple(sorted(WITHHOLDABLE)),
    )

    assert provider.tool_results["recompute"]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_a_provider_failure_is_recorded_rather_than_raised(
    session: Session, seeded: uuid.UUID
) -> None:
    """A reset connection produces a row, not a traceback.

    The attempt happened and the audit trail says so, with no stop reason -
    because no response was observed.
    """
    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=_StubProvider(report=None, error="connection reset"),
        model="claude-haiku-5-5",
    )

    assert outcome.report is None
    assert outcome.investigation.verdict == "failed"
    assert outcome.investigation.stop_reason is None
    assert outcome.investigation.model_returned is None


def test_a_refusal_is_recorded_as_failed(
    session: Session, seeded: uuid.UUID
) -> None:
    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=_StubProvider(report=None, stop_reason="refusal"),
        model="claude-haiku-5-5",
    )

    assert outcome.investigation.verdict == "failed"
    assert outcome.investigation.stop_reason == "refusal"


def test_a_fabricated_citation_is_surfaced(
    session: Session, seeded: uuid.UUID
) -> None:
    """Exit criterion 2 failing, visibly.

    The outcome names the unresolvable reference so the CLI can report it
    loudly. Swallowing it would make a fabricated citation look like a clean
    investigation.
    """
    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=_StubProvider(
            report=_report(cited=[ORDER_REF, "ORD-9999-INVENTED"])
        ),
        model="claude-haiku-5-5",
    )

    assert outcome.unresolved == ("ORD-9999-INVENTED",)
    assert outcome.investigation.verdict == "reported"


def test_an_unstable_citation_is_surfaced_separately(
    session: Session, seeded: uuid.UUID
) -> None:
    """Resolution and stability are different answers.

    A fee id names a real record, so it is not a fabrication - it simply will
    not find that record again after a reseed. Reported as a warning rather
    than as a fabricated citation.
    """
    from reckonai.models import PlatformFee

    fee_ref = session.scalar(
        select(PlatformFee.external_id).where(PlatformFee.tenant_id == seeded)
    )
    assert fee_ref is not None

    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=_StubProvider(report=_report(cited=[fee_ref])),
        model="claude-haiku-5-5",
    )

    assert outcome.unresolved == ()
    assert outcome.unstable == (fee_ref,)


# ---------------------------------------------------------------------------
# The CLI surface
# ---------------------------------------------------------------------------


def test_the_cli_requires_a_target() -> None:
    """Neither `--order` nor `--fault` is not a runnable request."""
    with pytest.raises(SystemExit):
        main([])


def test_the_cli_refuses_an_unknown_withhold_kind() -> None:
    """argparse `choices` catches the typo before a database is opened.

    `--withhold ledgers` that withheld nothing would be a false pass on the
    exit criterion most at risk.
    """
    with pytest.raises(SystemExit):
        main(["--fault", "F1", "--withhold", "ledgers"])


def test_the_cli_refuses_a_provider_paired_with_the_wrong_model() -> None:
    """`--provider openai` while the model is still a Claude id.

    Returns an exit code rather than raising: the overrides are re-validated
    through `Settings`, so the pairing rule holds for a CLI run as much as for
    the environment.
    """
    assert main(["--fault", "F1", "--provider", "openai"]) == 2
