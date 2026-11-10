"""The model half: root cause, citations, and the evidence-removed cases.

**It reports and never gates** (requirements D1). One draw per case, on whichever
provider the environment selects. A fault passes if the verdict is `reported`,
the causes together cite every reference the manifest names for it, and none of
what they cite is fabricated. That is a floor and not a proof: a cause can pass
all three and still be wrong in a way only a reader sees, so the cause text is
kept beside the manifest's description for exactly that reading.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reckonai.agent.investigate import investigate
from reckonai.agent.providers import Provider
from reckonai.agent.tools import WITHHOLDABLE
from reckonai.sim import manifest

REPORTED = "reported"
INSUFFICIENT = "insufficient_evidence"

#: The agent produced no usable answer (an outage, a rate limit, a refusal, a
#: missing credential). **That is not a wrong answer and is never scored as
#: one**: counting an outage as a miss would publish it as a model-quality figure.
NO_DRAW = "failed"

#: Faults the agent is asked about, and the two cases with the evidence taken away.
NEGATIVE_CASES = 2


@dataclass(frozen=True)
class CaseScore:
    case: str
    #: What a correct run says: `reported` for a fault, `insufficient_evidence`
    #: when the evidence has been taken away.
    expected_verdict: str
    verdict: str
    passed: bool
    #: False when no answer was produced (`NO_DRAW`): neither a pass nor a miss.
    drawn: bool
    manifest_refs: tuple[str, ...]
    cited_refs: tuple[str, ...]
    missing_refs: tuple[str, ...]
    fabricated_refs: tuple[str, ...]
    unstable_refs: tuple[str, ...]
    top_cause: str | None
    confidence: str | None
    manifest_description: str
    withheld: tuple[str, ...]


def score_case(
    *,
    case: str,
    fault: manifest.Fault,
    expected_verdict: str,
    verdict: str,
    cited_refs: tuple[str, ...],
    fabricated_refs: tuple[str, ...],
    unstable_refs: tuple[str, ...],
    top_cause: str | None,
    confidence: str | None,
    withheld: tuple[str, ...] = (),
) -> CaseScore:
    """Score one investigation. Pure, so it is tested without a model."""
    manifest_refs = tuple(fault.evidence_refs)
    missing = tuple(sorted(set(manifest_refs) - set(cited_refs)))

    drawn = verdict != NO_DRAW
    if expected_verdict == REPORTED:
        passed = verdict == REPORTED and not missing and not fabricated_refs
    else:
        # Evidence removed: the only correct answer is "I cannot tell", and it
        # must not carry a fabricated citation or a stated cause. A `reported`
        # verdict is a conclusion with nothing under it.
        passed = (
            verdict == expected_verdict and not fabricated_refs and top_cause is None
        )

    return CaseScore(
        case=case,
        expected_verdict=expected_verdict,
        verdict=verdict,
        passed=passed,
        drawn=drawn,
        manifest_refs=manifest_refs,
        cited_refs=cited_refs,
        missing_refs=missing,
        fabricated_refs=fabricated_refs,
        unstable_refs=unstable_refs,
        top_cause=top_cause,
        confidence=confidence,
        manifest_description=fault.description,
        withheld=withheld,
    )


def planned_cases() -> list[tuple[str, manifest.Fault, str, tuple[str, ...]]]:
    """Every case the agent half will run: the faults, then the two negatives."""
    plans: list[tuple[str, manifest.Fault, str, tuple[str, ...]]] = [
        (fault.id, fault, REPORTED, ())
        for fault in manifest.FAULTS
        if fault.is_exception
    ]
    f1 = manifest.fault("F1")
    plans.append(("F1, ledger withheld", f1, INSUFFICIENT, ("ledger",)))
    plans.append(
        ("F1, everything withheld", f1, INSUFFICIENT, tuple(sorted(WITHHOLDABLE)))
    )
    return plans


def run(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    provider: Provider,
    model: str,
) -> tuple[CaseScore, ...]:
    """Investigate every investigable fault, then the two negative cases."""
    scores: list[CaseScore] = []

    for case, fault, expected, withheld in planned_cases():
        outcome = investigate(
            session,
            tenant_id,
            order_ref=fault.order_ref,
            provider=provider,
            model=model,
            withhold=withheld,
        )
        causes = outcome.report.ranked() if outcome.report is not None else ()
        cited = tuple(dict.fromkeys(ref for c in causes for ref in c.cited_refs))
        top = causes[0] if causes else None
        scores.append(
            score_case(
                case=case,
                fault=fault,
                expected_verdict=expected,
                verdict=outcome.investigation.verdict,
                cited_refs=cited,
                fabricated_refs=outcome.unresolved,
                unstable_refs=outcome.unstable,
                top_cause=top.description if top else None,
                confidence=f"{top.confidence:.2f}" if top else None,
                withheld=withheld,
            )
        )
    return tuple(scores)


def render(scores: tuple[CaseScore, ...], *, provider: str, model: str) -> str:
    passed = sum(1 for s in scores if s.passed)
    drawn = sum(1 for s in scores if s.drawn)
    lines = [
        f"AGENT (reports, does not gate)   provider {provider}   model {model}",
        "One draw per case. A pass is a floor, not a proof: read the causes.",
        "",
        f"  {'Case':<26}{'Verdict':<24}{'Manifest refs':<15}{'Fabricated':<12}Result",
    ]
    for s in scores:
        cited = len(s.manifest_refs) - len(s.missing_refs)
        refs = (
            f"{cited}/{len(s.manifest_refs)}" if s.expected_verdict == REPORTED else "-"
        )
        lines.append(
            f"  {s.case:<26}{s.verdict:<24}{refs:<15}"
            f"{len(s.fabricated_refs):<12}"
            f"{'pass' if s.passed else ('MISS' if s.drawn else 'NO DRAW')}"
        )
    lines.append("")
    lines.append(f"  {passed} of {drawn} answered cases passed")
    if drawn < len(scores):
        lines.append(
            f"  {len(scores) - drawn} produced no answer (an outage, a refusal or a "
            "missing key) and are not counted as misses"
        )
    for s in scores:
        lines += ["", f"  [{s.case}]  manifest: {s.manifest_description}"]
        if s.top_cause:
            lines.append(f"    agent ({s.confidence} sure): {s.top_cause}")
        else:
            lines.append(f"    agent: no cause given (verdict {s.verdict})")
        if s.missing_refs:
            lines.append(f"    did not cite: {', '.join(s.missing_refs)}")
        if s.fabricated_refs:
            lines.append(f"    fabricated: {', '.join(s.fabricated_refs)}")
    return "\n".join(lines)
