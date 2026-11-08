"""The mapping document, its transforms, and the validation gate.

specs/roadmap.md Phase 6's second exit criterion - "a missing required mapping
fails loudly at config-validation time, not at ingestion time" - plus
specs/tech-stack.md section 9.4's six checks and section 9.3's hard failure.

**Every gate here is tested in the failing direction too.** A validator that
accepts everything passes a suite that only feeds it valid documents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from reckonai.connector.mapping import (
    SYNONYMS,
    TRANSFORMS,
    FieldMapping,
    MappingDocument,
    MoneyMapping,
    ResourceMapping,
    TransformError,
    apply_transform,
    suggest,
)
from reckonai.connector.sim_mapping import SIM_MAPPING
from reckonai.connector.validate import (
    MappingInvalid,
    validate,
    validate_against_samples,
    validate_document,
)

# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def test_the_transform_set_is_closed() -> None:
    """A mapping is configuration and must not become an execution surface.

    specs/tech-stack.md section 9.3: "transforms are a closed, named set ...
    not arbitrary expressions".
    """
    with pytest.raises(TransformError, match="not one of"):
        apply_transform("eval", "1 + 1")

    assert "eval" not in TRANSFORMS
    assert "lambda" not in TRANSFORMS


def test_the_decimal_transform_refuses_a_float() -> None:
    """A JSON number is an IEEE-754 double.

    Refused here rather than converted, so the error names the mapping entry
    instead of surfacing from deep inside the money layer.
    """
    with pytest.raises(TransformError, match="float"):
        apply_transform("decimal", 160.00)

    assert apply_transform("decimal", "160.00") == Decimal("160.00")


def test_the_currency_transform_refuses_a_non_code() -> None:
    """Section 9.3: a key named `currency` is not necessarily a money envelope.

    "Treating a bare string as one produces an amount with no value, silently."
    """
    assert apply_transform("currency", "usd") == "USD"

    for bad in ("US", "DOLLARS", "", "US$"):
        with pytest.raises(TransformError, match="three-letter"):
            apply_transform("currency", bad)


def test_the_timestamp_transform_refuses_a_naive_value() -> None:
    """Every stored timestamp is TIMESTAMPTZ.

    A naive value in a system reconciling across PayPal's clock and a
    marketplace's own is a defect waiting for a daylight-saving boundary.
    """
    aware = apply_transform("timestamp", "2026-11-08T12:00:00+00:00")
    assert aware == datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

    with pytest.raises(TransformError, match="timezone"):
        apply_transform("timestamp", "2026-11-08T12:00:00")


def test_an_unrecognised_enum_value_is_refused_not_passed_through() -> None:
    """A state vocabulary quietly widened by a source is how an unknown value
    becomes a stored state nothing handles."""
    table = {"INSTANT": "INSTANT", "DELAYED": "DELAYED"}

    assert apply_transform("enum", "INSTANT", enum_map=table) == "INSTANT"

    with pytest.raises(TransformError, match="not in the declared table"):
        apply_transform("enum", "IMMEDIATE", enum_map=table)


def test_no_transform_has_a_fallback_for_an_absent_value() -> None:
    """Every failure path raises. The alternative is a parse failure becoming a
    financial figure."""
    for transform in sorted(TRANSFORMS):
        with pytest.raises(TransformError):
            apply_transform(transform, None)


# ---------------------------------------------------------------------------
# Section 9.2 passes 1 and 2
# ---------------------------------------------------------------------------


def test_pass_one_matches_after_normalising_case_and_separators() -> None:
    resolved, unresolved = suggest(
        ["Seller-ID", "placed_at"], ["seller_id", "placed_at"]
    )

    assert resolved["Seller-ID"] == ("seller_id", "exact")
    assert resolved["placed_at"] == ("placed_at", "exact")
    assert not unresolved


def test_pass_two_uses_the_curated_synonym_table() -> None:
    """The table section 9.2 says "covers most of what the readme's examples
    show", and the three names the readme names explicitly."""
    resolved, unresolved = suggest(
        ["vendor_id", "order_ref", "item_total"],
        ["seller_id", "order_id", "line_total"],
    )

    assert resolved["vendor_id"] == ("seller_id", "synonym")
    assert resolved["order_ref"] == ("order_id", "synonym")
    assert resolved["item_total"] == ("line_total", "synonym")
    assert not unresolved


def test_whatever_the_two_passes_cannot_resolve_is_reported_not_guessed() -> None:
    """R11 cut the LLM pass; the residue goes to human review.

    The function must say what it could not resolve rather than returning a
    best effort, because a mapping cannot be activated by anything but a human
    and an unreported gap would be invisible to that review.
    """
    resolved, unresolved = suggest(
        ["vendor_id", "frobnicator_code"], ["seller_id", "category"]
    )

    assert resolved["vendor_id"] == ("seller_id", "synonym")
    assert unresolved == ["frobnicator_code"]


