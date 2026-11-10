"""Investigations, and the causes they ranked.

`specs/2026-11-10-investigation-agent/requirements.md` D1 and D2. Phase 12's
sixth exit criterion is this pair of tables: the summarized reasoning is
persisted with the report, and the model id from the response is stored and
compared against the model requested.

**Append-only**, for the same reason `reconciliation_run` is. A second
investigation of the same exception writes a second row, and the difference
between the two *is* the record that the verdict changed. An upserted
current-state table would overwrite exactly the evidence that a conclusion was
revised, which specs/tech-stack.md section 10 forbids.

**Causes are rows, not a JSONB document** (D1). Phase 12's second exit criterion
is that every cited id resolves to a real record, and that should be checkable
in SQL rather than by parsing a blob. Block F's evidence trail also renders
citations one at a time.

**The provider is stored next to the model id** rather than inferred from it.
Two providers' model ids are not one namespace, and Block G compares across
them (D12), so a column makes provenance a filter rather than a prefix match.

**Nothing here is a monetary value.** specs/mission.md P1 keeps every figure in
an investigation coming from `reckonai.reconcile`, so a report cites records
and explains amounts it never computed. That is why this module declares no
`money_columns` - there is no amount for the agent to own.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import Base, TenantMixin, TimestampMixin
from reckonai.models.lifecycle import state_check

#: Which LLM provider served an investigation.
#:
#: A checked vocabulary rather than free text, the same reasoning as
#: `RUN_STATUSES`: a typo would become a new provider silently, and this column
#: is part of the audit trail that says which system produced a financial
#: conclusion.
AGENT_PROVIDERS: tuple[str, ...] = ("anthropic", "openai")

#: How an investigation ended.
#:
#: `insufficient_evidence` is a **verdict, not a failure** - specs/mission.md
#: P5 calls "I cannot tell from this evidence" a correct and expected answer,
#: and the replan's never-dropped list protects this branch specifically. It is
#: the criterion most at risk on a cheaper model, which is why Block G scores
#: it on its own rather than folding it into overall detection.
#:
#: `failed` is where a refusal lands. Haiku 5.5 has no server-side refusal
#: fallback, so a `stop_reason: "refusal"` simply stops, and
#: specs/tech-stack.md section 5 decided that such an investigation is
#: surfaced as failed - no report, no cause list, nothing presented as a
#: conclusion.
VERDICTS: tuple[str, ...] = ("reported", "insufficient_evidence", "failed")

#: Every conclusion an investigation may be *about*, pinned to
#: `reckonai.recon.vocabulary`.
#:
#: Duplicated as literals rather than imported, on purpose and for the same
#: reason `reconciliation.EXCEPTION_CODES` is: the models package must not
#: depend on `recon/`, which is pure and under the purity gate. A test asserts
#: the three lists agree, so the duplication cannot drift.
INVESTIGABLE_CODES: tuple[str, ...] = (
    "missing_ledger_adjustment",
    "commission_mismatch",
    "fx_discrepancy",
    "duplicate_ledger_entry",
    "awaiting_evidence",
    "disbursement_mismatch",
    "unresolvable_commission_rule",
    "mixed_currency_order",
    "refund_exceeds_share",
    "refund_unattributable",
    "duplicate_provider_record",
)


class Investigation(TenantMixin, TimestampMixin, Base):
    """One attempt to explain one exception.

    One row per attempt, whatever the outcome. A refused or failed attempt is
    recorded as fully as a successful one: an audit trail that kept only the
    reports that worked would overstate how reliable the agent is.
    """

    __tablename__ = "investigation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Which run surfaced the exception, where one did.
    #:
    #: **Nullable, deliberately.** An investigation is about an exception, not
    #: about a run, and the CLI can be pointed at an order no stored run ever
    #: covered - `reckonai.reconcile.run` returns every finding regardless of
    #: any window, so the agent is not limited to what a scheduled run saw.
    run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: The exception under investigation, by the marketplace's own order
    #: reference plus the code. Named `order_external_ref` for the same reason
    #: `reconciliation_exception` is: `order_ref` is one of the sim's
    #: deliberately non-canonical outward names, and
    #: `tests/test_sim_fields.py` fails if a canonical column claims one.
    order_external_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)

    # ---- The audit trail (specs/tech-stack.md section 5.2) ---------------

    provider: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: What we asked for, from `settings.agent_model`.
    model_requested: Mapped[str] = mapped_column(String(64), nullable=False)

    #: What the response said served it.
    #:
    #: Nullable because a request that never produced a response - a network
    #: failure, an absent credential - has no answer to this, and writing the
    #: requested model here would be asserting something we did not observe
    #: (specs/mission.md P3 applied to provenance rather than to money).
    model_returned: Mapped[str | None] = mapped_column(String(64))

    #: Set where `model_returned` differs from `model_requested`.
    #:
    #: **Nothing in the MVP can set this, and that is the point.** Haiku 5.5
    #: has no server-side refusal fallback and there is no cross-provider
    #: fallback either, so nothing downgrades at all. The column is what
    #: *proves* that claim rather than leaving it asserted; an unflagged
    #: downgrade would make the audit trail lie, which specs/tech-stack.md
    #: section 5.2 calls worse than a failed investigation.
    downgraded: Mapped[bool] = mapped_column(nullable=False, server_default="false")

    #: The provider's own stop reason, read **before** any response content.
    #:
    #: A `refusal` arrives as HTTP 200, so reading content first would process
    #: a refusal as though it were a report. Nullable for the same reason
    #: `model_returned` is.
    stop_reason: Mapped[str | None] = mapped_column(String(32))

    verdict: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    #: The summarized thinking, persisted as supporting context.
    #:
    #: **Never presented as the authoritative cause** - it is a summary, not a
    #: raw chain of thought, and the cited source records are the authority
    #: (specs/mission.md P5). Nullable: a refusal produces none, and a model
    #: run with thinking display omitted would return empty text rather than
    #: a summary.
    summarized_reasoning: Mapped[str | None] = mapped_column(Text)

    #: Which record kinds were withheld from the agent's tools, if any.
    #:
    #: A JSONB array of strings. Stored because an `insufficient_evidence`
    #: verdict is only interpretable alongside what the agent could not see -
    #: without this column, the deliberate evidence gap that proves Phase 12's
    #: third exit criterion would be indistinguishable from a real one.
    withheld: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    started_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    #: `NULL` while in flight. Never defaulted to the start time: an
    #: investigation that crashed and one that returned instantly would
    #: otherwise be indistinguishable.
    finished_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        state_check("provider", AGENT_PROVIDERS, name="provider"),
        state_check("verdict", VERDICTS, name="verdict"),
        state_check("code", INVESTIGABLE_CODES, name="code"),
        # A downgrade claim requires both model ids to compare. Without this,
        # `downgraded` could be true on a row that never observed a response -
        # a flag asserting something the row has no evidence for.
        CheckConstraint(
            "NOT downgraded OR model_returned IS NOT NULL",
            name="downgrade_needs_a_returned_model",
        ),
        # **"A `reported` verdict requires at least one cause" is NOT here**,
        # and the plan asked for it as a `CHECK`. It cannot be one: the causes
        # live in another table and Postgres forbids a subquery in a `CHECK`.
        # The options were a deferred constraint trigger or enforcing it where
        # the rows are written. It is enforced in `reckonai.agent.store`, which
        # writes both tables in one transaction, and pinned by a test - a
        # trigger would put a second financial rule in a second language for a
        # condition one function already owns.
        UniqueConstraint("tenant_id", "id"),
        # The read Block F's investigation detail makes: the latest
        # investigation of one exception. Block D's debt 2 is the same shape
        # one layer out, and the index is added here rather than guessed later
        # because this table is append-only from its first row.
        Index(
            "ix_investigation_latest_per_exception",
            "tenant_id",
            "order_external_ref",
            "code",
            "started_at",
        ),
    )


class InvestigationCause(TenantMixin, TimestampMixin, Base):
    """One ranked candidate cause, with the records it rests on.

    The output contract specs/tech-stack.md section 5.2 requires: ranked
    candidate causes, per-cause cited record ids, and a confidence value.
    """

    __tablename__ = "investigation_cause"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    investigation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    #: 1 is the most likely cause. Ranked rather than scored-and-sorted at read
    #: time, because the ranking is the model's judgment and re-deriving it
    #: from `confidence` would silently reorder ties.
    rank: Mapped[int] = mapped_column(nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)

    #: How confident the agent is in **this cause**.
    #:
    #: This is the second of the three things the replan's R11 calls
    #: "confidence", and the only one in Block E's scope: a candidate cause in
    #: an investigation report. The field-mapping confidence is cut; the
    #: queue-ranking confidence is Block F's.
    #:
    #: **`NUMERIC(4, 3)`, not a binary float**, although this is not a
    #: monetary value and the `float` ban in specs/tech-stack.md section 2 does
    #: not reach it.
    #:
    #: `tests/test_models_schema.py::test_no_column_is_a_binary_float` is
    #: broader than the money rule - it refuses a binary float *anywhere* in
    #: the schema - and it caught this column on the first run. Exempting it
    #: was the alternative, and this is better: three decimals is ample for a
    #: model-reported confidence, exact storage makes Block G's comparison
    #: across providers free of representation drift, and the gate stays
    #: absolute rather than acquiring its first exception.
    #:
    #: The report model carries a plain `float`, which is what the providers'
    #: JSON returns; `reckonai.agent.store` quantizes once at the boundary.
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)

    #: The record ids this cause cites. A JSONB array of strings, for the same
    #: reason `reconciliation_exception.evidence_refs` is one: a reference
    #: containing a separator would split into ids that resolve to nothing,
    #: and the failure would look like bad evidence rather than bad storage.
    cited_refs: Mapped[list] = mapped_column(JSONB, nullable=False)

    #: The references that did **not** resolve to a real record.
    #:
    #: Phase 12's second exit criterion is that every cited id resolves. The
    #: honest way to hold that is to measure it, so an unresolvable citation
    #: is persisted *as* unresolvable rather than silently repaired or
    #: dropped. An empty array is the passing case; a non-empty one is a
    #: fabricated reference, recorded where a reader will find it.
    unresolved_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    __table_args__ = (
        # Rank 1 is the top cause; 0 or a negative rank has no meaning.
        CheckConstraint("rank >= 1", name="rank_is_positive"),
        # A probability, not a percentage or a score. Pinned so a provider
        # returning 0-100 cannot be stored as though it were 0-1 - the two
        # look alike in a column and would make Block G's comparison across
        # providers silently wrong.
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="confidence_is_a_probability"
        ),
        # Two causes cannot claim one rank within an investigation.
        UniqueConstraint("investigation_id", "rank"),
        ForeignKeyConstraint(
            ["tenant_id", "investigation_id"],
            ["investigation.tenant_id", "investigation.id"],
            ondelete="RESTRICT",
            name="investigation",
        ),
        UniqueConstraint("tenant_id", "id"),
    )
