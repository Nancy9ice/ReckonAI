"""The seed builder, and the consistency check that makes its output trustworthy.

`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D6 and D5a,
amended by the replan's R16.

**This writes the marketplace's books and nothing else.** Orders, items,
sellers, commission rules, the seller ledger, and the marketplace's own
payment and refund records. It writes no capture, no platform fee, no
conversion and no disbursement: those are PayPal's, they are captured in
`fixtures/paypal/`, and `reckonai.paypal.replay` loads them. Block A's
synthetic provider side (its D12) is gone - R16 retired it rather than
scheduling its removal.

**`verify()` is not optional.** Phase 2's D13 ties no constraint between an
order's total and the sum of its items, because a marketplace whose own
records disagree is what this product exists to find. The consequence is that
the clean baseline has **no database backstop**: a silent inconsistency would
be measured by Block C as a false positive forever. So `verify()` recomputes
every figure `data.py` states as a literal, using the real `reckonai.engine`
resolver, and raises naming the row.

**Three things bite a seed builder here**, all inherited from Phase 3:

  1. The `raw_payload` link is **NOT NULL** on every financial table.
  2. `seller_ledger_entry.amount` is **unsigned**, `CHECK (> 0)`, and needs a
     `transaction_type`.
  3. **Flush order between unrelated mappers is not derived from foreign
     keys.** No `relationship()` is declared anywhere, so SQLAlchemy's unit of
     work has no mapper dependency to sort on. Every stage flushes explicitly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.engine.entitlement import expected_entitlement
from reckonai.engine.ledger import position, signed
from reckonai.models import (
    CommissionRule,
    Order,
    OrderItem,
    Payment,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
    Tenant,
)
from reckonai.money import Money
from reckonai.sim import data
from reckonai.sim.data import Dataset, OrderSpec


class SeedInconsistency(AssertionError):
    """The seeded dataset disagrees with the figures `data.py` states.

    An `AssertionError` subclass so a failure reads as a broken invariant.
    Nothing should handle it: it means the baseline is not clean.
    """


@dataclass
class SeedResult:
    tenant_id: uuid.UUID
    seller_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    order_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    rule_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    refund_ids: dict[str, uuid.UUID] = field(default_factory=dict)


#: The `endpoint` stamped on a marketplace payload. `None` for the seed, which
#: has no read path. The live write side sets it for the duration of a call, so
#: a record it created is labelled simulated at the record level
#: (`specs/2026-11-11-live-ingestion/requirements.md` F14, D4).
ENDPOINT_MARKER: ContextVar[str | None] = ContextVar("seed_endpoint", default=None)


def _digest(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    record_type: str,
    record_id: str,
    body: dict,
    retrieved_at: datetime,
) -> RawPayload:
    """One `raw_payload` row for a marketplace record, flushed.

    **The body carries its own record id, and must.** `raw_payload` is unique
    on `(tenant_id, source, source_record_type, body_sha256)`, so two
    different records with byte-identical bodies collide - which happened
    immediately when two orders shared a commission figure. Embedding the id
    is also what a real response does.

    `endpoint` is `NULL`: Phase 3 added that column because field
    availability depends on the read path, and a row the builder wrote
    directly had no read path. That is what tells a seeded row from one the
    connector fetched.
    """
    full = {"id": record_id, **body}
    row = RawPayload(
        tenant_id=tenant_id,
        source="marketplace",
        source_record_type=record_type,
        source_record_id=record_id,
        endpoint=ENDPOINT_MARKER.get(),
        retrieved_at=retrieved_at,
        body=full,
        body_sha256=_digest(full),
    )
    session.add(row)
    session.flush()
    return row


def build(
    session: Session,
    dataset: Dataset | None = None,
    *,
    tenant_slug: str = data.TENANT_SLUG,
    tenant_name: str = data.TENANT_NAME,
) -> SeedResult:
    """Write the marketplace's dataset and return what was written."""
    spec = dataset if dataset is not None else data.CLEAN

    tenant = Tenant(name=tenant_name, slug=tenant_slug)
    session.add(tenant)
    session.flush()
    result = SeedResult(tenant_id=tenant.id)

    for seller_spec in spec.sellers:
        seller = Seller(
            tenant_id=tenant.id,
            external_ref=seller_spec.ref,
            name=seller_spec.name,
            paypal_merchant_id=seller_spec.merchant_id,
            default_currency=seller_spec.default_currency,
            partner_fee_enabled=seller_spec.partner_fee_enabled,
            delay_funds_enabled=seller_spec.delay_funds_enabled,
        )
        session.add(seller)
        session.flush()
        result.seller_ids[seller_spec.ref] = seller.id

    for rule_spec in spec.rules:
        rule = CommissionRule(
            tenant_id=tenant.id,
            seller_id=(
                result.seller_ids[rule_spec.seller_ref]
                if rule_spec.seller_ref
                else None
            ),
            category=rule_spec.category,
            rate=Decimal(rule_spec.rate),
            valid_from=data.RULES_VALID_FROM,
            valid_to=None,
        )
        session.add(rule)
        session.flush()
        result.rule_ids[rule_spec.ref] = rule.id

    for order_spec in spec.orders:
        _build_order(session, result, order_spec)

    return result


