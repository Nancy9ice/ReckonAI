"""Normalization: marketplace records into the canonical tables.

specs/roadmap.md Phase 6, and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D9 and D11.

Three properties this file exists to hold:

**Zero data loss.** Every retrieved record becomes either a canonical row or a
`quarantined_record` row. `ingested + quarantined == retrieved` is an identity
the result object reports, and a gap in it is a bug rather than a silence.

**Idempotent.** Re-running inserts nothing new. Upserts key on
`UNIQUE (tenant_id, external_ref)` or `(tenant_id, external_id)`, which is what
Phase 3 built them for, and **a populated value is never overwritten with
NULL** on a re-read - Phase 3's Phase 7 rule, which applies identically here.

**Never defaults money.** A missing amount quarantines the record. Section 9.6:
"defaulting money is how a reconciliation system produces a confident wrong
answer."
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.connector.client import MarketplaceClient, Page
from reckonai.connector.mapping import (
    MappingDocument,
    ResourceMapping,
    TransformError,
    apply_transform,
)
from reckonai.connector.validate import _resolve
from reckonai.models import (
    ConnectorMapping,
    Order,
    OrderItem,
    Payment,
    QuarantinedRecord,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
    Tenant,
)
from reckonai.money import InvalidAmount, Money

#: Target table -> (model, the column its upsert keys on).
#: **`type[Any]`, and that is a statement rather than a shrug.** This is a
#: runtime dispatch table: `_ingest_one` looks a model up from a string, so the
#: class is genuinely not known statically and no TypeVar can recover it -
#: unlike `_index` below, which is called with two known classes.
#:
#: mypy therefore cannot check that each model here is tenant-scoped and
#: carries its named reference column, which is exactly what
#: `_ingest_one` relies on. `tests/test_connector_ingest.py::
#: test_every_target_is_tenant_scoped_with_its_reference_column` asserts both
#: at runtime instead - a **stronger** check than the annotation, because it
#: verifies the columns exist rather than that a type says they might.
#: `specs/2026-11-09-entitlement-engine/validation.md` D9 records why the
#: static check cannot cross the ORM boundary.
TARGETS: dict[str, tuple[type[Any], str]] = {
    "seller": (Seller, "external_ref"),
    "order": (Order, "external_ref"),
    "order_item": (OrderItem, "external_ref"),
    "seller_ledger_entry": (SellerLedgerEntry, "external_ref"),
    "payment": (Payment, "external_id"),
    "refund": (Refund, "external_id"),
}

#: The order resources must be ingested in, so a reference resolves when it is
#: needed. **Not derived from the mapping's own ordering**, which a tenant
#: controls and could get wrong, and not from foreign keys either - Phase 3's
#: finding is that flush order between unrelated mappers is not derived from
#: them, and the same reasoning applies to resolution order here.
INGEST_ORDER: tuple[str, ...] = (
    "seller",
    "order",
    "order_item",
    "payment",
    "refund",
    "seller_ledger_entry",
)


@dataclass
class IngestResult:
    """What one run did, in enough detail to prove zero data loss."""

    tenant_id: uuid.UUID
    mapping_version: int

    retrieved: Counter[str] = field(default_factory=Counter)
    inserted: Counter[str] = field(default_factory=Counter)
    updated: Counter[str] = field(default_factory=Counter)
    quarantined: Counter[str] = field(default_factory=Counter)

    @property
    def total_retrieved(self) -> int:
        return sum(self.retrieved.values())

    @property
    def total_written(self) -> int:
        return sum(self.inserted.values()) + sum(self.updated.values())

    @property
    def total_quarantined(self) -> int:
        return sum(self.quarantined.values())

    def accounts_for_everything(self) -> bool:
        """The identity that makes "zero data loss" checkable.

        Every retrieved record is either written or quarantined. Nothing is
        skipped, and nothing is counted twice.
        """
        return self.total_retrieved == self.total_written + self.total_quarantined


def active_mapping(
    session: Session, tenant_id: uuid.UUID, connector: str
) -> ConnectorMapping:
    """The active mapping row, or a refusal.

    **Ingestion will not run on a draft.** Section 9.2: activation is a human
    action, recorded with who and when, and a mapping a model produced cannot
    activate itself. Reading a draft here would route around that.
    """
    found = session.scalar(
        select(ConnectorMapping).where(
            ConnectorMapping.tenant_id == tenant_id,
            ConnectorMapping.connector == connector,
            ConnectorMapping.status == "active",
        )
    )
    if found is None:
        raise LookupError(
            f"no active mapping for connector {connector!r}. A draft is not "
            "enough: activation is a human action and ingestion will not "
            "route around it."
        )
    return found


def _digest(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload_for(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    record_type: str,
    record_id: str | None,
    body: dict[str, Any],
    endpoint: str,
    mapping_version: int,
    retrieved_at: datetime,
) -> RawPayload:
    """Write or reuse the `raw_payload` row for one retrieved record.

    **Reused on an unchanged re-read**, which is half of what makes this
    idempotent: `UNIQUE (tenant_id, source, source_record_type, body_sha256)`
    means an identical body is the same row. A *changed* body writes a second
    row, deliberately, and that is the mechanism Block D's payload diff needs.

    `endpoint` is recorded because **field availability depends on the read
    path** - the absence of a field in a payload might be the source's answer
    or might be an artefact of where we asked. The seed builder leaves it NULL
    because there was no read path; here there is one.
    """
    digest = _digest(body)
    existing = session.scalar(
        select(RawPayload).where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source == "marketplace",
            RawPayload.source_record_type == record_type,
            RawPayload.body_sha256 == digest,
        )
    )
    if existing is not None:
        return existing

    row = RawPayload(
        tenant_id=tenant_id,
        source="marketplace",
        source_record_type=record_type,
        source_record_id=record_id,
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        body=body,
        body_sha256=digest,
        mapping_version=mapping_version,
    )
    session.add(row)
    session.flush()
    return row


@dataclass
class _Resolution:
    """What one record's mapping produced, or why it could not."""

    values: dict[str, Any] = field(default_factory=dict)
    failure: tuple[str, str | None, str] | None = None


