"""Response shapes. Amounts are `MoneyOut`; confidences and scores are strings.

Strings for the unitless figures too: a score or a confidence parsed through a
binary float in the browser would be wrong in the last place, and the client
only ever displays them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from reckonai.schemas.money import MoneyOut


class RunOut(BaseModel):
    id: uuid.UUID
    status: str
    window_start: datetime
    window_end: datetime
    started_at: datetime
    finished_at: datetime | None
    order_count: int
    finding_count: int
    exception_count: int


class DecisionOut(BaseModel):
    id: uuid.UUID
    outcome: str
    approver: str
    note: str | None
    decided_at: datetime
    investigation_id: uuid.UUID | None


class PriorityOut(BaseModel):
    score: str
    #: Each factor in zero-to-one terms; `None` where it does not exist (an
    #: exception no one has investigated has no confidence), never zero.
    factors: dict[str, str | None]


class InvestigationSummary(BaseModel):
    id: uuid.UUID
    verdict: str
    provider: str
    model_requested: str
    started_at: datetime
    #: Confidence of the top-ranked cause; `None` for `insufficient_evidence`
    #: and `failed`, which have none.
    top_confidence: str | None


class ExceptionOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    code: str
    order_ref: str
    detail: str
    #: `confirmed` or `suspected`, from the stored column.
    certainty: str
    impact: MoneyOut
    expected: MoneyOut | None
    reported: MoneyOut | None
    seller_id: uuid.UUID | None
    seller_name: str | None
    seller_count: int
    #: `None` if the order row is absent, never a guessed date.
    order_placed_at: datetime | None
    evidence_refs: list[str]
    evidence_basis: str
    priority: PriorityOut
    investigation: InvestigationSummary | None
    #: The newest decision, or `None` if nobody has decided.
    decision: DecisionOut | None


class CauseOut(BaseModel):
    rank: int
    description: str
    confidence: str
    cited_refs: list[str]
    unresolved_refs: list[str]


class InvestigationDetail(InvestigationSummary):
    downgraded: bool
    stop_reason: str | None
    model_returned: str | None
    summarized_reasoning: str | None
    #: Evidence kinds the agent was not allowed to see. Shown as unavailable,
    #: never as an empty list.
    withheld: list[str]
    finished_at: datetime | None
    causes: list[CauseOut]


class JobOut(BaseModel):
    id: uuid.UUID
    status: str
    attempts: int
    #: Why it failed, where it did: a missing agent credential reads here.
    last_error: str | None
    created_at: datetime


class ExceptionDetail(ExceptionOut):
    investigation_detail: InvestigationDetail | None
    decisions: list[DecisionOut]
    #: The newest investigation job for this exception, so the page can show
    #: "queued" or "failed: <why>" while no investigation row exists yet.
    investigation_job: JobOut | None


class DecisionIn(BaseModel):
    """A human's decision. Approving and rejecting are the same shape."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approved", "rejected"]
    #: Free text, unauthenticated: it records what was typed, not a verified
    #: identity (requirements D5).
    approver: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    note: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None
    ) = None