def _build_order(session: Session, result: SeedResult, spec: OrderSpec) -> None:
    order = Order(
        tenant_id=result.tenant_id,
        external_ref=spec.ref,
        total=Money(spec.total, spec.currency),
        placed_at=spec.placed_at,
        disbursement_mode=spec.disbursement_mode,
    )
    session.add(order)
    session.flush()
    result.order_ids[spec.ref] = order.id

    for item in spec.items:
        session.add(
            OrderItem(
                tenant_id=result.tenant_id,
                order_id=order.id,
                seller_id=result.seller_ids[item.seller_ref],
                external_ref=item.ref,
                category=item.category,
                quantity=item.quantity,
                unit_price=Money(item.unit_price, spec.currency),
                line_total=Money(item.line_total, spec.currency),
            )
        )
    session.flush()

    first_seller = result.seller_ids[spec.items[0].seller_ref]

    payload = _payload(
        session,
        tenant_id=result.tenant_id,
        record_type="payment",
        record_id=f"PAY-{spec.ref}",
        body={"order_ref": spec.ref, "paid_amount": spec.total, "ccy": spec.currency},
        retrieved_at=spec.placed_at,
    )
    session.add(
        Payment(
            tenant_id=result.tenant_id,
            raw_payload_id=payload.id,
            external_id=f"PAY-{spec.ref}",
            seller_id=first_seller,
            order_id=order.id,
            amount=Money(spec.total, spec.currency),
            status="COMPLETED",
            intent="CAPTURE",
            # **No payer reference.** A card-funded order carries no `payer`
            # block at all - confirmed against the real captures. Phase 0b
            # section 4.6 concluded buyer identity lives on the order, but
            # that was a wallet payment; it does not hold for card.
            payer_reference=None,
            disbursement_mode=spec.disbursement_mode,
            occurred_at=spec.placed_at,
        )
    )
    session.flush()

    _build_ledger(session, result, spec, order.id)

    for refund_spec in spec.refunds:
        _build_refund(session, result, spec, refund_spec, order.id)

    for dispute_spec in spec.disputes:
        _build_dispute(session, result, spec, dispute_spec)