def _apply_mapping(
    resource: ResourceMapping, record: dict[str, Any]
) -> _Resolution:
    """Turn one source record into canonical values, or report the failure.

    Returns on the **first** failure rather than collecting them all, which is
    the opposite of the validation gate's behaviour and deliberately so: the
    gate is run once by a human fixing a document and wants the whole list,
    while this runs per record and the first reason is the one that goes on the
    quarantine row.
    """
    out = _Resolution()

    for entry in resource.fields:
        raw = _resolve(record, entry.source_path)
        if raw is None:
            if entry.canonical in resource.required_fields:
                return _Resolution(
                    failure=(
                        "missing_required_field",
                        entry.canonical,
                        f"{entry.source_path!r} is absent from the record",
                    )
                )
            out.values[entry.canonical] = None
            continue
        try:
            out.values[entry.canonical] = apply_transform(
                entry.transform, raw, enum_map=entry.enum_map
            )
        except TransformError as exc:
            return _Resolution(
                failure=("transform_failed", entry.canonical, str(exc))
            )

    for money in resource.money_fields:
        amount = _resolve(record, money.amount_path)
        currency = _resolve(record, money.currency_path)

        if amount is None and currency is None:
            if money.canonical in resource.required_fields:
                return _Resolution(
                    failure=(
                        "missing_required_field",
                        money.canonical,
                        f"{money.amount_path!r} is absent from the record",
                    )
                )
            out.values[money.canonical] = None
            continue

        # **Half a pair is a quarantine, not a zero and not a bare amount.**
        if amount is None or currency is None:
            return _Resolution(
                failure=(
                    "incomplete_money",
                    money.canonical,
                    (
                        f"resolved amount={amount!r} currency={currency!r}. "
                        "An amount without its currency is a hard failure, "
                        "never a default."
                    ),
                )
            )

        try:
            parsed = apply_transform("decimal", amount)
            code = apply_transform("currency", currency)
            out.values[money.canonical] = Money(parsed, code)
        except (TransformError, InvalidAmount) as exc:
            return _Resolution(
                failure=("transform_failed", money.canonical, str(exc))
            )

    return out


