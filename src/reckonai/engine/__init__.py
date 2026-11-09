"""The deterministic reconciliation engine: pure functions, no I/O.

specs/tech-stack.md section 6 is a structural constraint rather than a
technology choice: "records in, expected financial position and classified
exceptions out. No database handle, no HTTP client, no clock reads inside the
calculation."

Two consequences, and both are enforced by `tests/test_money_purity.py` rather
than trusted:

  - **No I/O imports.** This package imports no SQLAlchemy, no `psycopg`, no
    HTTP client and nothing from `reckonai.db`. It takes the records it needs
    as arguments. The functions here therefore accept `typing.Protocol` types
    rather than the ORM classes, so an ORM row and a test dataclass both fit
    and this package never imports `reckonai.models`.
  - **No clock reads.** A function that depends on "now" takes the timestamp as
    a parameter. That is what makes a historical reconciliation run
    reproducible: re-running it for last Tuesday must resolve the rules that
    applied last Tuesday, not the ones that apply today
    (specs/mission.md P7).

This is also why the AI agent can safely call this code as a tool
(specs/tech-stack.md section 5): the agent cannot produce a number the engine
would not produce, because it is the same function.

Phase 2 establishes the package with commission-rule resolution. Phase 3 adds
`signed` and `position`, the one place a ledger direction becomes a sign - see
`ledger.py`. Phase 10 adds expected entitlement, proportional refund
allocation and commission adjustment.
"""

from __future__ import annotations

from .entitlement import (
    FixedCommissionCurrencyMismatch,
    MixedCurrencyOrder,
    OrderEntitlement,
    OrderItemLike,
    OrderLike,
    SellerEntitlement,
    commission_for,
    expected_entitlement,
)
from .ledger import (
    CREDIT,
    DEBIT,
    LedgerEntry,
    UnknownTransactionType,
    position,
    signed,
)
from .refunds import (
    CaptureLike,
    FlatComponentReversalUndefined,
    RefundAttribution,
    RefundExceedsShare,
    RefundLike,
    UnattributableRefund,
    allocate_refund_across_sellers,
    apply_refund,
    attribute_refund,
    commission_reversal,
    split_by_rate,
    total_refunded,
)
from .rules import (
    CommissionRuleAmbiguity,
    CommissionRuleLike,
    EngineError,
    NoApplicableCommissionRule,
    resolve_commission_rule,
)

__all__ = [
    "CREDIT",
    "DEBIT",
    "CommissionRuleAmbiguity",
    "CommissionRuleLike",
    "CaptureLike",
    "EngineError",
    "FixedCommissionCurrencyMismatch",
    "FlatComponentReversalUndefined",
    "LedgerEntry",
    "MixedCurrencyOrder",
    "NoApplicableCommissionRule",
    "OrderEntitlement",
    "OrderItemLike",
    "OrderLike",
    "RefundAttribution",
    "RefundExceedsShare",
    "RefundLike",
    "SellerEntitlement",
    "UnattributableRefund",
    "UnknownTransactionType",
    "allocate_refund_across_sellers",
    "apply_refund",
    "attribute_refund",
    "commission_for",
    "commission_reversal",
    "expected_entitlement",
    "position",
    "resolve_commission_rule",
    "signed",
    "split_by_rate",
    "total_refunded",
]
