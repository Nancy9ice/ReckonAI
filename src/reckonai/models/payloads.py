"""The raw payload store: the evidence behind every financial record.

specs/mission.md P7 - "source payloads, reconciliation history and
investigation reasoning are retained so any decision can be reconstructed after
the fact". This is the first half of that, and the half every later phase cites
from.

**One central table, not a JSONB column per table**
(specs/2026-11-08-canonical-events/requirements.md D2). A single PayPal capture
payload contains the capture, its platform fee and its conversion, so a column
per table would store it three times and leave nowhere to diff retained
payloads against the model - which specs/roadmap.md Phase 7 requires. Here the
payload is stored once and referenced three times.

**The link from a financial record is NOT NULL**, which is what makes P7
unskippable rather than aspirational: a record inserted without its evidence
fails, instead of becoming a NULL that a schema test has to go looking for
later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from reckonai.models.base import Base, TenantMixin, TimestampMixin
from reckonai.models.lifecycle import state_check

#: Which system a payload came from. The **only** place a record's origin is
#: stored (requirements.md D3): a financial record's source is reached by
#: joining its payload, and is deliberately not repeated on the record, because
#: two places for one fact is the shape of every reconciliation bug this
#: product exists to find.
PAYLOAD_SOURCES: tuple[str, ...] = ("paypal", "marketplace")

#: Length of a hex-encoded SHA-256 digest.
SHA256_HEX_LENGTH = 64


class RawPayload(TenantMixin, TimestampMixin, Base):
    """One retrieved source document, kept verbatim.

    `body` is what the wire actually carried, not what a deserializer made of
    it. specs/paypal-fields.md is blunt about why: PayPal returned three fields
    that neither of its own published contracts declares, and two of them
    stopped being returned a day later. A generated deserializer discards what
    it does not know, silently, so the model is built from the payload rather
    than the payload from the model.

    **This table is tenant-scoped like any other.** It is the one most easily
    rationalised as infrastructure, but it holds a marketplace's financial
    documents, so `tests/test_models_schema.py` scopes it with the rest.
    """

    __tablename__ = "raw_payload"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    source: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: `capture`, `refund`, `order`, `ledger_entry` and so on. Free-form rather
    #: than a checked vocabulary: a new record type is a Phase 7 discovery, and
    #: a `CHECK` here would make meeting one a migration.
    source_record_type: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The source's own identifier for the record, where there is one. NULL for
    #: a list response, which carries many records and no single id.
    source_record_id: Mapped[str | None] = mapped_column(String(128), index=True)

    #: The read path this came from, and it matters more than it looks.
    #:
    #: **Field availability depends on the read path.** specs/roadmap.md Phase 7
    #: requires refunds to be read through the order, because the refund
    #: endpoint omits `platform_fees` for the same record. A payload whose path
    #: is unrecorded cannot be interpreted later: the absence of a field in it
    #: might be PayPal's answer or might be an artefact of where we asked.
    endpoint: Mapped[str | None] = mapped_column(String(256))

    #: When the payload was **fetched**, passed in by the caller rather than
    #: server-defaulted. `created_at` is when the row was written, and a
    #: replayed or backfilled payload does not share the two. Presenting one as
    #: the other is the substitution specs/mission.md P3 forbids.
    retrieved_at: Mapped[datetime] = mapped_column(nullable=False)

    body: Mapped[dict] = mapped_column(JSONB, nullable=False)

    #: Hex SHA-256 of the canonical serialization of `body`. Computed by the
    #: caller, since hashing is not the database's job, and used by the unique
    #: constraint below.
    body_sha256: Mapped[str] = mapped_column(
        String(SHA256_HEX_LENGTH), nullable=False
    )

    #: Which `connector_mapping` version turned this payload into canonical
    #: records. NULL where no mapping was involved - a payload written by the
    #: seed builder, or a PayPal payload, which is normalized by a provider
    #: adapter rather than by a tenant's mapping.
    #:
    #: **This is the single place a canonical record's mapping version is
    #: recorded**, and the reason it is here rather than on every financial
    #: table is the same reason `source` is here: one place for one fact.
    #: specs/tech-stack.md section 9.1 requires every canonical record to carry
    #: the version that produced it, so a historical run stays re-derivable
    #: (specs/mission.md P7) - and every financial record already links to its
    #: payload `NOT NULL`, so the pointer is reachable from all of them through
    #: a join that is guaranteed to resolve. Nine nullable integer columns kept
    #: in step by convention would be nine chances to disagree.
    #:
    #: Recorded as `specs/2026-11-08-block-a-marketplace-connector/
    #: requirements.md` D13, with the rejected alternative.
    mapping_version: Mapped[int | None] = mapped_column()

    __table_args__ = (
        # Composite-foreign-key target, so a financial record's link carries
        # `tenant_id` and a cross-tenant reference is unwritable.
        UniqueConstraint("tenant_id", "id"),
        # **An unchanged re-read stores nothing**, which is what makes Phases 6,
        # 7 and 9 idempotent. A *changed* re-read stores a second row, which is
        # deliberate and is the only way Phase 7's payload diff can see a field
        # that stopped being returned.
        UniqueConstraint(
            "tenant_id", "source", "source_record_type", "body_sha256"
        ),
        state_check("source", PAYLOAD_SOURCES, name="source"),
    )


class RawPayloadMixin:
    """`raw_payload_id`, NOT NULL, on every financial table.

    specs/mission.md P7 and requirements.md D2. The column is supplied here and
    the foreign key by `raw_payload_fk()` below, for the reason
    `LifecycleMixin` gives: a `declared_attr` for `__table_args__` has to merge
    with each table's own, and that merge fails quietly when a table forgets to
    call up the chain. One visible line per table instead, enforced by
    `tests/test_model_registry.py` rather than by remembering.
    """

    @declared_attr
    def raw_payload_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(nullable=False, index=True)


def raw_payload_fk() -> ForeignKeyConstraint:
    """The composite foreign key for `RawPayloadMixin`'s column.

    `ondelete="RESTRICT"`: deleting the evidence under a financial record is
    never something to do silently, and P7 says it is never something to do at
    all.
    """
    return ForeignKeyConstraint(
        ["tenant_id", "raw_payload_id"],
        ["raw_payload.tenant_id", "raw_payload.id"],
        ondelete="RESTRICT",
    )


def capture_or_refund_parent() -> tuple[object, ...]:
    """The `(capture_id, refund_id)` parent pair, with exactly one required.

    `platform_fee` and `fx_conversion` both hang off whichever record they came
    from. A capture carries platform fees and may carry a conversion; a refund
    carries the pro-rata fee reversal and declares an **array** of conversion
    entries. So each table carries both parent columns, nullable, and a `CHECK`
    that exactly one is populated
    (specs/2026-11-08-canonical-events/requirements.md D5).

    Emitted from one call so the two tables cannot spell it differently, and
    returned as a tuple to splice into `__table_args__`.

    Considered and rejected: a generic `(parent_type, parent_id)` pair. It
    extends to a new parent with no migration, and gives up both referential
    integrity and tenant isolation at the database level - the two guarantees
    Phase 2's D7 built the composite keys to get. Adding a third parent site
    here instead costs one column, one foreign key and a rewritten `CHECK`.

    `specs/paypal-fields.md` warns that a *third* site probably exists: a
    refund's `net_amount_breakdown` is recorded **unanswered rather than
    absent**, since no cross-currency refund fixture was creatable.
    """
    return (
        ForeignKeyConstraint(
            ["tenant_id", "capture_id"],
            ["capture.tenant_id", "capture.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "refund_id"],
            ["refund.tenant_id", "refund.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "num_nonnulls(capture_id, refund_id) = 1",
            name="one_parent",
        ),
    )
