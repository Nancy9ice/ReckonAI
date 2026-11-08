"""Schema-level gates over `Base.metadata`.

These need no database. They read the model metadata, which is what a developer
edits, and they iterate it rather than listing tables - so **a table added in
Phase 3 is covered the moment it is declared**, with no change here.

That property is the entire point. A gate that enumerates today's five tables
passes today and notices nothing about the eight Phase 3 adds, which is when it
would matter. `tests/test_migrations.py` asserts the same rules against the
schema a migration actually produced; both can be true and only one of them is
what a deployed query runs against.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Boolean, Integer, Numeric, String, Table
from sqlalchemy.types import Float

from expected_schema import MODEL_TABLES
from reckonai.models import Base
from reckonai.money import supported_currencies

#: The only table permitted to have no `tenant_id`, because it is what one
#: points at. Everything else carrying marketplace data must be scoped
#: (specs/tech-stack.md section 10).
UNSCOPED_TABLES = frozenset({"tenant"})

#: Amount columns, per specs/tech-stack.md section 2.
AMOUNT_PRECISION = 20
AMOUNT_SCALE = 6

#: The floor for a rate's scale. An FX rate in Phase 3 wants `NUMERIC(20, 10)`
#: and a commission rate uses `NUMERIC(9, 6)`, so this is a minimum rather than
#: an exact value - six decimal places is a hundredth of a basis point.
MINIMUM_RATE_SCALE = 6


def tables() -> list[Table]:
    return list(Base.metadata.sorted_tables)


def table_ids(table: Table) -> str:
    return table.name


# ---------------------------------------------------------------------------
# The tenant gate
# ---------------------------------------------------------------------------


def test_the_metadata_is_not_empty() -> None:
    """A gate over an empty table list passes trivially.

    `reckonai.models.__init__` is what makes the metadata complete; if a module
    stopped being imported there, every test below would pass over nothing.
    """
    assert sorted(t.name for t in tables()) == list(MODEL_TABLES)


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_every_table_is_tenant_scoped(table: Table) -> None:
    """specs/tech-stack.md section 10 and CLAUDE.md: every table carrying
    marketplace data has a `tenant_id`.

    A **tenant** is one marketplace operator - ReckonAI's customer, and the
    platform in PayPal's multiparty model. Without this column one
    marketplace's reconciliation can include another's money, and the column is
    cheap now and expensive to retrofit once Phase 3's eight tables and every
    query exist.
    """
    if table.name in UNSCOPED_TABLES:
        assert "tenant_id" not in table.columns, (
            f"{table.name} is listed as unscoped but has a tenant_id; remove "
            "it from UNSCOPED_TABLES"
        )
        return

    assert "tenant_id" in table.columns, (
        f"{table.name} has no tenant_id. Add TenantMixin, or add the table to "
        "UNSCOPED_TABLES with a reason."
    )

    column = table.columns["tenant_id"]
    assert not column.nullable, f"{table.name}.tenant_id must be NOT NULL"
    assert column.foreign_keys, (
        f"{table.name}.tenant_id must reference tenant.id, or a row can name a "
        "tenant that does not exist"
    )


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_every_tenant_scoped_table_can_be_a_composite_foreign_key_target(
    table: Table,
) -> None:
    """requirements.md D8: a `UNIQUE (tenant_id, id)` on every scoped table.

    It exists so children can carry a composite foreign key on
    `(tenant_id, parent_id)`, which is what makes a cross-tenant link
    unwritable. Asserting it on every scoped table rather than only on today's
    parents means Phase 3's `capture` can reference `order` without first
    discovering the constraint is missing.
    """
    if table.name in UNSCOPED_TABLES:
        return

    pairs = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if hasattr(constraint, "columns")
    }

    assert ("tenant_id", "id") in pairs or ("id", "tenant_id") in pairs, (
        f"{table.name} has no UNIQUE (tenant_id, id); a child table cannot "
        f"carry a composite foreign key to it. Constraints found: {sorted(pairs)}"
    )


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_a_tenant_scoped_foreign_key_is_composite(table: Table) -> None:
    """A reference to a tenant-scoped table must carry `tenant_id` with it.

    This is the gate that would catch the realistic Phase 3 mistake: declaring
    `capture.order_id` as a plain single-column foreign key. It would work, and
    it would accept a capture pointing at another marketplace's order.
    """
    scoped = {t.name for t in tables() if t.name not in UNSCOPED_TABLES}

    for constraint in table.foreign_key_constraints:
        referred = constraint.referred_table.name
        if referred not in scoped:
            continue

        columns = [element.parent.name for element in constraint.elements]
        assert "tenant_id" in columns, (
            f"{table.name} references the tenant-scoped table {referred} "
            f"through {columns}, without tenant_id. Use a "
            f"ForeignKeyConstraint on (tenant_id, {referred}_id)."
        )


# ---------------------------------------------------------------------------
# The NUMERIC gate: the half that covers tables nobody has written yet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_no_column_is_a_binary_float(table: Table) -> None:
    """specs/tech-stack.md section 2: never `float`, at any layer.

    The AST gate in `tests/test_money_purity.py` reads our source; this reads
    the column types the driver round-trips through, which is the half that
    survives a `float` arriving from somewhere other than a literal in our
    code.
    """
    offenders = [
        f"{table.name}.{column.name}: {column.type}"
        for column in table.columns
        if isinstance(column.type, Float)
    ]

    assert not offenders, "Binary float columns:\n" + "\n".join(offenders)


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_amount_columns_are_exact_numeric(table: Table) -> None:
    """Every `*_amount` column is `NUMERIC(20, 6)` with its scale stated.

    A bare `NUMERIC` would be arbitrary-precision in Postgres and would
    therefore *pass* a naive "is it Numeric" check while storing a different
    number of decimal places per row - so the precision and scale are asserted,
    not just the type.
    """
    for column in table.columns:
        if not column.name.endswith("_amount"):
            continue

        qualified = f"{table.name}.{column.name}"
        assert isinstance(column.type, Numeric), f"{qualified} is not NUMERIC"
        assert column.type.precision == AMOUNT_PRECISION, (
            f"{qualified} has precision {column.type.precision}, "
            f"expected {AMOUNT_PRECISION}"
        )
        assert column.type.scale == AMOUNT_SCALE, (
            f"{qualified} has scale {column.type.scale}, expected {AMOUNT_SCALE}"
        )


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_rate_columns_are_exact_numeric_with_enough_scale(table: Table) -> None:
    """Every `*rate*` column is `NUMERIC` with at least six decimal places.

    A minimum rather than an exact value: Phase 3's `fx_conversion.rate` needs
    `NUMERIC(20, 10)` because PayPal's reported rate carries fifteen decimal
    places and specs/tech-stack.md section 2 calls `(20, 10)` "the floor, not a
    target".
    """
    for column in table.columns:
        if "rate" not in column.name:
            continue

        qualified = f"{table.name}.{column.name}"
        assert isinstance(column.type, Numeric), f"{qualified} is not NUMERIC"
        assert column.type.scale is not None, f"{qualified} has no stated scale"
        assert column.type.scale >= MINIMUM_RATE_SCALE, (
            f"{qualified} has scale {column.type.scale}, below the minimum "
            f"{MINIMUM_RATE_SCALE}"
        )


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_currency_columns_are_three_character_strings(table: Table) -> None:
    """A currency code is exactly three characters.

    `VARCHAR` without a length would accept a whole sentence, and the first
    symptom would be a `UnknownCurrency` raised on read - long after the write
    that caused it.
    """
    for column in table.columns:
        if not column.name.endswith("_currency"):
            continue

        qualified = f"{table.name}.{column.name}"
        assert isinstance(column.type, String), f"{qualified} is not a string"
        assert column.type.length == 3, (
            f"{qualified} has length {column.type.length}, expected 3"
        )


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_every_amount_has_a_matching_currency(table: Table) -> None:
    """specs/tech-stack.md section 9.3: an amount without a currency is a hard
    failure.

    `money_columns()` makes the pair impossible to get wrong when it is used.
    This catches the case where someone declares an amount column by hand and
    does not.
    """
    names = {column.name for column in table.columns}

    for name in sorted(names):
        if not name.endswith("_amount"):
            continue
        expected = f"{name.removesuffix('_amount')}_currency"
        assert expected in names, (
            f"{table.name}.{name} has no {expected}. Declare the pair with "
            "money_columns() rather than by hand."
        )


@pytest.mark.parametrize("table", tables(), ids=table_ids)
def test_a_nullable_money_pair_carries_its_check_constraint(table: Table) -> None:
    """A nullable amount needs `money_pair_check()` in `__table_args__`.

    Without it the database accepts an amount with no currency, and
    `_money_or_none` in the model layer raises on read instead - which is the
    right behaviour for a corrupt row but far too late to be useful.
    """
    checks = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )

    for column in table.columns:
        if not (column.name.endswith("_amount") and column.nullable):
            continue

        prefix = column.name.removesuffix("_amount")
        assert f"{prefix}_amount" in checks and f"{prefix}_currency" in checks, (
            f"{table.name}.{column.name} is nullable but has no "
            f"money_pair_check('{prefix}') constraint"
        )


# ---------------------------------------------------------------------------
# Column type sanity, so the gates above cannot pass over the wrong thing
# ---------------------------------------------------------------------------


def test_the_amount_gate_actually_sees_columns() -> None:
    """The gates above iterate and would pass over zero matching columns.

    This asserts there are amount, currency and rate columns to check, so a
    rename that silently emptied the gates fails here instead.
    """
    amounts: list[str] = []
    currencies: list[str] = []
    rates: list[str] = []

    for table in tables():
        for column in table.columns:
            if column.name.endswith("_amount"):
                amounts.append(f"{table.name}.{column.name}")
            if column.name.endswith("_currency"):
                currencies.append(f"{table.name}.{column.name}")
            if "rate" in column.name:
                rates.append(f"{table.name}.{column.name}")

    # Phase 2 pinned these as exact counts - 4 amounts, 5 currencies, one
    # rate. Phase 3 adds fifteen more money pairs across nine tables, and an
    # exact count would then be churn on every group for no signal: the
    # per-column type gates above already check *every* amount column, so the
    # only thing this test adds is that the list is not empty.
    #
    # The floor is Phase 2's own numbers, which still catches the failure it
    # was written for - a rename that silently emptied the gates.
    assert len(amounts) >= 4, amounts
    assert len(currencies) >= 5, currencies
    assert len(currencies) > len(amounts), (amounts, currencies)

    # Rate columns stay enumerated, because there are few of them and each one
    # carries a different precision for a stated reason: a commission rate is
    # `NUMERIC(9, 6)` and an FX rate `NUMERIC(20, 10)`, which
    # specs/tech-stack.md section 2 calls a floor rather than a target.
    assert set(rates) <= {"commission_rule.rate", "fx_conversion.rate"}, rates
    assert "commission_rule.rate" in rates, rates


def test_timestamps_are_timezone_aware() -> None:
    """A naive timestamp in a system reconciling PayPal's clock against a
    marketplace's own is a defect waiting for a daylight-saving boundary."""
    offenders: list[str] = []

    for table in tables():
        for column in table.columns:
            if not hasattr(column.type, "timezone"):
                continue
            if not column.type.timezone:
                offenders.append(f"{table.name}.{column.name}")

    assert not offenders, "Naive timestamp columns:\n" + "\n".join(offenders)


