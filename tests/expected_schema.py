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
    "seller_ledger_entry",
)

#: Block A's connector side: the versioned field mapping and the quarantine
#: store. specs/2026-11-08-block-a-marketplace-connector/requirements.md D7 and
#: D11.
CONNECTOR_TABLES: tuple[str, ...] = (
    "connector_mapping",
    "quarantined_record",
)

#: What demo dataset a database holds, so a deploy can tell stale from
#: current. specs/2026-11-09-paypal-capture/requirements.md D13.
DEMO_TABLES: tuple[str, ...] = ("demo_seed",)

#: Block D's execution substrate and its output: the queue, the dedupe marker,
#: and the runs and exceptions Block C froze a contract for.
#: specs/2026-11-09-queue-scheduled-run/requirements.md D1, D5, D6.
QUEUE_TABLES: tuple[str, ...] = (
    "job",
    "processed_event",
    "reconciliation_exception",
    "reconciliation_run",
)

#: Block E's output: one row per investigation attempt, and one per ranked
#: candidate cause. specs/2026-11-10-investigation-agent/requirements.md D1.
#:
#: Append-only, like the runs and exceptions above, and holding no monetary
#: value - specs/mission.md P1 keeps every figure in a report coming from the
#: deterministic engine.
AGENT_TABLES: tuple[str, ...] = (
    "investigation",
    "investigation_cause",
)

#: Every table in `Base.metadata`. Excludes `alembic_version`, which Alembic
#: owns and which never appears in the declarative metadata.
MODEL_TABLES: tuple[str, ...] = tuple(
    sorted(
        CATALOGUE_TABLES
        + EVENT_TABLES
        + CONNECTOR_TABLES
        + DEMO_TABLES
        + QUEUE_TABLES
        + AGENT_TABLES
    )
)

#: Every table in the database, which is the above plus Alembic's own.
DATABASE_TABLES: tuple[str, ...] = tuple(sorted(("alembic_version", *MODEL_TABLES)))
