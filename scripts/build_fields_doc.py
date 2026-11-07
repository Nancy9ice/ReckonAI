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
NO_PLATFORM_CAPABILITY = (
    "a platform fee is caller-supplied at capture time via "
    "`payment_instruction.platform_fees`, and the contract restricts it to "
    "merchants enabled for PayPal Complete Payments Platform for Marketplaces. "
    "This account has neither, so no platform fee could have been charged"
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
    {"entity": "payment", "field": "payment_id", "sources": [("orders", "$.id")],
     "note": "PayPal's order id. The canonical payment key."},
    {"entity": "payment", "field": "amount",
     "sources": [("orders", "$.purchase_units[].amount.value")]},
    {"entity": "payment", "field": "currency",
     "sources": [("orders", "$.purchase_units[].amount.currency_code")],
     "note": "Buyer-facing currency, not necessarily the settlement currency."},
    {"entity": "payment", "field": "status", "sources": [("orders", "$.status")]},
    {"entity": "payment", "field": "intent", "sources": [("orders", "$.intent")]},
    {"entity": "payment", "field": "created_at", "sources": [("orders", "$.create_time")]},
    {"entity": "payment", "field": "payer_identity",
     "sources": [("orders", "$.payer.payer_id")],
     "note": "**Present under multiparty, and it was not before.** Phase 0's "
     "card-funded orders carried no payer at all. A wallet-funded order carries "
     "`address`, `email_address`, `name` and `payer_id`."},
    {"entity": "payment", "field": "buyer_verification",
     "sources": [("orders", "$.payment_source.paypal.account_status")],
     "note": "`VERIFIED` observed. A buyer trust signal with no equivalent on a "
     "card payment."},
    {"entity": "payment", "field": "seller_identity",
     "sources": [("orders", "$.purchase_units[].payee.merchant_id")],
     "note": "**The join the whole product rests on.** Under multiparty the "
     "payee is the seller, not the marketplace."},
    {"entity": "payment", "field": "disbursement_mode",
     "sources": [("orders", "$.purchase_units[].payment_instruction.disbursement_mode")],
     "note": "`INSTANT` or `DELAYED`. Decides whether the seller is paid at "
     "capture or on release."},
    # ---------------------------------------------------------------- capture
    {"entity": "capture", "field": "capture_id", "sources": [("captures", "$.id")]},
    {"entity": "capture", "field": "payment_linkage",
     "sources": [("captures", "$.supplementary_data.related_ids.order_id")],
     "note": "A real field, not a link to parse."},
    {"entity": "capture", "field": "amount", "sources": [("captures", "$.amount.value")]},
    {"entity": "capture", "field": "currency",
     "sources": [("captures", "$.amount.currency_code")]},
    {"entity": "capture", "field": "status", "sources": [("captures", "$.status")],
     "note": "**Mutable, and it does not mean the seller was paid.** A `DELAYED` "
     "capture reads `COMPLETED` while funds are held, and a disputed capture "
     "reverts to `PENDING`."},
    {"entity": "capture", "field": "disbursement_mode",
     "sources": [("captures", "$.disbursement_mode")],
     "note": "Echoed onto the capture, so the mode is recoverable without the order."},
    {"entity": "capture", "field": "seller_identity",
     "sources": [("captures", "$.payee.merchant_id")]},
    {"entity": "capture", "field": "gross_amount",
     "sources": [("captures", "$.seller_receivable_breakdown.gross_amount.value")],
     "note": "Pre-fee, pre-conversion."},
    {"entity": "capture", "field": "net_amount",
     "sources": [("captures", "$.seller_receivable_breakdown.net_amount.value")],
     "note": "`gross - paypal_fee - platform_fees`, observed exactly on every "
     "capture carrying a breakdown."},
    {"entity": "capture", "field": "paypal_fee",
     "sources": [("captures", "$.seller_receivable_breakdown.paypal_fee.value")],
     "note": "**Phase 0 recorded this `absent`; under multiparty it is there.** "
     "3.98 on 100.00, identical across card-in-wallet and balance funding."},
    {"entity": "capture", "field": "settlement_amount",
     "sources": [("captures", "$.seller_receivable_breakdown.receivable_amount.value")],
     "note": "Only where a conversion occurred. **Converts the net, not the "
     "gross** - see fx_conversion.source_amount."},
    {"entity": "capture", "field": "settlement_currency",
     "sources": [
         ("captures", "$.seller_receivable_breakdown.receivable_amount.currency_code"),
         ("balances", "$.balances[].currency"),
     ],
     "note": "The balances endpoint answers this unconditionally, which beats "
     "inferring it per capture."},
    {"entity": "capture", "field": "created_at", "sources": [("captures", "$.create_time")]},
    # ----------------------------------------------------------- platform fee
    {"entity": "platform_fee", "field": "amount",
     "sources": [("captures", "$.seller_receivable_breakdown.platform_fees[].amount.value")],
     "note": "The marketplace's commission, as PayPal actually took it. "
     "Independent evidence against the marketplace's own rules."},
    {"entity": "platform_fee", "field": "currency",
     "sources": [("captures",
                  "$.seller_receivable_breakdown.platform_fees[].amount.currency_code")],
     "note": "Must match the transaction currency; PayPal rejects a mismatch."},
    {"entity": "platform_fee", "field": "payee",
     "sources": [("captures",
                  "$.seller_receivable_breakdown.platform_fees[].payee.merchant_id")],
     "note": "Where the commission was routed. Defaults to the API caller when "
     "the order does not name one, and PayPal fills it in explicitly."},
    {"entity": "platform_fee", "field": "reversal_on_refund",
     "sources": [("refunds", "$.seller_payable_breakdown.platform_fees[].amount.value")],
     "note": "**Reversed automatically and pro rata.** A 30.00 refund of a "
     "100.00 capture returned 3.00 of the 10.00 commission, with no instruction "
     "sent. Overriding the amount needs an account enablement we do not have "
     "(`PLATFORM_FEE_NOT_ENABLED`)."},
    {"entity": "platform_fee", "field": "settlement_reference", "sources": [],
     "note": "Partner fees settle as a next-day consolidated bank payment. No "
     "per-transaction settlement reference is exposed, and reconciling the "
     "aggregate is deferred (`mission.md` section 7)."},
    # ----------------------------------------------------------------- refund
    {"entity": "refund", "field": "refund_id", "sources": [("refunds", "$.id")]},
    {"entity": "refund", "field": "capture_linkage",
     "sources": [("refunds", "$.links[].href")],
     "note": "**Derived, not provider-supplied.** No capture id on a refund; the "
     "only link back is `links[rel=up]`, whose href must be parsed."},
    {"entity": "refund", "field": "amount", "sources": [("refunds", "$.amount.value")]},
    {"entity": "refund", "field": "currency",
     "sources": [("refunds", "$.amount.currency_code")]},
    {"entity": "refund", "field": "gross_amount",
     "sources": [("refunds", "$.seller_payable_breakdown.gross_amount.value")],
     "note": "A refund carries `seller_payable_breakdown`, a capture "
     "`seller_receivable_breakdown`. Mapping code cannot share a path."},
    {"entity": "refund", "field": "net_amount",
     "sources": [("refunds", "$.seller_payable_breakdown.net_amount.value")],
     "note": "The same identity as a capture: 30.00 - 0.00 - 3.00 = 27.00."},
    {"entity": "refund", "field": "paypal_fee",
     "sources": [("refunds", "$.seller_payable_breakdown.paypal_fee.value")],
     "note": "`0.00` observed: PayPal did not return its own fee on the refund."},
    {"entity": "refund", "field": "total_refunded_amount",
     "sources": [("refunds", "$.seller_payable_breakdown.total_refunded_amount.value")],
     "note": "What makes partial-refund tracking possible."},
    {"entity": "refund", "field": "status", "sources": [("refunds", "$.status")]},
    {"entity": "refund", "field": "created_at", "sources": [("refunds", "$.create_time")]},
    # ---------------------------------------------------------------- dispute
    {"entity": "dispute", "field": "dispute_id",
     "sources": [("disputes", "$.items[].dispute_id")]},
    {"entity": "dispute", "field": "status",
     "sources": [("disputes", "$.items[].status")],
     "note": "`WAITING_FOR_SELLER_RESPONSE` observed."},
    {"entity": "dispute", "field": "lifecycle_stage",
     "sources": [("disputes", "$.items[].dispute_life_cycle_stage")],
     "note": "`INQUIRY` observed - the pre-claim stage."},
    {"entity": "dispute", "field": "dispute_state",
     "sources": [("disputes", "$.items[].dispute_state")],
     "note": "`REQUIRED_ACTION`. Note the list endpoint filters on this: a "
     "default query returns the dispute, but `dispute_state=OPEN_INQUIRIES` "
     "does not."},
    {"entity": "dispute", "field": "amount",
     "sources": [("disputes", "$.items[].dispute_amount.value")]},
    {"entity": "dispute", "field": "currency",
     "sources": [("disputes", "$.items[].dispute_amount.currency_code")]},
    {"entity": "dispute", "field": "reason",
     "sources": [("disputes", "$.items[].reason")],
     "note": "`MERCHANDISE_OR_SERVICE_NOT_RECEIVED`."},
    {"entity": "dispute", "field": "channel",
     "sources": [("disputes", "$.items[].dispute_channel")],
     "note": "`INTERNAL` - raised through PayPal rather than as a card chargeback."},
    {"entity": "dispute", "field": "created_at",
     "sources": [("disputes", "$.items[].create_time")]},
    {"entity": "dispute", "field": "updated_at",
     "sources": [("disputes", "$.items[].update_time")]},
    {"entity": "dispute", "field": "seller_response_due_date",
     "sources": [("disputes", "$.items[].seller_response_due_date")],
     "note": "20 days out. Drives the awaiting-evidence lifecycle state of "
     "`mission.md` P4."},
    {"entity": "dispute", "field": "buyer_identity",
     "sources": [("disputes", "$.items[].disputed_transactions[].buyer.payer_id")]},
    {"entity": "dispute", "field": "seller_identity",
     "sources": [("disputes", "$.items[].disputed_transactions[].seller.merchant_id")]},
    {"entity": "dispute", "field": "capture_linkage",
     "sources": [("disputes", "$.items[].disputed_transactions[].buyer_transaction_id")],
     "note": "**Not our capture id.** The buyer's side of the transaction has "
     "its own identifier, so matching a dispute to a capture needs a join we "
     "have not established. Filtering by `disputed_transaction_id` does accept "
     "the capture id."},
    # ----------------------------------------------------------- disbursement
    {"entity": "disbursement", "field": "disbursement_id", "sources": [],
     "unobserved_reason": "the referenced-payouts endpoints answer 403 for this "
     "app, so no release could be made or read",
     "note": "Under `DELAYED`, release is "
     "`POST /v1/payments/referenced-payouts-items`."},
    {"entity": "disbursement", "field": "status", "sources": [],
     "unobserved_reason": "no release could be made; referenced payouts answer 403"},
    {"entity": "disbursement", "field": "released_at", "sources": [],
     "unobserved_reason": "no release could be made; referenced payouts answer 403",
     "note": "PayPal auto-releases held funds after 28 days with no instruction, "
     "so absence of a release does not mean funds are stuck."},
    {"entity": "disbursement", "field": "capture_linkage", "sources": [],
     "unobserved_reason": "no release could be made; referenced payouts answer 403",
     "note": "Declared as `reference_id` on the payout item, with "
     "`reference_type: TRANSACTION_ID`."},
    # --------------------------------------------------------- fx_conversion
    {"entity": "fx_conversion", "field": "source_amount",
     "sources": [("captures", "$.seller_receivable_breakdown.net_amount.value",
                  "$.seller_receivable_breakdown.exchange_rate.value")],
     "note": "**The net, not the gross.** PayPal deducts fees in the transaction "
     "currency and converts the remainder: 82.23 EUR net * rate = the reported "
     "89.59 USD. Converting the gross instead is wrong by the whole fee - 14.46 "
     "USD on this fixture."},
    {"entity": "fx_conversion", "field": "source_currency",
     "sources": [("captures",
                  "$.seller_receivable_breakdown.exchange_rate.source_currency")]},
    {"entity": "fx_conversion", "field": "target_amount",
     "sources": [("captures", "$.seller_receivable_breakdown.receivable_amount.value")],
     "note": "Rounded to the cent while the rate is not. Compare against this "
     "figure, never a recomputation."},
    {"entity": "fx_conversion", "field": "target_currency",
     "sources": [("captures",
                  "$.seller_receivable_breakdown.exchange_rate.target_currency")]},
    {"entity": "fx_conversion", "field": "rate",
     "sources": [("captures", "$.seller_receivable_breakdown.exchange_rate.value")],
     "note": "15 decimal places. Parse from the string into `Decimal` and store "
     "whole."},
    {"entity": "fx_conversion", "field": "timestamp", "sources": [],
     "note": "**No declared source on any conversion structure.** Read the "
     "reporting ledger's `T0200` event, which lags about a day, or derive from "
     "the capture time and flag `is_derived`."},
    {"entity": "fx_conversion", "field": "fee", "sources": [],
     "note": "**No conversion fee anywhere in the transaction APIs.** The "
     "residue of net times rate minus the reported settlement is rounding, not "
     "a fee."},
    {"entity": "fx_conversion", "field": "provider", "sources": [],
     "connector_metadata": True,
     "note": "Set by the ingestion connector."},
    {"entity": "fx_conversion", "field": "is_derived", "sources": [],
     "connector_metadata": True,
     "note": "Our own provenance flag."},
]

DECISIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("fx_conversion", "timestamp"): {
        "kind": "derive", "is_derived": True,
        "text": "Prefer the reporting ledger's `T0200` conversion event, which "
        "carries its own `transaction_initiation_date` and is provider evidence. "
        "It lags about a day; where reporting has not caught up, fall back to the "
        "capture's `create_time` with **`is_derived` true**. The field therefore "
        "carries a provider value sometimes and a derived one otherwise, which is "
        "exactly what `is_derived` exists to record.",
    },
    ("fx_conversion", "fee"): {
        "kind": "limitation", "is_derived": False,
        "text": "No conversion fee can be reported. Substituting `paypal_fee` is "
        "the substitution `mission.md` P3 forbids, and the arithmetic residue is "
        "PayPal's rounding. Consequence: readme section 13 Scenario 3 covers an "
        "incorrect conversion **value**, never an incorrect conversion **fee**.",
    },
    ("fx_conversion", "provider"): {
        "kind": "connector_metadata", "is_derived": False,
        "text": "Set by the connector, which knows which provider it called.",
    },
    ("fx_conversion", "is_derived"): {
        "kind": "connector_metadata", "is_derived": False,
        "text": "Our own provenance flag; the mechanism the decisions above rely on.",
    },
    ("platform_fee", "settlement_reference"): {
        "kind": "limitation", "is_derived": False,
        "text": "Partner fees arrive as a single next-day consolidated bank "
        "payment, and no per-transaction settlement reference is exposed. The "
        "per-transaction fee comparison is in scope; reconciling the aggregate is "
        "settlement-account work, deferred by `mission.md` section 7. A "
        "`platform_fee` row should still be able to carry a settlement reference "
        "later.",
    },
}

