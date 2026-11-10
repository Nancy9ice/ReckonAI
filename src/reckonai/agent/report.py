"""The structured report, and the one shape both providers must return.

specs/tech-stack.md section 5.2's output contract: ranked candidate causes,
per-cause cited record ids, a confidence value, and a required
`insufficient_evidence` branch "so specs/mission.md P5 is representable rather
than something the model has to work around".

**This model is the provider seam** (`requirements.md` D12). Anthropic and
OpenAI differ in how the report is requested and how the tool loop runs;
neither differs in what comes back. A provider that cannot produce this shape
fails the investigation rather than returning a looser one - two reports that
were not comparable would defeat the only reason for having two providers.

**The model cannot declare its own failure.** `ModelVerdict` has two members,
and `failed` is not one of them: that verdict is assigned by
`reckonai.agent.store` when a refusal or an error means no report exists. An
investigation that reached a conclusion reached one of these two; an
investigation that did not has no report to carry a verdict at all.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ModelVerdict(StrEnum):
    """What the agent concluded. Only the model's two possible answers.

    The persisted vocabulary (`reckonai.models.investigation.VERDICTS`) has a
    third member, `failed`, which the system assigns and the model never
    returns.
    """

    #: At least one candidate cause, each citing the records it rests on.
    REPORTED = "reported"

    #: The evidence available does not support a cause.
    #:
    #: **A correct and expected answer**, not a failure (specs/mission.md P5).
    #: Inventing a plausible explanation is the defect; this is the branch
    #: that makes refusing to guess representable. The replan's never-dropped
    #: list protects it, and Block G scores it on its own because it is the
    #: criterion most at risk on a cheaper model.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CandidateCause(BaseModel):
    """One possible explanation, with the evidence it rests on."""

    # `extra="forbid"` emits `additionalProperties: false`, which strict
    # structured outputs require on both providers.
    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        description=(
            "What went wrong, in one or two sentences, stated as a cause "
            "rather than a restatement of the discrepancy."
        )
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How likely this cause is, from 0 to 1. A probability, not a "
            "percentage."
        ),
    )

    cited_refs: list[str] = Field(
        description=(
            "The record references this cause rests on, exactly as the tools "
            "returned them. Every one must be a real reference retrieved from "
            "a tool; never construct, guess or reformat a reference."
        )
    )


class InvestigationReport(BaseModel):
    """What an investigation returns when it reaches a conclusion."""

    model_config = ConfigDict(extra="forbid")

    verdict: ModelVerdict = Field(
        description=(
            "'reported' if you have at least one evidence-backed cause; "
            "'insufficient_evidence' if the records available do not support "
            "one. Returning 'insufficient_evidence' is correct and expected "
            "when evidence is missing - it is never a failure."
        )
    )

    summary: str = Field(
        description=(
            "A short explanation for a finance operator, naming the figures "
            "from the recompute tool rather than any you calculated."
        )
    )

    causes: list[CandidateCause] = Field(
        description=(
            "Candidate causes, most likely first. Empty only when the verdict "
            "is 'insufficient_evidence'."
        )
    )

    def is_consistent(self) -> bool:
        """Whether the verdict and the causes agree.

        A `reported` verdict with no causes is "I found nothing" filed as a
        conclusion, which `reckonai.agent.store` refuses - the database cannot
        express this as a `CHECK` because the causes live in another table.

        `insufficient_evidence` *may* carry causes it considered and rejected,
        so that direction is not constrained.
        """
        return self.verdict is not ModelVerdict.REPORTED or bool(self.causes)

    def ranked(self) -> tuple[CandidateCause, ...]:
        """The causes in the order the model returned them.

        Not re-sorted by `confidence`. The ordering is the model's judgment,
        and re-deriving it from the confidence values would silently reorder
        ties - and would make the stored `rank` disagree with the report the
        model actually produced.
        """
        return tuple(self.causes)