def _build_ledger(
    session: Session, result: SeedResult, spec: OrderSpec, order_id: uuid.UUID
) -> None:
    """A sale credit and a commission debit per seller.

    Amounts are **unsigned** with the direction in its own column, which is
    what buys `CHECK (amount_amount > 0)`. A position is
    `SUM(credit) - SUM(debit)`, and the sign is applied in exactly one place:
    `reckonai.engine.ledger.signed`.
    """
    for seller_ref, owed in spec.expected_entitlements.items():
        seller_id = result.seller_ids[seller_ref]
        share = sum(
            (
                Money(item.line_total, spec.currency)
                for item in spec.items
                if item.seller_ref == seller_ref
            ),
            Money("0", spec.currency),
        )
        commission = share - Money(owed, spec.currency)

        # F3: the marketplace records the sale in its home currency at a
        # stale rate. F6: it credits less than the entitlement.
        credit = share
        currency = spec.currency
        if seller_ref in spec.recorded_in_currency:
            currency, amount = spec.recorded_in_currency[seller_ref]
            credit = Money(amount, currency)
            commission = Money("0", currency)
        elif seller_ref in spec.understated_credit:
            credit = Money(spec.understated_credit[seller_ref], spec.currency)
            commission = Money("0", spec.currency)

        _entry(
            session,
            result,
            spec,
            order_id,
            seller_id,
            seller_ref,
            "sale",
            "credit",
            credit,
            currency,
            "sale",
        )

        if spec.duplicate_ledger_entry:
            # **A distinct reference, which is what makes this a realistic
            # duplicate.** `UNIQUE (tenant_id, external_ref)` refuses a
            # literal repeat, and a real double-post carries its own id. The
            # fault is two entries describing one sale.
            _entry(
                session,
                result,
                spec,
                order_id,
                seller_id,
                seller_ref,
                "sale",
                "credit",
                credit,
                currency,
                "sale-dup",
            )

        # A commission of zero gets no entry: `CHECK (amount_amount > 0)`
        # refuses one, and that is right - an entry moving nothing is not a
        # movement.
        if not commission.is_zero():
            _entry(
                session,
                result,
                spec,
                order_id,
                seller_id,
                seller_ref,
                "commission",
                "debit",
                commission,
                currency,
                "comm",
            )


def _entry(
    session: Session,
    result: SeedResult,
    spec: OrderSpec,
    order_id: uuid.UUID | None,
    seller_id: uuid.UUID,
    seller_ref: str,
    category: str,
    direction: str,
    amount: Money,
    currency: str,
    suffix: str,
) -> None:
    ref = f"LED-{spec.ref}-{seller_ref}-{suffix}"
    payload = _payload(
        session,
        tenant_id=result.tenant_id,
        record_type="ledger_entry",
        record_id=ref,
        body={
            "txn_kind": category,
            "direction": direction,
            "entry_amount": str(amount.quantize().amount),
            "ccy": currency,
        },
        retrieved_at=spec.placed_at,
    )
    session.add(
        SellerLedgerEntry(
            tenant_id=result.tenant_id,
            raw_payload_id=payload.id,
            seller_id=seller_id,
            order_id=order_id,
            entry_category=category,
            transaction_type=direction,
            amount=amount,
            occurred_at=spec.placed_at,
            external_ref=ref,
        )
    )
    session.flush()


def _build_refund(
    session: Session,
    result: SeedResult,
    spec: OrderSpec,
    refund_spec: data.RefundSpec,
    order_id: uuid.UUID,
) -> None:
    """The marketplace's refund record, and - unless F1 - its ledger effect."""
    seller_id = result.seller_ids[refund_spec.seller_ref]
    amount = Money(refund_spec.amount, spec.currency)
    reversal = Money(refund_spec.expected_commission_reversal, spec.currency)

    payload = _payload(
        session,
        tenant_id=result.tenant_id,
        record_type="refund",
        record_id=refund_spec.ref,
        body={
            "order_ref": spec.ref,
            "refund_amount": refund_spec.amount,
            "ccy": spec.currency,
        },
        retrieved_at=spec.placed_at,
    )
    refund = Refund(
        tenant_id=result.tenant_id,
        raw_payload_id=payload.id,
        external_id=refund_spec.ref,
        # **No capture link.** The marketplace has no capture id - that is
        # PayPal's, and tying the two together is Block C's decision, not a
        # column here. `False` says no linkage was derived, which is the
        # honest value; `True` would claim a derivation that never ran.
        capture_id=None,
        capture_linkage_is_derived=False,
        capture_linkage_href=None,
        seller_id=seller_id,
        amount=amount,
        status="COMPLETED",
        occurred_at=spec.placed_at,
    )
    session.add(refund)
    session.flush()
    result.refund_ids[refund_spec.ref] = refund.id

    if not refund_spec.reflected_in_ledger:
        # F1: the refund exists and the ledger never heard about it.
        return

    for category, direction, value, suffix in (
        ("refund", "debit", amount, "refund"),
        ("commission_reversal", "credit", reversal, "revrs"),
    ):
        if not value.is_zero():
            _entry(
                session,
                result,
                spec,
                order_id,
                seller_id,
                refund_spec.seller_ref,
                category,
                direction,
                value,
                spec.currency,
                suffix,
            )


