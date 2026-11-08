"""The one place a ledger direction becomes a sign.

specs/2026-11-08-canonical-events/requirements.md D7 moves the sign out of the
data - `seller_ledger_entry.amount` is an unsigned magnitude and
`transaction_type` carries the direction - which means something has to put it
back. The failure mode if that is left implicit is twelve call sites each doing
it slightly differently, one of them inverting a `commission_reversal`.

So it happens here, once.

**Pure, with no I/O** (specs/tech-stack.md section 6). The entry is taken as a
`typing.Protocol` rather than as the ORM class, so `reckonai.engine` imports
nothing from `reckonai.models` and the purity gate in
`tests/test_money_purity.py` stays satisfiable. An ORM row and a test dataclass
both fit the protocol, which is the same approach
`reckonai.engine.rules.resolve_commission_rule` takes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from reckonai.money import Money

#: The two directions, repeated here rather than imported.
#:
#: `reckonai.models.lifecycle` holds the same pair for the `CHECK` constraint,
#: and importing it would make `engine/` depend on `models/` - which is exactly
#: what the purity gate forbids. `tests/test_ledger.py` asserts the two
#: definitions agree, so the duplication cannot drift silently.
DEBIT = "debit"
CREDIT = "credit"


class UnknownTransactionType(ValueError):
    """A direction that is neither `debit` nor `credit`.

    Raised rather than defaulted. specs/tech-stack.md section 9.6 forbids
    defaulting a monetary value, and silently treating an unrecognised
    direction as a credit would flip the sign of a seller's position without a
    sound.
    """


@runtime_checkable
class LedgerEntry(Protocol):
    """The shape `signed` needs: a direction and an unsigned amount."""

    @property
    def transaction_type(self) -> str: ...

    @property
    def amount(self) -> Money: ...


def signed(transaction_type: str, amount: Money) -> Money:
    """Apply a ledger direction to an unsigned amount.

    A credit is positive, a debit negative. The input is expected to be the
    unsigned magnitude the database's `CHECK (amount_amount > 0)` guarantees;
    this function does not re-check it, because a negative amount arriving here
    means the constraint is missing from the table rather than that the sign
    should be flipped twice.

    >>> signed("credit", Money("90.00", "USD"))
    Money(amount=Decimal('90.00'), currency='USD')
    >>> signed("debit", Money("40.00", "USD"))
    Money(amount=Decimal('-40.00'), currency='USD')
    """
    if transaction_type == CREDIT:
        return amount

    if transaction_type == DEBIT:
        return -amount

    raise UnknownTransactionType(
        f"{transaction_type!r} is neither {CREDIT!r} nor {DEBIT!r}. A ledger "
        "entry's direction is never defaulted: treating an unrecognised one as "
        "a credit would flip a seller's position silently."
    )


def position(entries: Iterable[LedgerEntry], currency: str) -> Money:
    """Sum one currency's entries into a seller's position.

    **Single-currency by construction.** `Money` refuses mixed-currency
    addition, so summing a seller who trades in USD and EUR would raise - which
    is correct (specs/mission.md P2: two amounts are never comparable because
    their numbers look alike) but unhelpful as an error. The currency is
    therefore a parameter, entries in other currencies are skipped, and a
    caller wanting a seller's whole position calls this once per currency.

    An empty selection returns zero **of the requested currency**, which is a
    real answer: a seller with no USD entries has a USD position of zero. That
    is not a defaulted amount in the sense section 9.6 forbids - the currency
    was supplied by the caller and the sum of nothing is genuinely nothing.
    """
    total = Money("0", currency)

    for entry in entries:
        if entry.amount.currency != currency:
            continue
        total = total + signed(entry.transaction_type, entry.amount)

    return total
