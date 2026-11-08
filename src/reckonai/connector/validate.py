"""The validation gate: deterministic, blocking, and run before activation.

specs/tech-stack.md section 9.4, and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D8.

**This is what specs/mission.md P1 means for mapping - AI suggests, code
decides.** Six checks, all deterministic, run against sampled live records. A
failure blocks activation and names the offending field, and **a mapping never
half-activates**: the gate returns every problem it found, and the caller
either activates the whole document or none of it.

The money check is the one to get right. Section 9.3 names an amount without a
resolved currency as the single most likely place for a mapping to be quietly
wrong, so it is a **hard failure, not a warning** - and `MoneyMapping`'s shape
means it is mostly unexpressible in the first place. The check remains because
a document can also be hand-written, or a money field can be mapped as a plain
`FieldMapping` with a `decimal` transform, which is precisely the mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reckonai.connector.mapping import (
    MappingDocument,
    ResourceMapping,
    TransformError,
    apply_transform,
)

#: Canonical fields that carry money, by target table. A plain `FieldMapping`
#: pointed at one of these is the section 9.3 mistake, whatever its transform.
MONEY_FIELDS: dict[str, frozenset[str]] = {
    "order": frozenset({"total"}),
    "order_item": frozenset({"unit_price", "line_total"}),
    "commission_rule": frozenset({"fixed"}),
    "payment": frozenset({"amount"}),
    "capture": frozenset(
        {"amount", "gross_amount", "net_amount", "paypal_fee", "settlement_amount"}
    ),
    "refund": frozenset(
        {"amount", "gross_amount", "net_amount", "paypal_fee",
         "total_refunded_amount"}
    ),
    "dispute": frozenset({"amount"}),
    "platform_fee": frozenset({"amount"}),
    "disbursement": frozenset({"amount"}),
    "fx_conversion": frozenset({"source", "target", "fee"}),
    "seller_ledger_entry": frozenset({"amount"}),
}


@dataclass(frozen=True)
class Problem:
    """One reason a mapping cannot be activated.

    Carries the resource and the field, because section 9.4 requires a failure
    to **name the offending field** rather than report that validation failed.
    """

    resource: str
    field: str | None
    code: str
    detail: str

    def __str__(self) -> str:
        where = f"{self.resource}.{self.field}" if self.field else self.resource
        return f"[{self.code}] {where}: {self.detail}"


class MappingInvalid(ValueError):
    """The gate refused a mapping. Carries every problem, not the first.

    Every problem, because fixing a mapping one error per round trip is how a
    mapping gets activated with the third mistake still in it.
    """

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} problem(s) block activation:\n"
            + "\n".join(f"  {p}" for p in problems)
        )


def _resolve(record: dict[str, Any], path: str) -> Any:
    """Read a dotted path out of a record, or `None` if it is absent.

    Absent and `None` are deliberately not distinguished here: both mean the
    source did not supply a value, and the caller's `required` flag is what
    decides whether that matters.
    """
    current: Any = record
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def validate_document(document: MappingDocument) -> list[Problem]:
    """The checks that need no sample data.

    Separated from `validate_against_samples` so a malformed document fails
    before any records are fetched - which is the difference between "fails
    loudly at config-validation time" and "fails at ingestion time", the
    distinction Phase 6's second exit criterion turns on.
    """
    problems: list[Problem] = []

    if not document.resources:
        problems.append(
            Problem("<document>", None, "no_resources", "maps nothing")
        )

    seen_tables: set[str] = set()
    for resource in document.resources:
        if resource.target_table in seen_tables:
            problems.append(
                Problem(
                    resource.target_table,
                    None,
                    "duplicate_resource",
                    "two resources target the same canonical table",
                )
            )
        seen_tables.add(resource.target_table)
        problems.extend(_check_resource(resource))

    return problems


def _check_resource(resource: ResourceMapping) -> list[Problem]:
    problems: list[Problem] = []
    mapped = resource.all_canonical()

    # Check 1 - every required canonical field is mapped.
    for name in resource.required_fields:
        if name not in mapped:
            problems.append(
                Problem(
                    resource.target_table,
                    name,
                    "required_unmapped",
                    "declared required but nothing maps to it",
                )
            )

    # Check 3 - every money field resolves both amount and currency.
    #
    # Expressed as "a money field is not mapped as a plain field", since
    # `MoneyMapping` requires both paths by construction. A plain entry
    # pointed at a money field is the section 9.3 mistake in its only
    # remaining form.
    money = MONEY_FIELDS.get(resource.target_table, frozenset())
    for field in resource.fields:
        if field.canonical in money:
            problems.append(
                Problem(
                    resource.target_table,
                    field.canonical,
                    "money_without_currency",
                    (
                        "is a monetary field but is mapped as a plain field, "
                        "so it resolves an amount with no currency. Section "
                        "9.3 treats this as a hard failure: comparing numbers "
                        "across currencies reports confident nonsense. Use a "
                        "money_fields entry with an amount_path and a "
                        "currency_path."
                    ),
                )
            )

    # Check 6 - no two canonical fields draw from the same source path.
    by_path: dict[str, list[str]] = {}
    for field in resource.fields:
        by_path.setdefault(field.source_path, []).append(field.canonical)
    for money_field in resource.money_fields:
        by_path.setdefault(money_field.amount_path, []).append(money_field.canonical)
    for path, canonicals in by_path.items():
        if len(canonicals) > 1:
            problems.append(
                Problem(
                    resource.target_table,
                    ", ".join(sorted(canonicals)),
                    "shared_source_path",
                    (
                        f"both draw from {path!r}. If that is intentional it "
                        "must be declared; otherwise one of them is wrong."
                    ),
                )
            )

    # The R3 guard, enforced at validation time rather than only in a test.
    for field in resource.fields:
        leaf = field.source_path.rsplit(".", 1)[-1]
        if leaf == field.canonical:
            problems.append(
                Problem(
                    resource.target_table,
                    field.canonical,
                    "identity_mapping",
                    (
                        f"draws from a source field of the same name "
                        f"({leaf!r}), so the mapping for it is not exercised. "
                        "The replan's R3 guard: we author both sides of this "
                        "boundary, and a trivially-matching mapping means "
                        "mapping was never tested."
                    ),
                )
            )

    return problems


def validate_against_samples(
    document: MappingDocument,
    samples: dict[str, list[dict[str, Any]]],
    *,
    minimum: int = 1,
) -> list[Problem]:
    """Checks 2, 4 and 5, which need real records.

    `samples` maps a target table to records fetched from its source endpoint.

      - Check 2: source and canonical types are compatible, proven by running
        the declared transform rather than by comparing type names.
      - Check 4: the mapping applies to N sampled records with **zero** parse
        errors.
      - Check 5: declared relationships hold - an `order_item`'s order
        reference actually resolves to a sampled order.

    `minimum` guards the shape of failure this check is most prone to: a gate
    run against **no** samples passes every one of them. A resource with fewer
    samples than `minimum` is a problem, not a pass.
    """
    problems: list[Problem] = []

    for resource in document.resources:
        records = samples.get(resource.target_table, [])

        if len(records) < minimum:
            problems.append(
                Problem(
                    resource.target_table,
                    None,
                    "insufficient_samples",
                    (
                        f"{len(records)} sampled record(s), fewer than the "
                        f"required {minimum}. A gate run against no samples "
                        "passes vacuously, which is worse than failing."
                    ),
                )
            )
            continue

        problems.extend(_check_samples(resource, records, samples))

    return problems


def _check_samples(
    resource: ResourceMapping,
    records: list[dict[str, Any]],
    samples: dict[str, list[dict[str, Any]]],
) -> list[Problem]:
    problems: list[Problem] = []

    for index, record in enumerate(records):
        for field in resource.fields:
            raw = _resolve(record, field.source_path)
            if raw is None:
                if field.canonical in resource.required_fields:
                    problems.append(
                        Problem(
                            resource.target_table,
                            field.canonical,
                            "required_absent_in_sample",
                            f"absent from sampled record {index}",
                        )
                    )
                continue
            try:
                apply_transform(
                    field.transform, raw, enum_map=field.enum_map
                )
            except TransformError as exc:
                problems.append(
                    Problem(
                        resource.target_table,
                        field.canonical,
                        "transform_failed",
                        f"on sampled record {index}: {exc}",
                    )
                )

        for money_field in resource.money_fields:
            amount = _resolve(record, money_field.amount_path)
            currency = _resolve(record, money_field.currency_path)

            if amount is None and currency is None:
                if money_field.canonical in resource.required_fields:
                    problems.append(
                        Problem(
                            resource.target_table,
                            money_field.canonical,
                            "required_absent_in_sample",
                            f"absent from sampled record {index}",
                        )
                    )
                continue

            # **Half a money pair is a hard failure**, in sampled data as in
            # the document. This is the same claim `money_pair_check` makes in
            # DDL, caught before activation instead of at insert.
            if amount is None or currency is None:
                problems.append(
                    Problem(
                        resource.target_table,
                        money_field.canonical,
                        "incomplete_money_in_sample",
                        (
                            f"sampled record {index} resolves "
                            f"amount={amount!r} and currency={currency!r}. An "
                            "amount without its currency is section 9.3's "
                            "hard failure."
                        ),
                    )
                )
                continue

            try:
                apply_transform("decimal", amount)
                apply_transform("currency", currency)
            except TransformError as exc:
                problems.append(
                    Problem(
                        resource.target_table,
                        money_field.canonical,
                        "transform_failed",
                        f"on sampled record {index}: {exc}",
                    )
                )

        # Check 5 - declared relationships resolve against sampled data.
        for canonical, target in resource.relationships.items():
            entry = next(
                (f for f in resource.fields if f.canonical == canonical), None
            )
            if entry is None:
                problems.append(
                    Problem(
                        resource.target_table,
                        canonical,
                        "relationship_unmapped",
                        f"declared as relating to {target!r} but is not mapped",
                    )
                )
                continue

            value = _resolve(record, entry.source_path)
            if value is None:
                continue

            if not _resolves_against(value, target, samples):
                problems.append(
                    Problem(
                        resource.target_table,
                        canonical,
                        "relationship_unresolved",
                        (
                            f"sampled record {index} references {value!r} in "
                            f"{target!r}, which is not among the sampled "
                            f"{target} records"
                        ),
                    )
                )

    return problems


def _resolves_against(
    value: Any,
    target: str,
    samples: dict[str, list[dict[str, Any]]],
) -> bool:
    """Whether `value` appears as an identifier in the sampled `target` records.

    Compared against **every** scalar value in the target's sampled records
    rather than against a declared key path. A relationship check that needed
    the target's key path declared too would be one more thing to get wrong,
    and the loose form has no false negatives that matter here: a reference
    that appears nowhere in the target's records is unresolved whatever the key
    is called.
    """
    for record in samples.get(target, []):
        for candidate in record.values():
            if candidate == value:
                return True
    return False


def validate(
    document: MappingDocument,
    samples: dict[str, list[dict[str, Any]]] | None = None,
    *,
    minimum: int = 1,
) -> None:
    """Run the gate and raise `MappingInvalid` if anything blocks activation.

    Raises rather than returning a verdict, because the only correct response
    to a failed gate is to not activate - and a boolean return invites a caller
    that forgets to check it.
    """
    problems = validate_document(document)

    if samples is not None:
        problems.extend(
            validate_against_samples(document, samples, minimum=minimum)
        )

    if problems:
        raise MappingInvalid(problems)
