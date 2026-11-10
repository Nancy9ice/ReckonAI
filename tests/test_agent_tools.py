"""The agent's tool surface: read-only, no amounts in, citations that resolve.

Block E, Group 3. Two of Phase 12's exit criteria are proven here rather than
by a live model call, because neither is about the model:

  4. **no tool can mutate financial state** - asserted by enumerating the
     surface and calling every method, not by reading the code
  2. **every cited id resolves to a real record** - asserted over every
     `evidence_ref` the classifier emits on the whole seeded dataset

The read-only test is the one worth reading twice. It calls every public
method on the class, so a tool added later is covered without anyone
remembering to extend the test - which is the failure mode a hand-listed set
of tool names would have.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from reckonai import demo_data, reconcile
from reckonai.agent.citations import (
    CAPTURE,
    ORDER,
    PLATFORM_FEE,
    CitationResolver,
)
from reckonai.agent.tools import (
    InvestigationTools,
    UnknownWithholdKind,
    Withheld,
)
from reckonai.models import Tenant
from reckonai.recon import ExceptionCode
from reckonai.sim import data, manifest

#: F1 is specs/mission.md section 6's Scenario 1, and Phase 12's first exit
#: criterion: a refund PayPal performed that the seller ledger does not record.
SCENARIO_1_ORDER = manifest.fault("F1").order_ref


@pytest.fixture
def seeded(session: Session) -> uuid.UUID:
    """The demo tenant, seeded into the rolled-back session.

    `demo_data.ensure` is the same entry point the deploy runs, so the records
    these tools read are the records the demo will show.
    """
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None, "the demo tenant was not seeded"
    return tenant_id


@pytest.fixture
def tools(session: Session, seeded: uuid.UUID) -> InvestigationTools:
    return InvestigationTools(session, seeded)


def _public_methods(tools: InvestigationTools) -> list[str]:
    """Every tool on the surface, discovered rather than listed."""
    return sorted(
        name
        for name, member in inspect.getmembers(type(tools), inspect.isfunction)
        if not name.startswith("_")
    )


# ---------------------------------------------------------------------------
# Exit criterion 4 - nothing mutates
# ---------------------------------------------------------------------------


def test_no_tool_writes_to_the_session(
    session: Session, tools: InvestigationTools
) -> None:
    """specs/mission.md P6: the agent recommends, a person approves.

    Every tool is called, then the session is asked whether anything is
    pending. `new`, `dirty` and `deleted` being empty is the whole claim:
    nothing was added, nothing was modified, nothing was removed.

    Discovering the methods means a tool added in a later block is covered
    here automatically. A hand-listed set would pass while saying nothing
    about the new one.
    """
    for name in _public_methods(tools):
        method = getattr(tools, name)
        signature = inspect.signature(method)
        arguments = {
            parameter: SCENARIO_1_ORDER
            for parameter in signature.parameters
            if parameter != "self"
        }
        method(**arguments)

    assert not session.new
    assert not session.dirty
    assert not session.deleted


def test_every_tool_is_covered_by_that_assertion(
    tools: InvestigationTools,
) -> None:
    """A guard on the guard above.

    If the surface ever became empty - a refactor moving the tools elsewhere -
    `test_no_tool_writes_to_the_session` would pass by calling nothing. This
    fails instead.
    """
    methods = _public_methods(tools)

    assert len(methods) >= 10, methods
    assert "recompute" in methods
    assert "fetch_raw_payload" in methods


def test_no_tool_parameter_is_a_monetary_amount(
    tools: InvestigationTools,
) -> None:
    """specs/mission.md P1, enforced structurally rather than by instruction.

    The agent cannot hand the engine a figure it invented because no tool
    takes one. This asserts the shape of that guarantee: every parameter is a
    reference or a filter, and none is named like an amount or annotated as a
    number.
    """
    offenders: list[str] = []

    for name in _public_methods(tools):
        signature = inspect.signature(getattr(tools, name))
        for parameter, spec in signature.parameters.items():
            if parameter == "self":
                continue
            looks_monetary = any(
                token in parameter
                for token in ("amount", "total", "money", "currency", "fee", "price")
            )
            numeric = spec.annotation in (int, float, "int", "float")
            if looks_monetary or numeric:
                offenders.append(f"{name}({parameter}: {spec.annotation})")

    assert not offenders, offenders


def test_the_tools_do_not_import_the_job_queue() -> None:
    """The agent enqueues nothing (P6).

    Asserted on the module source rather than on behaviour, because the point
    is that the capability is absent - a test that called a tool and saw no
    job could pass on a tool that simply was not exercised.
    """
    from pathlib import Path

    import reckonai.agent.tools as module

    source = Path(module.__file__ or "").read_text(encoding="utf-8")

    assert "reckonai.jobs" not in source
    assert "enqueue" not in source


# ---------------------------------------------------------------------------
# Exit criterion 2 - citations resolve
# ---------------------------------------------------------------------------


def test_every_evidence_ref_on_the_whole_dataset_resolves(
    session: Session, seeded: uuid.UUID
) -> None:
    """The classifier's own citations all name real records.

    This is the floor beneath Phase 12's second exit criterion. If the
    classifier emitted a reference that resolved to nothing, an agent citing
    it faithfully would be scored as having fabricated it - so the resolver is
    checked against every finding on the real dataset before any model is
    involved.
    """
    resolver = CitationResolver(session, seeded)
    findings = reconcile.run(session, seeded)

    assert findings, "the seeded dataset produced no findings to check"

    unresolved = {
        finding.order_ref: resolver.unresolved(finding.evidence_refs)
        for finding in findings
        if resolver.unresolved(finding.evidence_refs)
    }

    assert not unresolved, unresolved


def test_an_order_reference_resolves_as_an_order(
    session: Session, seeded: uuid.UUID
) -> None:
    resolver = CitationResolver(session, seeded)

    assert resolver.resolve(SCENARIO_1_ORDER).kind == ORDER


def test_a_fabricated_reference_does_not_resolve(
    session: Session, seeded: uuid.UUID
) -> None:
    resolver = CitationResolver(session, seeded)
    citation = resolver.resolve("ORD-9999-NOT-A-RECORD")

    assert citation.resolved is False
    assert citation.stable is False
    assert citation.kind is None


def test_surrounding_whitespace_is_not_a_fabrication(
    session: Session, seeded: uuid.UUID
) -> None:
    """A model emitting `"ORD-0013 "` has cited a real record.

    Reporting that as fabricated would be scoring our own parsing as the
    model's defect.
    """
    resolver = CitationResolver(session, seeded)

    assert resolver.resolve(f"  {SCENARIO_1_ORDER} ").kind == ORDER


def test_a_platform_fee_reference_resolves_but_is_not_stable(
    session: Session, seeded: uuid.UUID
) -> None:
    """Block C's handoff: cite the capture, never `platform_fee.external_id`.

    The fee id is a **real record** - reporting it as fabricated would be
    wrong, and would fail on the classifier's own findings, which cite the fee
    beside the capture deliberately. It is simply not findable again after a
    reseed, because it is rebuilt from a payload UUID.

    So resolution and stability are separate answers, and this is the test
    that pins them apart.
    """
    from reckonai.models import PlatformFee

    fee_ref = session.scalar(
        select(PlatformFee.external_id).where(PlatformFee.tenant_id == seeded)
    )
    assert fee_ref is not None, "the seeded dataset has no platform fee to check"

    resolver = CitationResolver(session, seeded)
    citation = resolver.resolve(fee_ref)

    assert citation.resolved is True
    assert citation.kind == PLATFORM_FEE
    assert citation.stable is False
    assert resolver.unstable((fee_ref,)) == (fee_ref,)
    assert resolver.unresolved((fee_ref,)) == ()


def test_a_capture_reference_is_stable(
    session: Session, seeded: uuid.UUID
) -> None:
    """The reference a report is supposed to cite for fee evidence."""
    from reckonai.models import Capture

    capture_ref = session.scalar(
        select(Capture.external_id).where(Capture.tenant_id == seeded)
    )
    assert capture_ref is not None

    citation = CitationResolver(session, seeded).resolve(capture_ref)

    assert citation.kind == CAPTURE
    assert citation.stable is True


def test_a_reference_from_another_tenant_does_not_resolve(
    session: Session, seeded: uuid.UUID
) -> None:
    """A resolver is scoped to one tenant.

    Resolving against another tenant's records would report a fabricated
    citation as genuine, which is the one way this check could be worse than
    useless.
    """
    resolver = CitationResolver(session, uuid.uuid4())

    assert resolver.resolve(SCENARIO_1_ORDER).resolved is False


# ---------------------------------------------------------------------------
# The withhold set - exit criterion 3's instrument
# ---------------------------------------------------------------------------


def test_withholding_the_ledger_empties_that_tool_only(
    session: Session, seeded: uuid.UUID
) -> None:
    """The database is untouched; only the agent's reach narrows.

    This is what keeps the exception under investigation a *real* one. The
    classifier still sees every row, so F1 is classified from real evidence -
    the agent just cannot reach the record that explains it.
    """
    full = InvestigationTools(session, seeded)
    withheld = InvestigationTools(session, seeded, withhold={"ledger"})

    assert full.fetch_seller_ledger_entries(SCENARIO_1_ORDER) is not None
    assert full.fetch_order(SCENARIO_1_ORDER) is not None
    # A withheld kind **raises** rather than returning empty. The earlier
    # version of this test asserted `== []`, which encoded the bug the live
    # run then found: an empty list is indistinguishable from "none exist".
    with pytest.raises(Withheld):
        withheld.fetch_seller_ledger_entries(SCENARIO_1_ORDER)

    # Only the named kind is out of reach.
    assert withheld.fetch_order(SCENARIO_1_ORDER) is not None
    assert withheld.withheld == ("ledger",)


def test_withholding_does_not_change_what_the_classifier_finds(
    session: Session, seeded: uuid.UUID
) -> None:
    """The exception is real whether or not the agent can see its cause.

    If withholding changed the finding, the `insufficient_evidence` test would
    be investigating a different exception than the one the gate measures.
    """
    before = reconcile.run(session, seeded)

    InvestigationTools(session, seeded, withhold={"ledger", "refunds"})

    after = reconcile.run(session, seeded)

    assert [f.as_dict() for f in before] == [f.as_dict() for f in after]


def test_recompute_is_never_withheld(
    session: Session, seeded: uuid.UUID
) -> None:
    """It returns verdicts, not evidence.

    Suppressing it would test whether the agent can work without the engine,
    which is not what the evidence gap is for.
    """
    withheld = InvestigationTools(
        session, seeded, withhold=sorted(_all_withholdable())
    )

    assert withheld.recompute()


def _all_withholdable() -> frozenset[str]:
    from reckonai.agent.tools import WITHHOLDABLE

    return WITHHOLDABLE


def test_a_misspelled_withhold_kind_fails_loudly(
    session: Session, seeded: uuid.UUID
) -> None:
    """`--withhold ledgers` must not quietly withhold nothing.

    An investigation that withheld nothing while claiming to would be a false
    pass on the exit criterion most at risk, so the typo is refused rather
    than ignored.
    """
    with pytest.raises(UnknownWithholdKind):
        InvestigationTools(session, seeded, withhold={"ledgers"})


# ---------------------------------------------------------------------------
# What the tools actually return
# ---------------------------------------------------------------------------


def test_every_amount_crosses_as_a_string_with_its_currency(
    tools: InvestigationTools,
) -> None:
    """specs/tech-stack.md section 2, at the model boundary.

    The model never receives a bare number it might add up. Checked on the
    order total, a capture and a commission rule's fixed component, which are
    three different paths into `_money`.
    """
    order = tools.fetch_order(SCENARIO_1_ORDER)
    assert order is not None
    assert isinstance(order["total"]["amount"], str)
    assert order["total"]["currency"] == "USD"

    for rule in tools.fetch_commission_rules():
        # A rate is a fraction, not a percentage, and exact - so it crosses as
        # a string too. A JSON float would not round-trip 0.100000.
        assert isinstance(rule["rate"], str)

    for capture in tools.fetch_captures(SCENARIO_1_ORDER):
        for field in ("gross", "net", "paypal_fee"):
            value = capture[field]
            if value is not None:
                assert isinstance(value["amount"], str), field
                assert value["currency"]


def test_an_absent_paypal_fee_stays_absent_rather_than_zero(
    tools: InvestigationTools,
) -> None:
    """specs/mission.md P3: an absent fee and a measured zero differ.

    No capture in the dataset states a `paypal_fee` at all, so every capture
    must report `None` here. A zero would be a fabricated financial value.
    """
    captures = tools.fetch_captures(SCENARIO_1_ORDER)
    assert captures, "Scenario 1's order has no captures to check"

    assert all(capture["paypal_fee"] is None for capture in captures)


def test_the_fee_tool_names_the_stable_reference_to_cite(
    tools: InvestigationTools,
) -> None:
    """The rule is carried in the payload the model reads, not just in a prompt.

    A field called `cite_this_ref` beside one called `unstable_fee_ref` is
    harder to get wrong than an instruction several thousand tokens earlier.

    **The stable reference is not always a capture.** Scenario 1's order
    carries a refund *reversal* fee, which has no capture to point at - the
    refund is its parent. Writing this test against `capture_ref` is what
    found that.
    """
    fees = tools.fetch_platform_fees(SCENARIO_1_ORDER)
    assert fees, "Scenario 1's order has no platform fee to check"

    for fee in fees:
        assert fee["fee_kind"] in {"capture_fee", "refund_reversal"}
        assert fee["cite_this_ref"], fee
        assert fee["unstable_fee_ref"]


def test_a_capture_fee_and_a_refund_reversal_are_distinguishable(
    session: Session, seeded: uuid.UUID, tools: InvestigationTools
) -> None:
    """`reckonai.models.fees`: adding a reversal to a capture fee doubles it.

    The agent is explaining commission, so it has to be able to tell the fee
    PayPal took from the fee it gave back. Asserted across the whole dataset
    rather than one order, because the two shapes do not both appear on one.
    """
    kinds: set[str] = set()
    for order_ref in {
        finding["order_ref"] for finding in tools.recompute()
    }:
        kinds.update(
            fee["fee_kind"] for fee in tools.fetch_platform_fees(order_ref)
        )

    assert "capture_fee" in kinds
    assert "refund_reversal" in kinds
    assert "unattached" not in kinds


def test_recompute_returns_the_frozen_finding_contract(
    tools: InvestigationTools,
) -> None:
    """The agent reads the same shape Block F renders.

    `recompute` is `reconcile.run`, so a report's figures are the engine's.
    """
    findings = tools.recompute()

    assert findings
    expected = {
        "code",
        "order_ref",
        "detail",
        "is_exception",
        "impact",
        "expected",
        "reported",
        "evidence_refs",
        "evidence_basis",
        "seller_id",
        "confirmed",
    }
    assert set(findings[0]) == expected


def test_recompute_exceptions_excludes_awaiting_evidence(
    tools: InvestigationTools,
) -> None:
    """Counted through `exceptions_only()`, never `len(findings)`.

    `awaiting_evidence` is a finding and not an exception, so an agent handed
    the raw count would investigate a lifecycle state as a discrepancy.
    """
    everything = tools.recompute()
    only_exceptions = tools.recompute_exceptions()

    assert len(only_exceptions) < len(everything)
    assert all(
        finding["code"] != str(ExceptionCode.AWAITING_EVIDENCE)
        for finding in only_exceptions
    )
    assert all(finding["is_exception"] for finding in only_exceptions)


def test_scenario_1_is_reachable_through_the_tools(
    tools: InvestigationTools,
) -> None:
    """Everything the first exit criterion needs is retrievable.

    Not a claim about the model - a claim that the evidence is *there*. If
    this failed, a failed investigation would be ambiguous between a weak
    model and a tool surface that never offered the records.
    """
    assert tools.fetch_order(SCENARIO_1_ORDER) is not None
    assert tools.fetch_refunds(SCENARIO_1_ORDER)
    assert tools.fetch_captures(SCENARIO_1_ORDER)

    codes = {
        finding["code"]
        for finding in tools.recompute()
        if finding["order_ref"] == SCENARIO_1_ORDER
    }
    assert str(ExceptionCode.MISSING_LEDGER_ADJUSTMENT) in codes


def test_a_raw_payload_is_fetched_only_by_reference(
    tools: InvestigationTools,
) -> None:
    """The expensive tool, and the one that must stay narrow.

    section 5.2 puts the cost exposure in evidence volume rather than model
    choice, so there is deliberately no "fetch every payload for this order".
    """
    captures = tools.fetch_captures(SCENARIO_1_ORDER)
    assert captures

    payload = tools.fetch_raw_payload(captures[0]["capture_ref"])
    assert payload is not None
    assert payload["body"]

    assert tools.fetch_raw_payload("NOT-A-RECORD") is None


# ---------------------------------------------------------------------------
# The withhold set must not be bypassable (code review, finding 1)
# ---------------------------------------------------------------------------


def test_a_withheld_records_payload_is_not_reachable_through_the_raw_tool(
    session: Session, seeded: uuid.UUID
) -> None:
    """`--withhold ledger` must not leave the ledger readable another way.

    `fetch_raw_payload` checked only the `payloads` kind, while
    `_payload_id_for` searches captures, refunds, payments, disbursements,
    disputes **and ledger entries** by reference. So the run that evidences
    exit criterion 3 could still retrieve a ledger entry's full JSONB -
    amount, transaction type, order - and the deliberate evidence gap was not
    a gap at all.

    The refs are reachable without the ledger tool: `recompute` is never
    withheld and `duplicate_ledger_entry` cites ledger `external_ref`s.
    """
    from reckonai.models import SellerLedgerEntry

    entry_ref = session.scalar(
        select(SellerLedgerEntry.external_ref).where(
            SellerLedgerEntry.tenant_id == seeded
        )
    )
    assert entry_ref is not None

    # Reachable with nothing withheld.
    assert InvestigationTools(session, seeded).fetch_raw_payload(entry_ref)

    # And out of reach when the ledger is withheld.
    withheld = InvestigationTools(session, seeded, withhold={"ledger"})
    with pytest.raises(Withheld) as raised:
        withheld.fetch_raw_payload(entry_ref)

    # **Raises rather than returning None.** None reads as "no such record",
    # which is the absent-versus-unavailable conflation that produced the
    # confabulation in validation.md section 7 finding 3.
    assert raised.value.kind == "ledger"


@pytest.mark.parametrize(
    ("kind", "model_name", "column_name"),
    [
        ("captures", "Capture", "external_id"),
        ("refunds", "Refund", "external_id"),
        ("disbursements", "Disbursement", "external_id"),
    ],
)
def test_every_record_kind_governs_its_own_payload(
    session: Session, seeded: uuid.UUID, kind: str, model_name: str, column_name: str
) -> None:
    """The bypass was general, not specific to the ledger.

    Parametrised so a future source added to `_payload_id_for` without a
    withhold kind is caught by extending a list rather than by someone
    remembering the whole mechanism.
    """
    import reckonai.models as models

    model = getattr(models, model_name)
    reference = session.scalar(
        select(getattr(model, column_name)).where(model.tenant_id == seeded)
    )
    assert reference is not None, f"no {model_name} seeded to check"

    with pytest.raises(Withheld):
        InvestigationTools(session, seeded, withhold={kind}).fetch_raw_payload(
            reference
        )


# ---------------------------------------------------------------------------
# Absent versus unavailable, by routes Withheld does not cover (code review)
# ---------------------------------------------------------------------------

_CASE_DEPENDENT_TOOLS = (
    "fetch_refunds",
    "fetch_disputes",
    "fetch_platform_fees",
    "fetch_disbursements",
)


@pytest.mark.parametrize("tool", _CASE_DEPENDENT_TOOLS)
def test_an_order_with_no_case_is_unavailable_not_empty(
    tools: InvestigationTools, tool: str
) -> None:
    """Finding 7. `[]` would say "no refunds, no fees, no releases".

    `load_order_cases` builds a case only for orders it matched to a PayPal
    payment. For anything else these four tools returned `[]`, which reads as
    a fact about the records when it is a fact about the assembly - and refund
    rows for the order may well exist. It is the same absent-versus-unavailable
    conflation as a withheld record, by a route `Withheld` does not cover.

    The seeded dataset has no unmatched order (all 18 match), which is why
    nothing but review could find it.
    """
    from reckonai.agent.tools import OrderNotAssembled

    with pytest.raises(OrderNotAssembled) as raised:
        getattr(tools, tool)("ORD-NEVER-MATCHED")

    assert raised.value.order_ref == "ORD-NEVER-MATCHED"


@pytest.mark.parametrize("tool", _CASE_DEPENDENT_TOOLS)
def test_that_reaches_the_model_as_an_unavailable_marker(
    tools: InvestigationTools, tool: str
) -> None:
    """The model must receive the marker, not an exception and not `[]`."""
    import json

    from reckonai.agent.registry import tool_specs

    spec = next(s for s in tool_specs(tools) if s.name == tool)
    payload = json.loads(spec.run_as_json(order_ref="ORD-NEVER-MATCHED"))

    assert payload["unavailable"] is True
    assert payload["record_kind"] == "order_case"
    assert "does NOT mean none exist" in payload["note"]


def test_recompute_and_the_retrieval_tools_share_one_snapshot(
    session: Session, seeded: uuid.UUID
) -> None:
    """Finding 6. A verdict must not cite a record the tools cannot show.

    `recompute` used to call `run()` fresh each time while `_cases` was cached
    from first use, so a commit from the connector or a scheduled run
    mid-investigation could make `recompute` report a finding citing a refund
    that `fetch_refunds` did not list. To the agent that reads as "the refund
    PayPal performed does not exist" - the confabulation `Withheld` exists to
    prevent, arriving another way.
    """
    tools = InvestigationTools(session, seeded)

    first = tools.recompute()
    # Anything that changed between calls must not change what a second call
    # returns. Dropping the underlying rows would make a fresh `run()` differ.
    session.execute(text("DELETE FROM seller_ledger_entry"))
    second = tools.recompute()

    assert first == second, "recompute re-read the database mid-investigation"
    assert tools.recompute_exceptions() == [
        finding for finding in second if finding["is_exception"]
    ]
