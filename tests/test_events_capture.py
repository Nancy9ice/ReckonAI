"""Payments and captures hold what PayPal gives and refuse what it does not.

specs/2026-11-08-canonical-events/validation.md C4, C10, C12, C16 and part of
C15.

The assertions that matter here are the *permissive* ones. A `NOT NULL` this
phase got wrong forces Phase 7 to invent a value, which is the one thing
specs/mission.md P3 forbids outright, and `requirements.md` section 6 names it
as the phase's whole risk surface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.models import Capture, Payment, RawPayload, Seller, Tenant
from reckonai.money import Money

OCCURRED_AT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def make_payment(
    tenant: Tenant, payload: RawPayload, **overrides: object
) -> Payment:
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "external_id": f"PAY-{uuid.uuid4().hex[:12]}",
        "amount": Money("100.00", "USD"),
        "status": "COMPLETED",
        "occurred_at": OCCURRED_AT,
    }
    fields.update(overrides)
    return Payment(**fields)


def make_capture(
    tenant: Tenant, payload: RawPayload, **overrides: object
) -> Capture:
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "external_id": f"CAP-{uuid.uuid4().hex[:12]}",
        "amount": Money("100.00", "USD"),
        "status": "COMPLETED",
        "occurred_at": OCCURRED_AT,
        "disbursement_state": "disbursed_at_capture",
    }
    fields.update(overrides)
    return Capture(**fields)


def test_a_capture_inserts_with_its_whole_breakdown_null(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """validation.md C12, and the most important test in this module.

    A capture loses its entire `seller_receivable_breakdown` while PENDING, and
    an open dispute puts it there - observed, then observed returning once the
    dispute resolved. A `NOT NULL` on any of these would make such a capture
    unstorable, and Phase 7 would have to invent the values.
    """
    capture = make_capture(tenant, payload, status="PENDING")
    session.add(capture)
    session.flush()

    assert capture.gross_amount is None
    assert capture.net_amount is None
    assert capture.paypal_fee is None
    assert capture.settlement_amount is None
    assert capture.settlement_currency is None


def test_a_capture_round_trips_its_breakdown_as_money(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """The populated case, with the observed multiparty figures.

    100.00 gross, 3.98 PayPal fee, 10.00 platform fee, 86.02 net - the identity
    `gross - paypal_fee - platform_fees` that held on every sampled capture.
    The 86.02 is also what the disbursement paid out, which is how the fee
    timing was confirmed from a second direction.
    """
    capture = make_capture(
        tenant,
        payload,
        gross_amount=Money("100.00", "USD"),
        paypal_fee=Money("3.98", "USD"),
        net_amount=Money("86.02", "USD"),
    )
    session.add(capture)
    session.flush()
    session.expire(capture)

    assert capture.gross_amount == Money("100.00", "USD")
    assert capture.paypal_fee == Money("3.98", "USD")
    assert capture.net_amount == Money("86.02", "USD")
    # The identity, in Decimal arithmetic through Money.
    platform_fee = Money("10.00", "USD")
    assert capture.gross_amount - capture.paypal_fee - platform_fee == (
        capture.net_amount
    )


def test_a_half_populated_money_pair_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """validation.md C16. An amount with no currency is a hard failure.

    Expressed in DDL so it holds for a row written by a migration or a seed
    script, not only one written through the ORM
    (specs/tech-stack.md section 9.3). Written as raw SQL because `Money` makes
    the invalid state unreachable through the ORM, which is the point of it.
    """
    capture = make_capture(tenant, payload)
    session.add(capture)
    session.flush()

    # The statement is autoflushed and the CHECK fires here, not at a later
    # flush - which is worth knowing, because wrapping the wrong call makes
    # this test pass for the wrong reason.
    with pytest.raises(IntegrityError):
        session.execute(
            Capture.__table__.update()
            .where(Capture.__table__.c.id == capture.id)
            .values(gross_amount_amount=None, gross_amount_currency="USD")
        )


def test_a_capture_without_a_payload_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """validation.md C4. specs/mission.md P7, enforced rather than asserted.

    `raw_payload_id` is NOT NULL, so a record inserted without its evidence
    fails instead of becoming a NULL a schema test has to go looking for.
    """
    session.add(make_capture(tenant, payload, raw_payload_id=None))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_duplicate_external_id_within_a_tenant_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """requirements.md D15. Idempotency as a database property.

    This constraint is what makes "re-running ingestion is idempotent"
    (specs/roadmap.md Phases 6 and 7) true of the database rather than a promise
    made by two different connectors.
    """
    session.add(make_capture(tenant, payload, external_id="CAP-FIXED"))
    session.flush()

    session.add(make_capture(tenant, payload, external_id="CAP-FIXED"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_external_id_in_another_tenant_inserts(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    other_tenant: Tenant,
    other_payload: RawPayload,
) -> None:
    """The constraint is scoped per tenant, deliberately.

    Two marketplaces may legitimately use the same internal reference, so a
    global unique constraint would make one of them unstorable.
    """
    session.add(make_capture(tenant, payload, external_id="CAP-SHARED"))
    session.add(
        make_capture(other_tenant, other_payload, external_id="CAP-SHARED")
    )
    session.flush()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("disbursement_state", "paid"),
        ("reconciliation_state", "broken"),
        ("disbursement_mode", "SOMEDAY"),
    ],
)
def test_a_state_outside_its_vocabulary_is_rejected(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    column: str,
    value: str,
) -> None:
    """validation.md C10. The CHECK is generated from the Python tuple.

    So this proves the two have not drifted as well: a value the tuple rejects
    is a value the database rejects.
    """
    session.add(make_capture(tenant, payload, **{column: value}))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("state", ["funds_held", "release_requested"])
def test_the_healthy_delayed_states_are_storable(
    session: Session, tenant: Tenant, payload: RawPayload, state: str
) -> None:
    """Held funds are a **healthy** state, not an unpaid obligation.

    specs/mission.md P4 and specs/multiparty-direction.md section 4. Classifying
    these as discrepancies would make every `DELAYED` transaction an exception,
    which is the single largest false-positive risk in the product. The schema
    has to be able to say "held" without that being an error, and this is that
    assertion.
    """
    session.add(
        make_capture(
            tenant,
            payload,
            disbursement_mode="DELAYED",
            disbursement_state=state,
        )
    )
    session.flush()


def test_disbursement_mode_may_be_null(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """Nullable rather than defaulted, and the reason matters.

    Assuming `INSTANT` for a record that did not state a mode would make every
    held-funds transaction look like a completed one.
    """
    capture = make_capture(tenant, payload, disbursement_mode=None)
    session.add(capture)
    session.flush()

    assert capture.disbursement_mode is None


def test_the_reconciliation_state_defaults_to_ingested(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """A record lands as `ingested` - not matched, and not an exception."""
    capture = make_capture(tenant, payload)
    session.add(capture)
    session.flush()

    assert capture.reconciliation_state == "ingested"


def test_a_cross_tenant_payload_link_is_rejected(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    other_payload: RawPayload,
) -> None:
    """validation.md C15. The composite foreign key makes this unwritable.

    Phase 2's D7 carried `tenant_id` into every reference for exactly this: a
    cross-tenant link is rejected by the database rather than by a code review.
    """
    session.add(make_capture(tenant, payload, raw_payload_id=other_payload.id))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_cross_tenant_seller_link_is_rejected(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    other_tenant: Tenant,
    other_payload: RawPayload,
    seller: Seller,
) -> None:
    """The same guarantee on the join the whole product rests on."""
    session.add(
        make_capture(other_tenant, other_payload, seller_id=seller.id)
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_capture_may_name_no_payment_and_no_seller(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """Both links are nullable, because resolution can fail.

    `supplementary_data.related_ids.order_id` is a real provider field, unlike
    the refund linkage - but a payee we hold no seller row for is a quarantined
    record Phase 11 should see, not an insert to reject
    (specs/tech-stack.md section 9.6).
    """
    session.add(make_capture(tenant, payload, payment_id=None, seller_id=None))
    session.flush()


def test_a_payment_and_its_capture_link_within_a_tenant(
    session: Session, tenant: Tenant, payload: RawPayload, seller: Seller
) -> None:
    """The ordinary case: a capture pointing at its payment and its seller."""
    payment = make_payment(tenant, payload, seller_id=seller.id)
    session.add(payment)
    session.flush()

    capture = make_capture(
        tenant, payload, payment_id=payment.id, seller_id=seller.id
    )
    session.add(capture)
    session.flush()

    assert capture.payment_id == payment.id
    assert capture.seller_id == seller.id


def test_a_payment_may_name_no_order(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """requirements.md D4: `order_id` means only what the source stated.

    NULL on a PayPal-origin record, where the tie to a marketplace order is a
    match and Phase 11 owns it. A NOT NULL here would have forced this phase to
    guess a matching.
    """
    session.add(make_payment(tenant, payload, order_id=None))
    session.flush()


def test_no_event_table_carries_a_cross_source_link() -> None:
    """requirements.md D4, pinned so it is not helpfully added later.

    Linking a marketplace record to its PayPal counterpart is a claim with a
    confidence and an evidence trail behind it, and Phase 11 owns it. A column
    here would be a matching decision with no run behind it.
    """
    for table in (Payment.__table__, Capture.__table__):
        names = set(table.c.keys())

        matching = {name for name in names if name.startswith("matched_")}
        assert not matching, f"{table.name} carries {matching}"
        assert "counterpart_id" not in names


def test_no_event_table_carries_its_own_source_column() -> None:
    """requirements.md D3: origin lives on `raw_payload` and nowhere else.

    Two places for one fact is the shape of every reconciliation bug this
    product exists to find, so the denormalisation is refused by test rather
    than by intention.
    """
    for table in (Payment.__table__, Capture.__table__):
        assert "source" not in table.c
