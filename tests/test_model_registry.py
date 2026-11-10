"""Every model module is imported, and every financial record carries evidence.

specs/2026-11-08-canonical-events/validation.md C3 and C22.

Phase 2's handoff names the trap this module exists for in writing: **a model
module that is never imported is absent from `Base.metadata`, and autogenerate
reads its table as one to drop.** With nine tables arriving across seven
modules, meeting it stopped being theoretical.

The check is three lines and cannot be satisfied by remembering.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Table

import reckonai.models
from expected_schema import CATALOGUE_TABLES, MODEL_TABLES
from reckonai.models import Base

MODELS_PATH = Path(reckonai.models.__file__).parent
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Modules under `models/` that define no table and so cannot be caught by the
#: metadata check alone. They still have to be importable from the package, or
#: a later table added to one of them would be invisible.
SUPPORTING_MODULES = frozenset({"base", "lifecycle", "payloads", "mixins"})

#: The one table without a `tenant_id`, since it is what a `tenant_id` points
#: at. Phase 2 exempted it by name and nothing else.
UNSCOPED_TABLES = frozenset({"tenant"})

#: Tables that hold no financial record: the catalogue, the payload store
#: itself, and Block A's two connector tables.
#:
#: **Derived by exclusion rather than listed**, so a new financial table is
#: covered by this gate automatically. That is worth more than the precision of
#: an explicit list: a table added to a hand-written `FINANCIAL_TABLES` tuple
#: can be forgotten, and the failure is a money table with no evidence link
#: that no gate complains about.
#:
#: The two connector tables are excluded for different reasons, both worth
#: stating so neither looks like an oversight. `connector_mapping` holds a
#: configuration document - no amount, no provider record, nothing to be
#: evidence *of*. `quarantined_record` does carry a `raw_payload_id`, and must,
#: but it is a record of a **failure to normalize** rather than a financial
#: record: it has no amount and no `reconciliation_state`, because a record
#: that could not be mapped has no position in reconciliation to hold.
TABLES_WITHOUT_EVIDENCE = (
    frozenset(CATALOGUE_TABLES)
    | {"raw_payload"}
    | {"connector_mapping", "quarantined_record"}
    # Records which dataset is loaded; holds no financial value.
    | {"demo_seed"}
    # Block D's four. The queue tables are easy: `job` and `processed_event`
    # hold an instruction and a dedupe marker, no amount and no provider
    # record, nothing to be evidence *of*.
    #
    # **`reconciliation_exception` is the hard one, because it does carry
    # amounts**, and excluding a money-bearing table from this gate is exactly
    # what the derive-by-exclusion comment above warns against. The reason it
    # is right here: P7 binds a *retrieved source document* to the record it
    # produced, and a finding is neither retrieved nor a record of a movement -
    # it is a conclusion drawn over many records at once. An order, its items,
    # a capture, a platform fee and several ledger entries each link to their
    # own payload; the finding rests on all of them. A single
    # `raw_payload_id` could only name one, so the column would assert that
    # one payload is the evidence for a conclusion the other five also
    # support - a narrower claim than the truth, written into the schema.
    #
    # What carries the evidence instead is `evidence_refs`, NOT NULL, holding
    # the external ids the finding rests on. Every id in it resolves to a
    # record that does carry a payload link, so P7's chain is unbroken - it
    # runs through the cited records rather than around them.
    # `test_an_exception_cannot_exist_without_its_evidence` below is the
    # compensating gate, so this exclusion costs no coverage.
    | {"job", "processed_event", "reconciliation_run", "reconciliation_exception"}
    # Block E's two, excluded on exactly the `reconciliation_exception`
    # reasoning above and with less tension, because **neither holds a
    # monetary value at all**. specs/mission.md P1 keeps every figure in an
    # investigation coming from `reckonai.reconcile`, so a report cites
    # records and explains amounts it never computed; there is no amount here
    # to demand evidence for.
    #
    # An investigation is a conclusion drawn over many records, so a single
    # `raw_payload_id` would assert that one payload is the evidence for a
    # conclusion several records support - the same narrower-than-the-truth
    # claim P7 is protected from in the finding's case.
    #
    # What carries the evidence is `investigation_cause.cited_refs`, NOT NULL,
    # and the chain is *stronger* here than for a finding: every cited id is
    # resolved against a real record when the row is written, and the ones
    # that did not resolve are persisted in `unresolved_refs` rather than
    # dropped. Phase 12's second exit criterion is that no citation is
    # fabricated, and that column is how it is measured rather than asserted.
    | {"investigation", "investigation_cause"}
    # Block F's decision record holds no financial value of its own: it names a
    # persisted exception, which carries the evidence, and records a human's
    # outcome on it.
    | {"exception_decision"}
)


def _module_names() -> list[str]:
    return sorted(
        name
        for _, name, is_package in pkgutil.iter_modules([str(MODELS_PATH)])
        if not is_package
    )


def financial_tables() -> list[Table]:
    return [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in TABLES_WITHOUT_EVIDENCE
    ]


def table_ids(table: Table) -> str:
    return table.name


# ---------------------------------------------------------------------------
# C22 - the registry
# ---------------------------------------------------------------------------


def test_there_are_modules_to_check() -> None:
    """A walk over an empty directory passes trivially."""
    assert len(_module_names()) >= 8, _module_names()


def _tables_from_a_clean_interpreter() -> list[str]:
    """Import only `reckonai.models` and report what landed in the metadata.

    **This runs in a subprocess, and that is the whole point.**

    The obvious in-process version of this check cannot fail. Calling
    `importlib.import_module("reckonai.models.ledger")` *binds* `ledger` as an
    attribute of `reckonai.models`, so a `hasattr` assertion afterwards passes
    because the test itself just did the thing it was checking for - and the
    same import repairs `Base.metadata` for every assertion that follows. The
    first version of this module did exactly that and passed with the import
    commented out.

    A subprocess importing nothing but the package is the only honest form,
    and it is also precisely what `src/reckonai/migrations/env.py` does before
    autogenerate compares metadata against the database.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, reckonai.models;"
            "print(json.dumps(sorted(reckonai.models.Base.metadata.tables)))",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=PROJECT_ROOT,
    )
    return json.loads(result.stdout)


