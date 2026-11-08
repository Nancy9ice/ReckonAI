"""The canonical model: SQLAlchemy declarative tables.

**Importing this package is what makes `Base.metadata` complete.** Three things
depend on that and would silently do the wrong thing with a partial metadata:

  - `src/reckonai/migrations/env.py` compares `Base.metadata` against the
    database for autogenerate. A model module that was never imported looks
    like a table to drop.
  - `tests/test_models_schema.py` iterates `Base.metadata.sorted_tables` for
    the tenant and NUMERIC gates. A table absent from the metadata is a table
    the gates do not check.
  - `alembic check` reports no drift for a table it cannot see.

So every model module is imported here, and new ones in Phase 3 must be added
to this file. Re-exporting the classes is the mechanism: `__all__` alone would
not trigger the import.
"""

from __future__ import annotations

from reckonai.models.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    money_columns,
    money_pair_check,
)
from reckonai.models.catalogue import (
    DISBURSEMENT_MODES,
    CommissionRule,
    Order,
    OrderItem,
    Seller,
    Tenant,
)

__all__ = [
    "DISBURSEMENT_MODES",
    "Base",
    "CommissionRule",
    "Order",
    "OrderItem",
    "Seller",
    "TenantMixin",
    "Tenant",
    "TimestampMixin",
    "money_columns",
    "money_pair_check",
]