def test_the_disbursement_mode_check_matches_the_declared_modes() -> None:
    """The constant and the constraint must agree.

    They are declared in two places - `DISBURSEMENT_MODES` and the `CHECK` -
    and a drift between them would mean Python accepting a value the database
    rejects, or worse the reverse.
    """
    from reckonai.models import DISBURSEMENT_MODES

    order = Base.metadata.tables["order"]
    check = next(
        str(constraint.sqltext)
        for constraint in order.constraints
        if getattr(constraint, "name", "") == "ck_order_disbursement_mode"
    )

    for mode in DISBURSEMENT_MODES:
        assert f"'{mode}'" in check, f"{mode} is not in the CHECK constraint"


def test_the_currency_registry_fits_the_currency_columns() -> None:
    """Every code in the registry is storable.

    A four-character code added to the registry would be truncated by
    `VARCHAR(3)` on write, which Postgres does without complaint for a value
    that fits after trailing-space removal and errors on otherwise - either way
    the failure would surface far from the registry entry that caused it.
    """
    for code in supported_currencies():
        assert len(code) == 3, f"{code} does not fit a VARCHAR(3) currency column"


def test_quantity_like_columns_are_integers() -> None:
    """A quantity is a count, and a count is an integer.

    `quantity` as a `NUMERIC` would pass the float gate while allowing half an
    item, and `unit_price * quantity` would then produce a fractional line
    total that no allocation could seat on a minor unit.
    """
    item = Base.metadata.tables["order_item"]

    assert isinstance(item.columns["quantity"].type, Integer)


def test_the_capability_flags_are_booleans_with_a_server_default() -> None:
    """A seller onboarded before the column existed must read as `False`, not
    NULL - a NULL capability is indistinguishable from "not yet known" and
    Phase 11 would have to treat it as both."""
    seller = Base.metadata.tables["seller"]

    for name in ("partner_fee_enabled", "delay_funds_enabled"):
        column = seller.columns[name]
        assert isinstance(column.type, Boolean), name
        assert not column.nullable, name
        assert column.server_default is not None, name
