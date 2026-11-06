# PayPal field availability

What PayPal **actually returns** for the record types the canonical financial model is built on, against what its contract **declares**.

Phase 0 output. Scope and decisions: `specs/2026-11-05-paypal-field-discovery/requirements.md` - findings: `specs/2026-11-05-paypal-field-discovery/notes.md` - merge gate: `specs/2026-11-05-paypal-field-discovery/validation.md`

**Generated, not written.** `uv run scripts/build_fields_doc.py` builds this file from `artifacts/declared.json` and `artifacts/observed.json`. Every verdict is computed from the observed artifact; none can be typed in by hand. Edit the mapping in the script, not this file.

Generated 2026-11-06 from:

- **Declared** - PayPal Server SDK `2.4.0` (commit `b1460117a561`), and PayPal's published OpenAPI 3.0.3 documents (commit `90e8041ffe02`) for disputes and payouts, which the SDK does not model.
- **Observed** - live PayPal sandbox responses, `https://api-m.sandbox.paypal.com`, against the fixtures in `fixtures.md`.

---

## How to read this

**The two columns are independent on purpose, and the gap between them is the product of this phase.** A field that is declared but never observed is a field the canonical model would otherwise have been built on, and which would arrive empty in Phase 7 - after Phases 2 and 3 had already committed to it.

| Column | Means |
|---|---|
| **Declared** | The field exists in PayPal's contract, with its optionality and read-only status. `SDK` is the generated contract at a pinned tag; `OpenAPI` is PayPal's own published document at a pinned commit. **Context, never evidence.** |
| **Observed** | Sampled records in which the field held a non-empty value, as `populated/sampled`. This is the only admissible basis for a verdict (`requirements.md` D1). |

A cell marked **gated** is not a raw lookup. A few canonical fields are only meaningful when a second field is also present - a capture's `gross_amount` is always there, but it is the *source amount of a conversion* only where a conversion happened. Those rows name the gate and report the narrower of the two counts, so the table cannot claim conversion evidence we do not have. Every gated cell is reproducible as `min(field, gate)` from `observed.json`.

### Verdicts

| Verdict | Means |
|---|---|
| `present` | Populated on every sampled record. |
| `present-but-unreliable` | Populated on some records and not others, **with the condition stated**. A field present only on cross-currency transactions is useful information, not a failure (`requirements.md` D5). |
| `absent` | A record that should have carried the field did not. This is a claim about PayPal. |
| `unanswered` | No fixture exercised the condition under which the field could appear. This is a statement about **our fixtures**, not about PayPal, and the reason is always given. |

**`absent` and `unanswered` are different facts and must not be conflated.** `validation.md` section 5 says so for the FX row, and `notes.md` records how readily a missing permission or a missing fixture masquerades as a finding about the API. `requirements.md` D6 originally allowed three verdicts; the fourth was added when two fixtures proved uncreatable, and it is constrained to cases where the unexercised condition can be named. It is not a hedge: every `unanswered` row says what would answer it.

### Summary

| Verdict | Canonical fields |
|---|---|
| `present` | 47 |
| `present-but-unreliable` | 9 |
| `absent` | 6 |
| `unanswered` | 11 |
| **total** | **73** |

No blank verdicts, and no canonical field without a row (`validation.md` C4, C5).

---

## 1. Payment

