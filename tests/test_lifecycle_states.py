"""The state vocabularies carry what the roadmap requires, by name.

specs/2026-11-08-canonical-events/validation.md C9. Asserted by name rather
than by count, so dropping a state is a failure that says which one rather than
a smaller tuple nobody notices.

No database is needed here: these are tuples and a constraint factory. The
database half - that a value outside a vocabulary is rejected - is C10, and it
lives with the tables that carry the columns.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint

from reckonai.models.lifecycle import (
    DISBURSEMENT_STATES,
    LEDGER_ENTRY_CATEGORIES,
    RECONCILIATION_STATES,
    TRANSACTION_TYPES,
    reconciliation_state_check,
    state_check,
)

#: Postgres truncates an identifier at 63 characters, and Phase 2's naming
#: convention wraps a constraint name in `ck_%(table_name)s_`.
MAX_IDENTIFIER_LENGTH = 63

ALL_VOCABULARIES = {
    "RECONCILIATION_STATES": RECONCILIATION_STATES,
    "DISBURSEMENT_STATES": DISBURSEMENT_STATES,
    "LEDGER_ENTRY_CATEGORIES": LEDGER_ENTRY_CATEGORIES,
    "TRANSACTION_TYPES": TRANSACTION_TYPES,
}


@pytest.mark.parametrize(
    "state",
    [
        # specs/mission.md P4's non-error states, which are the reason this
        # vocabulary exists rather than a status string.
        "unmatched",
        "delayed",
        "awaiting_evidence",
    ],
)
def test_the_non_error_states_are_present(state: str) -> None:
    assert state in RECONCILIATION_STATES


@pytest.mark.parametrize(
    "state",
    [
        # specs/multiparty-direction.md section 4's five states, with the
        # INSTANT case named and state 5 split in two.
        "disbursed_at_capture",
        "funds_held",
        "release_requested",
        "disbursement_completed",
        "disbursement_pending",
        "disbursement_failed",
    ],
)
def test_the_disbursement_states_are_present(state: str) -> None:
    assert state in DISBURSEMENT_STATES


@pytest.mark.parametrize(
    "category",
    [
        "sale",
        "commission",
        # The reversal is automatic and pro rata on PayPal's side, so the
        # ledger needs somewhere to record it.
        "commission_reversal",
        "refund",
        "dispute",
        "adjustment",
    ],
)
def test_the_ledger_categories_are_present(category: str) -> None:
    assert category in LEDGER_ENTRY_CATEGORIES


def test_the_transaction_types_are_exactly_debit_and_credit() -> None:
    """Exactly two, because the column is `VARCHAR(6)` and `credit` fills it.

    A third value would silently truncate rather than raise.
    """
    assert TRANSACTION_TYPES == ("debit", "credit")
    assert max(len(value) for value in TRANSACTION_TYPES) <= 6


@pytest.mark.parametrize("name", sorted(ALL_VOCABULARIES))
def test_no_vocabulary_contains_a_duplicate(name: str) -> None:
    """A duplicate renders a `CHECK` with a repeated literal.

    Harmless in SQL and a sign the tuple was edited carelessly, which is worth
    catching while the tuples are small.
    """
    values = ALL_VOCABULARIES[name]

    assert len(values) == len(set(values))


@pytest.mark.parametrize("name", sorted(ALL_VOCABULARIES))
def test_every_value_is_lower_case_and_unspaced(name: str) -> None:
    """One spelling convention, so a comparison never fails on a capital.

    `INSTANT` and `DELAYED` are upper-case because they are PayPal's own values
    echoed back; these are ours, and they are lower-case snake throughout.
    """
    for value in ALL_VOCABULARIES[name]:
        assert value == value.lower()
        assert " " not in value


def test_state_check_renders_the_whole_vocabulary() -> None:
    constraint = state_check("state", ("a", "b"), name="state")

    assert isinstance(constraint, CheckConstraint)
    assert str(constraint.sqltext) == "state IN ('a', 'b')"


def test_state_check_can_permit_null() -> None:
    """For a column where "not stated" is an answer rather than a gap."""
    constraint = state_check("state", ("a",), name="state", nullable=True)

    assert str(constraint.sqltext) == "state IS NULL OR state IN ('a')"


def test_the_generated_constraint_name_fits_an_identifier() -> None:
    """Phase 2's convention wraps the name, and Postgres truncates at 63.

    A truncated name is the specific failure where `downgrade()` cannot drop
    what `upgrade()` created, because the two computed different identifiers.
    """
    constraint = reconciliation_state_check()
    longest_table = "seller_ledger_entry"

    rendered = f"ck_{longest_table}_{constraint.name}"

    assert len(rendered) <= MAX_IDENTIFIER_LENGTH


def test_nothing_orders_the_disbursement_states() -> None:
    """The absence of an ordering is load-bearing, so it is pinned.

    PayPal auto-releases held funds after 28 days, so a transaction reaches
    `disbursement_completed` without ever passing through `release_requested`.
    This module exports no successor map, no transition table and no ordering
    helper, and a later contributor adding one needs to read
    specs/multiparty-direction.md section 4 first.
    """
    import reckonai.models.lifecycle as lifecycle

    exported = set(dir(lifecycle))

    assert not exported & {"STATE_TRANSITIONS", "next_state", "can_transition"}
