"""The vocabulary, pinned to the manifest and to the engine.

Block A's `validation.md` debt 1: `reckonai.sim.manifest`'s six classification
strings "are not yet a shared vocabulary", and reconciling them is Block C's
first task. This module is the reconciliation - not by editing the manifest,
which is ground truth and must stay independent, but by asserting the two
agree and failing if either moves.

Three directions are checked, because only the first is obvious:

  - every manifest string is a real code (a typo in either becomes a failure)
  - every `EngineError` subclass reaches a named code (an unmapped one would be
    swallowed by a bare `except EngineError` on first contact with real data)
  - the overlap with `RECONCILIATION_STATES` is exactly `awaiting_evidence`
    (so a future state and a future code cannot collide unnoticed)
"""

from __future__ import annotations

import inspect

import pytest

from reckonai import engine
from reckonai.engine import CaptureNotSupplied, EngineError
from reckonai.models.lifecycle import RECONCILIATION_STATES
from reckonai.recon import EvidenceBasis, ExceptionCode, code_for_engine_error
from reckonai.recon.vocabulary import _ENGINE_ERROR_CODES
from reckonai.sim import manifest

#: The manifest's six, read from the module rather than retyped here. Retyping
#: them would make this test agree with a copy instead of with the manifest.
MANIFEST_CODES: tuple[str, ...] = (
    manifest.MISSING_LEDGER_ADJUSTMENT,
    manifest.COMMISSION_MISMATCH,
    manifest.FX_DISCREPANCY,
    manifest.DUPLICATE_LEDGER_ENTRY,
    manifest.AWAITING_EVIDENCE,
    manifest.DISBURSEMENT_MISMATCH,
)


@pytest.mark.parametrize("code", MANIFEST_CODES)
def test_every_manifest_classification_is_a_real_code(code: str) -> None:
    """Debt 1, discharged in the direction that matters most.

    If this fails, the manifest expects a conclusion the classifier cannot
    reach, so the fault it describes can never be detected correctly - and the
    eval harness would score it as a miss without saying why.
    """
    assert code in set(ExceptionCode), (
        f"the manifest expects {code!r}, which is not an ExceptionCode. "
        "Add it to the vocabulary; do not edit the manifest to match the "
        "implementation."
    )


def test_every_fault_expects_a_real_code() -> None:
    """The same check through the faults, which is what the eval actually reads.

    The constants above could all exist and still not be the strings the
    `Fault` rows carry, if one were written as a literal.
    """
    for fault in manifest.FAULTS:
        assert fault.expected_classification in set(ExceptionCode), (
            f"{fault.id} expects {fault.expected_classification!r}"
        )


def test_the_manifest_agrees_about_which_code_is_not_an_exception() -> None:
    """Two independent statements of the same fact, checked against each other.

    The manifest marks F5 `is_exception=False`; the vocabulary marks
    `awaiting_evidence` a non-error code. Both exist for the same reason and
    neither derives from the other, so a disagreement means one of them is
    wrong about specs/mission.md P4 - and the false-positive gate counts
    exceptions, so being wrong here moves the hardest criterion's number.
    """
    from reckonai.recon import is_exception

    for fault in manifest.FAULTS:
        code = ExceptionCode(fault.expected_classification)
        assert is_exception(code) == fault.is_exception, (
            f"{fault.id}: the manifest says is_exception={fault.is_exception}, "
            f"the vocabulary says {is_exception(code)} for {code!r}"
        )


def engine_error_subclasses() -> list[type[EngineError]]:
    """Every `EngineError` subclass the engine exports.

    Read off the package rather than listed, so an exception added to the
    engine in a later block appears here without anyone remembering to add it.
    """
    found = [
        obj
        for _, obj in inspect.getmembers(engine, inspect.isclass)
        if issubclass(obj, EngineError) and obj is not EngineError
    ]
    assert len(found) >= 9, f"expected the engine's nine raises, found {len(found)}"
    return found


@pytest.mark.parametrize(
    "error_type", engine_error_subclasses(), ids=lambda t: t.__name__
)
def test_every_engine_error_reaches_a_named_code(
    error_type: type[EngineError],
) -> None:
    """`requirements.md` D5, enumerated rather than trusted.

    `CaptureNotSupplied` is the deliberate exception: it means this package
    called the engine with the wrong captures, so it must fail the run rather
    than appear as a finding about the data.
    """
    error = error_type("constructed for the test")

    if error_type is CaptureNotSupplied:
        with pytest.raises(CaptureNotSupplied):
            code_for_engine_error(error)
        return

    assert code_for_engine_error(error) in set(ExceptionCode)


def test_an_unmapped_engine_error_fails_rather_than_defaulting() -> None:
    """The gate, seen failing.

    A `TypeError` here is the point: a new engine refusal must be given a
    meaning in D5, not filed under whichever code happens to be first.
    """

    class UnconsideredRefusal(EngineError):
        pass

    with pytest.raises(TypeError, match="no classification"):
        code_for_engine_error(UnconsideredRefusal("x"))


def test_capture_not_supplied_is_matched_before_its_parent() -> None:
    """Order matters, and ordering bugs here are silent.

    `CaptureNotSupplied` subclasses `UnattributableRefund`. The isinstance walk
    would classify it as `refund_unattributable` - a plausible-looking finding
    about the data - if the explicit re-raise were removed. This asserts the
    parent is still in the table, so the test above is proving the re-raise
    rather than an accidental absence.
    """
    mapped = [error_type.__name__ for error_type, _ in _ENGINE_ERROR_CODES]

    assert "UnattributableRefund" in mapped
    assert "CaptureNotSupplied" not in mapped


def test_the_overlap_with_lifecycle_states_is_exactly_awaiting_evidence() -> None:
    """The two vocabularies are different questions, and stay that way.

    `RECONCILIATION_STATES` says where a record sits; `ExceptionCode` says what
    is wrong with it. One name is in both on purpose. A second would mean the
    two have started answering each other's question, which is how a lifecycle
    state ends up rendered as a discrepancy.
    """
    overlap = {code.value for code in ExceptionCode} & set(RECONCILIATION_STATES)

    assert overlap == {"awaiting_evidence"}, f"unexpected overlap: {overlap}"


def test_codes_are_their_own_strings() -> None:
    """A `StrEnum` member must serialize as the bare value.

    The dashboard contract carries codes as JSON strings and the manifest
    compares against plain literals. `str(SomeEnum.X)` returning
    `'ExceptionCode.X'` is the classic version of this bug, and it would put
    the wrong string in front of a judge.
    """
    assert str(ExceptionCode.COMMISSION_MISMATCH) == "commission_mismatch"
    assert str(EvidenceBasis.DERIVED_LINKAGE) == "derived_linkage"
    assert ExceptionCode.COMMISSION_MISMATCH == "commission_mismatch"


def test_no_two_codes_share_a_value() -> None:
    """`StrEnum` aliases a duplicate value silently rather than raising.

    Two names with one value would collapse into one member, so a
    classification would quietly become a synonym of another - and the eval
    would still pass, scoring the alias as correct.
    """
    values = [code.value for code in ExceptionCode]

    assert len(values) == len(set(values))
    assert len(list(ExceptionCode)) == 11
