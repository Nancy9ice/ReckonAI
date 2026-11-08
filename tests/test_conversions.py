"""FX conversions carry only what PayPal supplies, and refuse the rest.

specs/2026-11-08-canonical-events/validation.md C5, C6, C7 and C8.

This is the table `requirements.md` section 6 names as the one most likely to
be wrong in a way tests do not catch, because the only cross-currency fixture
in existence is a single capture. The constraints below are therefore enforced
by the **database**, so they hold for a row written by a seed script or a
hand-typed INSERT and not only for one written through the ORM.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Numeric
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.models import Capture, FxConversion, RawPayload, Refund, Tenant
from reckonai.money import Money
from test_events_capture import make_capture
from test_events_refund import make_refund

#: The observed cross-currency capture, to 15 decimal places as PayPal sent it.
#: Parsed from a string into `Decimal`; a `float` here would lose digits before
#: the value ever reached the database.
OBSERVED_RATE = Decimal("1.089477173902071")

#: The net being converted - 82.23 EUR - and PayPal's reported settlement.
#: Note `89.59` is **reported**, not computed: 82.23 * the rate is
#: 89.59072... and PayPal rounds to the cent while leaving the rate unrounded.
OBSERVED_SOURCE = Money("82.23", "EUR")
OBSERVED_TARGET = Money("89.59", "USD")


def make_conversion(tenant: Tenant, payload: RawPayload, **overrides: object):
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "source": OBSERVED_SOURCE,
        "target": OBSERVED_TARGET,
        "rate": OBSERVED_RATE,
        "provider": "paypal",
        "is_derived": False,
    }
    fields.update(overrides)
    return FxConversion(**fields)


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


# ---------------------------------------------------------------------------
# C5 - the nine fields, at the right types
# ---------------------------------------------------------------------------


def test_the_conversion_carries_all_nine_fields() -> None:
    """validation.md C5. The names are asserted literally.

    `is_derived` in particular keeps the name specs/roadmap.md Phase 3 gives
    it, rather than being renamed for precision (requirements.md D10).
    """
    columns = set(FxConversion.__table__.c.keys())

    for name in (
        "source_amount",
        "source_currency",
        "target_amount",
        "target_currency",
        "rate",
        "converted_at",
        "fee_amount",
        "fee_currency",
        "provider",
        "is_derived",
    ):
        assert name in columns, name


def test_the_rate_column_has_the_precision_the_specs_set() -> None:
    """`NUMERIC(20, 10)` - a floor, not a target.

    specs/tech-stack.md section 2. The observed rate had 15 decimal places as a
    string, so the stored value is a rounding of it; what must not happen is
    the value passing through `float` on the way in.
    """
    column = FxConversion.__table__.c.rate

    assert isinstance(column.type, Numeric)
    assert column.type.precision == 20
    assert column.type.scale == 10
    assert column.type.asdecimal is not False


def test_the_provenance_flag_cannot_be_unstated() -> None:
    """`is_derived` is NOT NULL with no default.

    A `server_default` of false would make every unstated timestamp claim to be
    provider-supplied, which is the inversion specs/mission.md P3 forbids.
    """
    column = FxConversion.__table__.c.is_derived

    assert not column.nullable
    assert column.default is None
    assert column.server_default is None


# ---------------------------------------------------------------------------
# C6 - the fee rule
# ---------------------------------------------------------------------------


def test_a_null_fee_inserts(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """The normal case, and the only one PayPal's data produces today.

    No conversion fee exists anywhere in the transaction APIs, so `fee` stays
    NULL on every row this product will write in the MVP.
    """
    conversion = make_conversion(tenant, payload, capture_id=capture.id, fee=None)
    session.add(conversion)
    session.flush()

    assert conversion.fee is None


def test_a_zero_fee_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """validation.md C6, and the point of the whole constraint.

    **Absent and zero are different claims.** A zero would read downstream as
    "a conversion fee was measured and it was nothing", which is a fabricated
    financial value (specs/mission.md P3). The residue of net times rate
    against the reported settlement is PayPal's rounding, and `paypal_fee` is
    not a conversion fee - substituting it is the substitution P3 forbids.
    """
    session.add(
        make_conversion(
            tenant,
            payload,
            capture_id=capture.id,
            fee=Money("0.00", "EUR"),
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_real_fee_inserts(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """The constraint refuses zero without foreclosing a real fee.

    If a provider ever does report one, it is storable without a migration.
    That is the difference between `fee <> 0` and `fee IS NULL`.
    """
    conversion = make_conversion(
        tenant, payload, capture_id=capture.id, fee=Money("0.42", "EUR")
    )
    session.add(conversion)
    session.flush()

    assert conversion.fee == Money("0.42", "EUR")


# ---------------------------------------------------------------------------
# C7 - the same-currency refusal
# ---------------------------------------------------------------------------


def test_a_same_currency_conversion_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """validation.md C7. A conversion between one currency and itself is not one.

    This is how "created **only** where PayPal reported an exchange rate" is
    enforced rather than documented: a same-currency transaction has no
    conversion, and an empty or degenerate row would be indistinguishable from
    a conversion whose fields went missing.
    """
    session.add(
        make_conversion(
            tenant,
            payload,
            capture_id=capture.id,
            source=Money("82.23", "USD"),
            target=Money("82.23", "USD"),
            rate=Decimal("1"),
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_non_positive_rate_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """A rate of zero or less is not a rate.

    Not a tolerance and not a rounding constant - specs/roadmap.md Phase 11
    warns against introducing one of those while chasing noise, and this is
    neither.
    """
    session.add(
        make_conversion(tenant, payload, capture_id=capture.id, rate=Decimal("0"))
    )

    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# C8 - one-to-many, and the exactly-one-parent rule
# ---------------------------------------------------------------------------


def test_two_conversions_against_one_refund_insert(
    session: Session, tenant: Tenant, payload: RawPayload, refund: Refund
) -> None:
    """validation.md C8. A refund declares an **array** of conversion entries.

    specs/roadmap.md Phase 3 requires this explicitly, because a capture
    declares a single object and nothing in the schema may assume one
    conversion per record.

    **This check is weaker than it looks**, and validation.md says so: it
    proves our schema permits the shape, not that it matches what PayPal sends,
    because no cross-currency refund fixture was creatable.
    `specs/paypal-fields.md` records `net_amount_breakdown` as **unanswered
    rather than absent**.
    """
    session.add(make_conversion(tenant, payload, refund_id=refund.id))
    session.add(
        make_conversion(
            tenant,
            payload,
            refund_id=refund.id,
            source=Money("17.77", "EUR"),
            target=Money("19.36", "USD"),
        )
    )
    session.flush()


def test_both_parents_set_is_rejected(
    session: Session,
    tenant: Tenant,
    payload: RawPayload,
    capture: Capture,
    refund: Refund,
) -> None:
    """validation.md C7's exactly-one half, on `num_nonnulls`."""
    session.add(
        make_conversion(
            tenant, payload, capture_id=capture.id, refund_id=refund.id
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_neither_parent_set_is_rejected(
    session: Session, tenant: Tenant, payload: RawPayload
) -> None:
    """An orphan conversion is evidence about nothing."""
    session.add(make_conversion(tenant, payload))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_observed_fixture_round_trips_at_full_precision(
    session: Session, tenant: Tenant, payload: RawPayload, capture: Capture
) -> None:
    """The rate survives storage as a `Decimal`, and the target is not recomputed.

    `82.23 * 1.089477173902071` is `89.5907...`, and PayPal reported `89.59`.
    The difference is PayPal's cent-rounding. Phase 11 compares against the
    stored target; recomputing is one of the two named ways to fail its
    zero-false-positive gate.
    """
    conversion = make_conversion(tenant, payload, capture_id=capture.id)
    session.add(conversion)
    session.flush()
    session.expire(conversion)

    assert conversion.rate == OBSERVED_RATE.quantize(Decimal("0.0000000001"))
    assert conversion.target == OBSERVED_TARGET
    assert conversion.source == OBSERVED_SOURCE

    # The residue that must never be reported as a discrepancy.
    recomputed = conversion.source.amount * conversion.rate
    assert recomputed != conversion.target.amount
    assert abs(recomputed - conversion.target.amount) < Decimal("0.01")
