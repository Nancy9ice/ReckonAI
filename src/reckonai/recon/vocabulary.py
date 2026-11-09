"""What a finding can claim, and on what evidence it rests.

This module discharges debt 1 of
specs/2026-11-08-block-a-marketplace-connector/validation.md, which named it
Block C's first task: `reckonai.sim.manifest` carries six classification
strings written as plain `str` constants precisely so that the authoritative
vocabulary could be defined *here* rather than in the module that is supposed
to be independent ground truth for it.

The manifest keeps its own literals, and `tests/test_recon_vocabulary.py`
asserts every one of them is a member of `ExceptionCode`. So the two cannot
drift, and the manifest still does not import what it is evidence against - if
it did, "the classifier agrees with the manifest" would be a tautology.

**This is not `RECONCILIATION_STATES`.** `reckonai.models.lifecycle` records
where a record *sits* in reconciliation, which is not a verdict about it. This
module records what is *wrong*. The one name in both is `awaiting_evidence`,
and that overlap is intended rather than an accident to be cleaned up: it is
simultaneously a lifecycle state (a counterpart is expected and has not
arrived) and the manifest's expected classification for F5. The test above
pins the overlap at exactly that one name, so a future state and a future code
cannot quietly collide.
"""

from __future__ import annotations

from enum import StrEnum

from reckonai.engine import (
    CaptureNotSupplied,
    CommissionRuleAmbiguity,
    EngineError,
    FixedCommissionCurrencyMismatch,
    FlatComponentReversalUndefined,
    MixedCurrencyOrder,
    NoApplicableCommissionRule,
    RefundAlreadyApplied,
    RefundExceedsShare,
    UnattributableRefund,
)


class ExceptionCode(StrEnum):
    """Every conclusion the classifier is allowed to reach.

    A `StrEnum` so a code serializes as its own string into the dashboard
    contract and compares equal to the manifest's plain literal, while still
    being a closed set that mypy checks exhaustively. A free-form string would
    let a typo become a new classification silently.

    The first six are the manifest's. The rest exist because
    `reckonai.engine` raises where a figure would be invented, and
    `requirements.md` D5 requires each raise to become a *named* finding rather
    than a coerced number or a swallowed exception.
    """

    # ---- The manifest's six ------------------------------------------------

    #: A refund or reversal that PayPal performed and the seller ledger does
    #: not record. specs/mission.md section 6 Scenario 1.
    MISSING_LEDGER_ADJUSTMENT = "missing_ledger_adjustment"

    #: The commission the marketplace retained disagrees with the commission
    #: PayPal's `platform_fee` records. The platform fee is the independent
    #: evidence, never a recomputation (`requirements.md` D3).
    COMMISSION_MISMATCH = "commission_mismatch"

    #: A converted settlement disagrees with PayPal's reported figure by more
    #: than the rounding artefact of `tolerance.compare_converted_total`.
    FX_DISCREPANCY = "fx_discrepancy"

    #: One movement credited or debited twice in the seller ledger under
    #: distinct references.
    DUPLICATE_LEDGER_ENTRY = "duplicate_ledger_entry"

    #: A counterpart is expected and has not arrived. **Not an error state** -
    #: see `is_exception`. The replan's R10 and specs/mission.md P4: the
    #: record is late, not absent, and the next scheduled run is the retry.
    AWAITING_EVIDENCE = "awaiting_evidence"

    #: What PayPal released disagrees with the obligation, on a release that
    #: has completed - so nothing in flight explains it.
    DISBURSEMENT_MISMATCH = "disbursement_mismatch"

    # ---- The engine's raises, named --------------------------------------

    #: No rule applies, two rules apply equally, a flat component cannot be
    #: reversed, or a fixed commission is in the wrong currency. Grouped
    #: because the operator's action is identical in all four: fix the rule
    #: configuration. The `detail` on the finding carries which it was.
    UNRESOLVABLE_COMMISSION_RULE = "unresolvable_commission_rule"

    #: The order's purchase units are not all in one currency, so no single
    #: expected position exists. specs/mission.md P2 - two amounts are not
    #: comparable merely because their numbers look alike.
    MIXED_CURRENCY_ORDER = "mixed_currency_order"

    #: A refund exceeds the seller's share of the capture it is attributed to.
    REFUND_EXCEEDS_SHARE = "refund_exceeds_share"

    #: A refund cannot be attributed to one seller from the records available.
    #: Distinct from `AWAITING_EVIDENCE`: the evidence is present and does not
    #: resolve, rather than being late.
    REFUND_UNATTRIBUTABLE = "refund_unattributable"

    #: The same provider record arrived twice. A **finding**, never an amount
    #: to apply twice or to subtract - Block B's `validation.md` section 7
    #: finding 1 is what happens when a repeated refund is treated as
    #: arithmetic. F4 in the manifest is this shape on the ledger side.
    DUPLICATE_PROVIDER_RECORD = "duplicate_provider_record"


