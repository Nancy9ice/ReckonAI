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

import json
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
from reckonai.models import Investigation, Tenant
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
            # **Through `run_as_json`, not `call`.** That is the path both
            # real providers take, and it is where a withheld record kind
            # becomes an explicit `unavailable` marker. A stub calling `call`
            # directly would miss the translation entirely - which is how the
            # earlier version of these tests asserted an empty list and
            # encoded the bug the live run found.
            for spec in tools:
                if not spec.parameters:
                    self.tool_results[spec.name] = spec.run_as_json()
                elif "order_ref" in spec.parameters:
                    self.tool_results[spec.name] = spec.run_as_json(
                        order_ref=ORDER_REF
                    )

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

    # The marker, not an empty list. "You may not see the ledger" and "the
    # ledger has no entries" are different claims, and the agent reported the
    # stronger one when they looked identical.
    ledger = json.loads(str(provider.tool_results["fetch_seller_ledger_entries"]))
    assert ledger["unavailable"] is True
    assert ledger["record_kind"] == "ledger"
    assert "NOT mean none exist" in ledger["note"]

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


def test_the_cli_reports_a_missing_key_as_one_line(
    monkeypatch: pytest.MonkeyPatch, seeded: uuid.UUID
) -> None:
    """A missing credential is the likeliest failure, so it must read well.

    Regression test: `provider_for` sat outside the `ConfigurationError`
    handler, so running the CLI with no key printed a traceback instead of
    the named error. Found by running it for real.
    """
    from reckonai.config import get_settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        assert main(["--fault", "F1"]) == 2
    finally:
        get_settings.cache_clear()


def test_the_cli_refuses_a_provider_paired_with_the_wrong_model() -> None:
    """`--provider openai` while the model is still a Claude id.

    Returns an exit code rather than raising: the overrides are re-validated
    through `Settings`, so the pairing rule holds for a CLI run as much as for
    the environment.
    """
    assert main(["--fault", "F1", "--provider", "openai"]) == 2


def test_an_inconsistent_report_still_records_the_attempt(
    session: Session, seeded: uuid.UUID
) -> None:
    """A paid model call must leave a row, whatever came back.

    `store_investigation` refuses a `reported` verdict with no causes, and it
    raises **before** adding anything. Nothing caught that, so the CLI exited
    on a traceback, `session.commit()` never ran, and no `investigation` row
    existed at all - contradicting "one row per attempt, whatever the
    outcome" and emptying the audit trail in exactly the case where something
    went wrong.

    Both providers can produce it: neither schema puts `minItems` on
    `causes`, so a `reported` verdict with an empty list is schema-valid.
    Found by code review.
    """
    inconsistent = InvestigationReport.model_construct(
        verdict=ModelVerdict.REPORTED, summary="Nothing found.", causes=[]
    )

    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=_StubProvider(report=inconsistent),
        model="claude-haiku-5-5",
    )

    assert outcome.report is None
    assert outcome.investigation.verdict == "failed"
    assert "inconsistent" in (outcome.investigation.summarized_reasoning or "")

    # **The observed response is recorded**, unlike the ProviderError branch
    # where no response arrived. Writing NULL here would understate what is
    # known about a call that was paid for.
    assert outcome.investigation.model_returned == "claude-haiku-5-5"
    assert outcome.investigation.stop_reason == "end_turn"

    stored = session.scalars(
        select(Investigation).where(Investigation.tenant_id == seeded)
    ).all()
    assert len(stored) == 1


# ---------------------------------------------------------------------------
# CLI findings from code review
# ---------------------------------------------------------------------------


def test_an_unknown_fault_is_a_named_error_not_a_traceback() -> None:
    """Finding 11. Every other bad input in `main()` is one logged line.

    `manifest.fault` raises a bare `KeyError`, which escaped as a traceback
    although the module docstring states one-line errors as the design.
    """
    from reckonai.agent.investigate import UnknownFault, _order_for_fault

    with pytest.raises(UnknownFault) as raised:
        _order_for_fault("F9")

    assert "F9" in str(raised.value)
    assert "F1" in str(raised.value), "the message should list the known faults"
    assert main(["--fault", "F9"]) == 2


def test_impacts_in_different_currencies_are_never_ranked_by_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 8. 500 JPY is not larger than 100 USD.

    The old `max(key=impact.amount)` compared bare Decimals across currencies,
    which specs/mission.md P2 forbids - two amounts are not comparable merely
    because their numbers look alike. `mixed_currency_order` exists precisely
    because one order can carry both.
    """
    from types import SimpleNamespace

    from reckonai.agent import investigate as module
    from reckonai.money import Money

    def finding(code: str, amount: str, currency: str) -> SimpleNamespace:
        return SimpleNamespace(
            code=code,
            order_ref="ORD-X",
            impact=Money(amount, currency),
        )

    usd = finding("commission_mismatch", "100.00", "USD")
    jpy = finding("fx_discrepancy", "500", "JPY")

    monkeypatch.setattr(module, "run", lambda _session, _tenant: [])
    monkeypatch.setattr(module, "exceptions_only", lambda _findings: [usd, jpy])
    chosen = module._exception_for(None, uuid.uuid4(), "ORD-X", None)  # type: ignore[arg-type]

    # Not the JPY one that merely has the bigger number: with no comparable
    # basis it falls back to the engine's own emission order.
    assert chosen is usd


def test_same_currency_impacts_still_rank_worst_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The currency guard must not disable the ranking it protects."""
    from types import SimpleNamespace

    from reckonai.agent import investigate as module
    from reckonai.money import Money

    small = SimpleNamespace(
        code="a", order_ref="ORD-X", impact=Money("3.00", "USD")
    )
    large = SimpleNamespace(
        code="b", order_ref="ORD-X", impact=Money("27.00", "USD")
    )

    monkeypatch.setattr(module, "run", lambda _session, _tenant: [])
    monkeypatch.setattr(module, "exceptions_only", lambda _findings: [small, large])
    chosen = module._exception_for(None, uuid.uuid4(), "ORD-X", None)  # type: ignore[arg-type]

    assert chosen is large


def test_reported_citations_come_back_in_rank_order(
    session: Session, seeded: uuid.UUID
) -> None:
    """Finding 12. Everything else in this block is deterministic.

    The read-back had no ORDER BY, so the reported (and logged) citations came
    back in whatever order Postgres chose, which is a quiet way for Block G's
    cross-provider comparison to see noise that is not in the models.
    """
    report = InvestigationReport(
        verdict=ModelVerdict.REPORTED,
        summary="Two causes.",
        causes=[
            CandidateCause(
                description="First.", confidence=0.8, cited_refs=[ORDER_REF]
            ),
            CandidateCause(
                description="Second.",
                confidence=0.2,
                cited_refs=["INVENTED-A", "INVENTED-B"],
            ),
        ],
    )

    outcome = investigate(
        session,
        seeded,
        order_ref=ORDER_REF,
        provider=_StubProvider(report=report),
        model="claude-haiku-5-5",
    )

    assert outcome.unresolved == ("INVENTED-A", "INVENTED-B")
