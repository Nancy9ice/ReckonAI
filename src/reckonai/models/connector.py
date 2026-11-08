"""The connector's own tables: the field mapping, and quarantined records.

specs/tech-stack.md section 9.1 and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D7, D11 and
D13.

**A mapping is data, not code.** A versioned, per-tenant JSONB document
validated by a Pydantic schema, with a `status` and a record of who activated
it and when. Section 9.1's reason for the versioning is that reconciliation
must stay reproducible: editing a mapping would otherwise silently rewrite the
meaning of every past result (specs/mission.md P7).

**Activation is a human action and cannot be performed by a model.** That is
section 9.2's rule and it survives R11's cut of the LLM suggestion pass: the
`llm` provenance value stays in the schema so the pass can be added without a
migration, and so does the rule that an unconfirmed `llm` entry is reviewable
but not activatable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import Base, TenantMixin, TimestampMixin
from reckonai.models.lifecycle import state_check
from reckonai.models.payloads import RawPayloadMixin, raw_payload_fk

#: A mapping's lifecycle. Two states, not three: section 9.1 names `draft` and
#: `active`, and a `stale` *flag* is separate from the status because a stale
#: mapping is still the active one until a human replaces it - demoting it
#: automatically would stop ingestion on a renamed field, which section 9.6
#: explicitly does not want ("schema drift is detected, not absorbed").
MAPPING_STATUSES: tuple[str, ...] = ("draft", "active")

#: Where a mapping entry's value came from. `llm` is retained although R11 cuts
#: the pass that produces it, so adding the pass back is not a migration.
MAPPING_PROVENANCES: tuple[str, ...] = ("exact", "synonym", "llm", "human")

#: Why a record was quarantined rather than normalized.
QUARANTINE_REASONS: tuple[str, ...] = (
    # A required canonical field had no value in the source record.
    "missing_required_field",
    # A money field resolved an amount with no currency, or vice versa.
    "incomplete_money",
    # A declared relationship did not resolve - an item's order reference
    # pointing at an order that is not there.
    "unresolved_reference",
    # A transform raised. A parse failure is a quarantine, never a default.
    "transform_failed",
)


class ConnectorMapping(TenantMixin, TimestampMixin, Base):
    """One version of one tenant's field mapping.

    `document` holds the mapping itself, validated by
    `reckonai.connector.mapping.MappingDocument` before it is written. The
    column is JSONB rather than a set of normalized tables because a mapping is
    a document that is read whole, validated whole and activated whole - and
    section 9.4 is explicit that a mapping never half-activates.

    **`version` is per tenant and monotonic**, and it is what a canonical
    record points back at. See `RawPayload.mapping_version` for where that
    pointer lives and why there is only one of it.
    """

    __tablename__ = "connector_mapping"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Monotonic per tenant, starting at 1.
    version: Mapped[int] = mapped_column(nullable=False)

    #: Which marketplace integration this maps. Free-form: a tenant may run
    #: more than one storefront, and a checked vocabulary here would make
    #: meeting the second one a migration.
    connector: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    document: Mapped[dict] = mapped_column(JSONB, nullable=False)

    #: The hash of the source schema this mapping was validated against.
    #: Section 9.6: "the observed source schema is hashed at ingestion; a
    #: change marks the mapping `stale` and raises it for review rather than
    #: letting a renamed field map to the wrong place."
    source_schema_sha256: Mapped[str | None] = mapped_column(String(64))

    #: Set when the observed schema stops matching the hash above. **A flag,
    #: not a status** - see `MAPPING_STATUSES`.
    is_stale: Mapped[bool] = mapped_column(nullable=False, default=False)

    #: Who activated it, and when. **Both NULL for a draft, both set for an
    #: active mapping**, which the CHECK below enforces rather than trusting.
    #: Section 9.2: a mapping cannot be activated by the model, and the record
    #: of a human action is what makes that auditable rather than asserted.
    activated_by: Mapped[str | None] = mapped_column(String(200))
    activated_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        UniqueConstraint("tenant_id", "connector", "version"),
        UniqueConstraint("tenant_id", "id"),
        state_check("status", MAPPING_STATUSES, name="status"),
        CheckConstraint("version > 0", name="version_positive"),
        # An active mapping has an approver; a draft does not. This is
        # specs/mission.md P1's "AI suggests, code decides" expressed in DDL,
        # so it holds for a row written by a migration or by hand and not only
        # for one written through the service layer.
        CheckConstraint(
            "(status = 'active') = (activated_by IS NOT NULL "
            "AND activated_at IS NOT NULL)",
            name="activation_recorded",
        ),
    )


class QuarantinedRecord(RawPayloadMixin, TenantMixin, TimestampMixin, Base):
    """A source record that could not be normalized, kept visible and countable.

    specs/tech-stack.md section 9.6: "a missing required field quarantines the
    record. It is not defaulted and not skipped silently. A quarantined record
    is visible and countable."

    **This table is why "zero data loss" is checkable.** Without it a record
    that failed to map would be absent from the canonical tables and absent
    from any count, which is indistinguishable from a record that was never
    sent. With it, ingested + quarantined = retrieved, and a gap in that
    identity is a bug rather than a silence.

    It carries a `raw_payload_id` like any other record, NOT NULL. A quarantine
    without its evidence would be a report that something was wrong with a
    record nobody can look at.
    """

    __tablename__ = "quarantined_record"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: Which canonical table the record was headed for.
    target_table: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The source's own reference, so an operator can find it upstream.
    source_reference: Mapped[str | None] = mapped_column(String(128), index=True)

    reason: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    #: Which canonical field was at fault. Section 9.4 requires a failure to
    #: name the offending field rather than report that something went wrong.
    field_name: Mapped[str | None] = mapped_column(String(128))

    detail: Mapped[str] = mapped_column(String(512), nullable=False)

    #: The mapping version in force when this failed, so a quarantine can be
    #: re-evaluated against a corrected mapping rather than re-fetched.
    mapping_version: Mapped[int | None] = mapped_column()

    __table_args__ = (
        raw_payload_fk(),
        UniqueConstraint("tenant_id", "id"),
        state_check("reason", QUARANTINE_REASONS, name="reason"),
    )
