"""The decision record: equal outcomes, append-only, tenant-scoped."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from reckonai.models import DECISION_OUTCOMES, ExceptionDecision

SOURCE = Path(__file__).resolve().parent.parent / "src" / "reckonai"


def test_the_two_outcomes_are_symmetric() -> None:
    assert DECISION_OUTCOMES == ("approved", "rejected")


def test_a_blank_approver_is_refused() -> None:
    check = {c.name for c in ExceptionDecision.__table__.constraints}
    assert "ck_exception_decision_approver_not_blank" in check


def test_no_code_updates_or_deletes_a_decision() -> None:
    """Append-only is a property of the code paths, so scan them."""
    offenders = []
    for path in SOURCE.rglob("*.py"):
        text = path.read_text()
        if path.name == "decision.py" or "ExceptionDecision" not in text:
            continue
        for needle in ("delete(ExceptionDecision", "update(ExceptionDecision"):
            if needle in text:
                offenders.append((path.name, needle))
        if "session.delete(" in text and "ExceptionDecision" in text:
            offenders.append((path.name, "session.delete"))
    assert not offenders


def test_an_unknown_outcome_is_refused_by_the_database(session, seeded_exception):
    session.add(
        ExceptionDecision(
            tenant_id=seeded_exception.tenant_id,
            exception_id=seeded_exception.id,
            outcome="maybe",
            approver="Nancy",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_both_outcomes_are_stored_and_the_first_survives_a_second(
    session, seeded_exception
):
    for outcome in ("rejected", "approved"):
        session.add(
            ExceptionDecision(
                tenant_id=seeded_exception.tenant_id,
                exception_id=seeded_exception.id,
                outcome=outcome,
                approver="Nancy",
            )
        )
        session.flush()
    rows = (
        session.query(ExceptionDecision)
        .filter_by(exception_id=seeded_exception.id)
        .order_by(ExceptionDecision.decided_at)
        .all()
    )
    assert {r.outcome for r in rows} == {"approved", "rejected"}


def test_a_decision_cannot_name_another_tenants_exception(
    session, seeded_exception, other_tenant
):
    session.add(
        ExceptionDecision(
            tenant_id=other_tenant.id,
            exception_id=seeded_exception.id,
            outcome="approved",
            approver="Nancy",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