def test_importing_the_package_alone_registers_every_table() -> None:
    """requirements.md D17, and the trap Phase 2 handed this phase in writing.

    `reckonai.models.__init__` is what makes `Base.metadata` complete. A module
    missing from it is a table autogenerate proposes to **drop**, and all three
    things that depend on complete metadata - autogenerate, the schema gates
    and `alembic check` - then fail silently rather than loudly.

    Verified by hand once: commenting out a single import in
    `reckonai/models/__init__.py` makes this test fail and names the table.
    """
    found = _tables_from_a_clean_interpreter()

    assert found == list(MODEL_TABLES), (
        "importing reckonai.models did not register every table. Missing: "
        f"{sorted(set(MODEL_TABLES) - set(found))}. A module absent from "
        "reckonai/models/__init__.py is one autogenerate will propose to drop."
    )


@pytest.mark.parametrize("module_name", _module_names())
def test_every_model_module_is_importable(module_name: str) -> None:
    """A weaker check than the one above, and a different one.

    This catches a module with a syntax error or a circular import, which the
    subprocess check would report as a missing table without saying why. It
    deliberately does **not** assert package membership: importing the module
    here would bind it on the package and make that assertion vacuous.
    """
    assert importlib.import_module(f"reckonai.models.{module_name}") is not None


def test_the_metadata_holds_exactly_the_expected_tables() -> None:
    """Named rather than counted, so a failure says which table is missing.

    Fourteen: Phase 2's five plus this phase's nine.
    """
    found = sorted(Base.metadata.tables)

    assert found == list(MODEL_TABLES), set(found) ^ set(MODEL_TABLES)


def test_every_declared_module_defines_a_table_or_is_a_known_helper() -> None:
    """A module that defines nothing is either a helper or a mistake.

    Keeping the helper list explicit means a new module that forgot its
    `__tablename__` is named here rather than quietly contributing nothing.
    """
    table_modules = {
        table.__module__.rsplit(".", 1)[-1]
        for table in Base.__subclasses__()
    }
    for mapper in Base.registry.mappers:
        table_modules.add(mapper.class_.__module__.rsplit(".", 1)[-1])

    unaccounted = set(_module_names()) - table_modules - SUPPORTING_MODULES

    assert not unaccounted, unaccounted


# ---------------------------------------------------------------------------
# C3 - every financial record links to its evidence
# ---------------------------------------------------------------------------


def test_there_are_financial_tables_to_check() -> None:
    """The gate below iterates and would pass over an empty list."""
    assert len(financial_tables()) == 8, [t.name for t in financial_tables()]


