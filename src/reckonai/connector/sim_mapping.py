"""The active mapping for the simulated marketplace.

`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D3 and D7.

**Every entry here maps a deliberately different name**, and the validation
gate enforces that rather than trusting it: `validate._check_resource` emits an
`identity_mapping` problem for any entry whose source path's leaf equals its
canonical name. So this document cannot drift into a trivial match without
failing activation.

It is checked in as code rather than as a JSON file because it is a fixture for
*our* simulated marketplace, not a tenant's configuration. A real tenant's
mapping is a `connector_mapping` row produced by `mapping.suggest` plus human
review; this is the one we author both sides of, and keeping it beside the
validator makes the two readable together.
"""

from __future__ import annotations

from reckonai.connector.mapping import (
    FieldMapping,
    MappingDocument,
    MoneyMapping,
    ResourceMapping,
)

CONNECTOR_NAME = "reckonmarket-sim"

SIM_MAPPING = MappingDocument(
    connector=CONNECTOR_NAME,
    resources=(
        ResourceMapping(
            source_endpoint="/marketplace/sellers",
            target_table="seller",
            fields=(
                FieldMapping(
                    canonical="external_ref",
                    source_path="vendor_id",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="name",
                    source_path="display_name",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="paypal_merchant_id",
                    source_path="payout_account",
                    transform="string",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="default_currency",
                    source_path="trading_ccy",
                    transform="currency",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="partner_fee_enabled",
                    source_path="fee_capable",
                    transform="boolean",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="delay_funds_enabled",
                    source_path="hold_capable",
                    transform="boolean",
                    required=True,
                    provenance="synonym",
                ),
            ),
            required_fields=(
                "external_ref",
                "name",
                "partner_fee_enabled",
                "delay_funds_enabled",
            ),
        ),
        ResourceMapping(
            source_endpoint="/marketplace/orders",
            target_table="order",
            fields=(
                FieldMapping(
                    canonical="external_ref",
                    source_path="order_ref",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="placed_at",
                    source_path="booked_on",
                    transform="timestamp",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="disbursement_mode",
                    source_path="payout_timing",
                    transform="enum",
                    provenance="synonym",
                    # An enum translation that happens to be the identity on
                    # its values, and declared anyway. The point is not the
                    # translation: it is that an unrecognised value raises
                    # rather than widening `DISBURSEMENT_MODES` by arriving.
                    enum_map={"INSTANT": "INSTANT", "DELAYED": "DELAYED"},
                ),
            ),
            money_fields=(
                MoneyMapping(
                    canonical="total",
                    amount_path="order_gross",
                    currency_path="ccy",
                    required=True,
                    provenance="synonym",
                ),
            ),
            required_fields=("external_ref", "placed_at", "total"),
        ),
        ResourceMapping(
            source_endpoint="/marketplace/order-items",
            target_table="order_item",
            fields=(
                FieldMapping(
                    canonical="external_ref",
                    source_path="line_ref",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="order_id",
                    source_path="order_ref",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="seller_id",
                    source_path="vendor_id",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="category",
                    source_path="product_class",
                    transform="string",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="quantity",
                    source_path="units",
                    transform="integer",
                    required=True,
                    provenance="synonym",
                ),
            ),
            money_fields=(
                MoneyMapping(
                    canonical="unit_price",
                    amount_path="unit_cost",
                    currency_path="ccy",
                    required=True,
                    provenance="synonym",
                ),
                MoneyMapping(
                    canonical="line_total",
                    amount_path="item_total",
                    currency_path="ccy",
                    required=True,
                    provenance="synonym",
                ),
            ),
            required_fields=(
                "external_ref",
                "order_id",
                "seller_id",
                "quantity",
                "unit_price",
                "line_total",
            ),
            # **Seller attribution is the join the whole reconciliation rests
            # on** (specs/tech-stack.md section 9.5), so it is declared and
            # validated rather than assumed.
            relationships={"order_id": "order", "seller_id": "seller"},
        ),
        ResourceMapping(
            source_endpoint="/marketplace/ledger-entries",
            target_table="seller_ledger_entry",
            fields=(
                FieldMapping(
                    canonical="external_ref",
                    source_path="entry_ref",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="seller_id",
                    source_path="vendor_id",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="order_id",
                    source_path="order_ref",
                    transform="string",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="entry_category",
                    source_path="txn_kind",
                    transform="enum",
                    required=True,
                    provenance="synonym",
                    enum_map={
                        "sale": "sale",
                        "commission": "commission",
                        "commission_reversal": "commission_reversal",
                        "refund": "refund",
                        "dispute": "dispute",
                        "adjustment": "adjustment",
                        "fee": "fee",
                    },
                ),
                FieldMapping(
                    canonical="transaction_type",
                    source_path="direction",
                    transform="enum",
                    required=True,
                    provenance="synonym",
                    enum_map={"debit": "debit", "credit": "credit"},
                ),
                FieldMapping(
                    canonical="occurred_at",
                    source_path="posted_on",
                    transform="timestamp",
                    required=True,
                    provenance="synonym",
                ),
            ),
            money_fields=(
                MoneyMapping(
                    canonical="amount",
                    amount_path="entry_amount",
                    currency_path="ccy",
                    required=True,
                    provenance="synonym",
                ),
            ),
            required_fields=(
                "external_ref",
                "seller_id",
                "entry_category",
                "transaction_type",
                "occurred_at",
                "amount",
            ),
            # **`order_id` has to be declared here, not just mapped.** The
            # source states it as `ORD-0001`; the column is a UUID. A mapped
            # reference with no relationship declared reaches the insert as a
            # string, which Postgres refuses - loudly, which is the good case.
            # Declaring it is what turns the reference into a primary key.
            relationships={"seller_id": "seller", "order_id": "order"},
        ),
        ResourceMapping(
            source_endpoint="/marketplace/payments",
            target_table="payment",
            fields=(
                FieldMapping(
                    canonical="external_id",
                    source_path="payment_ref",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="order_id",
                    source_path="order_ref",
                    transform="string",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="seller_id",
                    source_path="vendor_id",
                    transform="string",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="status",
                    source_path="payment_state",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="disbursement_mode",
                    source_path="payout_timing",
                    transform="enum",
                    provenance="synonym",
                    enum_map={"INSTANT": "INSTANT", "DELAYED": "DELAYED"},
                ),
                FieldMapping(
                    canonical="occurred_at",
                    source_path="paid_on",
                    transform="timestamp",
                    required=True,
                    provenance="synonym",
                ),
            ),
            money_fields=(
                MoneyMapping(
                    canonical="amount",
                    amount_path="paid_amount",
                    currency_path="ccy",
                    required=True,
                    provenance="synonym",
                ),
            ),
            required_fields=("external_id", "status", "occurred_at", "amount"),
            relationships={"seller_id": "seller", "order_id": "order"},
        ),
        ResourceMapping(
            source_endpoint="/marketplace/refunds",
            target_table="refund",
            fields=(
                FieldMapping(
                    canonical="external_id",
                    source_path="refund_ref",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="seller_id",
                    source_path="vendor_id",
                    transform="string",
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="status",
                    source_path="refund_state",
                    transform="string",
                    required=True,
                    provenance="synonym",
                ),
                FieldMapping(
                    canonical="occurred_at",
                    source_path="refunded_on",
                    transform="timestamp",
                    required=True,
                    provenance="synonym",
                ),
            ),
            money_fields=(
                MoneyMapping(
                    canonical="amount",
                    amount_path="refund_amount",
                    currency_path="ccy",
                    required=True,
                    provenance="synonym",
                ),
            ),
            required_fields=("external_id", "status", "occurred_at", "amount"),
            relationships={"seller_id": "seller"},
        ),
    ),
)