#: The one code that is not an error. Kept as a set rather than a flag on the
#: member, because a `StrEnum` member cannot carry attributes without becoming
#: a different kind of object and losing its `str` identity.
#:
#: The manifest agrees independently: F5 carries `is_exception=False` with
#: `expected_classification="awaiting_evidence"`, and
#: `tests/test_recon_vocabulary.py` checks the two agree.
_NON_ERROR_CODES: frozenset[ExceptionCode] = frozenset(
    {ExceptionCode.AWAITING_EVIDENCE}
)


def is_exception(code: ExceptionCode) -> bool:
    """Whether `code` is a genuine discrepancy rather than a lifecycle state.

    The false-positive gate (specs/roadmap.md Phase 11's hardest criterion)
    counts exceptions, not findings. A classifier that returned
    `awaiting_evidence` for F5 and had it counted as an exception would fail
    the gate while being completely correct, so the distinction has to be in
    the vocabulary rather than in whoever happens to be counting.
    """
    return code not in _NON_ERROR_CODES


class EvidenceBasis(StrEnum):
    """How firmly a finding's evidence links its records.

    specs/roadmap.md Phase 11 requires this explicitly: "Refund-to-capture
    matching rests on a derived linkage, parsed from a HATEOAS link. An
    exception resting on it must say so." Carrying it per finding is the only
    way the dashboard can show a confirmed-versus-suspected distinction
    (Phase 13) without re-deriving how each pairing was made.
    """

    #: Both sides carry the identifier the pairing was made on, as PayPal
    #: reported it.
    PROVIDER_REPORTED = "provider_reported"

    #: The pairing was parsed out of a HATEOAS `up` link rather than stated as
    #: a field. True of every refund-to-capture link.
    DERIVED_LINKAGE = "derived_linkage"


#: Each `EngineError` subclass, mapped to the code it becomes.
#:
#: Ordered most-specific first and matched by `isinstance` in order, because
#: `CaptureNotSupplied` subclasses `UnattributableRefund` and a dict lookup on
#: `type(error)` would miss a subclass raised from deeper in the engine.
#:
#: `CaptureNotSupplied` is deliberately **absent**. Block B's handoff: it means
#: the call was scoped wrongly, not that evidence is missing, so classifying it
#: would report a bug in the caller as a finding about the data.
#: `code_for_engine_error` re-raises it instead.
_ENGINE_ERROR_CODES: tuple[tuple[type[EngineError], ExceptionCode], ...] = (
    (NoApplicableCommissionRule, ExceptionCode.UNRESOLVABLE_COMMISSION_RULE),
    (CommissionRuleAmbiguity, ExceptionCode.UNRESOLVABLE_COMMISSION_RULE),
    (FlatComponentReversalUndefined, ExceptionCode.UNRESOLVABLE_COMMISSION_RULE),
    (FixedCommissionCurrencyMismatch, ExceptionCode.UNRESOLVABLE_COMMISSION_RULE),
    (MixedCurrencyOrder, ExceptionCode.MIXED_CURRENCY_ORDER),
    (RefundExceedsShare, ExceptionCode.REFUND_EXCEEDS_SHARE),
    (RefundAlreadyApplied, ExceptionCode.DUPLICATE_PROVIDER_RECORD),
    (UnattributableRefund, ExceptionCode.REFUND_UNATTRIBUTABLE),
)


def code_for_engine_error(error: EngineError) -> ExceptionCode:
    """The classification an engine refusal becomes.

    The engine raises rather than inventing a figure, and
    `requirements.md` D5 makes deciding what each raise *means* this package's
    job. Every subclass reaches a named code, and
    `tests/test_recon_vocabulary.py` asserts that by enumerating them - a
    subclass with no mapping is a path that would otherwise be swallowed by a
    bare `except EngineError` on first contact with real data.

    Raises:
        CaptureNotSupplied: never classified. It subclasses
            `UnattributableRefund`, so a general handler would absorb it, and
            it signals that this module called the engine with the wrong
            captures - a caller bug, which must fail the run loudly rather
            than appear in a reconciliation report.
        TypeError: for an `EngineError` subclass with no mapping, so adding one
            to the engine without deciding what it means here fails rather
            than defaulting.
    """
    if isinstance(error, CaptureNotSupplied):
        raise error

    for error_type, code in _ENGINE_ERROR_CODES:
        if isinstance(error, error_type):
            return code

    raise TypeError(
        f"{type(error).__name__} has no classification. Add it to "
        "_ENGINE_ERROR_CODES and say in requirements.md D5 what it means; "
        "defaulting it would file a wrong conclusion under a right-looking code."
    )
