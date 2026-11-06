#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build specs/paypal-fields.md - Phase 0, task group 5.

Generates the field availability table from artifacts/declared.json and
artifacts/observed.json. Every verdict is computed from observed.json, never
from the declared column (requirements.md D1), so validation.md C6 holds by
construction rather than by review.

What is human input here, and what is not:

  - The CANONICAL list below is judgement: which canonical field maps to which
    PayPal path, what the field is for, and - where a field could not be
    observed - the reason no fixture exercised it. That is versioned here so it
    is reviewable.
  - Every verdict, count and condition is mechanical. No verdict can be written
    by hand into the document.

Verdict rules, applied in this order:

  present                 the path was populated on every sampled record
  present-but-unreliable  populated on some but not all, with the condition
                          named from the sample labels (requirements.md D5)
  unanswered              no sampled record exercised the condition under
                          which the field could appear, and that condition is
                          known and stateable
  absent                  a record that should have carried the field did not

The difference between the last two is the whole point of being careful here.
"absent" is a claim about PayPal. "unanswered" is a statement about our
fixtures. notes.md records how easily a missing permission or a missing fixture
masquerades as a finding about the API, and validation.md section 5 is explicit
that the two must not be conflated.

Usage:
    uv run scripts/build_fields_doc.py --help
    uv run scripts/build_fields_doc.py \\
        --artifacts specs/2026-11-05-paypal-field-discovery/artifacts \\
        --out specs/paypal-fields.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRESENT = "present"
UNRELIABLE = "present-but-unreliable"
ABSENT = "absent"
UNANSWERED = "unanswered"
VERDICTS = (PRESENT, UNRELIABLE, ABSENT, UNANSWERED)

# Reasons a field could not be observed. Naming the reason is what makes
# "unanswered" a finding rather than a shrug; each one names the fixture that
# would answer it.
NO_DISPUTE = (
    "no dispute record exists; a wallet-funded capture would answer it"
)
NO_WALLET = (
    "both order fixtures were card-funded, and a card-funded order carries no "
    "payer at all. A wallet-funded order would answer it"
)
NO_XC_REFUND = (
    "the partial refund was taken against the USD capture, so no conversion "
    "occurred. A refund against the EUR capture would answer it"
)
NO_XC_PAYOUT = (
    "both payout batches were USD to USD. A cross-currency payout batch would "
    "answer it"
)

