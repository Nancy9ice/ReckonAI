"""The guard against this suite's order-dependence.

`specs/2026-11-08-block-a-marketplace-connector/validation.md` section 8 debt
4, settled by `specs/2026-11-09-entitlement-engine/requirements.md` D6.

**A gate that has never been seen to fail is not a gate.** Phase 3 learned that
the expensive way: its first model-import gate passed with the import it was
checking commented out, because the test's own setup performed the action it
was asserting. So this module provokes the guard rather than trusting it - and
does so **without committing a tenant row**, because committing one is the very
thing that would break the downgrade this guard protects, for every module
collected after it.

The filename sorts after `test_migrations.py` and before `test_sim_api.py`,
which leaves the existing alphabetical window intact.
"""

from __future__ import annotations

import pytest
import sqlalchemy
from _pytest.outcomes import Failed
from sqlalchemy import Engine

import conftest


def test_the_guard_passes_when_nothing_is_committed(engine: Engine) -> None:
    """The normal case, and the one that must not become noisy."""
    assert conftest.committed_tenant_count(engine) == 0
    conftest.assert_no_committed_marketplace_rows(engine)


def test_the_guard_fails_when_rows_exist(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failing direction. The count is faked rather than created, so this
    test cannot itself leave the rows that break the downgrade."""
    monkeypatch.setattr(conftest, "committed_tenant_count", lambda _engine: 3)

    with pytest.raises(Failed) as raised:
        conftest.assert_no_committed_marketplace_rows(engine)

    message = str(raised.value)
    assert "3 committed tenant row(s)" in message
    assert "ondelete=RESTRICT" in message
    assert "debt 4" in message, "the message must name where the reasoning lives"

    # **Both causes, and the fix for the one that has a one-command fix.**
    # The message offered only the collection-order cause until Block E, whose
    # agent is the first component that needs committed rows to be run by hand
    # at all. A developer who seeds and forgets to reset gets 49 failures and
    # a diagnosis pointing at a pytest ordering bug that is not there.
    #
    # Asserted here rather than left to review, because the whole value of
    # that message is that it appears at the moment of failure - and a
    # diagnosis nothing checks is how it drifted out of date the first time.
    assert "demo_data --reset" in message, "the message must name the fix"
    assert "demo_data --ensure" in message, "the message must name the cause"
    assert "debt 3" in message, "and where that reasoning lives too"


def test_the_count_answers_zero_when_the_schema_is_at_base(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At `base` the `tenant` table does not exist, and the count must answer 0
    rather than raise `UndefinedTable` - otherwise the guard would break the
    very downgrade tests it exists to protect.

    `committed_tenant_count` imports `inspect` at call time, which is what
    makes this patchable without reaching into the function.
    """

    class _NoTables:
        def get_table_names(self) -> list[str]:
            return []

    monkeypatch.setattr(sqlalchemy, "inspect", lambda _engine: _NoTables())

    assert conftest.committed_tenant_count(engine) == 0


def test_the_count_sees_the_tenant_table_normally(engine: Engine) -> None:
    """The positive control for the test above: without the patch, the table is
    found, so that test is exercising the missing-table branch rather than
    passing for an unrelated reason."""
    assert "tenant" in sqlalchemy.inspect(engine).get_table_names()
