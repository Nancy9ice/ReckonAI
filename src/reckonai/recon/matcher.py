"""Pairing marketplace records with PayPal records on stable identifiers.

`reckonai.models.events` is explicit that this is Block C's work and not the
schema's: "One table per record type holds both sources... **Nothing here links
one to the other**: that link is a *claim* that two records describe one event,
and establishing it is Phase 11's work, with a confidence and an evidence trail
behind it." `Payment.order_id` is NULL on every PayPal-origin row for the same
reason, and says so in its own comment.

**Being unmatched is not a verdict.** specs/mission.md P4: a record with no
counterpart may never have existed, may be late, or may be a genuine
discrepancy, and deciding which is `classifier.py`'s job. This module reports
what pairs and what does not, and nothing else.

Every pairing carries the `EvidenceBasis` it was made on, because the three
available here are not equally strong and a finding that rests on the weakest
must say so (specs/roadmap.md Phase 11).

## The order-to-payment link, and where it actually lives

The identifier that ties a PayPal purchase unit to a marketplace order is
`purchase_units[].reference_id`, written as `f"{order_ref}-{index + 1}"` by
`reckonai.paypal.capture`. **It is in the retained payload and in no canonical
column** - the replay ingester reads the payee, the amounts and the breakdown
out of a purchase unit, but not its reference.

So `order_references` takes the retained order payload as a plain `dict` and
returns the links in it. The caller reads the payload row; this module parses
it. That keeps the package pure while leaving the one real cross-source
identifier available, and it avoids adding a column whose only purpose would be
to restate what the evidence already says.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reckonai.money import Money
from reckonai.recon.vocabulary import EvidenceBasis


@runtime_checkable
class OrderRecordLike(Protocol):
    """The marketplace order. `external_ref` is the matching key."""

    id: uuid.UUID
    external_ref: str
    total: Money


@runtime_checkable
class PaymentRecordLike(Protocol):
    """PayPal's order, whose `external_id` is PayPal's own order id.

    `order_id` is on the row and is always NULL here; it is deliberately not
    read, so this module cannot accidentally depend on a link the ingester
    does not establish.
    """

    id: uuid.UUID
    external_id: str
    amount: Money


@runtime_checkable
class RefundRecordLike(Protocol):
    """A refund from either source.

    Partitioned by the caller rather than by a field here: which source a
    record came from is a property of its `raw_payload` row, and joining to
    that would need a database handle. The caller filters in SQL, which is
    where that filter belongs anyway.
    """

    id: uuid.UUID
    external_id: str
    amount: Money
    capture_id: uuid.UUID | None
    capture_linkage_is_derived: bool
    seller_id: uuid.UUID | None


@runtime_checkable
class LedgerEntryLike(Protocol):
    """A marketplace ledger entry, grouped for presence checks."""

    id: uuid.UUID
    order_id: uuid.UUID | None
    seller_id: uuid.UUID
    entry_category: str
    transaction_type: str
    amount: Money
    external_ref: str


@dataclass(frozen=True, slots=True)
class OrderMatch:
    """One marketplace order paired with PayPal's record of it."""

    order_ref: str
    order_id: uuid.UUID
    payment_id: uuid.UUID
    payment_external_id: str
    basis: EvidenceBasis


@dataclass(frozen=True, slots=True)
class RefundMatch:
    """A marketplace refund paired with PayPal's record of the same refund."""

    marketplace_refund_id: uuid.UUID
    marketplace_external_id: str
    provider_refund_id: uuid.UUID
    provider_external_id: str
    amount: Money
    basis: EvidenceBasis

    #: True when more than one candidate had the same amount on this order, so
    #: the pairing is one of several equally good readings. The classifier must
    #: not report a confirmed finding on an ambiguous pair.
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class MatchSet:
    """What paired, and what did not. The unpaired sides are not verdicts."""

    matched: tuple[OrderMatch, ...] = ()

    #: Marketplace order refs with no PayPal payment. Could be a sale PayPal
    #: never saw, or reporting lag.
    marketplace_only: tuple[str, ...] = ()

    #: PayPal order ids with no marketplace order, keyed by PayPal's id
    #: because there is no marketplace ref to key them by.
    provider_only: tuple[str, ...] = ()

    #: Order refs that more than one PayPal payment claimed. Reported rather
    #: than resolved: picking one would discard evidence.
    contested: tuple[str, ...] = ()


