"""Runs, and the exceptions they found.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D1 and D2. The table
Block C froze a contract for and deliberately did not build
(`specs/2026-11-09-matching-classification/requirements.md` D8), so that
`reckonai/recon/` stayed pure and the purity gate stayed honest.

**Append-only.** A later run writes new rows and never updates or deletes an
earlier run's. specs/tech-stack.md section 10 requires it, and it is also what
makes the replan's R10 visible: an order parked as `awaiting_evidence` on
Monday and cleared on Tuesday leaves two rows that differ, and that difference
*is* the evidence the re-evaluation happened. An upserted current-state table
would overwrite exactly the row that proves the mechanism works.

**The exception row is the frozen `Finding` contract, column for column.** It
is not a summary of one and not a projection: Block F renders these rows, and a
verdict recomputed at read time would be a second code path reaching a
financial conclusion, which specs/mission.md section 4 forbids.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    money_columns,
    money_pair_check,
)
from reckonai.models.lifecycle import state_check
from reckonai.money import Money

#: How a run ended.
#:
#: **Only `completed` is ever committed today, and that is a consequence of
#: the one-transaction design rather than an oversight.** The handler, the run
#: row and every exception row share a single transaction, so a run that dies
#: partway is rolled back entirely and leaves no row at all - its failure is
#: recorded on the `job` row, which is also the thing that retries it. A
#: `running` row is never visible outside its own transaction either.
#:
#: The vocabulary keeps all three because the states are real and a future
#: partial-run design would need them, and because a `CHECK` is cheaper to
#: keep than to add. Code review found this stated the other way round: a
#: `last_error` column here and docstrings reasoning about failed runs that
#: "may have reconciled some of their window", which this design cannot
#: produce. The column is gone and the reasoning is corrected rather than
#: left to mislead whoever reads the table next.
RUN_STATUSES: tuple[str, ...] = ("running", "completed", "failed")

#: Every conclusion a row may carry, pinned to `reckonai.recon.vocabulary`.
#:
#: Duplicated here as literals rather than imported, on purpose: the models
#: package must not depend on `recon/`, which is a pure package under the
#: purity gate. A test asserts the two agree, so the duplication cannot drift
#: - the same pattern as the manifest and the exception vocabulary.
EXCEPTION_CODES: tuple[str, ...] = (
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

#: How firmly a finding's evidence links its records. Pinned the same way.
EVIDENCE_BASES: tuple[str, ...] = (
    "provider_reported",
    "derived_linkage",
    "amount_within_order",
)


class ReconciliationRun(TenantMixin, TimestampMixin, Base):
    """One execution of the reconciliation, over one date window.

    The run row is what makes a scheduled run visible: R13's lean run summary
    reads it, and without it nobody can see that a cron invocation happened
    and what it changed.
    """

    __tablename__ = "reconciliation_run"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Half-open: `>= window_start`, `< window_end`. Stored so a reader knows
    #: what a run covered rather than inferring it from what it found.
    window_start: Mapped[datetime] = mapped_column(nullable=False)
    window_end: Mapped[datetime] = mapped_column(nullable=False)

    #: The resolved second selection clause (requirements.md D3): orders of any
    #: age holding evidence retrieved since this instant were reconciled too.
    #:
    #: `NULL` on the first run for a tenant, because there is no completed run
    #: to read a `finished_at` from - which is not a special case to code
    #: around, since the clause then selects nothing and the window stands
    #: alone. Stored because it is the difference between "this order was in
    #: the window" and "this order had new evidence", and a reader cannot
    #: reconstruct which clause pulled an order in.
    evidence_since: Mapped[datetime | None] = mapped_column()

    started_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    #: `NULL` while the run is in flight. Never defaulted to the start time: a
    #: run that crashed and one that finished instantly would then be
    #: indistinguishable, and this column is what `evidence_since` is read
    #: from on the next run.
    finished_at: Mapped[datetime | None] = mapped_column()

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="running", index=True
    )

    #: How many orders were selected, so an empty window is distinguishable
    #: from a window nothing was wrong in.
    order_count: Mapped[int] = mapped_column(nullable=False, server_default="0")

    #: Every finding, including the non-exceptions.
    finding_count: Mapped[int] = mapped_column(nullable=False, server_default="0")

    #: The subset that counts toward the false-positive gate. **Counted
    #: through `exceptions_only()`, never `len(findings)`**: `awaiting_evidence`
    #: is a finding and not an exception, so the naive count would fail the
    #: gate while being entirely correct about how many findings there were.
    exception_count: Mapped[int] = mapped_column(nullable=False, server_default="0")

    __table_args__ = (
        state_check("status", RUN_STATUSES, name="status"),
        CheckConstraint("window_end > window_start", name="window_ordered"),
        CheckConstraint("order_count >= 0", name="order_count_not_negative"),
        CheckConstraint("finding_count >= 0", name="finding_count_not_negative"),
        CheckConstraint(
            "exception_count >= 0", name="exception_count_not_negative"
        ),
        # An exception is a finding, so there cannot be more of the subset than
        # of the whole. This is the constraint that would have caught Block C's
        # own trap - counting `len(findings)` into one column and
        # `exceptions_only()` into the other, in the wrong order.
        CheckConstraint(
            "exception_count <= finding_count", name="exceptions_within_findings"
        ),
        UniqueConstraint("tenant_id", "id"),
    )


class ReconciliationException(TenantMixin, TimestampMixin, Base):
    """One classified conclusion about one order, as a persisted row.

    The frozen `Finding` contract, column for column. A test round-trips a
    `Finding` through this table and asserts no field was lost, so the two
    cannot drift apart.

    Named `exception` because that is the product's word for it, including on
    rows where `is_exception` is false: a finding is what the classifier
    returns, and the non-error states are findings that happen not to count.
    Keeping them is not optional - `awaiting_evidence` is how the replan's R10
    is observable at all.
    """

    __tablename__ = "reconciliation_exception"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Which run reached this conclusion. Composite, so a row cannot name
    #: another tenant's run.
    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)

    #: The marketplace's own order reference, not a surrogate id. It is what
    #: the manifest names, what an investigation cites, and what a human reads.
    #:
    #: **Named `order_external_ref`, while the frozen JSON contract's key stays
    #: `order_ref`.** The sim's outward field names are deliberately
    #: non-canonical so that the connector's mapping is genuinely exercised
    #: (the replan's R3), and `order_ref` is one of them -
    #: `tests/test_sim_fields.py` fails if any of them is also a canonical
    #: column name, because a name that is canonical somewhere else is the
    #: near-miss a mapping gets wrong. Claiming `order_ref` here would have
    #: weakened that guard to save a word.
    order_external_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )

    detail: Mapped[str] = mapped_column(Text, nullable=False)

    #: Whether this row counts toward the false-positive gate.
    #:
    #: **Stored rather than derived in SQL.** Block F's queue filters on a
    #: column, and a `WHERE code NOT IN (...)` written at the query site could
    #: disagree with `exceptions_only()` about what counts - which is exactly
    #: the mistake Block C's handoff warns about, one layer out.
    is_exception: Mapped[bool] = mapped_column(nullable=False, index=True)

    #: The magnitude of the difference. Zero on a non-error finding, so a
    #: verdict and its impact can never contradict each other. NOT NULL for
    #: that reason: an absent impact would be a third state the contract does
    #: not have.
    impact: Mapped[Money] = money_columns("impact")

    #: The two figures compared, where the finding came from a comparison.
    #: Both nullable together - a finding that is not a comparison has neither.
    expected: Mapped[Money | None] = money_columns("expected", nullable=True)
    reported: Mapped[Money | None] = money_columns("reported", nullable=True)

    #: External references an investigation should cite. A JSONB array of
    #: strings: real ids only, since Block E is scored on every cited id
    #: resolving to a real record.
    #:
    #: **Not a comma-joined string.** A reference containing the separator
    #: would split into two ids that resolve to nothing, and the failure would
    #: look like bad evidence rather than bad storage.
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)

    evidence_basis: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Nullable: a finding about an order as a whole has no single seller.
    seller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: False where the pairing behind the finding was ambiguous or derived, so
    #: Block F can show a confirmed-versus-suspected distinction without
    #: re-deriving how each pairing was made.
    confirmed: Mapped[bool] = mapped_column(nullable=False, server_default="true")

    __table_args__ = (
        state_check("code", EXCEPTION_CODES, name="code"),
        state_check("evidence_basis", EVIDENCE_BASES, name="evidence_basis"),
        # The amount is a magnitude, and a negative one would make the
        # direction ambiguous - the same reasoning that keeps the ledger
        # amount unsigned. Zero is permitted, because a non-error finding has
        # exactly that.
        CheckConstraint("impact_amount >= 0", name="impact_not_negative"),
        money_pair_check("expected"),
        money_pair_check("reported"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["reconciliation_run.tenant_id", "reconciliation_run.id"],
            ondelete="RESTRICT",
            name="run",
        ),
        UniqueConstraint("tenant_id", "id"),
        # The dashboard's primary read: this run's exceptions, worst first.
        # Block F sorts on impact, and without this it is a sequential scan
        # over every run ever executed.
        Index(
            "ix_reconciliation_exception_run_impact",
            "tenant_id",
            "run_id",
            "impact_amount",
        ),
    )