The buyer-facing transaction. Read from `GET /v2/checkout/orders/{id}`.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `payment_id` | `orders` `$.id` | Optional, read-only (SDK) | 2/2 | `present` | PayPal's order id. The canonical payment key. |
| `amount` | `orders` `$.purchase_units[].amount.value` | Required (SDK) | 2/2 | `present` | String, parses straight to Decimal. |
| `currency` | `orders` `$.purchase_units[].amount.currency_code` | Required (SDK) | 2/2 | `present` | Buyer-facing currency, not necessarily the settlement currency. |
| `status` | `orders` `$.status` | Optional (SDK) | 2/2 | `present` | COMPLETED observed. Feeds the lifecycle enum of Phase 3. |
| `intent` | `orders` `$.intent` | Optional (SDK) | 2/2 | `present` | CAPTURE observed. Distinguishes authorize-then-capture flows. |
| `created_at` | `orders` `$.create_time` | Optional (SDK) | 2/2 | `present` | RFC 3339. |
| `updated_at` | `orders` `$.update_time` | Optional (SDK) | 2/2 | `present` | RFC 3339. |
| `payer_identity` | `orders` `$.payer` | Optional (SDK) | 0/2 | `unanswered` | **both order fixtures were card-funded, and a card-funded order carries no payer at all. A wallet-funded order would answer it.** Buyer identity. See notes.md F5: a refund's payer block carries the MERCHANT, so it is not a substitute. |
| `funding_instrument` | `orders` `$.payment_source.card.brand` | Optional (SDK) | 2/2 | `present` | Card brand. The funding instrument determines whether payer identity and disputes are available at all. |
| `seller_identity` | `orders` `$.purchase_units[].payee.merchant_id` | Optional (SDK) | 2/2 | `present` | The payee merchant id. Ties a payment to a seller. |

## 2. Capture

The settlement-side record, and where PayPal puts its conversion evidence. Read from `GET /v2/payments/captures/{id}`.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `capture_id` | `captures` `$.id` | Optional, read-only (SDK) | 2/2 | `present` | The authoritative settlement-side key. |
| `payment_linkage` | `captures` `$.supplementary_data.related_ids.order_id` | Optional (SDK) | 2/2 | `present` | A real field, not a link to parse. Capture to order is a provider-supplied foreign key. |
| `amount` | `captures` `$.amount.value` | Required (SDK) | 2/2 | `present` | - |
| `currency` | `captures` `$.amount.currency_code` | Required (SDK) | 2/2 | `present` | - |
| `gross_amount` | `captures` `$.seller_receivable_breakdown.gross_amount.value` | Required (SDK) | 2/2 | `present` | Pre-fee, pre-conversion. |
| `net_amount` | `captures` `$.seller_receivable_breakdown.net_amount.value` | Required (SDK) | 2/2 | `present` | Equal to gross on our fixtures because no fee was assessed. |
| `paypal_fee` | `captures` `$.seller_receivable_breakdown.paypal_fee.value` | Required (SDK) | 0/2 | `absent` | **The weakest row in this table.** Absent on both captures, yet the refund carries paypal_fee as an explicit 0.00, so PayPal does emit the field when it has a value. Cause not established. Phase 7 must NOT conclude that PayPal withholds capture fees; re-verify against a fee-bearing transaction. See notes.md. |
| `settlement_amount` | `captures` `$.seller_receivable_breakdown.receivable_amount.value` | Required (SDK) | 1/2 | `present-but-unreliable` | **only on capture-cross-currency.** What the seller actually receives, in the settlement currency. |
| `settlement_currency` | `balances` `$.balances[].currency` | Required (SDK) | 1/1 | `present` | The balances endpoint answers this directly and unconditionally, which is why it beats inferring it per capture. |
| `status` | `captures` `$.status` | Optional, read-only (SDK) | 2/2 | `present` | PARTIALLY_REFUNDED observed, which the order view agrees with. |
| `final_capture` | `captures` `$.final_capture` | Optional, read-only (SDK) | 2/2 | `present` | Whether more captures may follow against the authorization. |
| `created_at` | `captures` `$.create_time` | Optional (SDK) | 2/2 | `present` | - |
| `updated_at` | `captures` `$.update_time` | Optional (SDK) | 2/2 | `present` | - |
| `platform_fee` | `captures` `$.seller_receivable_breakdown.platform_fees[].amount.value` | Required (SDK) | 0/2 | `absent` | Marketplace commission. Absent on captures; see the refund row, where it appears only through the order view. |

## 3. Refund