def order_references(order_body: dict[str, object]) -> tuple[str, ...]:
    """Every `purchase_units[].reference_id` in a retained order payload.

    Returns the references as stated. Mapping one back to a marketplace order
    ref is `order_ref_for_reference`, which is kept separate because the
    stated value is evidence and the mapping is an inference.
    """
    units = order_body.get("purchase_units")
    if not isinstance(units, list):
        return ()

    found: list[str] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        reference = unit.get("reference_id")
        if isinstance(reference, str) and reference:
            found.append(reference)
    return tuple(found)


def order_ref_for_reference(reference: str, known_refs: frozenset[str]) -> str | None:
    """Which marketplace order a purchase-unit reference belongs to.

    Two readings are tried, in this order:

      1. the reference *is* an order ref, for a single-unit order whose
         reference was not suffixed
      2. the reference with one trailing `-<digits>` segment removed, which is
         the `f"{order_ref}-{index + 1}"` form

    **Resolved against `known_refs` rather than by stripping blindly**, which
    matters because a marketplace ref is free-form text: an order genuinely
    called `ORD-0009-1` would otherwise be rewritten to `ORD-0009` and its
    records filed against a different seller's order. Checking membership
    first makes the ambiguous case resolve to the reference that actually
    exists, and returns `None` rather than a guess when neither does.
    """
    if reference in known_refs:
        return reference

    head, _, tail = reference.rpartition("-")
    if head and tail.isdigit() and head in known_refs:
        return head

    return None


def match_orders(
    orders: tuple[OrderRecordLike, ...],
    payments: tuple[PaymentRecordLike, ...],
    references: dict[uuid.UUID, tuple[str, ...]],
) -> MatchSet:
    """Pair marketplace orders with PayPal payments.

    `references` maps a payment's id to the purchase-unit references in its
    retained payload - `order_references` applied to the row the caller read.
    A payment absent from the mapping is treated as carrying none, which puts
    it in `provider_only`: the honest answer when the evidence for the link was
    not supplied.

    The basis is always `PROVIDER_REPORTED`. The reference is PayPal's own
    field, echoing back the value the marketplace sent at order creation, so it
    is the strongest link in the dataset - unlike the refund linkage, which has
    to be parsed out of an href.
    """
    by_ref = {order.external_ref: order for order in orders}
    known = frozenset(by_ref)

    claims: dict[str, list[PaymentRecordLike]] = defaultdict(list)
    unlinked: list[str] = []

    for payment in payments:
        resolved = {
            ref
            for reference in references.get(payment.id, ())
            if (ref := order_ref_for_reference(reference, known)) is not None
        }
        if not resolved:
            unlinked.append(payment.external_id)
            continue
        for ref in sorted(resolved):
            claims[ref].append(payment)

    matched: list[OrderMatch] = []
    contested: list[str] = []

    for ref, candidates in claims.items():
        distinct = {payment.id: payment for payment in candidates}
        if len(distinct) > 1:
            contested.append(ref)
            continue
        payment = next(iter(distinct.values()))
        matched.append(
            OrderMatch(
                order_ref=ref,
                order_id=by_ref[ref].id,
                payment_id=payment.id,
                payment_external_id=payment.external_id,
                basis=EvidenceBasis.PROVIDER_REPORTED,
            )
        )

    paired = {match.order_ref for match in matched} | set(contested)

    return MatchSet(
        matched=tuple(sorted(matched, key=lambda m: m.order_ref)),
        marketplace_only=tuple(sorted(known - paired)),
        provider_only=tuple(sorted(unlinked)),
        contested=tuple(sorted(contested)),
    )


