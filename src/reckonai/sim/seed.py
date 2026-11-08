"""The seed builder, and the consistency check that makes its output trustworthy.

specs/2026-11-08-block-a-marketplace-connector/requirements.md D2, D6, D5a and
D12.

**`verify()` is not optional and not a convenience.** Phase 2's D13 ties no
constraint between an order's total and the sum of its items - a marketplace
whose own records disagree is exactly what this product exists to find, so a
`CHECK` would turn a Phase 11 finding into an ingestion crash. The consequence
is that the clean baseline has **no database backstop**: a silent inconsistency
here would be measured by Block C as a false positive forever, and
specs/roadmap.md Phase 11's hardest exit criterion would be scored against a
baseline that was never clean.

So `verify()` recomputes every figure `data.py` states as a literal, using the
real `reckonai.engine` resolver rather than a shortcut, and raises naming the
row. D5a explains why the two sides are derived independently.

**Three things bite a seed builder here**, all inherited from Phase 3 and all
worth meeting in writing rather than at a stack trace:

  1. The `raw_payload` link is **NOT NULL** on every financial table, so a
     financial row cannot be inserted without a payload row to point at.
  2. `seller_ledger_entry.amount` is **unsigned**, with `CHECK (> 0)`, and
     needs a `transaction_type` of `debit` or `credit`.
  3. **Flush order between unrelated mappers is not derived from foreign
     keys.** This project declares no `relationship()` anywhere, so
     SQLAlchemy's unit of work has no mapper dependency to sort on and will
     insert a child before its parent. Every stage below flushes explicitly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.engine.ledger import position, signed
from reckonai.engine.rules import resolve_commission_rule
from reckonai.models import (
    Capture,
    CommissionRule,
    Disbursement,
    FxConversion,
    Order,
    OrderItem,
    Payment,
    PlatformFee,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
    Tenant,
)
from reckonai.money import Money
from reckonai.sim import data
from reckonai.sim.data import Dataset, OrderSpec

#: Marks a row written as synthetic provider evidence rather than captured from
#: PayPal (requirements.md D12). Block D replaces every row carrying it, and
#: owes a check that none survives into the demo tenant.
SYNTHETIC_MARKER = {"_synthetic": True, "replaced_by": "block-d"}


class SeedInconsistency(AssertionError):
    """The seeded dataset disagrees with the figures `data.py` states.

    An `AssertionError` subclass so a failure reads as a broken invariant
    rather than as an exceptional condition the caller might handle. Nothing
    should handle this: it means the baseline is not clean.
    """


@dataclass
class SeedResult:
    """What the builder wrote, so callers and tests can address it."""

    tenant_id: uuid.UUID
    seller_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    order_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    rule_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    capture_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    refund_ids: dict[str, uuid.UUID] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _digest(body: dict) -> str:
    """SHA-256 of a canonical serialization of `body`.

    `sort_keys` and `separators` fixed, so two equal bodies hash equally
    regardless of construction order. The column is `VARCHAR(64)`, which is
    exactly a hex digest.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    source: str,
    record_type: str,
    record_id: str,
    body: dict,
    retrieved_at: datetime,
) -> RawPayload:
    """Write one `raw_payload` row and return it, flushed.

    `endpoint` is left `NULL` deliberately, and that is requirements.md D2's
    second separator. Phase 3 added the column because **field availability
    depends on the read path**, and a row the builder wrote directly had no read
    path - so `NULL` is the true answer, and it is what tells a seeded row from
    one the connector fetched through `GET /sim/marketplace/...`.

    Flushed before return because the caller is about to point a NOT NULL
    foreign key at it, and flush order between unrelated mappers is not derived
    from that key.
    """
    # **The body carries its own record id, and must.** `raw_payload` is unique
    # on `(tenant_id, source, source_record_type, body_sha256)`, so two
    # genuinely different records whose bodies happen to be byte-identical
    # collide. That is the constraint working: Phase 3 built it so an unchanged
    # re-read stores nothing, which is what makes ingestion idempotent.
    #
    # It bit this builder immediately - `ORD-0003` and `ORD-0008` both carry a
    # commission of 12.00 USD, so their platform-fee bodies were identical and
    # the second insert failed. Embedding the id is also what a real provider
    # response does, so this is the payload becoming more faithful rather than
    # a hash being worked around.
    full_body = {"id": record_id, **body}

    row = RawPayload(
        tenant_id=tenant_id,
        source=source,
        source_record_type=record_type,
        source_record_id=record_id,
        endpoint=None,
        retrieved_at=retrieved_at,
        body=full_body,
        body_sha256=_digest(full_body),
    )
    session.add(row)
    session.flush()
    return row


