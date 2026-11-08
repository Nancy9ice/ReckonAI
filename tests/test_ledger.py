"""The seller ledger: unsigned, positive, and deliberately unconstrained.

specs/2026-11-08-canonical-events/validation.md C23.

The fourth group of tests here is the unusual one: it asserts that an
*inconsistent* row inserts. That absence of a constraint is a decision
(requirements.md D7), and a test is the only thing that stops a later
contributor adding one as a tidy-up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from reckonai.engine import UnknownTransactionType, position, signed
from reckonai.engine import ledger as engine_ledger
from reckonai.models import RawPayload, Seller, SellerLedgerEntry, Tenant
from reckonai.models.lifecycle import TRANSACTION_TYPES
from reckonai.money import CurrencyMismatch, Money

OCCURRED_AT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def make_entry(
    tenant: Tenant, seller: Seller, payload: RawPayload, **overrides: object
) -> SellerLedgerEntry:
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "raw_payload_id": payload.id,
        "seller_id": seller.id,
        "external_ref": f"LE-{uuid.uuid4().hex[:12]}",
        "entry_category": "sale",
        "transaction_type": "credit",
        "amount": Money("90.00", "USD"),
        "occurred_at": OCCURRED_AT,
    }
    fields.update(overrides)
    return SellerLedgerEntry(**fields)


@dataclass(frozen=True)
class FakeEntry:
    """A test double fitting the `LedgerEntry` protocol.

    Its existence is the point of the protocol: `engine/` never imports
    `models/`, so the pure functions must work against anything with the right
    two attributes.
    """

    transaction_type: str
    amount: Money


# ---------------------------------------------------------------------------
# The amount is unsigned, and the database enforces it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0.00", "-40.00"])
def test_a_non_positive_amount_is_rejected(
    session: Session,
    tenant: Tenant,
    seller: Seller,
    payload: RawPayload,
    amount: str,
) -> None:
    """validation.md C23, and what requirements.md D7 buys.

    With a signed amount there is no way to distinguish a wrong sign from a
    legitimate negative, so neither of these could be refused. With the
    direction in its own column, the database refuses both.
    """
    session.add(make_entry(tenant, seller, payload, amount=Money(amount, "USD")))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_debit_is_stored_as_an_unsigned_magnitude(
    session: Session, tenant: Tenant, seller: Seller, payload: RawPayload
) -> None:
    """A 40.00 refund is `debit` + `40.00`, never `-40.00`."""
    entry = make_entry(
        tenant,
        seller,
        payload,
        entry_category="refund",
        transaction_type="debit",
        amount=Money("40.00", "USD"),
    )
    session.add(entry)
    session.flush()
    session.expire(entry)

    assert entry.amount == Money("40.00", "USD")
    assert entry.amount.amount > 0
    assert entry.transaction_type == "debit"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        # Within `VARCHAR(6)`, so the CHECK is what rejects it rather than the
        # column width - an over-long value raises `DataError` first, which
        # would make this test pass without the constraint existing.
        ("transaction_type", "DEBIT"),
        ("transaction_type", "dr"),
        ("entry_category", "nonsense"),
    ],
)
def test_a_value_outside_its_vocabulary_is_rejected(
    session: Session,
    tenant: Tenant,
    seller: Seller,
    payload: RawPayload,
    column: str,
    value: str,
) -> None:
    """The vocabularies are case-sensitive and exact.

    `DEBIT` is rejected: `INSTANT` and `DELAYED` are upper-case because they
    are PayPal's own values echoed back, and these are ours, which are
    lower-case throughout.
    """
    session.add(make_entry(tenant, seller, payload, **{column: value}))

    with pytest.raises(IntegrityError):
        session.flush()


def test_an_over_long_direction_is_rejected_by_the_column_width(
    session: Session, tenant: Tenant, seller: Seller, payload: RawPayload
) -> None:
    """`VARCHAR(6)` fits `credit` exactly and nothing longer.

    Worth its own test because the two rejections have different causes: this
    one is `DataError` from the column width, and the one above is
    `CheckViolation`. A test using an over-long value to probe the CHECK would
    pass even if the constraint were dropped.
    """
    session.add(
        make_entry(tenant, seller, payload, transaction_type="creditx")
    )

    with pytest.raises(DataError):
        session.flush()


# ---------------------------------------------------------------------------
# The deliberate absence of a cross-column constraint
# ---------------------------------------------------------------------------


def test_a_sale_may_be_a_debit(
    session: Session, tenant: Tenant, seller: Seller, payload: RawPayload
) -> None:
    """**This row is inconsistent, and it inserts.** requirements.md D7.

    A `sale` is always a credit in practice, so `sale` + `debit` is a
    disagreement - but the marketplace supplies both columns, and a
    disagreement between them is a **finding about the marketplace's data**
    rather than an insert to reject. Phase 2's D13 reasoning applied unchanged:
    enforcing it would convert a Phase 11 exception into an ingestion crash,
    and specs/tech-stack.md section 9.6 is clear that a record failing
    validation is quarantined and visible, never silently rejected.

    Pinned by test so it is not added later as a tidy-up.
    """
    session.add(
        make_entry(
            tenant,
            seller,
            payload,
            entry_category="sale",
            transaction_type="debit",
        )
    )
    session.flush()


def test_the_table_carries_no_running_balance() -> None:
    """requirements.md D7's dropped column, pinned.

    A `balance_after` is a derived monetary value persisted beside its inputs,
    which specs/tech-stack.md section 9.6 forbids, and an out-of-order
    ingestion makes it confidently wrong with nothing to recompute it. A
    position is computed from the entries, every time.
    """
    names = set(SellerLedgerEntry.__table__.c.keys())

    assert not names & {
        "balance_after_amount",
        "balance_after_currency",
        "running_balance_amount",
        "balance",
    }


# ---------------------------------------------------------------------------
# The sign helper
# ---------------------------------------------------------------------------


def test_a_credit_keeps_its_sign() -> None:
    assert signed("credit", Money("90.00", "USD")) == Money("90.00", "USD")


def test_a_debit_is_negated() -> None:
    assert signed("debit", Money("40.00", "USD")) == Money("-40.00", "USD")


def test_an_unknown_direction_raises() -> None:
    """Never defaulted to `credit`.

    Silently treating an unrecognised direction as a credit would flip a
    seller's position without a sound, and specs/tech-stack.md section 9.6
    forbids defaulting a monetary value.
    """
    with pytest.raises(UnknownTransactionType):
        signed("CREDIT", Money("1.00", "USD"))

    with pytest.raises(UnknownTransactionType):
        signed("", Money("1.00", "USD"))


def test_the_engine_and_the_model_agree_on_the_directions() -> None:
    """The two definitions of debit/credit cannot drift silently.

    `engine/` may not import `models/` - the purity gate forbids it - so the
    pair is spelled in both places. This is what keeps that duplication honest.
    """
    assert set(TRANSACTION_TYPES) == {engine_ledger.DEBIT, engine_ledger.CREDIT}


def test_the_mission_worked_example_sums_correctly() -> None:
    """specs/mission.md section 6: seller A at 90.00, then a 40.00 refund.

    The worked example in full belongs to Phase 10; this is only the ledger
    half of it, which is the part `signed` is responsible for. 90.00 credited
    then 40.00 debited leaves 50.00 - and a 54.00 entitlement after the
    commission adjustment is Phase 10's arithmetic, not this function's.
    """
    entries = [
        FakeEntry("credit", Money("90.00", "USD")),
        FakeEntry("debit", Money("40.00", "USD")),
    ]

    assert position(entries, "USD") == Money("50.00", "USD")


def test_a_position_skips_other_currencies() -> None:
    """Single-currency by construction, because `Money` refuses mixing.

    specs/mission.md P2: two amounts are never comparable because their numbers
    look alike. A seller trading in USD and EUR has two positions, and asking
    for one of them must not raise on the other's entries.
    """
    entries = [
        FakeEntry("credit", Money("90.00", "USD")),
        FakeEntry("credit", Money("75.00", "EUR")),
        FakeEntry("debit", Money("40.00", "USD")),
    ]

    assert position(entries, "USD") == Money("50.00", "USD")
    assert position(entries, "EUR") == Money("75.00", "EUR")


def test_an_empty_position_is_zero_of_the_requested_currency() -> None:
    """A real answer, not a defaulted amount.

    The currency came from the caller and the sum of nothing is genuinely
    nothing, so this is not the section 9.6 failure of inventing a figure.
    """
    assert position([], "KWD") == Money("0", "KWD")


def test_a_position_never_mixes_currencies_by_accident() -> None:
    """The guard behind the skip: `Money` itself refuses the addition.

    If `position` ever stopped filtering, this is the error it would produce
    rather than a wrong number.
    """
    with pytest.raises(CurrencyMismatch):
        Money("90.00", "USD") + Money("75.00", "EUR")


def test_a_three_decimal_currency_signs_correctly() -> None:
    """KWD has an exponent of 3, and the sign must not disturb it."""
    assert signed("debit", Money("1.234", "KWD")) == Money(
        Decimal("-1.234"), "KWD"
    )