Read from `GET /v2/payments/refunds/{id}`, except where the order view carries more - see `platform_fee`.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `refund_id` | `refunds` `$.id` | Optional, read-only (SDK) | 1/1 | `present` | - |
| `capture_linkage` | `refunds` `$.links[].href` | Required (SDK) | 1/1 | `present` | **Derived, not provider-supplied.** There is no capture_id field on a refund; the only link back is links[rel=up], whose href must be parsed. Phase 7 must record this as a derived linkage. |
| `amount` | `refunds` `$.amount.value` | Required (SDK) | 1/1 | `present` | - |
| `currency` | `refunds` `$.amount.currency_code` | Required (SDK) | 1/1 | `present` | - |
| `gross_amount` | `refunds` `$.seller_payable_breakdown.gross_amount.value` | Required (SDK) | 1/1 | `present` | Note the name: a refund carries seller_PAYABLE_breakdown, a capture seller_RECEIVABLE_breakdown. Mapping code cannot share a path. |
| `net_amount` | `refunds` `$.seller_payable_breakdown.net_amount.value` | Required (SDK) | 1/1 | `present` | - |
| `paypal_fee` | `refunds` `$.seller_payable_breakdown.paypal_fee.value` | Required (SDK) | 1/1 | `present` | Present as an explicit 0.00. This is the evidence that the capture-side absence is not PayPal withholding the field. |
| `total_refunded_amount` | `refunds` `$.seller_payable_breakdown.total_refunded_amount.value` | Required (SDK) | 1/1 | `present` | What makes partial-refund tracking possible. readme section 13 Scenario 1 depends on it. |
| `platform_fee` | `orders` `$.purchase_units[].payments.refunds[].seller_payable_breakdown.platform_fees[].amount.value` | Required (SDK) | 1/2 | `present-but-unreliable` | **only on order-same-currency.** **Read-path dependent.** Populated when the refund is read through the order, absent from the refund endpoint for the same record. Phase 7 should ingest refunds via the order. See notes.md F2. |
| `status` | `refunds` `$.status` | Optional, read-only (SDK) | 1/1 | `present` | - |
| `created_at` | `refunds` `$.create_time` | Optional (SDK) | 1/1 | `present` | Returned with a -08:00 offset where captures use Z. Both are RFC 3339; a naive string comparison across the two would be wrong. |

## 4. Dispute

