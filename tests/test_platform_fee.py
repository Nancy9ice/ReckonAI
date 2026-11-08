"""The platform fee: PayPal's figure, and the direction carried by the parent.

specs/2026-11-08-canonical-events/validation.md C7's platform_fee half.

This table is why commission validation became a real comparison under the
multiparty direction: in the merchant-of-record model the commission was
whatever the marketplace kept, with no provider figure to check it against.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.models import Capture, PlatformFee, RawPayload, Refund, Seller, Tenant
from reckonai.money import Money
from test_events_capture import OCCURRED_AT, make_capture
from test_events_refund import make_refund


def make_fee(tenant: Tenant, payload: RawPayload, **overrides: object) -> PlatformFee:
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "external_id": f"FEE-{uuid.uuid4().hex[:12]}",
        "amount": Money("10.00", "USD"),
        "occurred_at": OCCURRED_AT,
    }
    fields.update(overrides)
    return PlatformFee(**fields)


@pytest.fixture
def capture(session: Session, tenant: Tenant, payload: RawPayload) -> Capture:
    row = make_capture(tenant, payload)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def refund(session: Session, tenant: Tenant, payload: RawPayload) -> Refund:
    row = make_refund(tenant, payload)
    session.add(row)
    session.flush()
    return row


def test_a_fee_taken_at_capture_hangs_off_the_capture(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """The 10.00 PayPal took on the observed 100.00 capture.

    Taken **at capture in both disbursement modes** - Phase 0b established
    that, and the disbursement amount confirms it from the other direction: a
    release pays out the capture's net, fees already deducted.
    """
    fee = make_fee(tenant, payload, capture_id=capture.id)
    session.add(fee)
    session.flush()

    assert fee.capture_id == capture.id
    assert fee.refund_id is None
    assert fee.amount == Money("10.00", "USD")


def test_a_reversal_hangs_off_the_refund_and_is_stored_positive(
    session: Session, tenant: Tenant, payload: RawPayload, refund: Refund
) -> None:
    """requirements.md D6: the direction is the parent, not a sign or a flag.

    A 30.00 refund of the 100.00 capture returned 3.00 of the 10.00 commission
    - automatically and pro rata. The row is stored **positive, exactly as
    PayPal reported it**, and that it is a reversal is said by its parent being
    a refund.
    """
    fee = make_fee(
        tenant, payload, refund_id=refund.id, amount=Money("3.00", "USD")
    )
    session.add(fee)
    session.flush()

    assert fee.refund_id == refund.id
    assert fee.capture_id is None
    # Positive at rest. Phase 10 applies the direction; the data does not.
    assert fee.amount.amount > 0


def test_the_table_carries_no_reversal_flag_and_no_sign_column() -> None:
    """requirements.md D6, pinned so it is not helpfully added later.

    An `is_reversal` column would be derivable from the parent, and a derivable
    column is a second place for one fact to disagree with itself.
    """
    names = set(PlatformFee.__table__.c.keys())

    assert "is_reversal" not in names
    assert "direction" not in names
    assert "sign" not in names


def test_both_parents_set_is_rejected(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    capture: Capture,
    refund: Refund,
) -> None:
    """`num_nonnulls(capture_id, refund_id) = 1`, on this table too.

    Emitted from one shared helper so the two tables cannot spell it
    differently.
    """
    session.add(
        make_fee(tenant, payload, capture_id=capture.id, refund_id=refund.id)
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_neither_parent_set_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """A fee belonging to no transaction is evidence about nothing."""
    session.add(make_fee(tenant, payload))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_settlement_reference_is_nullable_and_stays_null(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """specs/mission.md section 7: the model must not foreclose it.

    Partner fees settle as a single next-day consolidated bank payment and no
    per-transaction reference is exposed, so this cannot be populated from
    anything PayPal returns today. The column exists so that reconciling the
    aggregate - deferred as settlement-account work - does not later need a
    migration against a deployed schema.
    """
    fee = make_fee(tenant, payload, capture_id=capture.id)
    session.add(fee)
    session.flush()

    assert fee.settlement_reference is None
    assert PlatformFee.__table__.c.settlement_reference.nullable


def test_a_fee_records_where_the_commission_was_routed(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    capture: Capture,
    seller: Seller,
) -> None:
    """`payee_reference` is `platform_fees[].payee.merchant_id`.

    It defaults to the API caller when the order does not name one, and PayPal
    fills it in explicitly rather than leaving it out.
    """
    fee = make_fee(
        tenant,
        payload,
        capture_id=capture.id,
        seller_id=seller.id,
        payee_reference="G2K2FVVLETKYG",
    )
    session.add(fee)
    session.flush()

    assert fee.payee_reference == "G2K2FVVLETKYG"


def test_several_fees_may_hang_off_one_capture(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """`platform_fees` is an array in PayPal's contract, so this is one-to-many."""
    session.add(make_fee(tenant, payload, capture_id=capture.id))
    session.add(
        make_fee(tenant, payload, capture_id=capture.id, amount=Money("1.50", "USD"))
    )
    session.flush()
