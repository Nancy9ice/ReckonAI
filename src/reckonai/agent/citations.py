"""Resolving a cited reference to a real record.

Phase 12's second exit criterion: "Every cited id resolves to a real record -
no fabricated references." This module is how that is **measured** rather than
asserted. A report citing an id that names nothing is persisted with that fact
recorded (`investigation_cause.unresolved_refs`), not silently repaired and not
discarded - the criterion is about honesty, and quietly fixing a fabricated
citation would hide exactly the defect it exists to catch.

**Resolution and stability are two different questions**, and conflating them
was the first mistake available here.

`platform_fee.external_id` is built as `f"pf-{payload_uuid}-{index}"` and
changes on every seed. It is a **real record** - it resolves - but it is not a
durable reference, so a report that cites it names evidence nobody can find
again after a reseed. Block C's handoff states the rule as "cite the capture,
never `platform_fee.external_id`", and the classifier already cites the capture
*beside* the fee for this reason.

So a resolution carries both answers. `resolved` says the id names a record;
`stable` says the id will still name it tomorrow. The exit criterion is about
the first; the citation rule is about the second, and keeping them apart is
what lets both be checked without either weakening the other. Rejecting fee
ids outright was the alternative and it would have been wrong twice over: it
would report a real record as fabricated, and it would fail on the
classifier's own findings.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from reckonai.models import (
    Capture,
    Disbursement,
    Order,
    OrderItem,
    Payment,
    PlatformFee,
    Refund,
    SellerLedgerEntry,
)

#: What kind of record a reference names. Plain strings rather than an enum:
#: these reach a JSONB column and a model-facing tool description, where a
#: `StrEnum` would serialize the same and buy nothing.
ORDER = "order"
ORDER_ITEM = "order_item"
PAYMENT = "payment"
CAPTURE = "capture"
REFUND = "refund"
LEDGER_ENTRY = "seller_ledger_entry"
DISBURSEMENT = "disbursement"
PLATFORM_FEE = "platform_fee"

#: Record kinds whose external reference is rebuilt on every ingestion and so
#: cannot be used to find the record again.
#:
#: Exactly one member today. Kept as a set rather than a comparison against
#: one name so that adding a second unstable reference is a one-line change
#: here instead of a condition somewhere downstream.
_UNSTABLE_KINDS: frozenset[str] = frozenset({PLATFORM_FEE})


@dataclass(frozen=True)
class Citation:
    """One cited reference, and what it turned out to name."""

    ref: str

    #: `None` where the reference names no record for this tenant - a
    #: fabricated citation.
    kind: str | None

    @property
    def resolved(self) -> bool:
        """Whether this reference names a real record.

        Phase 12's second exit criterion counts through this.
        """
        return self.kind is not None

    @property
    def stable(self) -> bool:
        """Whether the reference will still name that record after a reseed.

        An unresolved reference is not stable either - there is nothing for it
        to be stable about - so this is never true where `resolved` is false.
        """
        return self.resolved and self.kind not in _UNSTABLE_KINDS


class CitationResolver:
    """Resolves references for one tenant, over one snapshot of the records.

    Every lookup table is loaded once, lazily, on first use. An investigation
    resolves a handful of references per cause across several causes, and
    issuing a query per reference would make the citation check more expensive
    than the reconciliation it is checking.

    **Scoped to one tenant**, which the conventions require and which matters
    more than usual here: a reference that resolved against another tenant's
    records would report a fabricated citation as genuine.
    """

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def resolve(self, ref: str) -> Citation:
        """What `ref` names, or a `Citation` whose `kind` is `None`.

        Whitespace is stripped, because a model emitting `"ORD-0001 "` has
        cited a real record and a resolver that said otherwise would be
        reporting its own parsing as a fabrication. Nothing else is
        normalised - case is not folded, because PayPal ids are
        case-sensitive and folding them could make two distinct captures look
        like one.
        """
        candidate = ref.strip()
        if not candidate:
            return Citation(ref=ref, kind=None)

        return Citation(ref=ref, kind=self._index.get(candidate))

    def unresolved(self, refs: tuple[str, ...]) -> tuple[str, ...]:
        """Those of `refs` that name no record, in the order given.

        The shape `investigation_cause.unresolved_refs` stores. Order is
        preserved so a reader can line the column up against `cited_refs`.
        """
        return tuple(ref for ref in refs if not self.resolve(ref).resolved)

    def unstable(self, refs: tuple[str, ...]) -> tuple[str, ...]:
        """Those of `refs` that resolve but will not survive a reseed.

        Separate from `unresolved` so the citation rule and the exit criterion
        are measured independently.
        """
        return tuple(
            ref
            for ref in refs
            if self.resolve(ref).resolved and not self.resolve(ref).stable
        )

    @cached_property
    def _index(self) -> dict[str, str]:
        """Every citable reference for this tenant, mapped to its record kind.

        Built in one pass per table. Where two tables somehow carried the same
        external reference the **first** wins, and the order below puts the
        stable kinds first on purpose: if a fee id ever collided with a
        capture id, resolving it as the capture is the answer that keeps a
        citation findable.
        """
        index: dict[str, str] = {}

        sources: tuple[tuple[str, InstrumentedAttribute[str]], ...] = (
            (ORDER, Order.external_ref),
            (ORDER_ITEM, OrderItem.external_ref),
            (PAYMENT, Payment.external_id),
            (CAPTURE, Capture.external_id),
            (REFUND, Refund.external_id),
            (LEDGER_ENTRY, SellerLedgerEntry.external_ref),
            (DISBURSEMENT, Disbursement.external_id),
            (PLATFORM_FEE, PlatformFee.external_id),
        )

        for kind, column in sources:
            for reference in self._session.scalars(
                select(column).where(column.class_.tenant_id == self._tenant_id)
            ):
                index.setdefault(reference, kind)

        return index
