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
        evidence_refs=("ORD-0014", "FEE-ORD-0014", "ITEM-0014-A"),
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
        evidence_refs=("ORD-0015", "FX-ORD-0015", "CAP-ORD-0015"),
        description=(
            "The conversion reports a rate of 0.9183 on a 276.00 USD seller "
            "share, which implies 253.45 EUR, and a settlement of 248.40 EUR "
            "- consistent with a stale rate near 0.90. The discrepancy is "
            "5.05 EUR, two orders of magnitude clear of the one-cent rounding "
            "artefact that ORD-0008 carries legitimately, so no threshold can "
            "confuse the two."
        ),
        expected_classification=FX_DISCREPANCY,
        impact_amount="5.05",
        impact_currency="EUR",
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
        evidence_refs=("REL-ORD-0018", "ORD-0018", "ITEM-0018-A"),
        description=(
            "PayPal released 120.00 EUR against an obligation of 138.00, an "
            "18.00 shortfall on a release recorded as completed - so there is "
            "nothing still in flight to explain it."
        ),
        expected_classification=DISBURSEMENT_MISMATCH,
        impact_amount="18.00",
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
