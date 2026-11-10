"""The investigation tables: vocabularies, constraints, and the audit columns.

Block E, Group 2. These are the gates that hold Phase 12's sixth exit criterion
to its word - the reasoning is persisted, the model id is stored and compared -
plus the two vocabulary pins that stop `models/` and `recon/` drifting apart
while deliberately not importing each other.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from reckonai.models import (
    EXCEPTION_CODES,
    INVESTIGABLE_CODES,
    Investigation,
    InvestigationCause,
    Tenant,
)
from reckonai.recon import ExceptionCode

# ---------------------------------------------------------------------------
# The vocabularies cannot drift
# ---------------------------------------------------------------------------


def test_investigable_codes_agree_with_the_exception_vocabulary() -> None:
    """`models/` duplicates the codes as literals rather than importing them.

    The models package must not depend on `recon/`, which is pure and under
    the purity gate, so the list is duplicated and pinned here instead - the
    same pattern as the sim manifest and `EXCEPTION_CODES`. Without this test
    the duplication is just a second place to be wrong.
    """
    assert set(INVESTIGABLE_CODES) == {str(code) for code in ExceptionCode}


def test_investigable_codes_agree_with_the_persisted_exception_codes() -> None:
    """An investigation must be able to be about any exception that persists.

    If these two lists ever disagreed, there would be a code that
    `reconciliation_exception` can store and `investigation` cannot reference -
    an exception that is, by schema, uninvestigable.
    """
    assert set(INVESTIGABLE_CODES) == set(EXCEPTION_CODES)


# ---------------------------------------------------------------------------
# The audit columns
# ---------------------------------------------------------------------------


def _investigation(tenant_id: uuid.UUID, **overrides: object) -> Investigation:
    fields: dict[str, object] = {
        "tenant_id": tenant_id,
        "order_external_ref": "ORD-0001",
        "code": str(ExceptionCode.MISSING_LEDGER_ADJUSTMENT),
        "provider": "anthropic",
        "model_requested": "claude-haiku-5-5",
        "model_returned": "claude-haiku-5-5",
        "verdict": "reported",
    }
    fields.update(overrides)
    return Investigation(**fields)


def test_a_downgrade_claim_requires_a_returned_model_to_compare(
    session: Session, tenant: Tenant
) -> None:
    """`downgraded` asserts two ids differed, so both must be present.

    Without this a row could claim a downgrade while having observed no
    response at all - a flag with no evidence behind it, in the one table
    whose job is to carry the evidence.
    """
    session.add(
        _investigation(
            tenant.id, model_returned=None, downgraded=True, verdict="failed"
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_refusal_is_recorded_with_no_returned_model(
    session: Session, tenant: Tenant
) -> None:
    """Haiku 5.5 has no refusal fallback, so a refusal just stops.

    specs/tech-stack.md section 5 decided such an investigation is surfaced as
    failed rather than papered over, and `model_returned` stays NULL because
    writing the requested model there would assert something unobserved.
    """
    session.add(
        _investigation(
            tenant.id,
            verdict="failed",
            stop_reason="refusal",
            model_returned=None,
            summarized_reasoning=None,
        )
    )
    session.flush()

    stored = session.scalars(select(Investigation)).one()
    assert stored.verdict == "failed"
    assert stored.stop_reason == "refusal"
    assert stored.model_returned is None
    assert stored.downgraded is False


def test_an_unknown_provider_is_refused_by_the_database(
    session: Session, tenant: Tenant
) -> None:
    session.add(_investigation(tenant.id, provider="gemini"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_an_unknown_verdict_is_refused_by_the_database(
    session: Session, tenant: Tenant
) -> None:
    session.add(_investigation(tenant.id, verdict="probably"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_withheld_set_defaults_to_empty_rather_than_null(
    session: Session, tenant: Tenant
) -> None:
    """An `insufficient_evidence` verdict is only readable alongside this.

    Empty means nothing was withheld, which is a different claim from "we did
    not record what was withheld" - and the difference is what separates a
    real evidence gap from the deliberate one that proves exit criterion 3.
    """
    session.add(_investigation(tenant.id))
    session.flush()

    assert session.scalars(select(Investigation)).one().withheld == []


# ---------------------------------------------------------------------------
# The causes
# ---------------------------------------------------------------------------


def _cause(
    tenant_id: uuid.UUID, investigation_id: uuid.UUID, **overrides: object
) -> InvestigationCause:
    fields: dict[str, object] = {
        "tenant_id": tenant_id,
        "investigation_id": investigation_id,
        "rank": 1,
        "description": "The refund was never reflected in the seller ledger.",
        "confidence": Decimal("0.850"),
        "cited_refs": ["ORD-0001", "5TY05013RG002845M"],
    }
    fields.update(overrides)
    return InvestigationCause(**fields)


def test_two_causes_cannot_claim_the_same_rank(
    session: Session, tenant: Tenant
) -> None:
    investigation = _investigation(tenant.id)
    session.add(investigation)
    session.flush()

    session.add(_cause(tenant.id, investigation.id, rank=1))
    session.add(_cause(tenant.id, investigation.id, rank=1))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("confidence", [Decimal("-0.1"), Decimal("1.1")])
def test_a_confidence_outside_zero_to_one_is_refused(
    session: Session, tenant: Tenant, confidence: Decimal
) -> None:
    """Pinned so a provider returning 0-100 cannot be stored as a probability.

    The two look alike in a column, and the mistake would make Block G's
    comparison across providers silently wrong rather than failing.
    """
    investigation = _investigation(tenant.id)
    session.add(investigation)
    session.flush()

    session.add(_cause(tenant.id, investigation.id, confidence=confidence))

    with pytest.raises((IntegrityError, DataError)):
        session.flush()


def test_a_rank_below_one_is_refused(
    session: Session, tenant: Tenant
) -> None:
    investigation = _investigation(tenant.id)
    session.add(investigation)
    session.flush()

    session.add(_cause(tenant.id, investigation.id, rank=0))

    with pytest.raises(IntegrityError):
        session.flush()


def test_confidence_round_trips_exactly(
    session: Session, tenant: Tenant
) -> None:
    """`NUMERIC(4, 3)` rather than a binary float, so this is exact.

    Worth asserting rather than assuming: Block G compares confidences across
    two providers, and a value that did not round-trip would make that
    comparison measure storage rather than the models.
    """
    investigation = _investigation(tenant.id)
    session.add(investigation)
    session.flush()
    session.add(_cause(tenant.id, investigation.id, confidence=Decimal("0.425")))
    session.flush()
    session.expunge_all()

    stored = session.scalars(select(InvestigationCause)).one()
    assert stored.confidence == Decimal("0.425")


def test_unresolved_refs_defaults_to_empty(
    session: Session, tenant: Tenant
) -> None:
    """Empty is the passing case for exit criterion 2.

    A non-empty array is a fabricated citation, recorded rather than repaired
    or dropped - which is the only honest way to measure "no cited id was
    invented".
    """
    investigation = _investigation(tenant.id)
    session.add(investigation)
    session.flush()
    session.add(_cause(tenant.id, investigation.id))
    session.flush()

    assert session.scalars(select(InvestigationCause)).one().unresolved_refs == []


def test_a_cause_cannot_name_another_tenants_investigation(
    session: Session, tenant: Tenant
) -> None:
    """The composite foreign key is what makes a cross-tenant link impossible.

    Every table carries `tenant_id` and every query filters by it, but a
    single-column FK to `investigation.id` would let a cause attach to another
    tenant's investigation and no query would notice.
    """
    investigation = _investigation(tenant.id)
    session.add(investigation)
    session.flush()

    session.add(_cause(uuid.uuid4(), investigation.id))

    with pytest.raises(IntegrityError):
        session.flush()