_CONDITION_IS_CORRECT = {
    "kind": "no_action", "is_derived": False,
    "text": "Populated only where a conversion occurred, which is right: a "
    "same-currency transaction has no conversion and must carry no "
    "`fx_conversion` row at all. Nothing is derived and nothing is lost.",
}
for _field in ("source_amount", "source_currency", "target_amount",
               "target_currency", "rate"):
    DECISIONS[("fx_conversion", _field)] = _CONDITION_IS_CORRECT

_BREAKDOWN_GONE_WHILE_PENDING = {
    "kind": "limitation", "is_derived": False,
    "text": "**A capture loses its entire `seller_receivable_breakdown` while "
    "pending, and a dispute puts it there.** The disputed fixture reverted from "
    "`COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform "
    "fee with it. The SDK model documents this (\"not available for transactions "
    "that are in pending state\") but it had never been observed. So these values "
    "must be captured at ingestion and retained; re-reading later can return "
    "nothing, and the absence is not evidence that no fee was charged.",
}
for _field in ("gross_amount", "net_amount", "paypal_fee", "settlement_amount",
               "settlement_currency"):
    DECISIONS[("capture", _field)] = _BREAKDOWN_GONE_WHILE_PENDING
for _field in ("amount", "currency", "payee"):
    DECISIONS[("platform_fee", _field)] = _BREAKDOWN_GONE_WHILE_PENDING