def _marketplace_payload(
    session: Session, result: SeedResult, record_type: str, record_id: str, body: dict,
    retrieved_at: datetime,
) -> RawPayload:
    return _payload(
        session,
        tenant_id=result.tenant_id,
        source="marketplace",
        record_type=record_type,
        record_id=record_id,
        body=body,
        retrieved_at=retrieved_at,
    )


def _paypal_payload(
    session: Session, result: SeedResult, record_type: str, record_id: str, body: dict,
    retrieved_at: datetime,
) -> RawPayload:
    """A synthetic provider payload, marked as such (requirements.md D12).

    The marker is inside `body` rather than beside it, so it travels with the
    evidence and cannot be separated from it by a query that selects the body
    alone.
    """
    return _payload(
        session,
        tenant_id=result.tenant_id,
        source="paypal",
        record_type=record_type,
        record_id=record_id,
        body={**body, **SYNTHETIC_MARKER},
        retrieved_at=retrieved_at,
    )


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def build(
    session: Session,
    dataset: Dataset | None = None,
    *,
    tenant_slug: str = data.TENANT_SLUG,
    tenant_name: str = data.TENANT_NAME,
) -> SeedResult:
    """Write the dataset and return what was written.

    Stages flush in dependency order: tenant, sellers, rules, orders, items,
    then the event side. See the module docstring's third point for why that is
    explicit rather than left to SQLAlchemy.
    """
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
                if rule_spec.seller_ref is not None
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
    """One order, its items, and the whole event chain hanging off it."""
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

    settles_to = spec.settles_to or spec.items[0].seller_ref
    seller_id = result.seller_ids[settles_to]

    # --- the marketplace's payment record ---------------------------------
    payment_payload = _marketplace_payload(
        session,
        result,
        "payment",
        f"PAY-{spec.ref}",
        {
            "payment_ref": f"PAY-{spec.ref}",
            "order_ref": spec.ref,
            "paid_amount": spec.total,
            "ccy": spec.currency,
        },
        spec.placed_at,
    )
    payment = Payment(
        tenant_id=result.tenant_id,
        raw_payload_id=payment_payload.id,
        external_id=f"PAY-{spec.ref}",
        seller_id=seller_id,
        order_id=order.id,
        amount=Money(spec.total, spec.currency),
        status="COMPLETED",
        intent="CAPTURE",
        payer_reference="BUYER-SANDBOX-01",
        disbursement_mode=spec.disbursement_mode,
        occurred_at=spec.placed_at,
    )
    session.add(payment)
    session.flush()

    # --- PayPal's capture (synthetic, per D12) -----------------------------
    #
    # `disbursement_state` is what says whether the seller actually has the
    # money. `status` does not: a `DELAYED` capture reads `COMPLETED` while
    # PayPal still holds the funds, which is why the two columns exist.
    held = spec.disbursement_mode == "DELAYED"
    capture_state = "funds_held" if held else "disbursed_at_capture"

    capture_payload = _paypal_payload(
        session,
        result,
        "capture",
        f"CAP-{spec.ref}",
        {"id": f"CAP-{spec.ref}", "amount": {"value": spec.total,
                                             "currency_code": spec.currency}},
        spec.placed_at,
    )
    capture = Capture(
        tenant_id=result.tenant_id,
        raw_payload_id=capture_payload.id,
        external_id=f"CAP-{spec.ref}",
        payment_id=payment.id,
        seller_id=seller_id,
        amount=Money(spec.total, spec.currency),
        gross_amount=Money(spec.total, spec.currency),
        # **Never defaulted to zero.** `None` means PayPal did not state a fee,
        # which is a different claim from a fee of nothing
        # (specs/roadmap.md Phase 10, specs/tech-stack.md section 9.6).
        paypal_fee=(
            Money(spec.paypal_fee, spec.currency)
            if spec.paypal_fee is not None
            else None
        ),
        net_amount=(
            Money(spec.total, spec.currency) - Money(spec.paypal_fee, spec.currency)
            if spec.paypal_fee is not None
            else None
        ),
        settlement_amount=(
            Money(spec.fx.settlement_amount, spec.fx.target_currency)
            if spec.fx is not None
            else None
        ),
        settlement_currency=spec.fx.target_currency if spec.fx is not None else None,
        status="COMPLETED",
        disbursement_mode=spec.disbursement_mode,
        disbursement_state=capture_state,
        occurred_at=spec.placed_at,
    )
    session.add(capture)
    session.flush()
    result.capture_ids[spec.ref] = capture.id

    # --- PayPal's platform fee, where the seller can carry one -------------
    #
    # Omitted entirely for a seller without `PARTNER_FEE`: PayPal cannot take a
    # platform fee there, so no row is the accurate record. A zero-amount row
    # would assert a measured fee of nothing, which is the substitution P3
    # forbids.
    commission = Money(spec.expected_commission, spec.currency)
    if data.seller(settles_to).partner_fee_enabled and not commission.is_zero():
        fee_payload = _paypal_payload(
            session,
            result,
            "platform_fee",
            f"FEE-{spec.ref}",
            {"platform_fees": [{"amount": {"value": spec.expected_commission,
                                           "currency_code": spec.currency}}]},
            spec.placed_at,
        )
        session.add(
            PlatformFee(
                tenant_id=result.tenant_id,
                raw_payload_id=fee_payload.id,
                external_id=f"FEE-{spec.ref}",
                capture_id=capture.id,
                refund_id=None,
                seller_id=seller_id,
                payee_reference=data.seller(settles_to).merchant_id,
                amount=commission,
                settlement_reference=None,
                occurred_at=spec.placed_at,
            )
        )
        session.flush()

    # --- the conversion PayPal reported, where there was one ---------------
    if spec.fx is not None:
        entitlement = Money(
            spec.expected_entitlements[settles_to], spec.currency
        )
        fx_payload = _paypal_payload(
            session,
            result,
            "fx_conversion",
            f"FX-{spec.ref}",
            {"exchange_rate": {"value": spec.fx.rate,
                               "source_currency": spec.currency,
                               "target_currency": spec.fx.target_currency}},
            spec.placed_at,
        )
        session.add(
            FxConversion(
                tenant_id=result.tenant_id,
                raw_payload_id=fx_payload.id,
                capture_id=capture.id,
                refund_id=None,
                source=entitlement,
                target=Money(spec.fx.settlement_amount, spec.fx.target_currency),
                rate=Decimal(spec.fx.rate),
                converted_at=spec.placed_at,
                # PayPal reported this rate, so it is not derived.
                is_derived=False,
                # **Stays NULL.** PayPal publishes no conversion fee, and the
                # table's CHECK refuses a zero one: an absent fee and a
                # measured fee of nothing are different claims (P3).
                fee=None,
                provider="paypal",
            )
        )
        session.flush()

    # --- the release record, for a DELAYED order ---------------------------
    if held:
        release_payload = _paypal_payload(
            session,
            result,
            "disbursement",
            f"REL-{spec.ref}",
            {"status": "HELD"},
            spec.placed_at,
        )
        session.add(
            Disbursement(
                tenant_id=result.tenant_id,
                raw_payload_id=release_payload.id,
                external_id=f"REL-{spec.ref}",
                capture_id=capture.id,
                seller_id=seller_id,
                recipient_reference=data.seller(settles_to).merchant_id,
                payout_transaction_id=None,
                amount=Money(spec.expected_entitlements[settles_to], spec.currency),
                provider_status="HELD",
                state="funds_held",
                # **No default, ever.** PayPal's release record carries no
                # usable timestamp, and writing an ingestion time here would
                # present our clock as a provider timestamp (P3).
                released_at=None,
                provider_errors=None,
            )
        )
        session.flush()

    # --- the marketplace's ledger entries ----------------------------------
    _build_ledger(session, result, spec, order.id)

    # --- refunds, and their ledger effect ----------------------------------
    for refund_spec in spec.refunds:
        _build_refund(session, result, spec, refund_spec, capture.id, order.id)

    # --- disputes ----------------------------------------------------------
    for dispute_spec in spec.disputes:
        _build_dispute(session, result, spec, dispute_spec, capture.id)