**Every row here is `unanswered`.** `POST /v1/customer/disputes` accepts creation but rejects a card-funded capture with `TRANSACTION_NOT_ELIGIBLE`, and both fixtures were card-funded, so no dispute record exists to observe. The declared column is complete and comes from PayPal's own OpenAPI document, so the shape is known even though the behaviour is not. **None of these rows may be read as `absent`** - that would be a claim about PayPal that our evidence does not support.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `dispute_id` | `disputes` `$.items[].dispute_id` | Optional, read-only (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `status` | `disputes` `$.items[].status` | Optional, read-only (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `lifecycle_stage` | `disputes` `$.items[].dispute_life_cycle_stage` | Optional, read-only (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** Declared read-only. The lifecycle states mission.md P4 wants. |
| `dispute_state` | `disputes` `$.items[].dispute_state` | Optional, read-only (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `amount` | `disputes` `$.items[].dispute_amount.value` | Required (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `currency` | `disputes` `$.items[].dispute_amount.currency_code` | Required (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `reason` | `disputes` `$.items[].reason` | Optional (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `created_at` | `disputes` `$.items[].create_time` | Optional, read-only (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** |
| `seller_response_due_date` | `disputes` `$.items[].seller_response_due_date` | Optional, read-only (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** Drives the awaiting-evidence lifecycle state of mission.md P4. |
| `capture_linkage` | `disputes` `$.items[].links[].href` | Required (OpenAPI) | 0/1 | `unanswered` | **no dispute record exists; a wallet-funded capture would answer it.** The list response declares no transaction id at all, only links. Linkage would have to come from the single-dispute endpoint. |

## 5. Payout batch

Read from `GET /v1/payments/payouts/{id}`. Two batches sampled, one `SUCCESS` and one `DENIED`, which is what made the fee findings possible.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `batch_id` | `payouts` `$.batch_header.payout_batch_id` | Required (OpenAPI) | 2/2 | `present` | - |
| `status` | `payouts` `$.batch_header.batch_status` | Required (OpenAPI) | 2/2 | `present` | SUCCESS and DENIED both observed. |
| `amount` | `payouts` `$.batch_header.amount.value` | Required (OpenAPI) | 2/2 | `present` | - |
| `currency` | `payouts` `$.batch_header.amount.currency` | Required (OpenAPI) | 2/2 | `present` | **Spelled `currency`, not `currency_code`.** Payouts v1 is the only API that does this, and the contract declares it. See notes.md F3 and G4. |
| `fees` | `payouts` `$.batch_header.fees.value` | Required (OpenAPI) | 2/2 | `present` | **Only trustworthy on a batch that processed.** On SUCCESS it equals the sum of item fees. On DENIED the figure is not the fee that would have been charged - observed wrong by a factor of 2.6. Never carry a denied batch's fee forward. |
| `created_at` | `payouts` `$.batch_header.time_created` | Optional (OpenAPI) | 2/2 | `present` | - |
| `completed_at` | `payouts` `$.batch_header.time_completed` | Optional (OpenAPI) | 2/2 | `present` | Populated on the denied batch too; it records when processing ended, not that it succeeded. |
| `funding_source` | `payouts` `$.batch_header.funding_source` | Optional (OpenAPI) | 2/2 | `present` | - |

## 6. Payout item

Four items across the two batches, two successful and two failed.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `item_id` | `payouts` `$.items[].payout_item_id` | Required (OpenAPI) | 2/2 | `present` | - |
| `batch_linkage` | `payouts` `$.items[].payout_batch_id` | Required (OpenAPI) | 2/2 | `present` | Provider-supplied, repeated on every item. |
| `amount` | `payouts` `$.items[].payout_item.amount.value` | Required (OpenAPI) | 2/2 | `present` | - |
| `currency` | `payouts` `$.items[].payout_item.amount.currency` | Required (OpenAPI) | 2/2 | `present` | - |
| `fee` | `payouts` `$.items[].payout_item_fee.value` | Required (OpenAPI) | 2/2 | `present` | Populated in both terminal states, but 0.00 on the failed items. Reconcile against the batch fee only on a successful batch. |
| `status` | `payouts` `$.items[].transaction_status` | Optional (OpenAPI) | 2/2 | `present` | SUCCESS and FAILED both observed. |
| `transaction_id` | `payouts` `$.items[].transaction_id` | Optional (OpenAPI) | 1/2 | `present-but-unreliable` | **only on payout-batch-success.** The link to the money movement. Absent on a failed item, which is correct - no transaction occurred. |
| `processed_at` | `payouts` `$.items[].time_processed` | Optional (OpenAPI) | 2/2 | `present` | **Populated on failed items too** (4 of 4). PayPal stamps a time on any item it attempted, so this timestamp is not evidence of payment. This corrects an earlier claim in fixtures.md; see notes.md F1. |
| `recipient` | `payouts` `$.items[].payout_item.receiver` | Required (OpenAPI) | 2/2 | `present` | Redacted in the committed artifacts as buyer PII; presence is what the verdict rests on. |
| `failure_reason` | `payouts` `$.items[].errors.name` | Required (OpenAPI) | 1/2 | `present-but-unreliable` | **only on payout-batch-denied.** RECEIVER_COUNTRY_NOT_ALLOWED observed. **Account-dependent**: a different sandbox account denied the same request with errors empty. When it is absent the product must answer insufficient_evidence rather than assemble a cause. |
| `sender_item_reference` | `payouts` `$.items[].payout_item.sender_item_id` | Optional (OpenAPI) | 2/2 | `present` | Our own idempotency/correlation key, echoed back. |

## 7. FX conversion

**The reason this phase ran first.** `mission.md` P3 forbids substituting an estimate for a provider record, so the model may only carry what PayPal actually supplies. Rows are gated on a real conversion existing: an amount that was never converted is not conversion evidence.

On the count: `validation.md` C4 asks for "all eight `fx_conversion` fields" while `requirements.md` section 7 enumerates nine items. The nine are listed here. The eight are the PayPal-sourced ones; `is_derived` is our own provenance flag and was never going to come from a provider. Covering nine satisfies the check either way.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `source_amount` | `captures` `$.seller_receivable_breakdown.gross_amount.value`<br>gated on `$.seller_receivable_breakdown.exchange_rate.value` | Required (SDK) | 1/2 gated | `present-but-unreliable` | **only on capture-cross-currency.** Gated on a conversion existing. EUR 95.50 observed. |
| `source_currency` | `captures` `$.seller_receivable_breakdown.exchange_rate.source_currency` | Optional (SDK) | 1/2 | `present-but-unreliable` | **only on capture-cross-currency.** Declared on the conversion record itself. |
| `target_amount` | `captures` `$.seller_receivable_breakdown.receivable_amount.value` | Required (SDK) | 1/2 | `present-but-unreliable` | **only on capture-cross-currency.** USD 104.05 observed. **Rounded to the cent by PayPal**, while the rate is not. Compare against this figure, never against a recomputation of gross times rate, or every cross-currency transaction shows a half-cent exception that is a rounding artefact. |
| `target_currency` | `captures` `$.seller_receivable_breakdown.exchange_rate.target_currency` | Optional (SDK) | 1/2 | `present-but-unreliable` | **only on capture-cross-currency.** |
| `rate` | `captures` `$.seller_receivable_breakdown.exchange_rate.value` | Optional (SDK) | 1/2 | `present-but-unreliable` | **only on capture-cross-currency.** **15 decimal places observed** (1.089477173902071). Parse from the string into Decimal and store at full precision; rounding it before the Phase 11 comparison manufactures a discrepancy. |
| `timestamp` | none | **not declared** | no path | `absent` | **No declared source anywhere in PayPal's contract.** Settled across all four declared conversion sites and both source grades; see notes.md G3. No further probing can change this. A timestamp taken from the capture's create_time is a derivation, not provider evidence. |
| `fee` | none | **not declared** | no path | `absent` | **No declared FX conversion fee anywhere.** paypal_fee and paypal_fee_in_receivable_currency are declared on a capture but neither is declared AS the conversion fee, and the payouts conversion structure declares no fee at all. Treating one as an FX fee is the substitution mission.md P3 forbids. |
| `provider` | none | **not declared** | no path | `absent` | Not a PayPal field and never will be. Set by the ingestion connector, which knows which provider it called. An absent verdict here is not a product limitation. |
| `is_derived` | none | **not declared** | no path | `absent` | Our own provenance flag (tech-stack.md section 2). Absent from PayPal by definition; it exists to mark the fields above that we had to derive. |

---

## Declared but not observed

The highest-value output of this phase (`plan.md` Group 5 step 4): fields PayPal's contract promises that our sandbox never delivered. Each gets a line on what its absence means for the canonical model.

**Scoped deliberately.** The contract declares 1148 paths and we observed 300, so the raw difference runs to hundreds - most of it alternative payment methods and optional reporting blocks nested under objects that never appeared at all. Listing those would bury the findings. This section lists the fields whose **parent object was observed**: PayPal returned the enclosing structure and left this member out. That is the set where an absence is informative. The full difference is derivable from the two artifacts at any time.

Fields whose parent object was observed: **108**, from a raw difference of 852.

### balances

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.balances[].primary` | Optional | Which balance is primary. With one currency on the account there is nothing to disambiguate; it matters for a multi-currency account. |

### captures

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.disbursement_mode` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.network_transaction_reference.acquirer_reference_number` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.network_transaction_reference.date` | Optional | Card network timestamp. Not a settlement timestamp and not a substitute for an FX conversion time. |
| `$.processor_response.payment_advice_code` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.seller_protection.dispute_categories` | Optional, read-only | Which dispute categories the capture is protected against. Unobserved, and seller_protection was NOT_ELIGIBLE on both fixtures. |
| `$.seller_receivable_breakdown.paypal_fee` | Optional | The capture fee. See the capture.paypal_fee row - the one place our evidence is weaker than the format suggests. |
| `$.seller_receivable_breakdown.paypal_fee_in_receivable_currency` | Optional | The capture fee in settlement currency. Would have been the closest thing to an FX-side fee; not populated, and not declared as a conversion fee in any case. |
| `$.seller_receivable_breakdown.platform_fees` | Optional | Marketplace commission on a capture. Its absence means platform fees cannot be read from the capture endpoint; the order view carries them on refunds. |
| `$.status_details` | Optional | The reason behind a non-terminal status. Absent because every fixture reached a terminal state; it will matter for the PENDING and delayed lifecycle states of mission.md P4. |
| `$.supplementary_data.related_ids.authorization_id` | Optional | Capture to authorization linkage. Unobserved because our captures had no separate authorization step. |
| `$.supplementary_data.related_ids.capture_id` | Optional | Present only on records other than the capture itself. |

### orders

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.payer` | Optional | Buyer identity on an order. Unanswered, not absent: both fixtures were card-funded and a card-funded order carries no payer. Reconciling a marketplace to a PayPal buyer account is impossible for card transactions. |
| `$.payment_source.apple_pay` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.bancontact` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.blik` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.card.attributes` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.card.authentication_result` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.card.available_networks` | Optional, read-only | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.card.from_request` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.card.stored_credential` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.eps` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.giropay` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.google_pay` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.ideal` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.mybank` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.p24` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.sofort` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.trustly` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.venmo` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.processing_instruction` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.purchase_units[].id` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].invoice_id` | Optional | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.purchase_units[].items` | Optional | Line items. Their absence means basket-level reconciliation is not available from PayPal and must come from the marketplace side. |
| `$.purchase_units[].most_recent_errors` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payment_instruction` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.authorizations` | Optional | Authorizations. Our fixtures captured directly, so the authorize-then-capture lifecycle is unobserved. |
| `$.purchase_units[].payments.captures[].custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.purchase_units[].payments.captures[].disbursement_mode` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.purchase_units[].payments.captures[].network_transaction_reference.acquirer_reference_number` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].network_transaction_reference.date` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].processor_response.avs_code` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].processor_response.payment_advice_code` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].seller_protection.dispute_categories` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].seller_receivable_breakdown.paypal_fee` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].seller_receivable_breakdown.paypal_fee_in_receivable_currency` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].seller_receivable_breakdown.platform_fees` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].status_details` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].acquirer_reference_number` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.purchase_units[].payments.refunds[].invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.purchase_units[].payments.refunds[].payer` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.net_amount_breakdown` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.net_amount_in_receivable_currency` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.paypal_fee_in_receivable_currency` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.platform_fees[].payee` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].status_details` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].supplementary_data.card` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].supplementary_data.risk.customer` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |

