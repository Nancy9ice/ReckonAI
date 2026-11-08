"""The mapping document, its transforms, and how one is produced.

specs/tech-stack.md section 9.1 to 9.3, and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D7.

**A mapping is configuration, not an execution surface.** Transforms are a
closed, named set - decimal parsing, currency extraction, timestamp with
timezone, enum translation. Not arbitrary expressions, because a document a
tenant can edit must not be able to run code.

**A money mapping resolves two paths, not one.** Section 9.3 calls this "the
single most likely place for a mapping to be quietly wrong: map `item_total` to
an amount and forget the currency, and the system will compare numbers across
currencies and report confident nonsense." So a money entry declares an
`amount_path` and a `currency_path`, both required, and `validate.py` treats a
missing currency as a hard failure rather than a warning.

**The LLM pass is not here.** Section 9.2's third pass - a Haiku call guessing
which source field name means which canonical one - is cut from the MVP
(replan R11). Passes 1 and 2 remain, the `llm` provenance value stays in the
vocabulary so the pass can be added without a migration, and whatever the two
deterministic passes leave unmapped goes to human review, which is where an
`llm` entry had to go anyway.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Transforms: a closed set
# ---------------------------------------------------------------------------

Transform = Literal[
    # A decimal amount from a string. Never from a float.
    "decimal",
    # A three-letter currency code, upper-cased.
    "currency",
    # An ISO-8601 timestamp, which must carry a timezone.
    "timestamp",
    # A string passed through, stripped.
    "string",
    # An integer.
    "integer",
    # A boolean.
    "boolean",
    # A value translated through a declared lookup table.
    "enum",
]

TRANSFORMS: frozenset[str] = frozenset(
    ("decimal", "currency", "timestamp", "string", "integer", "boolean", "enum")
)


class TransformError(ValueError):
    """A source value could not be transformed.

    **Always a quarantine, never a default.** specs/tech-stack.md section 9.6:
    "a monetary value is never defaulted to zero. A missing amount is a
    quarantine, not a `0`. Defaulting money is how a reconciliation system
    produces a confident wrong answer."
    """


_CURRENCY = re.compile(r"^[A-Za-z]{3}$")


def apply_transform(
    transform: str, value: Any, *, enum_map: dict[str, str] | None = None
) -> Any:
    """Apply one named transform, raising `TransformError` on anything odd.

    No transform has a fallback. Every failure path raises, because the
    alternative - returning `None`, or a zero - is how a parse failure becomes
    a financial figure.
    """
    if value is None:
        raise TransformError(f"{transform}: the source value is absent")

    if transform == "decimal":
        # **A float is refused rather than converted.** `Money` refuses an
        # inexact amount anyway, but refusing it here names the mapping entry
        # in the error instead of surfacing deep inside the money layer.
        if isinstance(value, float):
            raise TransformError(
                f"decimal: got a float {value!r}. A JSON number is an "
                "IEEE-754 double and cannot carry some decimals exactly; the "
                "source must state amounts as strings."
            )
        try:
            return Decimal(str(value).strip())
        except (InvalidOperation, ArithmeticError) as exc:
            raise TransformError(f"decimal: {value!r} is not a decimal") from exc

    if transform == "currency":
        text = str(value).strip()
        if not _CURRENCY.match(text):
            raise TransformError(
                f"currency: {value!r} is not a three-letter code. "
                "specs/tech-stack.md section 9.3 warns that a provider can "
                "spell money more than one way and that a key named "
                "'currency' is not necessarily a money envelope - a bare "
                "string reaching here as an envelope produces an amount with "
                "no value, silently."
            )
        return text.upper()

    if transform == "timestamp":
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise TransformError(
                f"timestamp: {value!r} is not an ISO-8601 timestamp"
            ) from exc
        if parsed.tzinfo is None:
            # A naive timestamp in a system reconciling across PayPal's clock
            # and a marketplace's own is a defect waiting for a
            # daylight-saving boundary.
            raise TransformError(
                f"timestamp: {value!r} carries no timezone, and this system "
                "stores TIMESTAMPTZ throughout"
            )
        return parsed

    if transform == "string":
        return str(value).strip()

    if transform == "integer":
        try:
            return int(str(value).strip())
        except ValueError as exc:
            raise TransformError(f"integer: {value!r} is not an integer") from exc

    if transform == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        raise TransformError(f"boolean: {value!r} is not a boolean")

    if transform == "enum":
        if not enum_map:
            raise TransformError("enum: the entry declares no translation table")
        text = str(value).strip()
        if text not in enum_map:
            # **Not passed through.** An unrecognised enum value silently
            # accepted is a state vocabulary quietly widened by a source.
            raise TransformError(
                f"enum: {value!r} is not in the declared table "
                f"{sorted(enum_map)}"
            )
        return enum_map[text]

    raise TransformError(f"{transform!r} is not one of {sorted(TRANSFORMS)}")


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class FieldMapping(BaseModel):
    """One canonical field, and where its value comes from.

    `model_config` forbids extra keys, so a typo in a mapping document fails
    validation rather than being ignored - which for a field named
    `currrency_path` would mean an amount with no currency.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str
    source_path: str
    transform: Transform = "string"
    required: bool = False
    provenance: Literal["exact", "synonym", "llm", "human"] = "human"

    #: Only for `transform="enum"`.
    enum_map: dict[str, str] | None = None

    #: Confidence, where the entry came from a pass that has one. Retained for
    #: the cut LLM pass (R11); `None` for a deterministic entry, because a
    #: match that is certain has no confidence to state.
    confidence: Decimal | None = None

    @field_validator("source_path", "canonical")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class MoneyMapping(BaseModel):
    """One canonical money field, resolving **both** an amount and a currency.

    Two paths, both required, and that is the whole reason this is a separate
    model from `FieldMapping` rather than a flag on it. Section 9.3 names
    forgetting the currency as the most likely quiet mapping error; making the
    currency a required field of a distinct type means the mistake cannot be
    expressed, rather than being caught downstream.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str
    amount_path: str
    currency_path: str
    required: bool = False
    provenance: Literal["exact", "synonym", "llm", "human"] = "human"
    confidence: Decimal | None = None

    @field_validator("amount_path", "currency_path", "canonical")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class ResourceMapping(BaseModel):
    """One source endpoint, and the canonical table it feeds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The path the connector reads, relative to `MARKETPLACE_BASE_URL`.
    source_endpoint: str

    target_table: str

    fields: tuple[FieldMapping, ...] = ()
    money_fields: tuple[MoneyMapping, ...] = ()

    #: Canonical fields that must resolve or the record is quarantined.
    #: Declared here rather than inferred from `required` on each entry, so a
    #: field that is required *and unmapped* is a validation failure rather
    #: than an absence nobody declared.
    required_fields: tuple[str, ...] = ()

    #: Declared relationships: canonical field -> the resource whose records it
    #: must resolve against. Section 9.4's fifth check.
    relationships: dict[str, str] = Field(default_factory=dict)

    def all_canonical(self) -> set[str]:
        return {f.canonical for f in self.fields} | {
            m.canonical for m in self.money_fields
        }


