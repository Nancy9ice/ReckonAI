"""The tables this project is expected to have, in one place.

Three gates assert against the table list and each one used to carry its own
copy: `tests/test_models_schema.py` over `Base.metadata`,
`tests/test_migrations.py` over the schema a migration produced, and
`tests/test_model_registry.py` over both. Phase 3 adds nine tables, so three
hard-coded lists would be three chances to update two of them.

**The useful property is "exactly these tables", not "some tables exist"** -
that is the note Phase 2 left in
`test_upgrade_creates_the_catalogue_tables`, and it is why this module lists
names rather than a count. A table that silently failed to register is then
named in the failure instead of inferred from a number.

Adding a table means adding its name here and nowhere else.
"""

from __future__ import annotations

#: Phase 2's catalogue: specs/roadmap.md Phase 2.
CATALOGUE_TABLES: tuple[str, ...] = (
    "commission_rule",
    "order",
    "order_item",
    "seller",
    "tenant",
)

#: Phase 3's event side: specs/roadmap.md Phase 3, plus the payload store.
#: Grown one group at a time as specs/2026-11-08-canonical-events/plan.md
#: lands each table.
EVENT_TABLES: tuple[str, ...] = (
    "capture",
    "disbursement",
    "dispute",
    "fx_conversion",
    "payment",
    "platform_fee",
    "raw_payload",
    "refund",
)

#: Every table in `Base.metadata`. Excludes `alembic_version`, which Alembic
#: owns and which never appears in the declarative metadata.
MODEL_TABLES: tuple[str, ...] = tuple(sorted(CATALOGUE_TABLES + EVENT_TABLES))

#: Every table in the database, which is the above plus Alembic's own.
DATABASE_TABLES: tuple[str, ...] = tuple(sorted(("alembic_version", *MODEL_TABLES)))