### payouts

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.batch_header.sender_batch_header.email_message` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.batch_header.sender_batch_header.recipient_type` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.items[].currency_conversion` | Optional | **A conversion record on payout items**, with exchange_rate, from_amount and to_amount. Unanswered: both batches were USD to USD. This is a third place FX evidence can come from and Phase 11 should know it exists. |
| `$.items[].errors.debug_id` | Required | PayPal's support correlation id on a failed item. Its absence means a failure cannot be quoted to PayPal support by id. |
| `$.items[].payout_item.recipient_name` | Optional | Recipient name. Not populated for an EMAIL-type recipient; identity is the email alone. |
| `$.total_items` | Optional | Declared by PayPal's own OpenAPI document on a payout batch and not returned by the API. The published spec overstates the payload; do not rely on it for pagination. |
| `$.total_pages` | Optional | As total_items. Payout item pagination cannot be driven off these fields. |

### refunds

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.acquirer_reference_number` | Optional | Acquirer reference on a refund. Would help trace a refund at the card network; not populated. |
| `$.custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.seller_payable_breakdown.net_amount_breakdown` | Optional, read-only | **A conversion record on refunds, and an array of them.** Unanswered rather than absent: our refund was same-currency. A refund against the EUR capture would answer it, and Phase 3 must not assume one conversion per record. |
| `$.seller_payable_breakdown.net_amount_in_receivable_currency` | Optional | The refund's net in settlement currency. Same cause as above - no conversion occurred. |
| `$.seller_payable_breakdown.paypal_fee_in_receivable_currency` | Optional | Refund fee in settlement currency. Same cause. |
| `$.seller_payable_breakdown.platform_fees` | Optional | Marketplace commission on a refund. Present through the order view for the same record, so this is a read-path artefact, not an API absence. |
| `$.status_details` | Optional | The reason behind a non-terminal status. Absent because every fixture reached a terminal state; it will matter for the PENDING and delayed lifecycle states of mission.md P4. |

