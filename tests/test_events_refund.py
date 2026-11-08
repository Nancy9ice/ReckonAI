"""Refunds and disputes: the two records whose linkage is weakest.

specs/2026-11-08-canonical-events/validation.md C11 and C13.

Each is weak for a different reason. A refund has no provider `capture_id` at
all, so the link is derived from a HATEOAS href. A dispute has an identifier for
the *buyer's* side of the transaction, which is not our capture id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.models import Dispute, RawPayload, Refund, Tenant
from reckonai.money import Money

OCCURRED_AT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

#: The href a refund's capture linkage is parsed out of. The only route back,
#: and the reason the linkage is recorded as derived.
UP_LINK = "https://api-m.sandbox.paypal.com/v2/payments/captures/5O190127TN364715T"


def make_refund(tenant: Tenant, payload: RawPayload, **overrides: object) -> Refund:
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "external_id": f"REF-{uuid.uuid4().hex[:12]}",
        "amount": Money("30.00", "USD"),
        "status": "COMPLETED",
        "occurred_at": OCCURRED_AT,
        "capture_linkage_is_derived": True,
        "capture_linkage_href": UP_LINK,
    }
    fields.update(overrides)
    return Refund(**fields)


def make_dispute(tenant: Tenant, payload: RawPayload, **overrides: object) -> Dispute:
    """A dispute with only the columns that are actually NOT NULL."""
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "external_id": f"PP-D-{uuid.uuid4().hex[:10]}",
    }
    fields.update(overrides)
    return Dispute(**fields)


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------


def test_a_refund_inserts_with_an_unresolved_capture_linkage(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """validation.md C13's permissive half.

    PayPal supplies no `capture_id` on a refund, so a linkage that could not be
    resolved is a recorded state - a Phase 11 `unmatched` - rather than a failed
    insert. A NOT NULL here would have forced Phase 7 to invent one.
    """
    refund = make_refund(tenant, payload, capture_id=None)
    session.add(refund)
    session.flush()

    assert refund.capture_id is None
    assert refund.capture_linkage_is_derived is True
    assert refund.capture_linkage_href == UP_LINK


def test_a_refund_cannot_omit_its_linkage_provenance(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """validation.md C13's strict half, and the one that carries the rule.

    `capture_linkage_is_derived` is NOT NULL **with no default**. A default
    would let a caller skip the question, and specs/roadmap.md Phase 3 requires
    the linkage to be recorded as derived rather than merely to be derived.
    """
    session.add(make_refund(tenant, payload, capture_linkage_is_derived=None))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_linkage_provenance_column_has_no_default() -> None:
    """Asserted against the model, because a default is invisible at runtime.

    A `server_default` of false here would make every unstated linkage claim to
    be provider-supplied, which is the exact inversion
    specs/mission.md P3 forbids.
    """
    column = Refund.__table__.c.capture_linkage_is_derived

    assert column.default is None
    assert column.server_default is None
    assert not column.nullable


def test_a_refund_round_trips_its_payable_breakdown(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """The observed identity: 30.00 - 0.00 - 3.00 = 27.00.

    The 3.00 is the pro-rata platform fee reversal, which PayPal applied
    automatically. `paypal_fee` was `0.00` on the refund - an observed zero,
    which is a different fact from an absent value.
    """
    refund = make_refund(
        tenant,
        payload,
        gross_amount=Money("30.00", "USD"),
        paypal_fee=Money("0.00", "USD"),
        net_amount=Money("27.00", "USD"),
        total_refunded_amount=Money("30.00", "USD"),
    )
    session.add(refund)
    session.flush()
    session.expire(refund)

    assert refund.gross_amount - refund.paypal_fee - Money("3.00", "USD") == (
        refund.net_amount
    )
    # An observed zero, retained as a zero rather than collapsed to NULL.
    assert refund.paypal_fee == Money("0.00", "USD")


def test_a_refund_inserts_with_its_whole_breakdown_null(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """A refund read from the refund endpoint alone omits `platform_fees`.

    specs/roadmap.md Phase 7 therefore reads refunds **through the order**.
    Field availability depends on the read path, so every breakdown column here
    is nullable too.
    """
    refund = make_refund(tenant, payload)
    session.add(refund)
    session.flush()

    assert refund.gross_amount is None
    assert refund.net_amount is None
    assert refund.paypal_fee is None
    assert refund.total_refunded_amount is None


# ---------------------------------------------------------------------------
# Dispute
# ---------------------------------------------------------------------------


def test_a_dispute_inserts_with_every_optional_column_null(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """validation.md C11, and the most important test in this module.

    Ten canonical dispute fields rest on PayPal's published contract rather
    than on an observed value, because creating a dispute against a card-funded
    capture answers `TRANSACTION_NOT_ELIGIBLE`. A NOT NULL anywhere here would
    force Phase 7 to invent a value.
    """
    dispute = make_dispute(tenant, payload)
    session.add(dispute)
    session.flush()

    for column in (
        "capture_id",
        "buyer_transaction_id",
        "seller_id",
        "amount",
        "status",
        "lifecycle_stage",
        "dispute_state",
        "reason",
        "channel",
        "outcome",
        "seller_response_due_date",
        "occurred_at",
        "updated_at",
    ):
        assert getattr(dispute, column) is None, column


def test_a_dispute_round_trips_the_observed_values(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """The one dispute that was observed, as it was observed."""
    dispute = make_dispute(
        tenant,
        payload,
        status="WAITING_FOR_SELLER_RESPONSE",
        lifecycle_stage="INQUIRY",
        dispute_state="REQUIRED_ACTION",
        reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        channel="INTERNAL",
        amount=Money("100.00", "USD"),
        buyer_transaction_id="3VW12345AB678901C",
        seller_response_due_date=datetime(2026, 11, 28, tzinfo=UTC),
    )
    session.add(dispute)
    session.flush()
    session.expire(dispute)

    assert dispute.status == "WAITING_FOR_SELLER_RESPONSE"
    assert dispute.amount == Money("100.00", "USD")
    # Not our capture id - the buyer's side has its own identifier.
    assert dispute.buyer_transaction_id == "3VW12345AB678901C"
    assert dispute.capture_id is None


def test_a_resolved_dispute_has_no_response_due_date(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """The absence is **correct**, and must never read as a missed deadline.

    Observed with a date 20 days out while the seller response was outstanding,
    and gone once the seller accepted the claim - because nothing is due any
    more. `specs/paypal-fields.md` decides the field `no_action` on exactly
    that basis.
    """
    dispute = make_dispute(
        tenant,
        payload,
        status="RESOLVED",
        outcome="RESOLVED_BUYER_FAVOUR",
        seller_response_due_date=None,
    )
    session.add(dispute)
    session.flush()

    assert dispute.seller_response_due_date is None
    assert dispute.status == "RESOLVED"


def test_the_response_due_date_is_nullable_in_the_schema() -> None:
    """Asserted on the column, so Phase 7 cannot be forced into a default."""
    assert Dispute.__table__.c.seller_response_due_date.nullable
    assert Dispute.__table__.c.occurred_at.nullable


def test_a_dispute_without_a_payload_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """Permissive everywhere except the evidence. specs/mission.md P7."""
    session.add(make_dispute(tenant, payload, raw_payload_id=None))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_cross_tenant_refund_capture_link_is_rejected(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    other_tenant: Tenant,
    other_payload: RawPayload,
) -> None:
    """validation.md C15 on the derived linkage.

    A derived link is still a composite foreign key, so a derivation that
    resolved to another tenant's capture is unwritable rather than merely
    wrong.
    """
    from test_events_capture import make_capture

    capture = make_capture(tenant, payload)
    session.add(capture)
    session.flush()

    session.add(
        make_refund(other_tenant, other_payload, capture_id=capture.id)
    )

    with pytest.raises(IntegrityError):
        session.flush()