class MappingDocument(BaseModel):
    """A whole mapping, as stored in `connector_mapping.document`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector: str
    resources: tuple[ResourceMapping, ...]

    def resource(self, target_table: str) -> ResourceMapping:
        for item in self.resources:
            if item.target_table == target_table:
                return item
        raise KeyError(f"no resource mapping for {target_table!r}")


# ---------------------------------------------------------------------------
# Section 9.2 passes 1 and 2
# ---------------------------------------------------------------------------

#: The curated synonym table. "Version-controlled project knowledge that
#: improves with every marketplace we meet", and section 9.2 says it "covers
#: most of what the readme's examples show".
#:
#: Keys are source field names as encountered; values are canonical names.
SYNONYMS: dict[str, str] = {
    "vendor_id": "seller_id",
    "vendor_ref": "seller_id",
    "merchant_id": "seller_id",
    "order_ref": "order_id",
    "order_number": "order_id",
    "line_ref": "external_ref",
    "entry_ref": "external_ref",
    "rule_ref": "id",
    "payment_ref": "external_id",
    "refund_ref": "external_id",
    "dispute_ref": "external_id",
    "release_ref": "external_id",
    "item_total": "line_total",
    "unit_cost": "unit_price",
    "order_gross": "total",
    "paid_amount": "amount",
    "refund_amount": "amount",
    "entry_amount": "amount",
    "disputed_amount": "amount",
    "released_amount": "amount",
    "display_name": "name",
    "product_class": "category",
    "units": "quantity",
    "commission_pct": "rate",
    "txn_kind": "entry_category",
    "direction": "transaction_type",
    "payout_timing": "disbursement_mode",
    "payout_account": "paypal_merchant_id",
    "trading_ccy": "default_currency",
    "fee_capable": "partner_fee_enabled",
    "hold_capable": "delay_funds_enabled",
    "booked_on": "placed_at",
    "paid_on": "occurred_at",
    "refunded_on": "occurred_at",
    "posted_on": "occurred_at",
    "raised_on": "occurred_at",
    "released_on": "released_at",
    "effective_from": "valid_from",
    "effective_to": "valid_to",
    "payment_state": "status",
    "refund_state": "status",
    "case_state": "status",
    "case_reason": "reason",
    "release_state": "state",
    "ccy": "currency",
}


def _normalise(name: str) -> str:
    """Lower-case, separators collapsed, for pass 1's comparison."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def suggest(
    source_fields: list[str], canonical_fields: list[str]
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Run passes 1 and 2 and report what they resolved and what they did not.

    Returns `({source_field: (canonical_field, provenance)}, unresolved)`.

    **Deterministic and explainable, in that order.** Section 9.2: "the cheap,
    explainable passes run first and the model only sees the residue." With
    pass 3 cut, the residue goes to human review - which is where an `llm`
    entry had to go anyway, since a mapping cannot be activated by a model.

    This function suggests and never decides. Nothing here writes a mapping or
    activates one.
    """
    canonical_by_normal = {_normalise(name): name for name in canonical_fields}
    resolved: dict[str, tuple[str, str]] = {}
    unresolved: list[str] = []

    for field in source_fields:
        # Pass 1: exact, or equal after normalising case and separators.
        exact = canonical_by_normal.get(_normalise(field))
        if exact is not None:
            resolved[field] = (exact, "exact")
            continue

        # Pass 2: the curated synonym table.
        synonym = SYNONYMS.get(field)
        if synonym is not None and synonym in canonical_fields:
            resolved[field] = (synonym, "synonym")
            continue

        unresolved.append(field)

    return resolved, unresolved
