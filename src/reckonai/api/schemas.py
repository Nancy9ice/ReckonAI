"""Response shapes. Amounts are `MoneyOut`; confidences and scores are strings.

Strings for the unitless figures too: a score or a confidence parsed through a
binary float in the browser would be wrong in the last place, and the client
only ever displays them.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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


#: Generous for a real grid schema, small enough to refuse a flood.
MAX_ASSISTANT_BYTES = 200_000


class GridAssistantIn(BaseModel):
    """A sentence plus the grid's own schema and state, from AG Grid's toolkit."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    state: dict[str, Any]
    #: Named `grid_schema` because `schema` shadows a pydantic attribute.
    grid_schema: dict[str, Any] = Field(alias="schema")

    @model_validator(mode="after")
    def _bounded(self) -> GridAssistantIn:
        """The whole body is forwarded to a paid model, so its size is capped."""
        size = len(json.dumps(self.state)) + len(json.dumps(self.grid_schema))
        if size > MAX_ASSISTANT_BYTES:
            raise ValueError("the grid state and schema are too large")
        return self


class GridAssistantOut(BaseModel):
    gridState: dict[str, Any]  # noqa: N815 - the toolkit's own key names
    propertiesToIgnore: list[str]  # noqa: N815
    explanation: str


class EvidenceField(BaseModel):
    label: str
    #: `None` with no `money` means the record does not state it, which is
    #: shown as "not stated" and is never a zero.
    value: str | None = None
    money: MoneyOut | None = None


class RawPayloadSummary(BaseModel):
    source: str
    record_type: str
    retrieved_at: datetime
    endpoint: str | None
    sha256: str


class EvidenceOut(BaseModel):
    ref: str
    kind: str
    #: False for a reference that resolves but will not find the record again
    #: after a reseed (a platform fee id).
    stable: bool
    fields: list[EvidenceField]
    raw: RawPayloadSummary | None


class ParkedOut(BaseModel):
    id: uuid.UUID
    order_ref: str
    detail: str
    run_id: uuid.UUID


class CleanOrderOut(BaseModel):
    order_ref: str
    total: MoneyOut
    placed_at: datetime
    sellers: list[str]


class CoverageOut(BaseModel):
    orders_total: int
    with_exceptions: int
    parked: int
    clean: int
    #: Orders no completed run has looked at since they arrived. Not clean: unknown.
    not_examined: int
    not_examined_orders: list[str]
    parked_items: list[ParkedOut]
    clean_orders: list[CleanOrderOut]
    #: Said plainly, because it bounds the claim: a run records how many orders
    #: it covered, not which, so this reads the newest findings per order.
    basis: str


class RunRequestOut(BaseModel):
    #: queued, in_flight, already_done or no_orders.
    status: str
    window_start: datetime | None
    window_end: datetime | None
    job: JobOut | None
    message: str


class BulkInvestigateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Bounded: each one is a paid model call.
    exception_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=100)]
    #: "Investigate all" skips what already has a conclusion; checking specific
    #: rows does not, because choosing a row is asking for it again.
    skip_settled: bool = False


class BulkInvestigateOut(BaseModel):
    queued: int
    in_flight: int
    skipped: int
    not_found: int
    jobs: list[JobOut]