def ingest(
    session: Session,
    client: MarketplaceClient,
    *,
    tenant_id: uuid.UUID,
    document: MappingDocument,
    mapping_version: int,
    retrieved_at: datetime | None = None,
) -> IngestResult:
    """Read every mapped resource and normalize it into the canonical tables.

    Resources are processed in `INGEST_ORDER`, so a reference resolves by the
    time something needs it, and the session is flushed between resources for
    the reason Phase 3 recorded: with no `relationship()` declared anywhere,
    SQLAlchemy's unit of work has no mapper dependency to sort on.
    """
    when = retrieved_at or datetime.now(UTC)
    result = IngestResult(tenant_id=tenant_id, mapping_version=mapping_version)

    # Reference indexes, filled as each resource lands.
    sellers: dict[str, uuid.UUID] = {}
    orders: dict[str, uuid.UUID] = {}

    by_table = {r.target_table: r for r in document.resources}

    for table in INGEST_ORDER:
        resource = by_table.get(table)
        if resource is None:
            continue

        page = client.fetch(resource.source_endpoint)
        result.retrieved[table] = len(page.records)

        for record in page.records:
            _ingest_one(
                session,
                resource,
                record,
                page=page,
                tenant_id=tenant_id,
                mapping_version=mapping_version,
                retrieved_at=when,
                result=result,
                sellers=sellers,
                orders=orders,
            )

        session.flush()

        if table == "seller":
            sellers = _index(session, tenant_id, Seller, "external_ref")
        elif table == "order":
            orders = _index(session, tenant_id, Order, "external_ref")

    return result


def _index[IndexedT: (Seller, Order)](
    session: Session, tenant_id: uuid.UUID, model: type[IndexedT], column: str
) -> dict[str, uuid.UUID]:
    """Map one external reference column to the row ids that carry it.

    **The type parameter is value-constrained to the two models this is
    called with**, and both alternatives were tried. `type[Base]` loses
    `tenant_id`, which `TenantMixin` supplies through `declared_attr` and mypy
    therefore cannot see on the class. A Protocol hits the
    `Mapped[X]`-versus-settable-member conflict recorded in
    `specs/2026-11-09-entitlement-engine/validation.md` D9. Constraining to
    the concrete classes resolves `tenant_id` and `id` on each, with no
    suppression.

    This was the error Block B flagged as real and assigned to "Block C or D";
    neither took it. See
    `specs/2026-11-10-investigation-agent/validation.md` section 9.
    """
    rows = session.scalars(select(model).where(model.tenant_id == tenant_id))
    return {getattr(row, column): row.id for row in rows}


