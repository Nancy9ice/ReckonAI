"""The lifecycle state vocabularies, and the mixin that carries one.

Three vocabularies, each a tuple of strings, plus a helper that turns a tuple
into the `CHECK` constraint enforcing it. The tuple is the single source: the
constraint is generated from it, so the Python values and the database values
cannot drift apart.

**No native Postgres enum type, deliberately.** This is the phase that owns the
lifecycle enum, and it continues specs/2026-11-07-canonical-core/requirements.md
D9 rather than meeting its trap one phase later: Alembic's autogenerate does not
reliably emit a native enum type's `CREATE`/`DROP` alongside its table's, so a
downgrade leaves an orphaned type and the next upgrade fails on a duplicate.
specs/2026-11-08-canonical-events/requirements.md D1 records the choice and the
rejected alternative.

**Nothing here enforces an ordering between the disbursement states, and that
is not an omission.** PayPal disburses held funds automatically after 28 days
with no marketplace instruction, so `disbursement_completed` is reachable
without `release_requested` ever occurring. An engine that treats
`release_requested` as a required predecessor mis-models every auto-released
transaction (specs/multiparty-direction.md section 4).
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

#: Where a record sits in reconciliation, which is **not** a verdict about it.
#:
#: The last three exist because specs/mission.md P4 requires them: "delayed
#: events, incomplete lifecycles and awaited evidence are first-class states,
#: distinct from genuine discrepancies". A false positive costs the user's trust
#: faster than a missed exception does, and these are the states that keep a
#: delay from being reported as an error.
#:
#: What is *wrong* with a record is a Phase 11 exception row, not a state here.
RECONCILIATION_STATES: tuple[str, ...] = (
    # Landed in the canonical model; no counterpart looked for yet.
    "ingested",
    # Paired with its counterpart in the other system.
    "matched",
    # No counterpart found. A non-error state: it may never have existed, or it
    # may be a genuine discrepancy, and deciding which is Phase 11's work.
    "unmatched",
    # A counterpart is expected and has not arrived yet. Non-error.
    "delayed",
    # Blocked on evidence from outside the system - a dispute waiting on a
    # seller response is the observed case. Non-error.
    "awaiting_evidence",
)

#: Whether, and how, the seller actually got the money.
#:
#: The five states of specs/multiparty-direction.md section 4, with the
#: `INSTANT` case named explicitly. States 2 through 5 there are **non-error
#: states**: holding funds under `DELAYED` is the expected intermediate
#: condition of a healthy transaction, not a marketplace failure to pay.
#:
#: Classifying `funds_held` or `release_requested` as a discrepancy would turn
#: every `DELAYED` transaction into an exception, breaching
#: specs/roadmap.md Phase 11's hardest exit criterion by design rather than by
#: bug.
DISBURSEMENT_STATES: tuple[str, ...] = (
    # `INSTANT`: the seller was paid at capture and no disbursement record
    # exists at all. This is why the column lives on `capture` - see
    # requirements.md D1.
    "disbursed_at_capture",
    # `DELAYED`, state 2: PayPal holds the seller's funds.
    "funds_held",
    # `DELAYED`, state 3: the marketplace asked PayPal to release them.
    # **Not a required predecessor of completion** - see the module docstring.
    "release_requested",
    # `DELAYED`, state 4: the seller has the money.
    "disbursement_completed",
    # `DELAYED`, state 5, split in two because they need different handling: a
    # release in flight is healthy, a failed one needs a cause.
    "disbursement_pending",
    "disbursement_failed",
)

#: What kind of movement a `seller_ledger_entry` records.
#:
#: This is what Phase 11 asks "is there a refund entry for this refund?"
#: against. specs/mission.md section 6 Scenario 1 - a partial refund never
#: reflected in the seller ledger - is a missing row of one of these
#: categories, so the vocabulary is what makes the absence findable.
#:
#: It is `entry_category` rather than `entry_type` so it cannot be confused
#: with `transaction_type`, which carries the debit/credit direction.
LEDGER_ENTRY_CATEGORIES: tuple[str, ...] = (
    "sale",
    "commission",
    # PayPal reverses the platform fee automatically and pro rata on a refund
    # and on a lost dispute alike, so the marketplace does not keep commission
    # on a reversed transaction. The ledger needs a category for that.
    "commission_reversal",
    "refund",
    "dispute",
    "adjustment",
    "fee",
)

#: The two directions a ledger entry can move money, and the whole reason the
#: amount column is unsigned.
#:
#: requirements.md D7: with a signed amount there is no way to distinguish a
#: wrong sign from a legitimate negative, so a zero or negative amount cannot be
#: refused. With the direction as its own column, `CHECK (amount_amount > 0)`
#: becomes possible and the database refuses both.
TRANSACTION_TYPES: tuple[str, ...] = ("debit", "credit")


def state_check(
    column: str, values: tuple[str, ...], *, name: str, nullable: bool = False
) -> CheckConstraint:
    """Build the `CHECK` that pins a column to one vocabulary.

    Generated from the tuple rather than written out, so adding a state is a
    one-line change and the constraint cannot fall behind the Python values.

    `name` is **required**, and short on purpose: specs Phase 2's
    `NAMING_CONVENTION` interpolates `%(constraint_name)s` into
    `ck_%(table_name)s_%(constraint_name)s`, and Postgres truncates an
    identifier at 63 characters. A long name here produces a constraint whose
    `downgrade()` cannot find it by the name `upgrade()` used.

    `nullable=True` permits NULL alongside the vocabulary, for a column where
    "not stated" is a real answer rather than a gap to fill.
    """
    quoted = ", ".join(f"'{value}'" for value in values)
    expression = f"{column} IN ({quoted})"

    if nullable:
        expression = f"{column} IS NULL OR {expression}"

    return CheckConstraint(expression, name=name)


class LifecycleMixin:
    """`reconciliation_state`, NOT NULL, defaulting to `ingested`.

    Every financial table carries this. The default is set both
    application-side and server-side, so a row inserted by a migration, a seed
    script or a hand-typed `INSERT` gets one too - the same reasoning as
    `TimestampMixin`'s server default.

    **The `CHECK` is not supplied by this mixin.** A `declared_attr` for
    `__table_args__` has to merge with each table's own, and the merge is the
    kind of implicit machinery that fails quietly when a table forgets to call
    up the chain. Each table instead adds `reconciliation_state_check()` to its
    own `__table_args__`, which is one visible line per table and is enforced
    by `tests/test_models_schema.py` rather than by remembering.
    """

    @declared_attr
    def reconciliation_state(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(
            String(32),
            nullable=False,
            default="ingested",
            server_default="ingested",
            index=True,
        )


def reconciliation_state_check() -> CheckConstraint:
    """The `CHECK` for `LifecycleMixin`'s column. One line per table."""
    return state_check(
        "reconciliation_state",
        RECONCILIATION_STATES,
        name="reconciliation_state",
    )
