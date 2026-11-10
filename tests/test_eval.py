"""The eval: it passes on the seeded data, and it fails when it should.

A gate that has never been seen failing is not a gate, so most of these corrupt the
input on purpose and assert the scorer notices.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data, reconcile
from reckonai.agent import citations
from reckonai.eval import __main__ as eval_main
from reckonai.eval import agent as agent_eval
from reckonai.eval import deterministic
from reckonai.models import Tenant
from reckonai.money import Money
from reckonai.recon import ExceptionCode, Finding
from reckonai.recon.vocabulary import EvidenceBasis
from reckonai.sim import data, manifest


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()
    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None
    return tenant_id


def _score(
    session: Session, tenant_id: uuid.UUID, findings: tuple[Finding, ...]
) -> deterministic.Deterministic:
    resolver = citations.CitationResolver(session, tenant_id)
    return deterministic.score(
        findings,
        faults=manifest.FAULTS,
        clean_orders=[o.ref for o in data.CLEAN_ORDERS],
        resolve=resolver.resolve,
    )


@pytest.fixture
def findings(session: Session, seeded: uuid.UUID) -> tuple[Finding, ...]:
    return reconcile.run(session, seeded)


def test_the_seeded_data_passes_the_gate(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    result = _score(session, seeded, findings)
    assert result.ok
    assert (result.detected, result.impacts_exact, result.currencies_ok) == (6, 6, 6)
    assert result.false_positives == ()
    assert "GATE: PASS" in deterministic.render(result)


def test_a_dropped_finding_fails_detection(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    kept = tuple(f for f in findings if f.order_ref != "ORD-0013")
    result = _score(session, seeded, kept)
    assert not result.ok
    assert result.detected == 5
    assert "GATE: FAIL" in deterministic.render(result)


def test_an_exception_on_a_clean_order_is_a_false_positive(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    clean = data.CLEAN_ORDERS[0].ref
    planted = Finding(
        code=ExceptionCode.COMMISSION_MISMATCH,
        order_ref=clean,
        detail="planted",
        impact=Money("1.00", "USD"),
        evidence_refs=(clean,),
        evidence_basis=EvidenceBasis.PROVIDER_REPORTED,
    )
    result = _score(session, seeded, (*findings, planted))
    assert not result.ok
    assert len(result.false_positives) == 1


def test_a_wrong_impact_fails(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    bent = tuple(
        dataclasses.replace(f, impact=Money("1.00", f.impact.currency))
        if f.order_ref == "ORD-0014"
        else f
        for f in findings
    )
    result = _score(session, seeded, bent)
    assert not result.ok
    assert result.impacts_exact == 5


def test_a_wrong_currency_fails(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    bent = tuple(
        dataclasses.replace(f, impact=Money(f.impact.amount, "JPY"))
        if f.order_ref == "ORD-0013"
        else f
        for f in findings
    )
    result = _score(session, seeded, bent)
    assert not result.ok
    assert result.currencies_ok == 5


def test_a_fabricated_reference_fails(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    bent = tuple(
        dataclasses.replace(f, evidence_refs=(*f.evidence_refs, "GHOST-1"))
        if f.order_ref == "ORD-0013"
        else f
        for f in findings
    )
    result = _score(session, seeded, bent)
    assert not result.ok
    assert result.unresolved_refs == ("GHOST-1",)


def test_a_missing_manifest_reference_fails(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    bent = tuple(
        dataclasses.replace(f, evidence_refs=f.evidence_refs[:1])
        if f.order_ref == "ORD-0016"
        else f
        for f in findings
    )
    assert not _score(session, seeded, bent).ok


# ---- the agent half, scored without a model --------------------------------

F1 = manifest.fault("F1")


def _case(**overrides: object) -> agent_eval.CaseScore:
    args: dict[str, object] = {
        "case": "F1",
        "fault": F1,
        "expected_verdict": agent_eval.REPORTED,
        "verdict": agent_eval.REPORTED,
        "cited_refs": tuple(F1.evidence_refs),
        "fabricated_refs": (),
        "unstable_refs": (),
        "top_cause": "The refund was never booked.",
        "confidence": "0.90",
    }
    args.update(overrides)
    return agent_eval.score_case(**args)  # type: ignore[arg-type]


def test_a_fault_passes_when_it_cites_everything_the_manifest_names() -> None:
    assert _case().passed


def test_a_fault_fails_when_a_manifest_reference_is_not_cited() -> None:
    score = _case(cited_refs=tuple(F1.evidence_refs[:1]))
    assert not score.passed
    assert score.missing_refs


def test_a_fault_fails_on_a_fabricated_citation() -> None:
    assert not _case(fabricated_refs=("GHOST-1",)).passed


def test_a_fault_fails_if_the_agent_gave_up() -> None:
    assert not _case(verdict=agent_eval.INSUFFICIENT).passed


def test_a_reported_verdict_with_the_evidence_removed_is_a_failure() -> None:
    parroting = _case(
        case="F1, ledger withheld",
        expected_verdict=agent_eval.INSUFFICIENT,
        verdict=agent_eval.REPORTED,
    )
    assert not parroting.passed


def test_saying_it_cannot_tell_passes_when_the_evidence_is_removed() -> None:
    honest = _case(
        case="F1, everything withheld",
        expected_verdict=agent_eval.INSUFFICIENT,
        verdict=agent_eval.INSUFFICIENT,
        cited_refs=(),
        top_cause=None,
        confidence=None,
    )
    assert honest.passed


# ---- the command ------------------------------------------------------------


def test_the_command_refuses_a_database_that_is_not_local(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from reckonai.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example.com:5432/reckonai")
    get_settings.cache_clear()
    try:
        assert eval_main.main([]) == 2
    finally:
        get_settings.cache_clear()
    assert "local only" in capsys.readouterr().err


def test_agent_mode_stops_before_any_call_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from reckonai.config import get_settings

    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_MODEL", "claude-haiku-5-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        assert eval_main.main(["--agent"]) == 2
    finally:
        get_settings.cache_clear()
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


# ---- found by the code review ------------------------------------------------


def test_an_exception_on_an_order_nobody_listed_is_a_false_positive(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    """Neither a manifest fault nor a listed clean order: still not allowed."""
    planted = Finding(
        code=ExceptionCode.COMMISSION_MISMATCH,
        order_ref="ORD-UNLISTED",
        detail="planted",
        impact=Money("1.00", "USD"),
        evidence_refs=("ORD-0001",),
        evidence_basis=EvidenceBasis.PROVIDER_REPORTED,
    )
    result = _score(session, seeded, (*findings, planted))
    assert not result.ok
    assert result.false_positives == ("ORD-UNLISTED: commission_mismatch 1.00 USD",)


def test_a_record_cited_twice_is_counted_once(
    session: Session, seeded: uuid.UUID, findings: tuple[Finding, ...]
) -> None:
    baseline = _score(session, seeded, findings).refs_checked
    doubled = tuple(
        dataclasses.replace(f, evidence_refs=(*f.evidence_refs, *f.evidence_refs))
        for f in findings
    )
    assert _score(session, seeded, doubled).refs_checked == baseline


def test_an_outage_is_not_scored_as_a_wrong_answer() -> None:
    outage = _case(verdict=agent_eval.NO_DRAW, cited_refs=(), top_cause=None)
    assert not outage.drawn
    assert not outage.passed
    text = agent_eval.render((outage, _case()), provider="openai", model="gpt-4.1")
    assert "NO DRAW" in text
    assert "1 of 1 answered cases passed" in text
    assert "not counted as misses" in text


def test_an_evidence_removed_case_fails_if_it_carries_a_fabricated_citation() -> None:
    dishonest = _case(
        expected_verdict=agent_eval.INSUFFICIENT,
        verdict=agent_eval.INSUFFICIENT,
        fabricated_refs=("GHOST-1",),
        top_cause=None,
    )
    assert not dishonest.passed


def test_an_evidence_removed_case_fails_if_it_still_states_a_cause() -> None:
    contradicting = _case(
        expected_verdict=agent_eval.INSUFFICIENT,
        verdict=agent_eval.INSUFFICIENT,
        top_cause="The refund was never booked.",
    )
    assert not contradicting.passed


def test_the_call_count_is_the_plan_not_a_constant() -> None:
    plan = agent_eval.planned_cases()
    faults = [p for p in plan if p[2] == agent_eval.REPORTED]
    assert len(plan) == len(faults) + agent_eval.NEGATIVE_CASES
    assert {p[1].id for p in faults} == {
        f.id for f in manifest.FAULTS if f.is_exception
    }


def test_an_agent_half_that_raises_leaves_the_free_half_and_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """The agent half reports and never gates, even when it blows up."""
    import json
    from pathlib import Path

    out = Path(str(tmp_path)) / "r.json"

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(eval_main, "provider_for", lambda _s: object())
    monkeypatch.setattr(agent_eval, "run", boom)

    assert eval_main.main(["--agent", "--json", str(out)]) == 0

    written = json.loads(out.read_text())
    assert written["deterministic"]["ok"] is True
    assert "provider exploded" in written["agent_error"]