def _ingest_one(
    session: Session,
    resource: ResourceMapping,
    record: dict[str, Any],
    *,
    page: Page,
    tenant_id: uuid.UUID,
    mapping_version: int,
    retrieved_at: datetime,
    result: IngestResult,
    sellers: dict[str, uuid.UUID],
    orders: dict[str, uuid.UUID],
) -> None:
    table = resource.target_table
    model, key_column = TARGETS[table]

    payload = _payload_for(
        session,
        tenant_id=tenant_id,
        record_type=table,
        record_id=_source_reference(resource, record),
        body=record,
        endpoint=page.endpoint,
        mapping_version=mapping_version,
        retrieved_at=retrieved_at,
    )

    resolution = _apply_mapping(resource, record)

    if resolution.failure is not None:
        reason, field_name, detail = resolution.failure
        _quarantine(
            session,
            tenant_id=tenant_id,
            payload=payload,
            table=table,
            source_reference=_source_reference(resource, record),
            reason=reason,
            field_name=field_name,
            detail=detail,
            mapping_version=mapping_version,
        )
        result.quarantined[table] += 1
        return

    values = resolution.values

    # Resolve declared relationships from reference to primary key.
    for canonical, target in resource.relationships.items():
        reference = values.get(canonical)
        if reference is None:
            continue
        index = sellers if target == "seller" else orders
        resolved = index.get(reference)
        if resolved is None:
            _quarantine(
                session,
                tenant_id=tenant_id,
                payload=payload,
                table=table,
                source_reference=_source_reference(resource, record),
                reason="unresolved_reference",
                field_name=canonical,
                detail=(
                    f"references {reference!r} in {target!r}, which has not "
                    "been ingested. Section 9.5: an inferred or unresolved "
                    "link may not silently drive a financial conclusion."
                ),
                mapping_version=mapping_version,
            )
            result.quarantined[table] += 1
            return
        values[canonical] = resolved

    key_value = values.get(key_column)
    if key_value is None:
        _quarantine(
            session,
            tenant_id=tenant_id,
            payload=payload,
            table=table,
            source_reference=None,
            reason="missing_required_field",
            field_name=key_column,
            detail=f"{key_column} is what the upsert keys on and it resolved to None",
            mapping_version=mapping_version,
        )
        result.quarantined[table] += 1
        return

    existing = session.scalar(
        select(model).where(
            model.tenant_id == tenant_id,
            getattr(model, key_column) == key_value,
        )
    )

    if existing is None:
        session.add(
            model(
                tenant_id=tenant_id,
                raw_payload_id=payload.id,
                **_for_insert(table, values),
            )
            if _carries_payload(table)
            else model(tenant_id=tenant_id, **_for_insert(table, values))
        )
        session.flush()
        result.inserted[table] += 1
        return

    # **A populated value is never overwritten with NULL.** Phase 3's rule for
    # Phase 7, and it applies identically here: the absence of a field in a
    # later read is not evidence that the value went away.
    changed = False
    for name, value in _for_insert(table, values).items():
        if value is None:
            continue
        if getattr(existing, name) != value:
            setattr(existing, name, value)
            changed = True

    if changed:
        session.flush()
        result.updated[table] += 1
    else:
        # An unchanged re-read is neither an insert nor an update, and still
        # has to be accounted for or `accounts_for_everything` breaks on the
        # second run. It counts as written: the row is there and correct.
        result.updated[table] += 1


def _carries_payload(table: str) -> bool:
    """Whether the target table has a `raw_payload_id`.

    The catalogue tables do not: Phase 2's five hold no financial record. The
    event tables do, `NOT NULL`.
    """
    return table not in {"seller", "order", "order_item", "commission_rule"}


def _for_insert(table: str, values: dict[str, Any]) -> dict[str, Any]:
    """Drop keys the model does not carry, and supply what it requires.

    `refund` is the one with a required column no source field maps to:
    `capture_linkage_is_derived` is `NOT NULL`, and a marketplace refund has no
    capture link at all - so the honest value is `False`, meaning "no linkage
    was derived", not `True`, which would claim a derivation that never ran.
    """
    out = {k: v for k, v in values.items() if v is not None}

    if table == "refund":
        out.setdefault("capture_linkage_is_derived", False)

    return out


def _source_reference(
    resource: ResourceMapping, record: dict[str, Any]
) -> str | None:
    """The source's own identifier, for a quarantine an operator must chase."""
    for entry in resource.fields:
        if entry.canonical in {"external_ref", "external_id"}:
            value = _resolve(record, entry.source_path)
            if value is not None:
                return str(value)
    return None


def _quarantine(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    payload: RawPayload,
    table: str,
    source_reference: str | None,
    reason: str,
    field_name: str | None,
    detail: str,
    mapping_version: int,
) -> None:
    """Record a failure to normalize, visibly and countably.

    The record is **not** skipped and **not** defaulted. Its payload is already
    written, so the evidence survives and the gap is recoverable once the
    mapping is corrected - section 9.6: "an unmapped source field is not lost."
    """
    session.add(
        QuarantinedRecord(
            tenant_id=tenant_id,
            raw_payload_id=payload.id,
            target_table=table,
            source_reference=source_reference,
            reason=reason,
            field_name=field_name,
            detail=detail[:512],
            mapping_version=mapping_version,
        )
    )
    session.flush()


def ensure_tenant(session: Session, *, slug: str, name: str) -> uuid.UUID:
    """The tenant the connector ingests into, created if absent.

    A **separate** tenant from the one the sim serves (requirements.md D2),
    which is what makes a row-count comparison between the two meaningful
    rather than circular.
    """
    found = session.scalar(select(Tenant).where(Tenant.slug == slug))
    if found is not None:
        return found.id

    tenant = Tenant(name=name, slug=slug)
    session.add(tenant)
    session.flush()
    return tenant.id