### transactions

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.transaction_details[].payer_info.account_id` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.address` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.country_code` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.email_address` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.payer_status` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.phone_number` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].transaction_info.annual_percentage_rate` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.bank_reference_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.credit_promotional_fee` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.credit_term` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.credit_transactional_fee` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.custom_field` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.discount_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.fee_amount` | Optional | **The fee Transaction Search was hoped to supply.** Not merely unpopulated - the key is absent from the indexed record entirely, even with fields=all. So the capture-fee question cannot be answered from reporting either. |
| `$.transaction_details[].transaction_info.insurance_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.invoice_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.other_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.payment_method_type` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.payment_tracking_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.paypal_account_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.paypal_reference_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.paypal_reference_id_type` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.sales_tax_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.shipping_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.shipping_discount_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.shipping_tax_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.tip_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.transaction_note` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |

---

## Observed but not declared

The reverse gap, and the direction nobody plans for: fields PayPal returned that **neither** of its own published contracts declares. `validation.md` has no check for this because nothing anticipated it.

| Record type | Observed path | Observed | What it means |
|---|---|---|---|
| `orders` | `$.purchase_units[].supplementary_data.risk.risk_level_indicator` | 2/2 | PayPal returned HIGH_RISK on both orders. The SDK declares the enclosing risk object but models only customer.ip_address, so a generated deserializer would drop this silently. Risk level is reconciliation-relevant and would have been invisible. |
| `payouts` | `$.items[].links[].encType` | 2/2 | Undeclared in both source grades, yet PayPal sends it on every HATEOAS link these APIs return. |
| `payouts` | `$.links[].encType` | 2/2 | Undeclared in both source grades, yet PayPal sends it on every HATEOAS link these APIs return. |
| `transactions` | `$.links[].encType` | 1/1 | Undeclared in both source grades, yet PayPal sends it on every HATEOAS link these APIs return. |

