"""Matching and exception classification: the only place a comparison happens.

specs/roadmap.md Phase 11. The engine next door answers what *should* be true;
this package is the one that compares that answer against what the marketplace
recorded and against what PayPal reported, and decides what each difference
means.

The split is load-bearing rather than tidy. `reckonai.engine` raises wherever a
figure would have to be invented - nine distinct `EngineError` subclasses - and
deliberately does not decide what any of them means, because "what does an
unresolvable commission rule mean for this order?" is a classification
question. Keeping comparison out of the engine is what lets the engine be
called as a tool by the AI agent (specs/tech-stack.md section 5) without the
agent being able to reach a verdict: the engine has no verdicts in it.

Four things live here, in dependency order:

  - `vocabulary.py` - what a finding can claim, and on what evidence.
  - `matcher.py` - pairing marketplace records with PayPal records on stable
    identifiers. Being unmatched is not a verdict (specs/mission.md P4).
  - `tolerance.py` - the four comparison rules, each with its reason. Every
    tolerance decision in the product is in that module and nowhere else.
  - `classifier.py` - the rules that turn a compared pair into a `Finding`.

**This package is pure**, on the same terms as `reckonai.engine`: no database
handle, no HTTP client, no clock read, `typing.Protocol` inputs rather than ORM
classes. `tests/test_money_purity.py` enforces it and `[tool.mypy]` checks it
strictly.

It also persists nothing. The exception *table* belongs to Block D, which owns
the date-windowed run that would write it; `requirements.md` D8 records why,
and the `Finding` JSON contract frozen here is what Block F renders.
"""

from __future__ import annotations

from .classifier import (
    CaptureRecordLike,
    DisbursementLike,
    Finding,
    OrderCase,
    PlatformFeeLike,
    classify_order,
    exceptions_only,
)
from .matcher import (
    LedgerEntryLike,
    MatchSet,
    OrderMatch,
    OrderRecordLike,
    PaymentRecordLike,
    RefundMatch,
    RefundRecordLike,
    capture_reference_from_href,
    ledger_by_category,
    match_orders,
    match_refunds,
    order_ref_for_reference,
    order_references,
)
from .tolerance import (
    AmountNotStated,
    Comparison,
    compare_converted_total,
    compare_exact,
    is_healthy_disbursement,
    require_stated,
)
from .vocabulary import (
    EvidenceBasis,
    ExceptionCode,
    code_for_engine_error,
    is_exception,
)

__all__ = [
    "AmountNotStated",
    "CaptureRecordLike",
    "Comparison",
    "DisbursementLike",
    "EvidenceBasis",
    "ExceptionCode",
    "Finding",
    "LedgerEntryLike",
    "MatchSet",
    "OrderCase",
    "OrderMatch",
    "OrderRecordLike",
    "PaymentRecordLike",
    "PlatformFeeLike",
    "RefundMatch",
    "RefundRecordLike",
    "capture_reference_from_href",
    "classify_order",
    "code_for_engine_error",
    "compare_converted_total",
    "compare_exact",
    "exceptions_only",
    "is_exception",
    "is_healthy_disbursement",
    "ledger_by_category",
    "match_orders",
    "match_refunds",
    "order_ref_for_reference",
    "order_references",
    "require_stated",
]