def test_suggest_never_returns_an_llm_provenance() -> None:
    """The value stays in the schema so the pass can be added without a
    migration - but nothing produces it while the pass is cut."""
    resolved, _ = suggest(list(SYNONYMS), list(set(SYNONYMS.values())))

    assert {p for _, p in resolved.values()} <= {"exact", "synonym"}


# ---------------------------------------------------------------------------
# The document schema
# ---------------------------------------------------------------------------


def test_a_money_mapping_cannot_omit_its_currency_path() -> None:
    """The section 9.3 mistake made unexpressible rather than merely caught.

    A required field of a distinct type means forgetting the currency is a
    construction error, not something a downstream check has to notice.
    """
    with pytest.raises(ValueError, match="currency_path"):
        MoneyMapping(canonical="total", amount_path="order_gross")  # type: ignore[call-arg]


def test_a_typo_in_a_mapping_entry_is_refused() -> None:
    """`extra="forbid"`, because `currrency_path` silently ignored would be an
    amount with no currency."""
    with pytest.raises(ValueError):
        MoneyMapping(
            canonical="total",
            amount_path="order_gross",
            currrency_path="ccy",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# The gate: the real document passes
# ---------------------------------------------------------------------------


def test_the_sim_mapping_passes_the_document_checks() -> None:
    assert validate_document(SIM_MAPPING) == []


def test_the_sim_mapping_maps_no_field_from_its_own_name() -> None:
    """R3's guard, enforced by the validator and not only by a test.

    `tests/test_sim_fields.py` checks the sim's outward names; this checks the
    mapping that consumes them, which is the other half of the same guard.
    """
    for resource in SIM_MAPPING.resources:
        for entry in resource.fields:
            leaf = entry.source_path.rsplit(".", 1)[-1]
            assert leaf != entry.canonical, (
                f"{resource.target_table}.{entry.canonical} maps from an "
                "identically-named source field"
            )


def test_every_sim_money_field_is_a_money_mapping() -> None:
    """No monetary field is mapped as a plain field anywhere in the document."""
    from reckonai.connector.validate import MONEY_FIELDS

    for resource in SIM_MAPPING.resources:
        money = MONEY_FIELDS.get(resource.target_table, frozenset())
        plain = {f.canonical for f in resource.fields}
        assert not (plain & money), (
            f"{resource.target_table} maps {sorted(plain & money)} as plain "
            "fields, which resolves an amount with no currency"
        )


# ---------------------------------------------------------------------------
# The gate: each check fails when it should
# ---------------------------------------------------------------------------


def _document(resource: ResourceMapping) -> MappingDocument:
    return MappingDocument(connector="test", resources=(resource,))


def test_check_one_a_required_field_with_no_mapping_is_refused() -> None:
    """Phase 6's second exit criterion: loudly, at config-validation time."""
    document = _document(
        ResourceMapping(
            source_endpoint="/x",
            target_table="order",
            fields=(
                FieldMapping(canonical="external_ref", source_path="order_ref"),
            ),
            required_fields=("external_ref", "placed_at"),
        )
    )

    problems = validate_document(document)

    assert [p.code for p in problems] == ["required_unmapped"]
    assert problems[0].field == "placed_at", "the failure must name the field"


def test_check_three_a_money_field_mapped_as_a_plain_field_is_refused() -> None:
    """Section 9.3's hard failure, in its only remaining expressible form."""
    document = _document(
        ResourceMapping(
            source_endpoint="/x",
            target_table="order",
            fields=(
                FieldMapping(
                    canonical="total", source_path="order_gross", transform="decimal"
                ),
            ),
        )
    )

    problems = validate_document(document)

    assert [p.code for p in problems] == ["money_without_currency"]
    assert "hard failure" in problems[0].detail


def test_check_six_two_canonical_fields_from_one_source_path_are_refused() -> None:
    document = _document(
        ResourceMapping(
            source_endpoint="/x",
            target_table="order",
            fields=(
                FieldMapping(canonical="external_ref", source_path="order_ref"),
                FieldMapping(canonical="disbursement_mode", source_path="order_ref"),
            ),
        )
    )

    problems = validate_document(document)

    assert "shared_source_path" in [p.code for p in problems]


def test_the_r3_guard_refuses_an_identity_mapping() -> None:
    """A mapping that matches trivially means mapping was never exercised."""
    document = _document(
        ResourceMapping(
            source_endpoint="/x",
            target_table="order",
            fields=(
                FieldMapping(canonical="external_ref", source_path="external_ref"),
            ),
        )
    )

    problems = validate_document(document)

    assert "identity_mapping" in [p.code for p in problems]


def test_a_gate_run_against_no_samples_fails_rather_than_passing() -> None:
    """The failure mode this check is most prone to.

    Every per-record assertion is vacuously true over an empty list, so a gate
    handed no samples would report a clean mapping. That is worse than failing,
    because it is indistinguishable from a real pass.
    """
    problems = validate_against_samples(SIM_MAPPING, {}, minimum=1)

    assert problems
    assert all(p.code == "insufficient_samples" for p in problems)
    assert "vacuously" in problems[0].detail


def test_check_four_a_parse_error_in_a_sample_blocks_activation() -> None:
    samples = {
        "order": [
            {
                "order_ref": "ORD-1",
                "order_gross": "10.00",
                "ccy": "USD",
                "booked_on": "not-a-timestamp",
                "payout_timing": "INSTANT",
            }
        ]
    }

    problems = validate_against_samples(
        MappingDocument(
            connector="t", resources=(SIM_MAPPING.resource("order"),)
        ),
        samples,
    )

    assert [p.code for p in problems] == ["transform_failed"]
    assert problems[0].field == "placed_at"


def test_an_amount_with_no_currency_in_a_sample_blocks_activation() -> None:
    """The same hard failure, in sampled data rather than in the document."""
    samples = {
        "order": [
            {
                "order_ref": "ORD-1",
                "order_gross": "10.00",
                "booked_on": "2026-11-08T12:00:00+00:00",
                "payout_timing": "INSTANT",
            }
        ]
    }

    problems = validate_against_samples(
        MappingDocument(
            connector="t", resources=(SIM_MAPPING.resource("order"),)
        ),
        samples,
    )

    assert [p.code for p in problems] == ["incomplete_money_in_sample"]


def test_check_five_an_unresolved_relationship_blocks_activation() -> None:
    """An item's order reference must actually resolve to a sampled order."""
    samples = {
        "order": [
            {
                "order_ref": "ORD-1",
                "order_gross": "10.00",
                "ccy": "USD",
                "booked_on": "2026-11-08T12:00:00+00:00",
                "payout_timing": "INSTANT",
            }
        ],
        "seller": [
            {
                "vendor_id": "vendor-a",
                "display_name": "A",
                "payout_account": "M1",
                "trading_ccy": "USD",
                "fee_capable": True,
                "hold_capable": False,
            }
        ],
        "order_item": [
            {
                "line_ref": "ITEM-1",
                # Points at an order that is not in the sample.
                "order_ref": "ORD-NOT-THERE",
                "vendor_id": "vendor-a",
                "product_class": "audio",
                "units": 1,
                "unit_cost": "10.00",
                "item_total": "10.00",
                "ccy": "USD",
            }
        ],
    }

    problems = validate_against_samples(SIM_MAPPING, samples, minimum=1)
    codes = {p.code for p in problems}

    assert "relationship_unresolved" in codes


def test_validate_raises_with_every_problem_not_the_first() -> None:
    """Fixing a mapping one error per round trip is how the third mistake ships."""
    document = _document(
        ResourceMapping(
            source_endpoint="/x",
            target_table="order",
            fields=(
                FieldMapping(canonical="external_ref", source_path="external_ref"),
                FieldMapping(
                    canonical="total", source_path="order_gross", transform="decimal"
                ),
            ),
            required_fields=("external_ref", "placed_at"),
        )
    )

    with pytest.raises(MappingInvalid) as caught:
        validate(document)

    codes = {p.code for p in caught.value.problems}
    assert codes == {"identity_mapping", "money_without_currency", "required_unmapped"}


def test_the_whole_sim_mapping_validates_against_real_shaped_samples() -> None:
    """The positive case over every resource, so the suite is not only negative."""
    samples = {
        "seller": [
            {
                "vendor_id": "vendor-a",
                "display_name": "A",
                "payout_account": "M1",
                "trading_ccy": "USD",
                "fee_capable": True,
                "hold_capable": False,
            }
        ],
        "order": [
            {
                "order_ref": "ORD-1",
                "order_gross": "10.00",
                "ccy": "USD",
                "booked_on": "2026-11-08T12:00:00+00:00",
                "payout_timing": "INSTANT",
            }
        ],
        "order_item": [
            {
                "line_ref": "ITEM-1",
                "order_ref": "ORD-1",
                "vendor_id": "vendor-a",
                "product_class": "audio",
                "units": 1,
                "unit_cost": "10.00",
                "item_total": "10.00",
                "ccy": "USD",
            }
        ],
        "payment": [
            {
                "payment_ref": "PAY-1",
                "order_ref": "ORD-1",
                "vendor_id": "vendor-a",
                "paid_amount": "10.00",
                "ccy": "USD",
                "payment_state": "COMPLETED",
                "payout_timing": "INSTANT",
                "paid_on": "2026-11-08T12:00:00+00:00",
            }
        ],
        "refund": [
            {
                "refund_ref": "RFND-1",
                "order_ref": "ORD-1",
                "vendor_id": "vendor-a",
                "refund_amount": "2.00",
                "ccy": "USD",
                "refund_state": "COMPLETED",
                "refunded_on": "2026-11-08T12:00:00+00:00",
            }
        ],
        "seller_ledger_entry": [
            {
                "entry_ref": "LED-1",
                "vendor_id": "vendor-a",
                "order_ref": "ORD-1",
                "txn_kind": "sale",
                "direction": "credit",
                "entry_amount": "10.00",
                "ccy": "USD",
                "posted_on": "2026-11-08T12:00:00+00:00",
            }
        ],
    }

    validate(SIM_MAPPING, samples, minimum=1)
