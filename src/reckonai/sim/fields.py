"""The simulated marketplace's outward field names, in one place.

specs/2026-11-08-block-a-marketplace-connector/requirements.md D1 and D3, and
specs/roadmap.md Phase 6's R3 guard. Every key the sim emits comes from a map
here, which makes the guard a single assertion over this module rather than a
review of every endpoint.

**Why the names are deliberately wrong.** We author both sides of this
boundary, so a mapping that matched trivially would be easy to write and would
mean mapping was never exercised at all - the risk R3 names so it is guarded
rather than discovered. So no canonical field draws from a source field of the
same name, and `tests/test_sim_fields.py` fails if one ever does.

Two strengths of check, both enforced:

  1. **Per entry:** no outward name equals the canonical name it maps from.
     This is R3's guard exactly.
  2. **Globally:** no outward name appears anywhere in the canonical schema -
     neither a column name from `Base.metadata` nor a composite attribute name
     such as `gross_amount`. Stronger, and worth having because a name that is
     canonical *somewhere else* is exactly the kind of near-miss a mapping gets
     wrong: `state` would be innocuous on a dispute and wrong on a
     disbursement. Composite attributes are included because they are not
     columns, so a column-only check passes them silently - see `ORDER_FIELDS`
     for the one name this actually caught.

**Money crosses as two keys, not an envelope object.** `{"gross_amount":
"160.00", "ccy": "USD"}`, so a money mapping has to resolve an amount path and
a currency path separately - which is what makes specs/tech-stack.md section
9.3's hard failure reachable rather than theoretical. One `ccy` key serves
every amount in a record, since a marketplace order has one currency, and that
is also the shape most likely to tempt a mapping into dropping the currency.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

#: The currency key every record carries beside its amounts.
CURRENCY_KEY: Final = "ccy"


def _frozen(mapping: dict[str, str]) -> MappingProxyType[str, str]:
    """Read-only, so an endpoint cannot quietly add a key past the guard."""
    return MappingProxyType(mapping)


# Canonical name -> the name the sim emits. The canonical side is the attribute
# or column the connector's mapping has to arrive at, which is why the keys are
# spelled as the model spells them.

SELLER_FIELDS = _frozen(
    {
        "external_ref": "vendor_id",
        "name": "display_name",
        "paypal_merchant_id": "payout_account",
        "default_currency": "trading_ccy",
        "partner_fee_enabled": "fee_capable",
        "delay_funds_enabled": "hold_capable",
    }
)

ORDER_FIELDS = _frozen(
    {
        "external_ref": "order_ref",
        # `order_gross` rather than the `gross_amount` requirements.md D3 first
        # named. `gross_amount` is a composite *attribute* on `capture` and
        # `refund` - not a column, so a column-name check would have waved it
        # through, while a mapping author reading `gross_amount` on an order
        # could reasonably reach for the capture field. The global check below
        # covers composite attribute names too, and this is the one name it
        # caught.
        "total_amount": "order_gross",
        "total_currency": CURRENCY_KEY,
        "placed_at": "booked_on",
        "disbursement_mode": "payout_timing",
    }
)

ORDER_ITEM_FIELDS = _frozen(
    {
        "external_ref": "line_ref",
        "order_id": "order_ref",
        "seller_id": "vendor_id",
        "category": "product_class",
        "quantity": "units",
        "unit_price_amount": "unit_cost",
        "line_total_amount": "item_total",
        "line_total_currency": CURRENCY_KEY,
    }
)

COMMISSION_RULE_FIELDS = _frozen(
    {
        # **`id`, not `external_ref`.** `commission_rule` is the one catalogue
        # table with no `external_ref` column - Phase 2 gave it none, since a
        # rule is the marketplace's own policy rather than a record it received
        # from somewhere. So the sim exposes the rule's UUID as its outward
        # reference, and A3's ingestion keys rules on it.
        "id": "rule_ref",
        "seller_id": "vendor_id",
        "category": "product_class",
        "rate": "commission_pct",
        "valid_from": "effective_from",
        "valid_to": "effective_to",
    }
)

PAYMENT_FIELDS = _frozen(
    {
        "external_id": "payment_ref",
        "order_id": "order_ref",
        "seller_id": "vendor_id",
        "amount_amount": "paid_amount",
        "amount_currency": CURRENCY_KEY,
        "status": "payment_state",
        "disbursement_mode": "payout_timing",
        "occurred_at": "paid_on",
    }
)

REFUND_FIELDS = _frozen(
    {
        "external_id": "refund_ref",
        "order_id": "order_ref",
        "seller_id": "vendor_id",
        "amount_amount": "refund_amount",
        "amount_currency": CURRENCY_KEY,
        "status": "refund_state",
        "occurred_at": "refunded_on",
    }
)

LEDGER_ENTRY_FIELDS = _frozen(
    {
        "external_ref": "entry_ref",
        "seller_id": "vendor_id",
        "order_id": "order_ref",
        "entry_category": "txn_kind",
        "transaction_type": "direction",
        "amount_amount": "entry_amount",
        "amount_currency": CURRENCY_KEY,
        "occurred_at": "posted_on",
    }
)

DISPUTE_FIELDS = _frozen(
    {
        "external_id": "dispute_ref",
        "order_id": "order_ref",
        "seller_id": "vendor_id",
        "amount_amount": "disputed_amount",
        "amount_currency": CURRENCY_KEY,
        # `case_state` rather than `dispute_state`, which is a real column on
        # the canonical `dispute` table - the global check above would catch it,
        # and it is the exact near-miss that check exists for.
        "status": "case_state",
        "reason": "case_reason",
        "occurred_at": "raised_on",
    }
)

#: The marketplace's statement of what it believes each seller is owed per
#: order. Not a canonical table - it is the marketplace's own expectation, and
#: specs/roadmap.md Phase 4 lists it among the endpoints the multiparty
#: amendment requires. Block B computes its own figure and Block C compares.
ENTITLEMENT_FIELDS = _frozen(
    {
        "order_ref": "order_ref",
        "seller_ref": "vendor_id",
        "owed_amount": "owed_amount",
        "commission_amount": "retained_amount",
        "currency": CURRENCY_KEY,
    }
)

#: **Not served.** The sim had a `/releases` endpoint reading `disbursement`,
#: but those rows are PayPal's and now come from `fixtures/paypal/` through
#: the replay ingester (R16). The marketplace's own release records - which
#: captures it instructed PayPal to disburse, per specs/mission.md section 3
#: - have no canonical table yet, so there is nothing marketplace-side to
#: serve. Kept here because the field names are settled and the endpoint
#: returns the moment that table exists.
DISBURSEMENT_FIELDS = _frozen(
    {
        "external_id": "release_ref",
        "seller_id": "vendor_id",
        "amount_amount": "released_amount",
        "amount_currency": CURRENCY_KEY,
        "state": "release_state",
        "released_at": "released_on",
    }
)

#: Every map, by the resource it describes. The guard iterates this, so a new
#: resource is covered by adding it here and nothing else.
ALL_FIELD_MAPS: Final[dict[str, MappingProxyType[str, str]]] = {
    "sellers": SELLER_FIELDS,
    "orders": ORDER_FIELDS,
    "order_items": ORDER_ITEM_FIELDS,
    "commission_rules": COMMISSION_RULE_FIELDS,
    "payments": PAYMENT_FIELDS,
    "refunds": REFUND_FIELDS,
    "ledger_entries": LEDGER_ENTRY_FIELDS,
    "disputes": DISPUTE_FIELDS,
}

#: `entitlements` is excluded from the guard above and listed separately,
#: because it is not a canonical table and its "canonical" keys are this
#: module's own invention - so "no outward name equals its canonical name"
#: would be asserting something about nothing. Its outward names still have to
#: clear the global check, which `tests/test_sim_fields.py` applies to it.
NON_CANONICAL_FIELD_MAPS: Final[dict[str, MappingProxyType[str, str]]] = {
    "entitlements": ENTITLEMENT_FIELDS,
}


def outward_names() -> set[str]:
    """Every key the sim can emit, across every resource."""
    names: set[str] = set()
    for mapping in (*ALL_FIELD_MAPS.values(), *NON_CANONICAL_FIELD_MAPS.values()):
        names.update(mapping.values())
    return names


def identical_pairs() -> list[tuple[str, str, str]]:
    """Every entry whose outward name equals its canonical name.

    Returns `(resource, canonical, outward)` triples. **Empty is the passing
    result** - a non-empty list is R3's guard failing, and the caller names the
    offenders rather than asserting a bare count.
    """
    return [
        (resource, canonical, outward)
        for resource, mapping in ALL_FIELD_MAPS.items()
        for canonical, outward in mapping.items()
        if canonical == outward
    ]