This is the concrete argument against putting a generated deserializer between the wire and the evidence, which `plan.md` Group 1 step 6 forbids: APIMatic drops unknown keys, so all of these would have been invisible and `observed.json` quietly wrong. It is also the evidence Phase 7 should weigh when it chooses whether to use the SDK at all.

---

## What this table cannot say

Stated so a reader does not take a verdict further than the evidence carries it.

1. **Sandbox, not production.** Every observation is from `api-m.sandbox.paypal.com`. The `capture.paypal_fee` row is the one where this most likely matters: no fee was assessed on our captures, and a production transaction may well populate it.
2. **Small samples.** Two captures, one refund, two payout batches, four payout items. A `present` verdict on 2 of 2 is not a guarantee of universality; it is the strongest statement the fixtures support.
3. **No disputes at all.** Ten canonical dispute fields rest entirely on the declared column.
4. **Two conversion sites unobserved.** Refunds and payout items both declare conversion records that our same-currency fixtures could not exercise. Phase 11 should not assume the capture site is the only one.
5. **Reporting endpoints were stale.** Transaction Search and balances shared one snapshot older than every fixture, so their rows describe an account with no activity. Treat them as eventually consistent and the v2 resource endpoints as authoritative.

Per `validation.md` section 5, none of this blocks merge. A clearly recorded absence is a successful outcome - it is the finding the phase was run to obtain.

---

## Related

- `specs/mission.md` - P1 and P3 govern what may be claimed here
- `specs/tech-stack.md` - section 2 money rules, section 3 the model
- `specs/roadmap.md` - Phase 3 consumes this, Phase 11 depends on the FX rows
- `specs/2026-11-05-paypal-field-discovery/` - the phase directory