@pytest.mark.parametrize("table", financial_tables(), ids=table_ids)
def test_every_financial_table_links_to_its_raw_payload(table: Table) -> None:
    """validation.md C3. specs/mission.md P7, over the metadata.

    Checked by iterating rather than against a hand-written list of eight
    tables, because such a list passes while being wrong about the ninth table
    someone adds in Phase 8.

    `NOT NULL` is the half that makes P7 unskippable: a record inserted without
    its evidence fails, instead of becoming a NULL that a schema test has to go
    looking for later.
    """
    assert "raw_payload_id" in table.c, (
        f"{table.name} holds financial records but carries no raw_payload_id"
    )
    assert not table.c.raw_payload_id.nullable, (
        f"{table.name}.raw_payload_id is nullable, so a record can exist "
        "without the evidence behind it (mission.md P7)"
    )


@pytest.mark.parametrize("table", financial_tables(), ids=table_ids)
def test_every_financial_table_links_compositely_to_its_payload(
    table: Table,
) -> None:
    """And the link carries `tenant_id`, so it cannot cross a tenant.

    Phase 2's D7 pattern: a cross-tenant link is rejected by the database
    rather than by a code review.
    """
    composite = [
        constraint
        for constraint in table.foreign_key_constraints
        if {"tenant_id", "raw_payload_id"} == {c.name for c in constraint.columns}
    ]

    assert composite, (
        f"{table.name}.raw_payload_id is not part of a composite foreign key "
        "with tenant_id"
    )


@pytest.mark.parametrize("table", financial_tables(), ids=table_ids)
def test_every_financial_table_carries_a_lifecycle_state(table: Table) -> None:
    """specs/mission.md P4's non-error states are per-record, so the column is.

    `unmatched`, `delayed` and `awaiting_evidence` have to be sayable about any
    record, or a delay on one of them gets reported as a discrepancy.
    """
    assert "reconciliation_state" in table.c, table.name
    assert not table.c.reconciliation_state.nullable, table.name


@pytest.mark.parametrize("table", financial_tables(), ids=table_ids)
def test_no_financial_table_denormalises_its_source(table: Table) -> None:
    """requirements.md D3: origin lives on `raw_payload` and nowhere else.

    Two places for one fact is the shape of every reconciliation bug this
    product exists to find, so the denormalisation is refused by test.
    """
    assert "source" not in table.c, table.name


@pytest.mark.parametrize("table", financial_tables(), ids=table_ids)
def test_no_financial_table_carries_a_cross_source_match(table: Table) -> None:
    """requirements.md D4: matching is Phase 11's, with its own table.

    A column here would be a matching decision with no run, no confidence and
    no evidence trail behind it.
    """
    matching = {name for name in table.c.keys() if name.startswith("matched_")}

    assert not matching, f"{table.name} carries {matching}"


# ---------------------------------------------------------------------------
# Block D - the compensating gates for the four tables excluded above
# ---------------------------------------------------------------------------


def test_an_exception_cannot_exist_without_its_evidence() -> None:
    """P7's chain, for a table that cannot carry a single `raw_payload_id`.

    `reconciliation_exception` is excluded from the evidence gate because a
    finding is a conclusion over many records rather than one retrieved
    document - see `TABLES_WITHOUT_EVIDENCE`. This is what replaces it: the
    refs column is NOT NULL, so a finding cannot be stored with no stated
    basis at all.

    It does not assert the list is non-empty, because that is a value rule
    rather than a schema one and `JSONB` cannot express it. The classifier is
    what guarantees the ids, and Block E's exit criterion is that each one
    resolves.
    """
    table = Base.metadata.tables["reconciliation_exception"]

    assert "evidence_refs" in table.c
    assert not table.c.evidence_refs.nullable, (
        "a finding could then be stored with no stated evidence at all"
    )
    assert not table.c.evidence_basis.nullable, (
        "specs/roadmap.md Phase 11 requires a finding resting on a derived "
        "linkage to say so"
    )


def test_the_persisted_vocabularies_match_the_classifier() -> None:
    """The models package duplicates `recon`'s enums as literals, on purpose.

    `reckonai.models` must not import `reckonai.recon`: the latter is a pure
    package under the purity gate, and a models-layer import would drag a
    session-free package into one full of them. So the strings are written
    twice and pinned here instead - the same pattern as the sim manifest and
    the exception vocabulary.

    Without this, a code added to the classifier would be rejected by a
    `CHECK` at the moment a run tried to persist it, which is the worst place
    to find out.
    """
    from reckonai.models.reconciliation import EVIDENCE_BASES, EXCEPTION_CODES
    from reckonai.recon.vocabulary import EvidenceBasis, ExceptionCode

    assert set(EXCEPTION_CODES) == {str(code) for code in ExceptionCode}
    assert set(EVIDENCE_BASES) == {str(basis) for basis in EvidenceBasis}