PENDING: dict[tuple[str, str], dict[str, str]] = {}
_DISBURSEMENT_PENDING = {
    "answered_by": "partner approval and a BN code, which the referenced-payouts "
    "endpoints require; they answer 403 for this app",
    "interim": "Model the disbursement states from the declared contract but "
    "mark nothing required, and do not infer that a seller was paid from a "
    "capture's `COMPLETED` status. Under `DELAYED` that status is reached while "
    "funds are still held.",
}
for _field in ("disbursement_id", "status", "released_at", "capture_linkage"):
    PENDING[("disbursement", _field)] = _DISBURSEMENT_PENDING

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
            # No PayPal path at all. That is `absent` when nothing in the
            # contract can carry the field, but `unanswered` when a reason is
            # recorded for why we could not reach one - the same distinction
            # the verdicts draw everywhere else, and the no-path case has to
            # honour it too or an unreachable endpoint reads as a claim about
            # PayPal.
            reason = item.get("unobserved_reason")
            best = {
                "verdict": UNANSWERED if reason else ABSENT,
                "cell": "no path",
                "condition": reason or "",
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

    # --------------------------------------------------------- decisions (G6)
    needs_decision = [r for r in rows if r["verdict"] in (ABSENT, UNRELIABLE)]
    pending_rows = [r for r in rows if r["verdict"] == UNANSWERED]

    missing = [
        f"{r['entity']}.{r['field']}"
        for r in needs_decision
        if (r["entity"], r["field"]) not in DECISIONS
    ]
    if missing:
        # Fail loudly rather than emit a table with a hole in it. This is the
        # mechanical form of plan.md group 6's verify step.
        sys.exit(
            "error: no decision recorded for: "
            + ", ".join(missing)
            + "\nAdd an entry to DECISIONS in scripts/build_fields_doc.py."
        )
    missing_pending = [
        f"{r['entity']}.{r['field']}"
        for r in pending_rows
        if (r["entity"], r["field"]) not in PENDING
    ]
    if missing_pending:
        sys.exit(
            "error: no interim handling recorded for: " + ", ".join(missing_pending)
        )

    add("## Decisions")
    add("")
    add(
        "Every `absent` and every `present-but-unreliable` field carries one of "
        "four decisions (`plan.md` Group 6, `validation.md` C8). A field left "
        "undecided fails the build of this document, not merely its review."
    )
    add("")
    add("| Kind | Means |")
    add("|---|---|")
    add(
        "| `derive` | We can compute the value. It is written with **`is_derived` "
        "true** and may never be presented as provider evidence "
        "(`tech-stack.md` section 2, `mission.md` P3). |"
    )
    add(
        "| `limitation` | We cannot have the value. The entry states what the "
        "product therefore cannot do. |"
    )
    add(
        "| `no_action` | The conditional availability is correct for the domain. "
        "Nothing is derived and nothing is lost; the entry says why the "
        "condition is right. |"
    )
    add(
        "| `connector_metadata` | Not a PayPal field at all, and in this table "
        "only because `requirements.md` section 7 lists it. |"
    )
    add("")
    kind_counts: dict[str, int] = {}
    for row in needs_decision:
        kind = DECISIONS[(row["entity"], row["field"])]["kind"]
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    add(
        f"{len(needs_decision)} fields need a decision: "
        + ", ".join(f"{count} `{kind}`" for kind, count in sorted(kind_counts.items()))
        + "."
    )
    add("")
    add("| Field | Verdict | Decision | `is_derived` | Detail |")
    add("|---|---|---|---|---|")
    for row in needs_decision:
        decision = DECISIONS[(row["entity"], row["field"])]
        flag = "**yes**" if decision["is_derived"] else "no"
        add(
            f"| `{row['entity']}.{row['field']}` | `{row['verdict']}` | "
            f"`{decision['kind']}` | {flag} | {md_escape(decision['text'])} |"
        )
    add("")
    add(
        "**Two derivations exist, and both are labelled.** "
        "`fx_conversion.timestamp` and the same-currency case of "
        "`capture.settlement_amount` are the only values this phase authorises "
        "computing, and each must carry `is_derived`. Everything else is either "
        "a stated limitation or a condition that is correct as it stands. No "
        "monetary value is derived from an undocumented formula - the "
        "`capture.paypal_fee` row explains why that mattered."
    )
    add("")
    add("---")
    add("")

    # ----------------------------------------------------- pending answers (G6)
    add("## Pending answers")
    add("")
    add(
        f"The {len(pending_rows)} `unanswered` fields get an interim handling "
        "and the fixture that would settle them - **not** a decision. A decision "
        "implies the evidence is in; for these it is not, and recording a "
        "limitation would overstate what we know."
    )
    add("")
    add("| Field | What would answer it | Interim handling |")
    add("|---|---|---|")
    for row in pending_rows:
        entry = PENDING[(row["entity"], row["field"])]
        add(
            f"| `{row['entity']}.{row['field']}` | "
            f"{md_escape(entry['answered_by'])} | "
            f"{md_escape(entry['interim'])} |"
        )
    add("")
    add(
        "Both fixtures need the same thing: **a wallet-funded capture**, which "
        "requires a sandbox personal account to approve an order in a browser. "
        "That one fixture would answer eleven rows, and would also let a "
        "cross-currency refund and payout settle the two unobserved conversion "
        "sites. It is the highest-value follow-up this phase produces and it is "
        "cheap."
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