def match_refunds(
    marketplace: tuple[RefundRecordLike, ...],
    provider: tuple[RefundRecordLike, ...],
) -> tuple[
    tuple[RefundMatch, ...], tuple[RefundRecordLike, ...], tuple[RefundRecordLike, ...]
]:
    """Pair the two sources' refund records within one order.

    Returns `(matches, marketplace_only, provider_only)`. Both unpaired sides
    are returned because they mean opposite things and neither is an error on
    its own: a marketplace refund with no PayPal record is the reporting lag
    R10 exists for, and a PayPal refund with no marketplace record is a
    refund the ledger has not been told about.

    **Paired on equal amount**, because there is no shared identifier: the
    marketplace carries its own reference and PayPal carries PayPal's. That is
    `EvidenceBasis.AMOUNT_WITHIN_ORDER`, the weakest basis in the vocabulary,
    and every match says so.

    Where two or more refunds on one order share an amount, the pairing is
    arbitrary among them, so each resulting match is flagged `ambiguous`. The
    count is still right - which is what the ledger checks need - but no
    individual pairing should be reported as confirmed.
    """
    pools: dict[tuple[str, str], list[RefundRecordLike]] = defaultdict(list)
    for record in provider:
        pools[_amount_key(record.amount)].append(record)

    duplicated = {key for key, records in pools.items() if len(records) > 1} | {
        key
        for key in pools
        if sum(1 for r in marketplace if _amount_key(r.amount) == key) > 1
    }

    matches: list[RefundMatch] = []
    unmatched_marketplace: list[RefundRecordLike] = []
    taken: set[uuid.UUID] = set()

    # Sorted for determinism: the same inputs must produce the same pairing on
    # every run, or a historical reconciliation is not re-derivable
    # (specs/mission.md P7).
    for record in sorted(marketplace, key=lambda r: r.external_id):
        candidates = [
            candidate
            for candidate in sorted(
                pools.get(_amount_key(record.amount), ()),
                key=lambda r: r.external_id,
            )
            if candidate.id not in taken
        ]
        if not candidates:
            unmatched_marketplace.append(record)
            continue

        counterpart = candidates[0]
        taken.add(counterpart.id)
        matches.append(
            RefundMatch(
                marketplace_refund_id=record.id,
                marketplace_external_id=record.external_id,
                provider_refund_id=counterpart.id,
                provider_external_id=counterpart.external_id,
                amount=record.amount,
                basis=EvidenceBasis.AMOUNT_WITHIN_ORDER,
                ambiguous=_amount_key(record.amount) in duplicated,
            )
        )

    unmatched_provider = [
        record
        for record in sorted(provider, key=lambda r: r.external_id)
        if record.id not in taken
    ]

    return (
        tuple(matches),
        tuple(unmatched_marketplace),
        tuple(unmatched_provider),
    )


def _amount_key(amount: Money) -> tuple[str, str]:
    """An amount as a hashable key.

    The quantized string, not the `Decimal`: `Decimal('20.0')` and
    `Decimal('20.00')` are equal and hash alike, but building the key from the
    raw value would make `20.0` and `20.00` separate pools if the hash ever
    stopped agreeing with equality. The currency is part of the key because
    specs/mission.md P2 - two amounts are not comparable merely because their
    numbers look alike.
    """
    return (str(amount.quantize().amount), amount.currency)


def ledger_by_category(
    entries: tuple[LedgerEntryLike, ...],
) -> dict[tuple[uuid.UUID, str], tuple[LedgerEntryLike, ...]]:
    """Ledger entries grouped by `(order_id, entry_category)`.

    This is the index the "is there a refund entry for this refund?" question
    is asked against - `reckonai.models.lifecycle` says so at
    `LEDGER_ENTRY_CATEGORIES`, and specs/mission.md section 6 Scenario 1 is a
    missing row of one of those categories. Grouping by category is what makes
    an *absence* findable, since there is nothing to look up by.

    Entries with no `order_id` are skipped: a manual adjustment or a periodic
    fee has no order behind it, so it belongs to no order's reconciliation.
    """
    grouped: dict[tuple[uuid.UUID, str], list[LedgerEntryLike]] = defaultdict(list)
    for entry in entries:
        if entry.order_id is None:
            continue
        grouped[(entry.order_id, entry.entry_category)].append(entry)

    return {
        key: tuple(sorted(group, key=lambda e: e.external_ref))
        for key, group in grouped.items()
    }