def _build_dispute(
    session: Session,
    result: SeedResult,
    spec: OrderSpec,
    dispute_spec: data.DisputeSpec,
    capture_id: uuid.UUID,
) -> None:
    """One dispute, with only the fields a marketplace would certainly hold.

    **Writes no ledger entry.** An open dispute moves no money: PayPal has not
    decided it, so there is nothing to debit. specs/mission.md P4 is the
    principle - an unresolved state is not a discrepancy - and writing a
    speculative debit here would make the seller's position wrong in the clean
    baseline, which is the one thing this dataset cannot afford.

    Every field Phase 0 did not observe stays `NULL`. See `DisputeSpec`.
    """
    from reckonai.models import Dispute

    payload = _marketplace_payload(
        session,
        result,
        "dispute",
        dispute_spec.ref,
        {
            "dispute_ref": dispute_spec.ref,
            "disputed_amount": dispute_spec.amount,
            "ccy": spec.currency,
            "case_reason": dispute_spec.reason,
        },
        spec.placed_at,
    )
    session.add(
        Dispute(
            tenant_id=result.tenant_id,
            raw_payload_id=payload.id,
            external_id=dispute_spec.ref,
            capture_id=capture_id,
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


def _build_ledger(
    session: Session, result: SeedResult, spec: OrderSpec, order_id: uuid.UUID
) -> None:
    """A sale credit and a commission debit per seller on the order.

    Amounts are **unsigned** and the direction is its own column - the whole
    reason `CHECK (amount_amount > 0)` is possible. A seller's position is
    `SUM(credit) - SUM(debit)`, applied in exactly one place,
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

        payload = _marketplace_payload(
            session,
            result,
            "ledger_entry",
            f"LED-{spec.ref}-{seller_ref}-sale",
            {"entry_ref": f"LED-{spec.ref}-{seller_ref}-sale",
             "txn_kind": "sale", "direction": "credit",
             "entry_amount": str(share.quantize().amount), "ccy": spec.currency},
            spec.placed_at,
        )
        session.add(
            SellerLedgerEntry(
                tenant_id=result.tenant_id,
                raw_payload_id=payload.id,
                seller_id=seller_id,
                order_id=order_id,
                entry_category="sale",
                transaction_type="credit",
                amount=share,
                occurred_at=spec.placed_at,
                external_ref=f"LED-{spec.ref}-{seller_ref}-sale",
            )
        )
        session.flush()

        # A commission of zero gets no entry. `CHECK (amount_amount > 0)`
        # refuses one anyway, and that constraint is right: an entry moving
        # nothing is not a movement.
        if commission.is_zero():
            continue

        payload = _marketplace_payload(
            session,
            result,
            "ledger_entry",
            f"LED-{spec.ref}-{seller_ref}-comm",
            {"entry_ref": f"LED-{spec.ref}-{seller_ref}-comm",
             "txn_kind": "commission", "direction": "debit",
             "entry_amount": str(commission.quantize().amount),
             "ccy": spec.currency},
            spec.placed_at,
        )
        session.add(
            SellerLedgerEntry(
                tenant_id=result.tenant_id,
                raw_payload_id=payload.id,
                seller_id=seller_id,
                order_id=order_id,
                entry_category="commission",
                transaction_type="debit",
                amount=commission,
                occurred_at=spec.placed_at,
                external_ref=f"LED-{spec.ref}-{seller_ref}-comm",
            )
        )
        session.flush()


def _build_refund(
    session: Session,
    result: SeedResult,
    spec: OrderSpec,
    refund_spec: data.RefundSpec,
    capture_id: uuid.UUID,
    order_id: uuid.UUID,
) -> None:
    """A refund, and - unless F1 suppresses them - its two ledger entries."""
    seller_id = result.seller_ids[refund_spec.seller_ref]
    amount = Money(refund_spec.amount, spec.currency)
    reversal = Money(refund_spec.expected_commission_reversal, spec.currency)

    payload = _marketplace_payload(
        session,
        result,
        "refund",
        refund_spec.ref,
        {"refund_ref": refund_spec.ref, "order_ref": spec.ref,
         "refund_amount": refund_spec.amount, "ccy": spec.currency},
        spec.placed_at,
    )
    refund = Refund(
        tenant_id=result.tenant_id,
        raw_payload_id=payload.id,
        external_id=refund_spec.ref,
        capture_id=capture_id,
        # **Derived, and recorded as derived.** PayPal supplies no capture
        # identifier on a refund; the only route back is `links[rel=up]`, whose
        # href has to be parsed. The marketplace's own refund does carry the
        # order, but the capture link is still a derivation, so the provenance
        # is stated rather than left to be assumed.
        capture_linkage_is_derived=True,
        capture_linkage_href=(
            f"https://api-m.sandbox.paypal.com/v2/payments/captures/CAP-{spec.ref}"
        ),
        seller_id=seller_id,
        amount=amount,
        status="COMPLETED",
        occurred_at=spec.placed_at,
    )
    session.add(refund)
    session.flush()
    result.refund_ids[refund_spec.ref] = refund.id

    if not refund_spec.reflected_in_ledger:
        # Fault F1: the refund exists and the ledger never heard about it.
        # specs/mission.md section 6 scenario 1.
        return

    for category, direction, value, suffix in (
        ("refund", "debit", amount, "refund"),
        ("commission_reversal", "credit", reversal, "revrs"),
    ):
        if value.is_zero():
            continue
        ref = f"LED-{refund_spec.ref}-{suffix}"
        entry_payload = _marketplace_payload(
            session,
            result,
            "ledger_entry",
            ref,
            {"entry_ref": ref, "txn_kind": category, "direction": direction,
             "entry_amount": str(value.quantize().amount), "ccy": spec.currency},
            spec.placed_at,
        )
        session.add(
            SellerLedgerEntry(
                tenant_id=result.tenant_id,
                raw_payload_id=entry_payload.id,
                seller_id=seller_id,
                order_id=order_id,
                entry_category=category,
                transaction_type=direction,
                amount=value,
                occurred_at=spec.placed_at,
                external_ref=ref,
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

    Four invariants, over the **clean** orders only - faulty ones are excluded
    by reference from `dataset.faulty_order_refs`, which the builder populates,
    never by a heuristic. The builder knows which rows it broke.

      1. Each line total equals unit price times quantity.
      2. Each order total equals the sum of its line totals.
      3. Each seller's commission and entitlement, recomputed through
         `reckonai.engine.rules.resolve_commission_rule`, equal the literals
         `data.py` states.
      4. Each seller's ledger position, per currency, equals
         `data.EXPECTED_POSITIONS`.

    Raises `SeedInconsistency`, naming the row and both figures. A caller that
    catches this is wrong: it means the baseline Block C is measured against is
    not clean.
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
        for row in session.scalars(
            select(Seller).where(Seller.tenant_id == tenant_id)
        )
    }

    for order in clean:
        _verify_order(order, rules, sellers)

    _verify_positions(session, tenant_id, clean, spec)


def _verify_order(
    order: OrderSpec,
    rules: list[CommissionRule],
    sellers: dict[str, Seller],
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

    commission_sum = Money("0", order.currency)

    for seller_ref, owed in order.expected_entitlements.items():
        share = Money("0", order.currency)
        category: str | None = None
        for item in order.items:
            if item.seller_ref == seller_ref:
                share = share + Money(item.line_total, order.currency)
                category = item.category

        rule = resolve_commission_rule(
            rules,
            seller_id=sellers[seller_ref].id,
            category=category,
            at=order.placed_at,
        )
        commission = (share * rule.rate).quantize()
        entitlement = share - commission
        commission_sum = commission_sum + commission

        stated_owed = Money(owed, order.currency)
        if entitlement != stated_owed:
            raise SeedInconsistency(
                f"{order.ref} / {seller_ref}: stated entitlement {stated_owed} "
                f"but {share} at rate {rule.rate} leaves {entitlement}"
            )

    stated_commission = Money(order.expected_commission, order.currency)
    if commission_sum != stated_commission:
        raise SeedInconsistency(
            f"{order.ref}: stated commission {stated_commission} but the "
            f"per-seller figures sum to {commission_sum}"
        )


def _verify_positions(
    session: Session,
    tenant_id: uuid.UUID,
    clean: list[OrderSpec],
    spec: Dataset,
) -> None:
    """Every seller's ledger position, per currency, against the literals.

    Read back from the database rather than recomputed from the specs, so this
    also proves the rows the builder wrote carry the directions it intended -
    a sale credited and a commission debited. A sign error would pass invariant
    3 and fail here.
    """
    clean_refs = {o.ref for o in clean}
    expected = {
        key: value
        for key, value in data.EXPECTED_POSITIONS.items()
        if _positions_apply(key, clean_refs, spec)
    }

    entries = list(
        session.scalars(
            select(SellerLedgerEntry).where(
                SellerLedgerEntry.tenant_id == tenant_id
            )
        )
    )
    seller_refs = {
        row.id: row.external_ref
        for row in session.scalars(
            select(Seller).where(Seller.tenant_id == tenant_id)
        )
    }
    clean_order_ids = {
        row.id
        for row in session.scalars(
            select(Order).where(
                Order.tenant_id == tenant_id, Order.external_ref.in_(clean_refs)
            )
        )
    }

    for (seller_ref, currency), stated in expected.items():
        mine = [
            entry
            for entry in entries
            if seller_refs.get(entry.seller_id) == seller_ref
            and entry.order_id in clean_order_ids
        ]
        found = position(mine, currency).quantize()
        if found != Money(stated, currency):
            raise SeedInconsistency(
                f"{seller_ref} {currency}: stated position {stated} but the "
                f"ledger sums to {found.amount}. Entries considered: "
                f"{len(mine)}. Check the direction on each - "
                f"{signed.__module__}.signed is where a sign is applied."
            )


def _positions_apply(
    key: tuple[str, str], clean_refs: set[str], spec: Dataset
) -> bool:
    """Whether a stated position is checkable for the dataset in hand.

    `data.EXPECTED_POSITIONS` is stated for the full clean set. A caller
    seeding a subset - which the tests do, to perturb one order - would
    otherwise fail against totals that were never going to be reached.
    """
    return len(clean_refs) == len(data.CLEAN_ORDERS) and not spec.faulty_order_refs
