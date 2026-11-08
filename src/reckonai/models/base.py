"""The declarative base, the tenant mixin, and the money column helper.

Everything structural that Phase 3's eight financial tables will also need
lives here, so it is decided once rather than per table.

Four things are set up, and each prevents a specific failure:

**A constraint naming convention.** Alembic names constraints from this, and
without it a constraint gets a server-generated name that differs between
machines - so a `downgrade()` cannot drop what an `upgrade()` created. That is
not a theoretical problem: specs/2026-11-07-repo-deployment-skeleton made
reversibility a property of every revision from the first one.

**A type annotation map.** `Mapped[Decimal]` lands on `NUMERIC(20, 6)` and
`Mapped[datetime]` on `TIMESTAMPTZ`, so a column declared from an annotation
cannot quietly get the wrong SQL type. specs/tech-stack.md section 2 fixes both.

**`TenantMixin`.** specs/tech-stack.md section 10 requires a `tenant_id` on
every table carrying marketplace data. A mixin makes it one line per table;
`tests/test_models_schema.py` makes it mandatory.

**`money_columns`.** The one place an amount can be separated from its
currency. See the function.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    composite,
    declared_attr,
    mapped_column,
)

from reckonai.money import InvalidAmount, Money

#: How Alembic names the constraints it creates.
#:
#: `ck` interpolates `%(constraint_name)s`, so **every CheckConstraint in this
#: package must be given a short name** - it becomes the suffix. An unnamed
#: check raises at table definition time rather than producing a surprise in a
#: migration, which is the preferable failure.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The declarative base for every ReckonAI table.

    `metadata` on this class is what `src/reckonai/migrations/env.py` compares
    against for autogenerate, and what the schema gates in
    `tests/test_models_schema.py` iterate. Both depend on every model module
    having been imported, which `reckonai.models.__init__` guarantees.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        # The money precision from specs/tech-stack.md section 2. A column that
        # needs different precision - an FX rate at NUMERIC(20, 10), a
        # commission rate at NUMERIC(9, 6) - states it explicitly.
        Decimal: Numeric(20, 6),
        # Timezone-aware, always. A naive timestamp in a system reconciling
        # across PayPal's clock and a marketplace's own is a defect waiting for
        # a daylight-saving boundary.
        datetime: DateTime(timezone=True),
        # `uuid.UUID` is deliberately absent: SQLAlchemy 2.0 already maps it to
        # its own `Uuid` type, which renders as native `UUID` on Postgres. An
        # entry here mapping it to itself makes SQLAlchemy try to instantiate
        # `uuid.UUID()` as a SQL type, which raises at class definition time.
    }


class TimestampMixin:
    """`created_at`, server-defaulted.

    Server-side rather than application-side so a row inserted by a migration
    or by hand still carries one. specs/tech-stack.md section 10 requires
    reconciliation runs and reports to be append-only and timestamped; this is
    the floor beneath that.
    """

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(server_default=func.now(), nullable=False)


class TenantMixin:
    """`tenant_id`, NOT NULL, indexed, referencing `tenant.id`.

    A **tenant** is one marketplace operator - ReckonAI's customer, and the
    *platform* in PayPal's multiparty model. A **seller** is a merchant inside
    that marketplace, PayPal's `payee`. Every table except `tenant` itself
    carries this column, and
    `tests/test_models_schema.py::test_every_table_is_tenant_scoped` fails if
    one does not.

    `declared_attr` is required rather than stylistic: a `ForeignKey` object
    cannot be shared between tables, so each table needs its own.

    `ondelete="RESTRICT"` because deleting a tenant with financial records
    under it is never something to do silently.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            ForeignKey("tenant.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )


def _money_or_none(amount: Decimal | None, currency: str | None) -> Money | None:
    """Build a `Money` from a column pair, or `None` when the pair is absent.

    **`composite()` does not do this for you**, which was established by test
    rather than assumed. SQLAlchemy calls the composite's class with whatever
    the columns hold, so an all-NULL pair reaches `Money(None, None)` and raises
    `UnknownCurrency` - on every load of a commission rule with no fixed
    component, which is most of them.

    So the composite is built from this factory instead of from `Money`
    directly. Three cases, and the third is the one worth having:

      - both NULL: the value is absent, and `None` is the right answer
      - both set: an ordinary `Money`
      - exactly one set: raise. The `money_pair_check` CHECK constraint should
        have made this unreachable, so meeting it means the constraint is
        missing from the table - a half-written amount is not something to
        paper over with a `None` (specs/tech-stack.md section 9.3).
    """
    if amount is None and currency is None:
        return None

    if amount is None or currency is None:
        raise InvalidAmount(
            f"a half-populated money pair reached the model layer: "
            f"amount={amount!r}, currency={currency!r}. The CHECK constraint "
            "from money_pair_check() is missing from this table."
        )

    return Money(amount, currency)


def money_columns(name: str, *, nullable: bool = False) -> Mapped[Money]:
    """Declare one monetary value as a `NUMERIC(20, 6)` + `VARCHAR(3)` pair.

        total: Mapped[Money] = money_columns("total")

    emits `total_amount` and `total_currency` and maps them to a single `Money`
    attribute. Reads and writes go through `Money`, so the ORM layer has no way
    to hand out an amount without its currency.

    **This is the one place in the codebase where an amount could be separated
    from its currency.** specs/tech-stack.md section 9.3 names that as the most
    likely place for a money mapping to be quietly wrong: "map `item_total` to
    an amount and forget the currency, and the system will compare numbers
    across currencies and report confident nonsense." Declaring the pair from
    one call means there is one such place rather than one per table.

    For a nullable value, pair this with `money_pair_check(name)` in
    `__table_args__`, so the database also refuses an amount with no currency.

    The composite is built from `_money_or_none` rather than from `Money`, for
    the reason documented there: `composite()` would otherwise construct
    `Money(None, None)` for an absent value. The factory is used for
    non-nullable pairs too, so there is one code path - a `None` assigned to a
    non-nullable attribute then fails at flush, against the column's NOT NULL,
    which is a clearer error than one raised mid-construction.
    """
    return composite(
        _money_or_none,
        mapped_column(f"{name}_amount", Numeric(20, 6), nullable=nullable),
        mapped_column(f"{name}_currency", String(3), nullable=nullable),
    )


def money_pair_check(name: str) -> CheckConstraint:
    """Require a nullable money pair to be wholly absent or wholly present.

    specs/tech-stack.md section 9.3 treats an amount without a resolved currency
    as a **hard failure**, not a warning. Expressing that in DDL means it holds
    for a row written by a migration, a seed script or a hand-typed `INSERT`,
    not only for one written through the ORM.
    """
    return CheckConstraint(
        f"({name}_amount IS NULL) = ({name}_currency IS NULL)",
        name=f"{name}_pair",
    )
