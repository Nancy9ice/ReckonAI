"""The free half: detection, false positives, evidence and currency.

The same comparisons `tests/test_recon_gate.py` makes, returned as numbers and a
table instead of as pass or fail, so a reader sees the figures and not only that
they held. **The manifest is ground truth and is read, never edited to fit.**
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from reckonai.agent.citations import Citation
from reckonai.money import Money
from reckonai.recon import Finding, exceptions_only
from reckonai.sim import manifest


@dataclass(frozen=True)
class FaultScore:
    fault_id: str
    order_ref: str
    expected_code: str
    got_codes: tuple[str, ...]
    #: The classification is the manifest's, and an exception is raised exactly
    #: when the manifest says one should be (F5 is a lifecycle state).
    classified: bool
    #: The impact is the manifest's figure, in the manifest's currency.
    impact_exact: bool
    #: Impact, expected and reported all stay in the one currency.
    currency_ok: bool
    #: Every reference the manifest names is cited.
    missing_refs: tuple[str, ...]


@dataclass(frozen=True)
class Deterministic:
    faults: tuple[FaultScore, ...]
    clean_orders: int
    #: One line per exception raised on an order the manifest says is clean.
    false_positives: tuple[str, ...]
    refs_checked: int
    unresolved_refs: tuple[str, ...]
    #: Resolve, but will not find the record again after a reseed.
    unstable_refs: int

    @property
    def detected(self) -> int:
        return sum(1 for f in self.faults if f.classified)

    @property
    def impacts_exact(self) -> int:
        return sum(1 for f in self.faults if f.impact_exact)

    @property
    def currencies_ok(self) -> int:
        return sum(1 for f in self.faults if f.currency_ok)

    @property
    def ok(self) -> bool:
        """The gate. Every clause is one of [[mission]] section 6's measures."""
        return (
            self.detected == len(self.faults)
            and self.impacts_exact == len(self.faults)
            and self.currencies_ok == len(self.faults)
            and all(not f.missing_refs for f in self.faults)
            and not self.false_positives
            and not self.unresolved_refs
        )


def score(
    findings: tuple[Finding, ...],
    *,
    faults: Iterable[manifest.Fault],
    clean_orders: Iterable[str],
    resolve: Callable[[str], Citation],
) -> Deterministic:
    fault_list = tuple(faults)
    clean = set(clean_orders)
    fault_orders = {fault.order_ref for fault in fault_list}
    scored: list[FaultScore] = []

    for fault in fault_list:
        reported = [f for f in findings if f.order_ref == fault.order_ref]
        got = tuple(str(f.code) for f in reported)
        first = reported[0] if reported else None
        want_money = Money(fault.impact_amount, fault.impact_currency)

        # An exception is raised exactly when the manifest says one should be
        # (F5 is a lifecycle state). Read from the flag the classifier stores.
        exception_matches = (
            first is not None and first.is_exception == fault.is_exception
        )
        scored.append(
            FaultScore(
                fault_id=fault.id,
                order_ref=fault.order_ref,
                expected_code=fault.expected_classification,
                got_codes=got,
                classified=got == (fault.expected_classification,)
                and exception_matches,
                impact_exact=first is not None and first.impact == want_money,
                currency_ok=first is not None
                and first.impact.currency == fault.impact_currency
                and all(
                    m is None or m.currency == fault.impact_currency
                    for m in (first.expected, first.reported)
                ),
                missing_refs=tuple(
                    sorted(
                        set(fault.evidence_refs)
                        - (set(first.evidence_refs) if first else set())
                    )
                ),
            )
        )

    # **Any exception on an order the manifest does not name as a fault is a false
    # positive**, whether the order is listed clean or not listed at all: a
    # "zero false positives" claim has no blind spot for an order nobody listed.
    # A second exception on a fault order is caught by `classified` instead.
    false_positives = tuple(
        f"{f.order_ref}: {f.code} {f.impact}"
        for f in exceptions_only(findings)
        if f.order_ref not in fault_orders
    )

    # Distinct references: a capture cited by three findings is one record, and
    # counting it three times would overstate how much evidence was checked.
    refs = list(dict.fromkeys(ref for f in findings for ref in f.evidence_refs))
    citations = [resolve(ref) for ref in refs]
    return Deterministic(
        faults=tuple(scored),
        clean_orders=len(clean),
        false_positives=false_positives,
        refs_checked=len(refs),
        unresolved_refs=tuple(c.ref for c in citations if not c.resolved),
        unstable_refs=sum(1 for c in citations if c.resolved and not c.stable),
    )


def render(result: Deterministic) -> str:
    total = len(result.faults)
    rows = [
        (
            "Detection",
            f"{result.detected} of {total} faults",
            "manifest code, and an exception exactly when the manifest says so",
        ),
        (
            "Impact figures",
            f"{result.impacts_exact} of {total} exact",
            "amount and currency equal the manifest's stated figure",
        ),
        (
            "False positives",
            f"{len(result.false_positives)} of {result.clean_orders} clean orders",
            "includes the held-funds and FX-rounding traps",
        ),
        (
            "Evidence completeness",
            f"{result.refs_checked - len(result.unresolved_refs)} of "
            f"{result.refs_checked} distinct cited records exist",
            f"{result.unstable_refs} resolve but will not survive a reseed",
        ),
        (
            "Manifest evidence cited",
            f"{sum(1 for f in result.faults if not f.missing_refs)} of {total}",
            "every reference the manifest names for the fault",
        ),
        (
            "Currency",
            f"{result.currencies_ok} of {total}",
            "impact, expected and reported stay in one currency; none invented",
        ),
    ]
    width = max(len(r[0]) for r in rows)
    value = max(len(r[1]) for r in rows)
    lines = ["DETERMINISTIC (gates; free, exact)", ""]
    for name, got, how in rows:
        lines.append(f"  {name:<{width}}  {got:<{value}}  {how}")
    lines.append("")
    lines.append(f"  {'Fault':<6}{'Order':<10}{'Expected':<28}{'Got':<28}Result")
    for f in result.faults:
        good = f.classified and f.impact_exact and f.currency_ok and not f.missing_refs
        lines.append(
            f"  {f.fault_id:<6}{f.order_ref:<10}{f.expected_code:<28}"
            f"{', '.join(f.got_codes) or '-':<28}{'ok' if good else 'MISS'}"
        )
    for line in result.false_positives:
        lines.append(f"  FALSE POSITIVE  {line}")
    for ref in result.unresolved_refs:
        lines.append(f"  UNRESOLVED REFERENCE  {ref}")
    lines.append("")
    lines.append("  GATE: " + ("PASS" if result.ok else "FAIL"))
    return "\n".join(lines)