def _build_dispute(
    session: Session,
    result: SeedResult,
    spec: OrderSpec,
    dispute_spec: data.DisputeSpec,
) -> None:
    """One dispute, with only the fields a marketplace would hold.

    **Writes no ledger entry.** An open dispute moves no money: PayPal has not
    decided it, so there is nothing to debit, and a speculative debit would
    make the seller's position wrong in the clean baseline (P4).
    """
    from reckonai.models import Dispute

    payload = _payload(
        session,
        tenant_id=result.tenant_id,
        record_type="dispute",
        record_id=dispute_spec.ref,
        body={
            "disputed_amount": dispute_spec.amount,
            "ccy": spec.currency,
            "case_reason": dispute_spec.reason,
        },
        retrieved_at=spec.placed_at,
    )
    session.add(
        Dispute(
            tenant_id=result.tenant_id,
            raw_payload_id=payload.id,
            external_id=dispute_spec.ref,
            capture_id=None,
            buyer_transaction_id=None,
            seller_id=result.seller_ids[dispute_spec.seller_ref],
            amount=Money(dispute_spec.amount, spec.currency),
            status=dispute_spec.status,
            lifecycle_stage=None,
            dispute_state=None,
            reason=dispute_spec.reason,
            channel=None,
            outcome=None,
            seller_response_due_date=None,
            occurred_at=spec.placed_at,
            updated_at=None,
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# The consistency check (requirements.md D6)
# ---------------------------------------------------------------------------


def verify(
    session: Session, tenant_id: uuid.UUID, dataset: Dataset | None = None
) -> None:
    """Recompute every stated figure and raise on the first disagreement.

    Over the **clean** orders only - faulty ones are excluded by reference
    from `dataset.faulty_order_refs`, never by a heuristic.

      1. Each line total equals unit price times quantity.
      2. Each order total equals the sum of its line totals.
      3. Each seller's commission and entitlement, recomputed through
         `resolve_commission_rule`, equal `data.py`'s literals.
      4. Each seller's ledger position, per currency, equals
         `data.EXPECTED_POSITIONS`.
    """
    spec = dataset if dataset is not None else data.CLEAN
    clean = [o for o in spec.orders if o.ref not in spec.faulty_order_refs]

    rules = list(
        session.scalars(
            select(CommissionRule).where(CommissionRule.tenant_id == tenant_id)
        )
    )
    sellers = {
        row.external_ref: row
        for row in session.scalars(select(Seller).where(Seller.tenant_id == tenant_id))
    }

    for order in clean:
        _verify_order(order, rules, sellers)

    _verify_positions(session, tenant_id, clean, spec)


@dataclass(frozen=True)
class _EngineOrder:
    """An `OrderSpec` in the shape `reckonai.engine` reads.

    The engine takes `typing.Protocol` types so it imports no ORM and no
    `reckonai.sim` (specs/tech-stack.md section 6). An `OrderSpec` carries
    amounts as strings and sellers by reference, so these two adapters are
    where that becomes `Money` and a real seller id - once, rather than at
    three call sites.
    """

    id: uuid.UUID
    total: Money
    placed_at: datetime


@dataclass(frozen=True)
class _EngineItem:
    """An `ItemSpec` in the shape `reckonai.engine` reads."""

    id: uuid.UUID
    seller_id: uuid.UUID
    category: str | None
    line_total: Money


def _verify_order(
    order: OrderSpec, rules: list[CommissionRule], sellers: dict[str, Seller]
) -> None:
    item_sum = Money("0", order.currency)

    for item in order.items:
        stated = Money(item.line_total, order.currency)
        computed = Money(item.unit_price, order.currency) * item.quantity
        if stated != computed.quantize():
            raise SeedInconsistency(
                f"{order.ref} item {item.ref}: stated line_total {stated} but "
                f"{item.quantity} x {item.unit_price} is {computed.quantize()}"
            )
        item_sum = item_sum + stated

    stated_total = Money(order.total, order.currency)
    if stated_total != item_sum:
        raise SeedInconsistency(
            f"{order.ref}: stated total {stated_total} but its items sum to "
            f"{item_sum}. Phase 2 D13 means no constraint catches this, which "
            "is exactly why this check exists."
        )

    # Invariants 3 and 4 run through the **real engine**, not a second copy of
    # the calculation (Block A's handoff, and
    # specs/2026-11-09-entitlement-engine/requirements.md D5). Two copies could
    # disagree, and the one living in a test fixture is the one nobody reviews.
    #
    # D5a's independence is preserved, which is the property that matters: this
    # check is still hand-written literals against a recomputation. The
    # recomputation is simply the engine now, rather than a stand-in for it.
    expected = expected_entitlement(
        _EngineOrder(
            id=uuid.uuid5(uuid.NAMESPACE_OID, order.ref),
            total=stated_total,
            placed_at=order.placed_at,
        ),
        [
            _EngineItem(
                id=uuid.uuid5(uuid.NAMESPACE_OID, item.ref),
                seller_id=sellers[item.seller_ref].id,
                category=item.category,
                line_total=Money(item.line_total, order.currency),
            )
            for item in order.items
        ],
        rules,
    )

    stated_sellers = set(order.expected_entitlements)
    computed_sellers = {item.seller_ref for item in order.items}
    if stated_sellers != computed_sellers:
        raise SeedInconsistency(
            f"{order.ref}: states entitlements for {sorted(stated_sellers)} but "
            f"its items belong to {sorted(computed_sellers)}. A seller on the "
            "order with no stated entitlement is an unchecked figure."
        )

    for seller_ref, owed in order.expected_entitlements.items():
        line = expected.for_seller(sellers[seller_ref].id)
        if line.entitlement != Money(owed, order.currency):
            raise SeedInconsistency(
                f"{order.ref} / {seller_ref}: stated entitlement {owed} but "
                f"{line.gross_share} at rate {line.commission_rate} leaves "
                f"{line.entitlement}"
            )

    if expected.commission != Money(order.expected_commission, order.currency):
        raise SeedInconsistency(
            f"{order.ref}: stated commission {order.expected_commission} but "
            f"the per-seller figures sum to {expected.commission}"
        )


def _verify_positions(
    session: Session, tenant_id: uuid.UUID, clean: list[OrderSpec], spec: Dataset
) -> None:
    """Each seller's ledger position, per currency, against the literals.

    Read back from the database rather than recomputed from the specs, so
    this also proves the rows carry the directions intended - a sale credited
    and a commission debited. A sign error passes invariant 3 and fails here.
    """
    if len(clean) != len(data.CLEAN_ORDERS) or spec.faulty_order_refs:
        # Stated for the full clean set only. A caller seeding a subset would
        # otherwise fail against totals that were never going to be reached.
        if spec.faulty_order_refs != data.FAULTY_ORDER_REFS:
            return

    clean_refs = {o.ref for o in clean}
    entries = list(
        session.scalars(
            select(SellerLedgerEntry).where(SellerLedgerEntry.tenant_id == tenant_id)
        )
    )
    seller_refs = {
        row.id: row.external_ref
        for row in session.scalars(select(Seller).where(Seller.tenant_id == tenant_id))
    }
    clean_order_ids = {
        row.id
        for row in session.scalars(
            select(Order).where(
                Order.tenant_id == tenant_id, Order.external_ref.in_(clean_refs)
            )
        )
    }

    for (seller_ref, currency), stated in data.EXPECTED_POSITIONS.items():
        mine = [
            e
            for e in entries
            if seller_refs.get(e.seller_id) == seller_ref
            and e.order_id in clean_order_ids
        ]
        found = position(mine, currency).quantize()
        if found != Money(stated, currency):
            raise SeedInconsistency(
                f"{seller_ref} {currency}: stated position {stated} but the "
                f"ledger sums to {found.amount} over {len(mine)} entries. "
                f"{signed.__module__}.signed is where a sign is applied."
            )
