"""The fault manifest: what was seeded, and what reconciliation should conclude.

specs/roadmap.md Phase 5's first exit criterion - "a manifest file records every
seeded fault and its expected classification" - and the ground truth Block G's
eval harness scores against.

**This module states expectations, not mechanics.** `data.py` says what the
marketplace's records contain; this says what the right answer about them is.
Keeping the two apart is the only thing stopping the manifest from being
written by reading the seeder, which would make the eval a test of whether the
engine reproduces the fixture rather than of whether it is correct.

So the `expected_classification` values below are **not** drawn from any
vocabulary this block defines. Block C owns the exception vocabulary, and when
it lands these strings have to be reconciled with it - `validation.md` section 8
records that as Block C's first task rather than leaving it to be discovered.

**One entry's expected answer is not an exception at all.** F5's refund is late,
not absent, and the correct conclusion is `awaiting_evidence`. It is in the
manifest because the eval has to score it: a system that flags it has produced
the exact false positive specs/roadmap.md Phase 11 forbids, and that failure is
only measurable if the manifest says what the right answer was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Expected classifications. Strings rather than an enum, because the
#: authoritative vocabulary is Block C's and inventing a competing one here
#: would guarantee a merge of two half-right lists.
MISSING_LEDGER_ADJUSTMENT: Final = "missing_ledger_adjustment"
COMMISSION_MISMATCH: Final = "commission_mismatch"
FX_DISCREPANCY: Final = "fx_discrepancy"
DUPLICATE_LEDGER_ENTRY: Final = "duplicate_ledger_entry"
AWAITING_EVIDENCE: Final = "awaiting_evidence"
DISBURSEMENT_MISMATCH: Final = "disbursement_mismatch"


@dataclass(frozen=True)
class Fault:
    """One seeded fault and the conclusion expected of it."""

    id: str

    #: The order the fault lives on. Faults occupy their own orders
    #: (requirements.md D5), so this is also what `verify()` excludes.
    order_ref: str

    #: The source references a correct investigation should cite. Block E's
    #: agent is required to cite real ids, and these are the ones that matter
    #: for this fault - so "did it cite the right evidence?" is scoreable and
    #: not a judgement call.
    evidence_refs: tuple[str, ...]

    description: str
    expected_classification: str

    #: The financial impact, as a string amount with its currency. **Never a
    #: float**, and stated rather than computed so the eval compares two
    #: independent figures (requirements.md D5a).
    impact_amount: str
    impact_currency: str

    #: Whether this fault should produce an exception at all. False for F5,
    #: whose correct answer is a non-error state.
    is_exception: bool = True

    #: Which of specs/mission.md section 6's three demo scenarios this is, if
    #: any. The demo walks these three; the other three faults are detection
    #: breadth rather than narrative.
    scenario: int | None = None


FAULTS: Final[tuple[Fault, ...]] = (
    Fault(
        id="F1",
        order_ref="ORD-0013",
        evidence_refs=("RFND-0013-A", "ORD-0013", "ITEM-0013-A"),
        description=(
            "A 30.00 USD partial refund was recorded against the order, and "
            "the seller ledger carries neither the refund debit nor the "
            "pro-rata commission reversal. The seller's position is "
            "overstated by 27.00 - the 30.00 refund less the 3.00 commission "
            "PayPal reversed automatically."
        ),
        expected_classification=MISSING_LEDGER_ADJUSTMENT,
        impact_amount="27.00",
        impact_currency="USD",
        scenario=1,
    ),
    Fault(
        id="F2",
        order_ref="ORD-0014",
        # **Corrected 2026-11-09, Block C.** This named `FEE-ORD-0014`, a
        # record that exists nowhere: it was one of Block A's synthetic
        # provider rows, which R16's capture retired. PayPal's real platform
        # fee cannot be named here either - `replay` builds its
        # `external_id` as `f"pf-{payload_uuid}-{index}"`, which changes on
        # every seed. So the capture that carries the fee is cited instead,
        # and it is stable because it comes from the committed fixture.
        evidence_refs=("ORD-0014", "61Y43309SU1359440", "ITEM-0014-A"),
        description=(
            "The marketplace retained 30.00 USD commission on a 200.00 order "
            "whose applicable rule is the tenant's 10% default, so 20.00. "
            "PayPal's platform fee records the rule-correct 20.00, which is "
            "the independent evidence the disagreement rests on."
        ),
        expected_classification=COMMISSION_MISMATCH,
        impact_amount="10.00",
        impact_currency="USD",
        scenario=2,
    ),
    Fault(
        id="F3",
        order_ref="ORD-0015",
        # **Corrected 2026-11-09, Block C.** `CAP-ORD-0015` and
        # `FX-ORD-0015` were synthetic rows retired by R16 and exist nowhere.
        # The real capture is cited from the fixture; the conversion is
        # reached through it, and `fx_conversion` has only a UUID primary key,
        # so there is no stable external reference to name.
        evidence_refs=("ORD-0015", "57663900RJ835061A", "ITEM-0015-A"),
        description=(
            "PayPal converted the 270.00 EUR net to a reported settlement of "
            "294.16 USD at a rate of 1.089477173902071. The marketplace "
            "records what it owes in its home currency at a stale rate, "
            "262.00 USD, so the discrepancy is 32.16 USD - three orders of "
            "magnitude clear of the one-cent rounding artefact ORD-0008 "
            "carries legitimately, so no threshold can confuse the two."
        ),
        expected_classification=FX_DISCREPANCY,
        # **Corrected 2026-11-09, Block C.** This read 5.05 EUR against a
        # 276.00 USD share at a rate of 0.9183, which describes no record in
        # the dataset: those were Block A's synthetic provider figures, and
        # R16's capture re-pointed the seed at real ones without this row
        # following. `fixtures/paypal/ORD-0015.json`, `sim/data.py` and
        # `specs/2026-11-09-paypal-capture/validation.md` section 2 all agree
        # on 270.00 EUR net and a 294.16 USD receivable; only this line did
        # not. The currency changes with it - the gap is in USD, which is the
        # currency the marketplace mis-recorded in.
        impact_amount="32.16",
        impact_currency="USD",
        scenario=3,
    ),
    Fault(
        id="F4",
        order_ref="ORD-0016",
        evidence_refs=(
            "LED-ORD-0016-vendor-aurora-sale",
            "LED-ORD-0016-vendor-aurora-sale-dup",
            "ORD-0016",
        ),
        description=(
            "One 90.00 USD sale is credited twice under distinct references, "
            "overstating the seller's position by the full 90.00. The "
            "references differ because the unique constraint refuses a literal "
            "repeat and a real double-post carries its own id."
        ),
        expected_classification=DUPLICATE_LEDGER_ENTRY,
        impact_amount="90.00",
        impact_currency="USD",
    ),
    Fault(
        id="F5",
        order_ref="ORD-0017",
        evidence_refs=("RFND-0017-A", "ORD-0017"),
        description=(
            "The marketplace recorded a 15.00 USD refund and reflected it in "
            "the ledger correctly; PayPal's own refund record has not arrived, "
            "because the reporting APIs run roughly a day behind. The record "
            "is late, not absent."
        ),
        # **Not an exception.** Flagging this is the false positive
        # specs/roadmap.md Phase 11's hardest criterion forbids, and R10's
        # answer is re-evaluation on the next scheduled run.
        expected_classification=AWAITING_EVIDENCE,
        is_exception=False,
        # No financial impact, because nothing is wrong. Zero is the measured
        # answer here rather than a default - the marketplace's arithmetic
        # holds completely.
        impact_amount="0.00",
        impact_currency="USD",
    ),
    Fault(
        id="F6",
        order_ref="ORD-0018",
        # **Corrected 2026-11-09, Block C.** `REL-ORD-0018` was synthetic
        # and exists nowhere. PayPal's release names itself by `item_id`,
        # which the fixture carries verbatim.
        evidence_refs=(
            "J8D5wYEtqZwFyqy3qu-l1zMou7z9EC1IHQig0jIm0ZDNuT-nFA3sKmwOENAIaRQ=",
            "ORD-0018",
            "ITEM-0018-A",
        ),
        description=(
            "PayPal released the full 135.00 EUR net and the seller ledger "
            "credits 120.00, a 15.00 EUR shortfall against an obligation "
            "PayPal has already discharged - so there is nothing still in "
            "flight to explain it."
        ),
        expected_classification=DISBURSEMENT_MISMATCH,
        # **Corrected 2026-11-09, Block C.** This read an 18.00 shortfall
        # against an obligation of 138.00, which implies a commission of
        # 12.00 on the 150.00 order. The applicable rule is the tenant's 10%,
        # so the commission is 15.00 and the entitlement 135.00 - as
        # `sim/data.py`, `fixtures/paypal/ORD-0018.json` and
        # `specs/2026-11-09-paypal-capture/validation.md` section 2 all state.
        # The 138.00 figure predates a commission change and was not carried
        # forward here.
        impact_amount="15.00",
        impact_currency="EUR",
    ),
)


#: Faults that should produce an exception. Five of six.
EXCEPTIONS: Final = tuple(f for f in FAULTS if f.is_exception)

#: The three demo scenarios, in order.
SCENARIOS: Final = tuple(
    sorted((f for f in FAULTS if f.scenario is not None), key=lambda f: f.scenario or 0)
)


def fault(fault_id: str) -> Fault:
    for entry in FAULTS:
        if entry.id == fault_id:
            return entry
    raise KeyError(f"no fault {fault_id!r}")


def by_order(order_ref: str) -> Fault:
    for entry in FAULTS:
        if entry.order_ref == order_ref:
            return entry
    raise KeyError(f"no fault on order {order_ref!r}")


def as_dict() -> dict[str, object]:
    """The manifest as plain data, for the eval harness and the dashboard.

    Amounts stay strings. A manifest that serialized them as JSON numbers would
    be a reconciliation system's ground truth stored in binary floating point,
    which is the one place it can least afford to be.
    """
    return {
        "version": 1,
        "faults": [
            {
                "id": f.id,
                "order_ref": f.order_ref,
                "evidence_refs": list(f.evidence_refs),
                "description": f.description,
                "expected_classification": f.expected_classification,
                "is_exception": f.is_exception,
                "impact": {"amount": f.impact_amount, "currency": f.impact_currency},
                "scenario": f.scenario,
            }
            for f in FAULTS
        ],
    }
