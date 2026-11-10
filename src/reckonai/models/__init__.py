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
from reckonai.models.connector import (
    MAPPING_PROVENANCES,
    MAPPING_STATUSES,
    QUARANTINE_REASONS,
    ConnectorMapping,
    QuarantinedRecord,
)
from reckonai.models.conversions import FxConversion
from reckonai.models.decision import DECISION_OUTCOMES, ExceptionDecision
from reckonai.models.demo import DemoSeed
from reckonai.models.disbursements import Disbursement
from reckonai.models.disputes import Dispute
from reckonai.models.events import Capture, Payment, Refund
from reckonai.models.fees import PlatformFee
from reckonai.models.investigation import (
    AGENT_PROVIDERS,
    INVESTIGABLE_CODES,
    VERDICTS,
    Investigation,
    InvestigationCause,
)
from reckonai.models.jobs import (
    DEFAULT_MAX_ATTEMPTS,
    JOB_STATUSES,
    JOB_TYPES,
    RETRY_DELAY_SECONDS,
    Job,
    ProcessedEvent,
)
from reckonai.models.ledger import SellerLedgerEntry
from reckonai.models.lifecycle import (
    DISBURSEMENT_STATES,
    LEDGER_ENTRY_CATEGORIES,
    RECONCILIATION_STATES,
    TRANSACTION_TYPES,
    LifecycleMixin,
    reconciliation_state_check,
    state_check,
)
from reckonai.models.payloads import (
    PAYLOAD_SOURCES,
    RawPayload,
    RawPayloadMixin,
    capture_or_refund_parent,
    raw_payload_fk,
)
from reckonai.models.reconciliation import (
    EVIDENCE_BASES,
    EXCEPTION_CODES,
    RUN_STATUSES,
    ReconciliationException,
    ReconciliationRun,
)

__all__ = [
    "DECISION_OUTCOMES",
    "ExceptionDecision",
    "AGENT_PROVIDERS",
    "DEFAULT_MAX_ATTEMPTS",
    "DISBURSEMENT_MODES",
    "DISBURSEMENT_STATES",
    "EVIDENCE_BASES",
    "EXCEPTION_CODES",
    "INVESTIGABLE_CODES",
    "JOB_STATUSES",
    "JOB_TYPES",
    "LEDGER_ENTRY_CATEGORIES",
    "MAPPING_PROVENANCES",
    "MAPPING_STATUSES",
    "PAYLOAD_SOURCES",
    "QUARANTINE_REASONS",
    "RECONCILIATION_STATES",
    "RETRY_DELAY_SECONDS",
    "RUN_STATUSES",
    "TRANSACTION_TYPES",
    "VERDICTS",
    "Base",
    "Capture",
    "CommissionRule",
    "ConnectorMapping",
    "DemoSeed",
    "Disbursement",
    "Dispute",
    "FxConversion",
    "Investigation",
    "InvestigationCause",
    "Job",
    "LifecycleMixin",
    "Order",
    "OrderItem",
    "Payment",
    "PlatformFee",
    "ProcessedEvent",
    "QuarantinedRecord",
    "RawPayload",
    "RawPayloadMixin",
    "ReconciliationException",
    "ReconciliationRun",
    "Refund",
    "Seller",
    "SellerLedgerEntry",
    "Tenant",
    "TenantMixin",
    "TimestampMixin",
    "capture_or_refund_parent",
    "money_columns",
    "money_pair_check",
    "raw_payload_fk",
    "reconciliation_state_check",
    "state_check",
]
