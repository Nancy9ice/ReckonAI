"""Disbursements: the only evidence a seller was paid, and its one hard gap.

specs/2026-11-08-canonical-events/validation.md C19.

Every field on this table is known from a **single observed record** against a
contract PayPal does not publish, so the tests here are as much a record of what
was seen as a check on the schema.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.models import Capture, Disbursement, RawPayload, Seller, Tenant
from reckonai.money import Money
from test_events_capture import make_capture


def make_disbursement(
    tenant: Tenant, payload: RawPayload, **overrides: object
) -> Disbursement:
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "external_id": f"ITEM-{uuid.uuid4().hex[:12]}",
        # The capture's net. Fees were already taken at capture.
        "amount": Money("86.02", "USD"),
        "state": "disbursement_completed",
        "provider_status": "SUCCESS",
    }
    fields.update(overrides)
    return Disbursement(**fields)


@pytest.fixture
def held_capture(session: Session, tenant: Tenant, payload: RawPayload) -> Capture:
    """A `DELAYED` capture whose funds are held - the state a release follows."""
    row = make_capture(
        tenant, payload, disbursement_mode="DELAYED", disbursement_state="funds_held"
    )
    session.add(row)
    session.flush()
    return row


def test_released_at_has_no_default_of_any_kind() -> None:
    """validation.md C19, and the whole point of the table's gap.

    The release record carries no usable timestamp - `time_updated` is the
    integer `0`, not a date. An application or server default here would write
    our clock into a column that reads as a provider timestamp, which is the
    substitution specs/mission.md P3 forbids. `created_at` already records when
    we saw the record.
    """
    column = Disbursement.__table__.c.released_at

    assert column.nullable
    assert column.default is None
    assert column.server_default is None


def test_released_at_has_no_default_in_the_migrated_schema(
    migrated_database, tenant: Tenant
) -> None:
    """The same claim against the database, not the model.

    A model with no default and a migration that added one would both pass the
    test above. This is the schema a query actually runs against.
    """
    columns = inspect(migrated_database).get_columns("disbursement")
    released_at = next(c for c in columns if c["name"] == "released_at")

    assert released_at["default"] is None
    assert released_at["nullable"] is True


def test_a_disbursement_inserts_with_released_at_null(
    session: Session, tenant: Tenant, payload: RawPayload, held_capture: Capture
) -> None:
    """The normal case, and the only one PayPal's data supports today."""
    row = make_disbursement(tenant, payload, capture_id=held_capture.id)
    session.add(row)
    session.flush()

    assert row.released_at is None
    # Our own observation time is still recorded, under its own name.
    assert row.created_at is not None


def test_the_observed_record_round_trips(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    held_capture: Capture,
    seller: Seller,
) -> None:
    """The one release that was observed, as it was observed.

    `SUCCESS`, 86.02 against a 100.00 capture, and the capture id as
    `reference_id` - provider-supplied, needing no parsing.
    """
    row = make_disbursement(
        tenant,
        payload,
        capture_id=held_capture.id,
        seller_id=seller.id,
        recipient_reference="C24NAWELTTHXJ",
        payout_transaction_id="4XY98765CD432109E",
    )
    session.add(row)
    session.flush()
    session.expire(row)

    assert row.provider_status == "SUCCESS"
    assert row.amount == Money("86.02", "USD")
    assert row.capture_id == held_capture.id
    assert row.recipient_reference == "C24NAWELTTHXJ"
    # The money movement, distinct from the release instruction.
    assert row.payout_transaction_id == "4XY98765CD432109E"


def test_provider_errors_round_trips_as_jsonb(
    session: Session, tenant: Tenant, payload: RawPayload, held_capture: Capture
) -> None:
    """`items[].errors`, retained whole.

    specs/roadmap.md Phase 7: when this is absent the answer is
    `insufficient_evidence`, never an assembled cause. Retaining it whole is
    what makes the difference between the two visible.
    """
    errors = {
        "name": "INSUFFICIENT_FUNDS",
        "message": "Sender does not have sufficient funds",
        "details": [{"issue": "RECEIVER_UNREGISTERED"}],
    }
    row = make_disbursement(
        tenant,
        payload,
        capture_id=held_capture.id,
        state="disbursement_failed",
        provider_status="FAILED",
        provider_errors=errors,
    )
    session.add(row)
    session.flush()
    session.expire(row)

    assert row.provider_errors == errors


def test_a_failed_disbursement_may_carry_no_errors(
    session: Session, tenant: Tenant, payload: RawPayload, held_capture: Capture
) -> None:
    """And that absence is the `insufficient_evidence` case, not a gap to fill."""
    row = make_disbursement(
        tenant,
        payload,
        capture_id=held_capture.id,
        state="disbursement_failed",
        provider_errors=None,
    )
    session.add(row)
    session.flush()

    assert row.provider_errors is None


def test_a_duplicate_external_id_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload, held_capture: Capture
) -> None:
    """PayPal's release is idempotent per reference; this is our half.

    A second attempt against the same capture answers
    `422 PAYOUT_ALREADY_COMPLETED_FOR_REFERENCE` on their side, so a retry
    cannot double-pay. The unique constraint means a retry cannot double-record
    either.
    """
    def one() -> Disbursement:
        return make_disbursement(
            tenant, payload, capture_id=held_capture.id, external_id="I-1"
        )

    session.add(one())
    session.flush()

    session.add(one())

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_state_outside_the_vocabulary_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload, held_capture: Capture
) -> None:
    session.add(
        make_disbursement(tenant, payload, capture_id=held_capture.id, state="done")
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_completion_needs_no_release_request_before_it(
    session: Session, tenant: Tenant, payload: RawPayload, held_capture: Capture
) -> None:
    """**The 28-day fuse**, pinned as a schema property.

    PayPal disburses held funds automatically after 28 days with no marketplace
    instruction, so a transaction reaches `disbursement_completed` without ever
    passing through `release_requested`. Nothing in the schema enforces an
    ordering, and an engine that treats `release_requested` as a required
    predecessor mis-models every auto-released transaction
    (specs/multiparty-direction.md section 4).
    """
    row = make_disbursement(
        tenant,
        payload,
        capture_id=held_capture.id,
        state="disbursement_completed",
    )
    session.add(row)
    session.flush()

    # The capture it belongs to never left `funds_held`, and that is legal.
    assert held_capture.disbursement_state == "funds_held"
    assert row.state == "disbursement_completed"


def test_the_table_carries_no_release_instruction(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """specs/mission.md P6, pinned. **Nothing moves money.**

    This product may observe that a release is due and recommend one; it never
    sends `POST /v1/payments/referenced-payouts-items`. No column here is an
    instruction, a request flag or a queue of pending releases, and a later
    contributor adding one needs to read P6 first.
    """
    names = set(Disbursement.__table__.c.keys())

    assert not names & {
        "release_requested_by",
        "release_instruction",
        "should_release",
        "approved_for_release",
    }