# Canonical field -> the PayPal path(s) that could carry it.
#
# "gate" names a second path that must also be populated for the field to mean
# what the canonical model wants. A capture's gross_amount is always there, but
# it is only the SOURCE AMOUNT OF A CONVERSION when a conversion happened, so
# the fx_conversion rows gate on the exchange rate. Without the gate the table
# would report provider conversion evidence we do not have.
CANONICAL: list[dict[str, Any]] = [
    # ---------------------------------------------------------------- payment
    {
        "entity": "payment",
        "field": "payment_id",
        "sources": [("orders", "$.id")],
        "note": "PayPal's order id. The canonical payment key.",
    },
    {
        "entity": "payment",
        "field": "amount",
        "sources": [("orders", "$.purchase_units[].amount.value")],
        "note": "String, parses straight to Decimal.",
    },
    {
        "entity": "payment",
        "field": "currency",
        "sources": [("orders", "$.purchase_units[].amount.currency_code")],
        "note": "Buyer-facing currency, not necessarily the settlement currency.",
    },
    {
        "entity": "payment",
        "field": "status",
        "sources": [("orders", "$.status")],
        "note": "COMPLETED observed. Feeds the lifecycle enum of Phase 3.",
    },
    {
        "entity": "payment",
        "field": "intent",
        "sources": [("orders", "$.intent")],
        "note": "CAPTURE observed. Distinguishes authorize-then-capture flows.",
    },
    {
        "entity": "payment",
        "field": "created_at",
        "sources": [("orders", "$.create_time")],
        "note": "RFC 3339.",
    },
    {
        "entity": "payment",
        "field": "updated_at",
        "sources": [("orders", "$.update_time")],
        "note": "RFC 3339.",
    },
    {
        "entity": "payment",
        "field": "payer_identity",
        "sources": [("orders", "$.payer")],
        "unobserved_reason": NO_WALLET,
        "note": "Buyer identity. See notes.md F5: a refund's payer block carries "
        "the MERCHANT, so it is not a substitute.",
    },
    {
        "entity": "payment",
        "field": "funding_instrument",
        "sources": [("orders", "$.payment_source.card.brand")],
        "note": "Card brand. The funding instrument determines whether payer "
        "identity and disputes are available at all.",
    },
    {
        "entity": "payment",
        "field": "seller_identity",
        "sources": [("orders", "$.purchase_units[].payee.merchant_id")],
        "note": "The payee merchant id. Ties a payment to a seller.",
    },
    # ---------------------------------------------------------------- capture
    {
        "entity": "capture",
        "field": "capture_id",
        "sources": [("captures", "$.id")],
        "note": "The authoritative settlement-side key.",
    },
    {
        "entity": "capture",
        "field": "payment_linkage",
        "sources": [("captures", "$.supplementary_data.related_ids.order_id")],
        "note": "A real field, not a link to parse. Capture to order is a "
        "provider-supplied foreign key.",
    },
    {
        "entity": "capture",
        "field": "amount",
        "sources": [("captures", "$.amount.value")],
    },
    {
        "entity": "capture",
        "field": "currency",
        "sources": [("captures", "$.amount.currency_code")],
    },
    {
        "entity": "capture",
        "field": "gross_amount",
        "sources": [("captures", "$.seller_receivable_breakdown.gross_amount.value")],
        "note": "Pre-fee, pre-conversion.",
    },
    {
        "entity": "capture",
        "field": "net_amount",
        "sources": [("captures", "$.seller_receivable_breakdown.net_amount.value")],
        "note": "Equal to gross on our fixtures because no fee was assessed.",
    },
    {
        "entity": "capture",
        "field": "paypal_fee",
        "sources": [("captures", "$.seller_receivable_breakdown.paypal_fee.value")],
        "note": "**The weakest row in this table.** Absent on both captures, yet "
        "the refund carries paypal_fee as an explicit 0.00, so PayPal does emit "
        "the field when it has a value. Cause not established. Phase 7 must NOT "
        "conclude that PayPal withholds capture fees; re-verify against a "
        "fee-bearing transaction. See notes.md.",
    },
    {
        "entity": "capture",
        "field": "settlement_amount",
        "sources": [
            ("captures", "$.seller_receivable_breakdown.receivable_amount.value")
        ],
        "note": "What the seller actually receives, in the settlement currency.",
    },
    {
        "entity": "capture",
        "field": "settlement_currency",
        "sources": [
            ("captures", "$.seller_receivable_breakdown.receivable_amount.currency_code"),
            ("balances", "$.balances[].currency"),
        ],
        "note": "The balances endpoint answers this directly and unconditionally, "
        "which is why it beats inferring it per capture.",
    },
    {
        "entity": "capture",
        "field": "status",
        "sources": [("captures", "$.status")],
        "note": "PARTIALLY_REFUNDED observed, which the order view agrees with.",
    },
    {
        "entity": "capture",
        "field": "final_capture",
        "sources": [("captures", "$.final_capture")],
        "note": "Whether more captures may follow against the authorization.",
    },
    {
        "entity": "capture",
        "field": "created_at",
        "sources": [("captures", "$.create_time")],
    },
    {
        "entity": "capture",
        "field": "updated_at",
        "sources": [("captures", "$.update_time")],
    },
    {
        "entity": "capture",
        "field": "platform_fee",
        "sources": [
            ("captures", "$.seller_receivable_breakdown.platform_fees[].amount.value")
        ],
        "note": "Marketplace commission. Absent on captures; see the refund row, "
        "where it appears only through the order view.",
    },
    # ----------------------------------------------------------------- refund
    {
        "entity": "refund",
        "field": "refund_id",
        "sources": [("refunds", "$.id")],
    },
    {
        "entity": "refund",
        "field": "capture_linkage",
        "sources": [("refunds", "$.links[].href")],
        "note": "**Derived, not provider-supplied.** There is no capture_id field "
        "on a refund; the only link back is links[rel=up], whose href must be "
        "parsed. Phase 7 must record this as a derived linkage.",
    },
    {
        "entity": "refund",
        "field": "amount",
        "sources": [("refunds", "$.amount.value")],
    },
    {
        "entity": "refund",
        "field": "currency",
        "sources": [("refunds", "$.amount.currency_code")],
    },
    {
        "entity": "refund",
        "field": "gross_amount",
        "sources": [("refunds", "$.seller_payable_breakdown.gross_amount.value")],
        "note": "Note the name: a refund carries seller_PAYABLE_breakdown, a "
        "capture seller_RECEIVABLE_breakdown. Mapping code cannot share a path.",
    },
    {
        "entity": "refund",
        "field": "net_amount",
        "sources": [("refunds", "$.seller_payable_breakdown.net_amount.value")],
    },
    {
        "entity": "refund",
        "field": "paypal_fee",
        "sources": [("refunds", "$.seller_payable_breakdown.paypal_fee.value")],
        "note": "Present as an explicit 0.00. This is the evidence that the "
        "capture-side absence is not PayPal withholding the field.",
    },
    {
        "entity": "refund",
        "field": "total_refunded_amount",
        "sources": [
            ("refunds", "$.seller_payable_breakdown.total_refunded_amount.value")
        ],
        "note": "What makes partial-refund tracking possible. readme section 13 "
        "Scenario 1 depends on it.",
    },
    {
        "entity": "refund",
        "field": "platform_fee",
        "sources": [
            (
                "orders",
                "$.purchase_units[].payments.refunds[].seller_payable_breakdown"
                ".platform_fees[].amount.value",
            ),
            ("refunds", "$.seller_payable_breakdown.platform_fees[].amount.value"),
        ],
        "note": "**Read-path dependent.** Populated when the refund is read "
        "through the order, absent from the refund endpoint for the same record. "
        "Phase 7 should ingest refunds via the order. See notes.md F2.",
    },
    {
        "entity": "refund",
        "field": "status",
        "sources": [("refunds", "$.status")],
    },
    {
        "entity": "refund",
        "field": "created_at",
        "sources": [("refunds", "$.create_time")],
        "note": "Returned with a -08:00 offset where captures use Z. Both are "
        "RFC 3339; a naive string comparison across the two would be wrong.",
    },
    # ---------------------------------------------------------------- dispute
    {
        "entity": "dispute",
        "field": "dispute_id",
        "sources": [("disputes", "$.items[].dispute_id")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "status",
        "sources": [("disputes", "$.items[].status")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "lifecycle_stage",
        "sources": [("disputes", "$.items[].dispute_life_cycle_stage")],
        "unobserved_reason": NO_DISPUTE,
        "note": "Declared read-only. The lifecycle states mission.md P4 wants.",
    },
    {
        "entity": "dispute",
        "field": "dispute_state",
        "sources": [("disputes", "$.items[].dispute_state")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "amount",
        "sources": [("disputes", "$.items[].dispute_amount.value")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "currency",
        "sources": [("disputes", "$.items[].dispute_amount.currency_code")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "reason",
        "sources": [("disputes", "$.items[].reason")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "created_at",
        "sources": [("disputes", "$.items[].create_time")],
        "unobserved_reason": NO_DISPUTE,
    },
    {
        "entity": "dispute",
        "field": "seller_response_due_date",
        "sources": [("disputes", "$.items[].seller_response_due_date")],
        "unobserved_reason": NO_DISPUTE,
        "note": "Drives the awaiting-evidence lifecycle state of mission.md P4.",
    },
    {
        "entity": "dispute",
        "field": "capture_linkage",
        "sources": [("disputes", "$.items[].links[].href")],
        "unobserved_reason": NO_DISPUTE,
        "note": "The list response declares no transaction id at all, only links. "
        "Linkage would have to come from the single-dispute endpoint.",
    },
    # ----------------------------------------------------------- payout batch
    {
        "entity": "payout_batch",
        "field": "batch_id",
        "sources": [("payouts", "$.batch_header.payout_batch_id")],
    },
    {
        "entity": "payout_batch",
        "field": "status",
        "sources": [("payouts", "$.batch_header.batch_status")],
        "note": "SUCCESS and DENIED both observed.",
    },
    {
        "entity": "payout_batch",
        "field": "amount",
        "sources": [("payouts", "$.batch_header.amount.value")],
    },
    {
        "entity": "payout_batch",
        "field": "currency",
        "sources": [("payouts", "$.batch_header.amount.currency")],
        "note": "**Spelled `currency`, not `currency_code`.** Payouts v1 is the "
        "only API that does this, and the contract declares it. See notes.md F3 "
        "and G4.",
    },
    {
        "entity": "payout_batch",
        "field": "fees",
        "sources": [("payouts", "$.batch_header.fees.value")],
        "note": "**Only trustworthy on a batch that processed.** On SUCCESS it "
        "equals the sum of item fees. On DENIED the figure is not the fee that "
        "would have been charged - observed wrong by a factor of 2.6. Never "
        "carry a denied batch's fee forward.",
    },
    {
        "entity": "payout_batch",
        "field": "created_at",
        "sources": [("payouts", "$.batch_header.time_created")],
    },
    {
        "entity": "payout_batch",
        "field": "completed_at",
        "sources": [("payouts", "$.batch_header.time_completed")],
        "note": "Populated on the denied batch too; it records when processing "
        "ended, not that it succeeded.",
    },
    {
        "entity": "payout_batch",
        "field": "funding_source",
        "sources": [("payouts", "$.batch_header.funding_source")],
    },
    # ------------------------------------------------------------ payout item
    {
        "entity": "payout_item",
        "field": "item_id",
        "sources": [("payouts", "$.items[].payout_item_id")],
    },
    {
        "entity": "payout_item",
        "field": "batch_linkage",
        "sources": [("payouts", "$.items[].payout_batch_id")],
        "note": "Provider-supplied, repeated on every item.",
    },
    {
        "entity": "payout_item",
        "field": "amount",
        "sources": [("payouts", "$.items[].payout_item.amount.value")],
    },
    {
        "entity": "payout_item",
        "field": "currency",
        "sources": [("payouts", "$.items[].payout_item.amount.currency")],
    },
    {
        "entity": "payout_item",
        "field": "fee",
        "sources": [("payouts", "$.items[].payout_item_fee.value")],
        "note": "Populated in both terminal states, but 0.00 on the failed items. "
        "Reconcile against the batch fee only on a successful batch.",
    },
    {
        "entity": "payout_item",
        "field": "status",
        "sources": [("payouts", "$.items[].transaction_status")],
        "note": "SUCCESS and FAILED both observed.",
    },
    {
        "entity": "payout_item",
        "field": "transaction_id",
        "sources": [("payouts", "$.items[].transaction_id")],
        "note": "The link to the money movement. Absent on a failed item, which "
        "is correct - no transaction occurred.",
    },
    {
        "entity": "payout_item",
        "field": "processed_at",
        "sources": [("payouts", "$.items[].time_processed")],
        "note": "**Populated on failed items too** (4 of 4). PayPal stamps a time "
        "on any item it attempted, so this timestamp is not evidence of payment. "
        "This corrects an earlier claim in fixtures.md; see notes.md F1.",
    },
    {
        "entity": "payout_item",
        "field": "recipient",
        "sources": [("payouts", "$.items[].payout_item.receiver")],
        "note": "Redacted in the committed artifacts as buyer PII; presence is "
        "what the verdict rests on.",
    },
    {
        "entity": "payout_item",
        "field": "failure_reason",
        "sources": [("payouts", "$.items[].errors.name")],
        "note": "RECEIVER_COUNTRY_NOT_ALLOWED observed. **Account-dependent**: a "
        "different sandbox account denied the same request with errors empty. "
        "When it is absent the product must answer insufficient_evidence rather "
        "than assemble a cause.",
    },
    {
        "entity": "payout_item",
        "field": "sender_item_reference",
        "sources": [("payouts", "$.items[].payout_item.sender_item_id")],
        "note": "Our own idempotency/correlation key, echoed back.",
    },
    # --------------------------------------------------------- fx_conversion
    # requirements.md section 7 enumerates these. They are gated on an actual
    # conversion record so the table cannot report conversion evidence that
    # only exists because an unconverted amount happened to be present.
    {
        "entity": "fx_conversion",
        "field": "source_amount",
        "sources": [
            (
                "captures",
                "$.seller_receivable_breakdown.gross_amount.value",
                "$.seller_receivable_breakdown.exchange_rate.value",
            )
        ],
        "note": "Gated on a conversion existing. EUR 95.50 observed.",
    },
    {
        "entity": "fx_conversion",
        "field": "source_currency",
        "sources": [
            ("captures", "$.seller_receivable_breakdown.exchange_rate.source_currency")
        ],
        "note": "Declared on the conversion record itself.",
    },
    {
        "entity": "fx_conversion",
        "field": "target_amount",
        "sources": [
            ("captures", "$.seller_receivable_breakdown.receivable_amount.value")
        ],
        "note": "USD 104.05 observed. **Rounded to the cent by PayPal**, while "
        "the rate is not. Compare against this figure, never against a "
        "recomputation of gross times rate, or every cross-currency transaction "
        "shows a half-cent exception that is a rounding artefact.",
    },
    {
        "entity": "fx_conversion",
        "field": "target_currency",
        "sources": [
            ("captures", "$.seller_receivable_breakdown.exchange_rate.target_currency")
        ],
    },
    {
        "entity": "fx_conversion",
        "field": "rate",
        "sources": [("captures", "$.seller_receivable_breakdown.exchange_rate.value")],
        "note": "**15 decimal places observed** (1.089477173902071). Parse from "
        "the string into Decimal and store at full precision; rounding it before "
        "the Phase 11 comparison manufactures a discrepancy.",
    },
    {
        "entity": "fx_conversion",
        "field": "timestamp",
        "sources": [],
        "note": "**No declared source anywhere in PayPal's contract.** Settled "
        "across all four declared conversion sites and both source grades; see "
        "notes.md G3. No further probing can change this. A timestamp taken from "
        "the capture's create_time is a derivation, not provider evidence.",
    },
    {
        "entity": "fx_conversion",
        "field": "fee",
        "sources": [],
        "note": "**No declared FX conversion fee anywhere.** paypal_fee and "
        "paypal_fee_in_receivable_currency are declared on a capture but neither "
        "is declared AS the conversion fee, and the payouts conversion structure "
        "declares no fee at all. Treating one as an FX fee is the substitution "
        "mission.md P3 forbids.",
    },
    {
        "entity": "fx_conversion",
        "field": "provider",
        "sources": [],
        "connector_metadata": True,
        "note": "Not a PayPal field and never will be. Set by the ingestion "
        "connector, which knows which provider it called. An absent verdict here "
        "is not a product limitation.",
    },
    {
        "entity": "fx_conversion",
        "field": "is_derived",
        "sources": [],
        "connector_metadata": True,
        "note": "Our own provenance flag (tech-stack.md section 2). Absent from "
        "PayPal by definition; it exists to mark the fields above that we had to "
        "derive.",
    },
]

# What a declared-but-not-observed field's absence means. Keyed on the exact
# path; the grouped rules below cover the rest.
GAP_MEANING: dict[str, str] = {
    "$.seller_receivable_breakdown.paypal_fee": "The capture fee. See the capture.paypal_fee row - the one place our evidence is weaker than the format suggests.",
    "$.seller_receivable_breakdown.paypal_fee_in_receivable_currency": "The capture fee in settlement currency. Would have been the closest thing to an FX-side fee; not populated, and not declared as a conversion fee in any case.",
    "$.seller_receivable_breakdown.platform_fees": "Marketplace commission on a capture. Its absence means platform fees cannot be read from the capture endpoint; the order view carries them on refunds.",
    "$.seller_payable_breakdown.net_amount_breakdown": "**A conversion record on refunds, and an array of them.** Unanswered rather than absent: our refund was same-currency. A refund against the EUR capture would answer it, and Phase 3 must not assume one conversion per record.",
    "$.seller_payable_breakdown.net_amount_in_receivable_currency": "The refund's net in settlement currency. Same cause as above - no conversion occurred.",
    "$.seller_payable_breakdown.paypal_fee_in_receivable_currency": "Refund fee in settlement currency. Same cause.",
    "$.seller_payable_breakdown.platform_fees": "Marketplace commission on a refund. Present through the order view for the same record, so this is a read-path artefact, not an API absence.",
    "$.items[].currency_conversion": "**A conversion record on payout items**, with exchange_rate, from_amount and to_amount. Unanswered: both batches were USD to USD. This is a third place FX evidence can come from and Phase 11 should know it exists.",
    "$.total_items": "Declared by PayPal's own OpenAPI document on a payout batch and not returned by the API. The published spec overstates the payload; do not rely on it for pagination.",
    "$.total_pages": "As total_items. Payout item pagination cannot be driven off these fields.",
    "$.items[].errors.debug_id": "PayPal's support correlation id on a failed item. Its absence means a failure cannot be quoted to PayPal support by id.",
    "$.items[].payout_item.recipient_name": "Recipient name. Not populated for an EMAIL-type recipient; identity is the email alone.",
    "$.transaction_details[].transaction_info.fee_amount": "**The fee Transaction Search was hoped to supply.** Not merely unpopulated - the key is absent from the indexed record entirely, even with fields=all. So the capture-fee question cannot be answered from reporting either.",
    "$.payer": "Buyer identity on an order. Unanswered, not absent: both fixtures were card-funded and a card-funded order carries no payer. Reconciling a marketplace to a PayPal buyer account is impossible for card transactions.",
    "$.purchase_units[].items": "Line items. Their absence means basket-level reconciliation is not available from PayPal and must come from the marketplace side.",
    "$.purchase_units[].payments.authorizations": "Authorizations. Our fixtures captured directly, so the authorize-then-capture lifecycle is unobserved.",
    "$.balances[].primary": "Which balance is primary. With one currency on the account there is nothing to disambiguate; it matters for a multi-currency account.",
    "$.supplementary_data.related_ids.authorization_id": "Capture to authorization linkage. Unobserved because our captures had no separate authorization step.",
    "$.supplementary_data.related_ids.capture_id": "Present only on records other than the capture itself.",
    "$.status_details": "The reason behind a non-terminal status. Absent because every fixture reached a terminal state; it will matter for the PENDING and delayed lifecycle states of mission.md P4.",
    "$.seller_protection.dispute_categories": "Which dispute categories the capture is protected against. Unobserved, and seller_protection was NOT_ELIGIBLE on both fixtures.",
    "$.network_transaction_reference.date": "Card network timestamp. Not a settlement timestamp and not a substitute for an FX conversion time.",
    "$.acquirer_reference_number": "Acquirer reference on a refund. Would help trace a refund at the card network; not populated.",
}

GAP_RULES: list[tuple[str, str]] = [
    (
        "$.payment_source.",
        "An alternative funding instrument. Unobserved because both fixtures were "
        "card-funded; one row per instrument PayPal declares, none of them "
        "exercised. Not an API absence.",
    ),
    (
        ".payer_info.",
        "Buyer detail on a reporting record. Unobserved on the single indexed "
        "transaction, which is the account's own opening balance credit and has no "
        "buyer.",
    ),
    (
        ".transaction_info.",
        "An optional reporting amount or reference. Unobserved on the one indexed "
        "record; Transaction Search had not caught up with any fixture.",
    ),
    (
        "invoice_id",
        "A caller-supplied external reference. We did not set one, so its absence "
        "says nothing about PayPal. The canonical model may rely on it only if the "
        "marketplace sets it at capture time.",
    ),
    (
        "custom_id",
        "A caller-supplied external reference, as invoice_id. This is the intended "
        "place to carry a marketplace order id into PayPal and have it come back.",
    ),
]

GAP_FALLBACK = (
    "Declared optional and not populated for our fixtures. The canonical model "
    "must not depend on it without a fixture that exercises it."
)

UNDECLARED_MEANING: dict[str, str] = {
    "$.purchase_units[].supplementary_data.risk.risk_level_indicator": "PayPal returned HIGH_RISK on both orders. The SDK declares the enclosing risk object but models only customer.ip_address, so a generated deserializer would drop this silently. Risk level is reconciliation-relevant and would have been invisible.",
    "$.items[].links[].encType": "Undeclared in both source grades, yet PayPal sends it on every HATEOAS link these APIs return.",
    "$.links[].encType": "Undeclared in both source grades, yet PayPal sends it on every HATEOAS link these APIs return.",
}


def state_of(observed_rt: dict[str, Any] | None, path: str) -> dict[str, Any]:
    """What observed.json says about one path."""
    if observed_rt is None:
        return {"known": False}
    field = observed_rt["fields"].get(path)
    samples = observed_rt.get("samples", 0)
    if field is None:
        return {"known": True, "populated": 0, "of": samples, "missing": True}
    return {
        "known": True,
        "populated": field["populated_on"],
        "of": field["of"],
        "labels": field.get("populated_on_labels", []),
        "empty": field.get("present_empty_on", 0),
        "missing": False,
    }


def rank(verdict: str) -> int:
    return {PRESENT: 3, UNRELIABLE: 2, UNANSWERED: 1, ABSENT: 0}[verdict]


def judge_source(
    observed: dict[str, Any],
    record_type: str,
    path: str,
    gate: str | None,
    unobserved_reason: str | None,
) -> dict[str, Any]:
    """Verdict for one candidate path, from observed.json alone."""
    rt = observed["record_types"].get(record_type)
    primary = state_of(rt, path)
    if not primary["known"]:
        return {
            "verdict": UNANSWERED,
            "cell": "no sample",
            "condition": f"record type {record_type} was not probed",
        }

    populated, total = primary["populated"], primary["of"]
    labels = list(primary.get("labels") or [])

    if gate:
        gate_state = state_of(rt, gate)
        # The field only means what the canonical model wants where the gate is
        # populated, so the evidence is the narrower of the two.
        if gate_state["populated"] < populated:
            populated = gate_state["populated"]
            labels = list(gate_state.get("labels") or [])

    cell = f"{populated}/{total}"
    if total == 0:
        return {
            "verdict": UNANSWERED,
            "cell": "no sample",
            "condition": unobserved_reason or "no record of this type was sampled",
        }
    gated = bool(gate) and populated != primary["populated"]
    if gated:
        # Make it visible that this count is not a raw lookup, so the cell
        # stays traceable: it is min(field, gate) and the gate is named.
        cell = f"{cell} gated"
    if populated == total:
        return {"verdict": PRESENT, "cell": cell, "condition": ""}
    if populated > 0:
        return {
            "verdict": UNRELIABLE,
            "cell": cell,
            "condition": "only on " + ", ".join(labels),
        }
    if unobserved_reason:
        return {"verdict": UNANSWERED, "cell": cell, "condition": unobserved_reason}
    return {"verdict": ABSENT, "cell": cell, "condition": ""}


def declared_cell(declared: dict[str, Any], record_type: str, path: str) -> str:
    rt = declared["record_types"].get(record_type)
    if rt is None:
        return "not declared"
    field = rt["fields"].get(path)
    if field is None:
        return "**not declared**"
    bits = ["Required" if field.get("required") else "Optional"]
    if field.get("read_only"):
        bits.append("read-only")
    grade = "SDK" if field.get("source_grade") == "sdk_generated_contract" else "OpenAPI"
    return f"{', '.join(bits)} ({grade})"


def gap_meaning(path: str) -> str:
    if path in GAP_MEANING:
        return GAP_MEANING[path]
    for needle, text in GAP_RULES:
        if needle in path:
            return text
    return GAP_FALLBACK


def parent_of(path: str) -> str | None:
    if path.endswith("[]"):
        return path[:-2]
    return path.rsplit(".", 1)[0] if "." in path else None


def md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def build(
    declared: dict[str, Any], observed: dict[str, Any]
) -> tuple[str, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    for item in CANONICAL:
        best: dict[str, Any] | None = None
        for source in item["sources"]:
            record_type, path = source[0], source[1]
            gate = source[2] if len(source) > 2 else None
            judged = judge_source(
                observed, record_type, path, gate, item.get("unobserved_reason")
            )
            judged.update({"record_type": record_type, "path": path, "gate": gate})
            if best is None or rank(judged["verdict"]) > rank(best["verdict"]):
                best = judged

        if best is None:
            # No PayPal path at all: nothing in the contract can carry it.
            best = {
                "verdict": ABSENT,
                "cell": "no path",
                "condition": "",
                "record_type": "-",
                "path": "-",
                "gate": None,
            }
        rows.append({**item, **best})

    out: list[str] = []
    add = out.append

    add("# PayPal field availability")
    add("")
    add(
        "What PayPal **actually returns** for the record types the canonical "
        "financial model is built on, against what its contract **declares**."
    )
    add("")
    add(
        "Phase 0 output. Scope and decisions: "
        "`specs/2026-11-05-paypal-field-discovery/requirements.md` - findings: "
        "`specs/2026-11-05-paypal-field-discovery/notes.md` - merge gate: "
        "`specs/2026-11-05-paypal-field-discovery/validation.md`"
    )
    add("")
    add(
        "**Generated, not written.** `uv run scripts/build_fields_doc.py` builds "
        "this file from `artifacts/declared.json` and `artifacts/observed.json`. "
        "Every verdict is computed from the observed artifact; none can be typed "
        "in by hand. Edit the mapping in the script, not this file."
    )
    add("")
    add(f"Generated {datetime.now(timezone.utc).date().isoformat()} from:")
    add("")
    provenance = declared["declared"]["provenance"]
    sdk = provenance["sdk_generated_contract"]
    api = provenance["openapi_specification"]
    add(
        f"- **Declared** - PayPal Server SDK `{sdk['tag']}` "
        f"(commit `{sdk['commit'][:12]}`), and PayPal's published OpenAPI 3.0.3 "
        f"documents (commit `{api['commit'][:12]}`) for disputes and payouts, "
        "which the SDK does not model."
    )
    add(
        f"- **Observed** - live PayPal sandbox responses, "
        f"`{observed['observed']['api_base']}`, against the fixtures in "
        "`fixtures.md`."
    )
    add("")
    add("---")
    add("")

    # ------------------------------------------------------------ how to read
    add("## How to read this")
    add("")
    add(
        "**The two columns are independent on purpose, and the gap between them "
        "is the product of this phase.** A field that is declared but never "
        "observed is a field the canonical model would otherwise have been built "
        "on, and which would arrive empty in Phase 7 - after Phases 2 and 3 had "
        "already committed to it."
    )
    add("")
    add("| Column | Means |")
    add("|---|---|")
    add(
        "| **Declared** | The field exists in PayPal's contract, with its "
        "optionality and read-only status. `SDK` is the generated contract at a "
        "pinned tag; `OpenAPI` is PayPal's own published document at a pinned "
        "commit. **Context, never evidence.** |"
    )
    add(
        "| **Observed** | Sampled records in which the field held a non-empty "
        "value, as `populated/sampled`. This is the only admissible basis for a "
        "verdict (`requirements.md` D1). |"
    )
    add("")
    add(
        "A cell marked **gated** is not a raw lookup. A few canonical fields are "
        "only meaningful when a second field is also present - a capture's "
        "`gross_amount` is always there, but it is the *source amount of a "
        "conversion* only where a conversion happened. Those rows name the gate "
        "and report the narrower of the two counts, so the table cannot claim "
        "conversion evidence we do not have. Every gated cell is reproducible as "
        "`min(field, gate)` from `observed.json`."
    )
    add("")
    add("### Verdicts")
    add("")
    add("| Verdict | Means |")
    add("|---|---|")
    add("| `present` | Populated on every sampled record. |")
    add(
        "| `present-but-unreliable` | Populated on some records and not others, "
        "**with the condition stated**. A field present only on cross-currency "
        "transactions is useful information, not a failure (`requirements.md` D5). |"
    )
    add(
        "| `absent` | A record that should have carried the field did not. This "
        "is a claim about PayPal. |"
    )
    add(
        "| `unanswered` | No fixture exercised the condition under which the "
        "field could appear. This is a statement about **our fixtures**, not "
        "about PayPal, and the reason is always given. |"
    )
    add("")
    add(
        "**`absent` and `unanswered` are different facts and must not be "
        "conflated.** `validation.md` section 5 says so for the FX row, and "
        "`notes.md` records how readily a missing permission or a missing fixture "
        "masquerades as a finding about the API. `requirements.md` D6 originally "
        "allowed three verdicts; the fourth was added when two fixtures proved "
        "uncreatable, and it is constrained to cases where the unexercised "
        "condition can be named. It is not a hedge: every `unanswered` row says "
        "what would answer it."
    )
    add("")

    # ---------------------------------------------------------------- summary
    counts = {verdict: 0 for verdict in VERDICTS}
    for row in rows:
        counts[row["verdict"]] += 1
    add("### Summary")
    add("")
    add("| Verdict | Canonical fields |")
    add("|---|---|")
    for verdict in VERDICTS:
        add(f"| `{verdict}` | {counts[verdict]} |")
    add(f"| **total** | **{len(rows)}** |")
    add("")
    add(
        "No blank verdicts, and no canonical field without a row "
        "(`validation.md` C4, C5)."
    )
    add("")
    add("---")
    add("")

    # ----------------------------------------------------------- the tables
    entities = []
    for row in rows:
        if row["entity"] not in entities:
            entities.append(row["entity"])

    titles = {
        "payment": "Payment",
        "capture": "Capture",
        "refund": "Refund",
        "dispute": "Dispute",
        "payout_batch": "Payout batch",
        "payout_item": "Payout item",
        "fx_conversion": "FX conversion",
    }
    leads = {
        "payment": "The buyer-facing transaction. Read from "
        "`GET /v2/checkout/orders/{id}`.",
        "capture": "The settlement-side record, and where PayPal puts its "
        "conversion evidence. Read from `GET /v2/payments/captures/{id}`.",
        "refund": "Read from `GET /v2/payments/refunds/{id}`, except where the "
        "order view carries more - see `platform_fee`.",
        "dispute": "**Every row here is `unanswered`.** `POST /v1/customer/disputes` "
        "accepts creation but rejects a card-funded capture with "
        "`TRANSACTION_NOT_ELIGIBLE`, and both fixtures were card-funded, so no "
        "dispute record exists to observe. The declared column is complete and "
        "comes from PayPal's own OpenAPI document, so the shape is known even "
        "though the behaviour is not. **None of these rows may be read as "
        "`absent`** - that would be a claim about PayPal that our evidence does "
        "not support.",
        "payout_batch": "Read from `GET /v1/payments/payouts/{id}`. Two batches "
        "sampled, one `SUCCESS` and one `DENIED`, which is what made the fee "
        "findings possible.",
        "payout_item": "Four items across the two batches, two successful and "
        "two failed.",
        "fx_conversion": "**The reason this phase ran first.** `mission.md` P3 "
        "forbids substituting an estimate for a provider record, so the model "
        "may only carry what PayPal actually supplies. Rows are gated on a real "
        "conversion existing: an amount that was never converted is not "
        "conversion evidence.\n\n"
        "On the count: `validation.md` C4 asks for \"all eight `fx_conversion` "
        "fields\" while `requirements.md` section 7 enumerates nine items. The "
        "nine are listed here. The eight are the PayPal-sourced ones; "
        "`is_derived` is our own provenance flag and was never going to come "
        "from a provider. Covering nine satisfies the check either way.",
    }

    for index, entity in enumerate(entities, start=1):
        add(f"## {index}. {titles.get(entity, entity)}")
        add("")
        add(leads.get(entity, ""))
        add("")
        add(
            "| Canonical field | PayPal path | Declared | Observed | Verdict | "
            "Condition / note |"
        )
        add("|---|---|---|---|---|---|")
        for row in (r for r in rows if r["entity"] == entity):
            if row["path"] == "-":
                path_cell = "none"
                declared_text = "**not declared**"
            else:
                path_cell = f"`{row['record_type']}` `{md_escape(row['path'])}`"
                if row.get("gate"):
                    path_cell += f"<br>gated on `{md_escape(row['gate'])}`"
                declared_text = declared_cell(
                    declared, row["record_type"], row["path"]
                )
            note = md_escape(row.get("note", "") or "")
            condition = md_escape(row.get("condition", "") or "")
            if condition and note:
                detail = f"**{condition}.** {note}"
            elif condition:
                detail = f"**{condition}.**"
            else:
                detail = note or "-"
            add(
                f"| `{row['field']}` | {path_cell} | {declared_text} | "
                f"{row['cell']} | `{row['verdict']}` | {detail} |"
            )
        add("")

    add("---")
    add("")

    # ------------------------------------------------- declared not observed
    add("## Declared but not observed")
    add("")
    add(
        "The highest-value output of this phase (`plan.md` Group 5 step 4): "
        "fields PayPal's contract promises that our sandbox never delivered. "
        "Each gets a line on what its absence means for the canonical model."
    )
    add("")
    declared_total = sum(
        len(rt["fields"]) for rt in declared["record_types"].values()
    )
    observed_total = sum(
        len(rt["fields"]) for rt in observed["record_types"].values()
    )
    add(
        f"**Scoped deliberately.** The contract declares {declared_total} paths "
        f"and we observed {observed_total}, so the raw difference runs to "
        "hundreds - most of it alternative payment methods and optional "
        "reporting blocks nested under objects that never appeared at all. "
        "Listing those would bury the findings. This section lists the fields "
        "whose **parent object was observed**: PayPal returned the enclosing "
        "structure and left this member out. That is the set where an absence is "
        "informative. The full difference is derivable from the two artifacts at "
        "any time."
    )
    add("")

    total_gap = 0
    total_near = 0
    sections: list[tuple[str, list[str]]] = []
    for record_type in sorted(observed["record_types"]):
        declared_rt = declared["record_types"].get(record_type)
        if declared_rt is None:
            continue
        observed_fields = observed["record_types"][record_type]["fields"]
        populated = {k for k, v in observed_fields.items() if v["populated_on"]}
        gap = sorted(set(declared_rt["fields"]) - set(observed_fields))
        total_gap += len(gap)
        near = [p for p in gap if parent_of(p) in populated]
        total_near += len(near)
        if near:
            sections.append((record_type, near))

    add(
        f"Fields whose parent object was observed: **{total_near}**, from a raw "
        f"difference of {total_gap}."
    )
    add("")

    for record_type, near in sections:
        declared_rt = declared["record_types"][record_type]
        add(f"### {record_type}")
        add("")
        add("| Declared path | Declared | What its absence means |")
        add("|---|---|---|")
        for path in near:
            field = declared_rt["fields"][path]
            bits = ["Required" if field.get("required") else "Optional"]
            if field.get("read_only"):
                bits.append("read-only")
            add(
                f"| `{md_escape(path)}` | {', '.join(bits)} | "
                f"{md_escape(gap_meaning(path))} |"
            )
        add("")

    add("---")
    add("")

    # ------------------------------------------------- observed not declared
    add("## Observed but not declared")
    add("")
    add(
        "The reverse gap, and the direction nobody plans for: fields PayPal "
        "returned that **neither** of its own published contracts declares. "
        "`validation.md` has no check for this because nothing anticipated it."
    )
    add("")
    add("| Record type | Observed path | Observed | What it means |")
    add("|---|---|---|---|")
    found_undeclared = False
    for record_type in sorted(observed["record_types"]):
        declared_rt = declared["record_types"].get(record_type)
        if declared_rt is None:
            continue
        observed_fields = observed["record_types"][record_type]["fields"]
        for path in sorted(set(observed_fields) - set(declared_rt["fields"])):
            entry = observed_fields[path]
            if not entry["populated_on"]:
                continue
            found_undeclared = True
            add(
                f"| `{record_type}` | `{md_escape(path)}` | "
                f"{entry['populated_on']}/{entry['of']} | "
                f"{md_escape(UNDECLARED_MEANING.get(path, GAP_FALLBACK))} |"
            )
    if not found_undeclared:
        add("| - | - | - | The set is empty. |")
    add("")
    add(
        "This is the concrete argument against putting a generated deserializer "
        "between the wire and the evidence, which `plan.md` Group 1 step 6 "
        "forbids: APIMatic drops unknown keys, so all of these would have been "
        "invisible and `observed.json` quietly wrong. It is also the evidence "
        "Phase 7 should weigh when it chooses whether to use the SDK at all."
    )
    add("")
    add("---")
    add("")

    # ----------------------------------------------------------- limitations
    add("## What this table cannot say")
    add("")
    add(
        "Stated so a reader does not take a verdict further than the evidence "
        "carries it."
    )
    add("")
    add(
        "1. **Sandbox, not production.** Every observation is from "
        "`api-m.sandbox.paypal.com`. The `capture.paypal_fee` row is the one "
        "where this most likely matters: no fee was assessed on our captures, "
        "and a production transaction may well populate it."
    )
    add(
        "2. **Small samples.** Two captures, one refund, two payout batches, "
        "four payout items. A `present` verdict on 2 of 2 is not a guarantee of "
        "universality; it is the strongest statement the fixtures support."
    )
    add(
        "3. **No disputes at all.** Ten canonical dispute fields rest entirely "
        "on the declared column."
    )
    add(
        "4. **Two conversion sites unobserved.** Refunds and payout items both "
        "declare conversion records that our same-currency fixtures could not "
        "exercise. Phase 11 should not assume the capture site is the only one."
    )
    add(
        "5. **Reporting endpoints were stale.** Transaction Search and balances "
        "shared one snapshot older than every fixture, so their rows describe an "
        "account with no activity. Treat them as eventually consistent and the "
        "v2 resource endpoints as authoritative."
    )
    add("")
    add(
        "Per `validation.md` section 5, none of this blocks merge. A clearly "
        "recorded absence is a successful outcome - it is the finding the phase "
        "was run to obtain."
    )
    add("")
    add("---")
    add("")
    add("## Related")
    add("")
    add("- `specs/mission.md` - P1 and P3 govern what may be claimed here")
    add("- `specs/tech-stack.md` - section 2 money rules, section 3 the model")
    add("- `specs/roadmap.md` - Phase 3 consumes this, Phase 11 depends on the FX rows")
    add("- `specs/2026-11-05-paypal-field-discovery/` - the phase directory")
    add("")

    return "\n".join(out) + "\n", counts


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="build_fields_doc.py",
        description=(
            "Generate specs/paypal-fields.md from declared.json and "
            "observed.json. Verdicts are computed from the observed artifact."
        ),
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        metavar="DIR",
        help="Directory holding declared.json and observed.json.",
    )
    parser.add_argument(
        "--out", type=Path, required=True, metavar="PATH", help="Where the table goes."
    )
    args = parser.parse_args()

    declared_path = args.artifacts / "declared.json"
    observed_path = args.artifacts / "observed.json"
    for path in (declared_path, observed_path):
        if not path.exists():
            sys.exit(f"error: missing artifact {path}")

    declared = json.loads(declared_path.read_text(encoding="utf-8"))
    observed = json.loads(observed_path.read_text(encoding="utf-8"))

    text, counts = build(declared, observed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")

    print(f"wrote {args.out}")
    print("  verdicts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
