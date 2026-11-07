# PayPal field availability

What PayPal **actually returns** for the record types the canonical financial model is built on, against what its contract **declares**.

Phase 0 output. Scope and decisions: `specs/2026-11-05-paypal-field-discovery/requirements.md` - findings: `specs/2026-11-05-paypal-field-discovery/notes.md` - merge gate: `specs/2026-11-05-paypal-field-discovery/validation.md`

**Generated, not written.** `uv run scripts/build_fields_doc.py` builds this file from `artifacts/declared.json` and `artifacts/observed.json`. Every verdict is computed from the observed artifact; none can be typed in by hand. Edit the mapping in the script, not this file.

Generated 2026-11-07 from:

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
| `present` | 44 |
| `present-but-unreliable` | 12 |
| `absent` | 5 |
| `unanswered` | 4 |
| **total** | **65** |

No blank verdicts, and no canonical field without a row (`validation.md` C4, C5).

---

## 1. Payment

The buyer-facing transaction. Read from `GET /v2/checkout/orders/{id}`.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `payment_id` | `orders` `$.id` | Optional, read-only (SDK) | 4/4 | `present` | PayPal's order id. The canonical payment key. |
| `amount` | `orders` `$.purchase_units[].amount.value` | Required (SDK) | 4/4 | `present` | - |
| `currency` | `orders` `$.purchase_units[].amount.currency_code` | Required (SDK) | 4/4 | `present` | Buyer-facing currency, not necessarily the settlement currency. |
| `status` | `orders` `$.status` | Optional (SDK) | 4/4 | `present` | - |
| `intent` | `orders` `$.intent` | Optional (SDK) | 4/4 | `present` | - |
| `created_at` | `orders` `$.create_time` | Optional (SDK) | 4/4 | `present` | - |
| `payer_identity` | `orders` `$.payer.payer_id` | Optional (SDK) | 4/4 | `present` | **Present under multiparty, and it was not before.** Phase 0's card-funded orders carried no payer at all. A wallet-funded order carries `address`, `email_address`, `name` and `payer_id`. |
| `buyer_verification` | `orders` `$.payment_source.paypal.account_status` | Optional, read-only (SDK) | 4/4 | `present` | `VERIFIED` observed. A buyer trust signal with no equivalent on a card payment. |
| `seller_identity` | `orders` `$.purchase_units[].payee.merchant_id` | Optional (SDK) | 4/4 | `present` | **The join the whole product rests on.** Under multiparty the payee is the seller, not the marketplace. |
| `disbursement_mode` | `orders` `$.purchase_units[].payment_instruction.disbursement_mode` | Optional (SDK) | 4/4 | `present` | `INSTANT` or `DELAYED`. Decides whether the seller is paid at capture or on release. |

## 2. Capture

The settlement-side record, and where PayPal puts its conversion evidence. Read from `GET /v2/payments/captures/{id}`.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `capture_id` | `captures` `$.id` | Optional, read-only (SDK) | 4/4 | `present` | - |
| `payment_linkage` | `captures` `$.supplementary_data.related_ids.order_id` | Optional (SDK) | 4/4 | `present` | A real field, not a link to parse. |
| `amount` | `captures` `$.amount.value` | Required (SDK) | 4/4 | `present` | - |
| `currency` | `captures` `$.amount.currency_code` | Required (SDK) | 4/4 | `present` | - |
| `status` | `captures` `$.status` | Optional, read-only (SDK) | 4/4 | `present` | **Mutable, and it does not mean the seller was paid.** A `DELAYED` capture reads `COMPLETED` while funds are held, and a disputed capture reverts to `PENDING`. |
| `disbursement_mode` | `captures` `$.disbursement_mode` | Optional (SDK) | 4/4 | `present` | Echoed onto the capture, so the mode is recoverable without the order. |
| `seller_identity` | `captures` `$.payee.merchant_id` | Optional (SDK) | 4/4 | `present` | - |
| `gross_amount` | `captures` `$.seller_receivable_breakdown.gross_amount.value` | Required (SDK) | 3/4 | `present-but-unreliable` | **only on capture-delayed-card, capture-instant-balance, capture-cross-currency.** Pre-fee, pre-conversion. |
| `net_amount` | `captures` `$.seller_receivable_breakdown.net_amount.value` | Required (SDK) | 3/4 | `present-but-unreliable` | **only on capture-delayed-card, capture-instant-balance, capture-cross-currency.** `gross - paypal_fee - platform_fees`, observed exactly on every capture carrying a breakdown. |
| `paypal_fee` | `captures` `$.seller_receivable_breakdown.paypal_fee.value` | Required (SDK) | 3/4 | `present-but-unreliable` | **only on capture-delayed-card, capture-instant-balance, capture-cross-currency.** **Phase 0 recorded this `absent`; under multiparty it is there.** 3.98 on 100.00, identical across card-in-wallet and balance funding. |
| `settlement_amount` | `captures` `$.seller_receivable_breakdown.receivable_amount.value` | Required (SDK) | 1/4 | `present-but-unreliable` | **only on capture-cross-currency.** Only where a conversion occurred. **Converts the net, not the gross** - see fx_conversion.source_amount. |
| `settlement_currency` | `balances` `$.balances[].currency` | Required (SDK) | 1/1 | `present` | The balances endpoint answers this unconditionally, which beats inferring it per capture. |
| `created_at` | `captures` `$.create_time` | Optional (SDK) | 4/4 | `present` | - |

## 3. Platform fee

PayPal's own record of the commission the marketplace took. Under multiparty this is independent evidence against the marketplace's rules - in the merchant-of-record model the commission was merely whatever the marketplace kept, with nothing to check it against.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `amount` | `captures` `$.seller_receivable_breakdown.platform_fees[].amount.value` | Required (SDK) | 3/4 | `present-but-unreliable` | **only on capture-delayed-card, capture-instant-balance, capture-cross-currency.** The marketplace's commission, as PayPal actually took it. Independent evidence against the marketplace's own rules. |
| `currency` | `captures` `$.seller_receivable_breakdown.platform_fees[].amount.currency_code` | Required (SDK) | 3/4 | `present-but-unreliable` | **only on capture-delayed-card, capture-instant-balance, capture-cross-currency.** Must match the transaction currency; PayPal rejects a mismatch. |
| `payee` | `captures` `$.seller_receivable_breakdown.platform_fees[].payee.merchant_id` | Optional (SDK) | 3/4 | `present-but-unreliable` | **only on capture-delayed-card, capture-instant-balance, capture-cross-currency.** Where the commission was routed. Defaults to the API caller when the order does not name one, and PayPal fills it in explicitly. |
| `reversal_on_refund` | `refunds` `$.seller_payable_breakdown.platform_fees[].amount.value` | Required (SDK) | 1/1 | `present` | **Reversed automatically and pro rata.** A 30.00 refund of a 100.00 capture returned 3.00 of the 10.00 commission, with no instruction sent. Overriding the amount needs an account enablement we do not have (`PLATFORM_FEE_NOT_ENABLED`). |
| `settlement_reference` | none | **not declared** | no path | `absent` | Partner fees settle as a next-day consolidated bank payment. No per-transaction settlement reference is exposed, and reconciling the aggregate is deferred (`mission.md` section 7). |

## 4. Refund

Read from `GET /v2/payments/refunds/{id}`, except where the order view carries more - see `platform_fee`.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `refund_id` | `refunds` `$.id` | Optional, read-only (SDK) | 1/1 | `present` | - |
| `capture_linkage` | `refunds` `$.links[].href` | Required (SDK) | 1/1 | `present` | **Derived, not provider-supplied.** No capture id on a refund; the only link back is `links[rel=up]`, whose href must be parsed. |
| `amount` | `refunds` `$.amount.value` | Required (SDK) | 1/1 | `present` | - |
| `currency` | `refunds` `$.amount.currency_code` | Required (SDK) | 1/1 | `present` | - |
| `gross_amount` | `refunds` `$.seller_payable_breakdown.gross_amount.value` | Required (SDK) | 1/1 | `present` | A refund carries `seller_payable_breakdown`, a capture `seller_receivable_breakdown`. Mapping code cannot share a path. |
| `net_amount` | `refunds` `$.seller_payable_breakdown.net_amount.value` | Required (SDK) | 1/1 | `present` | The same identity as a capture: 30.00 - 0.00 - 3.00 = 27.00. |
| `paypal_fee` | `refunds` `$.seller_payable_breakdown.paypal_fee.value` | Required (SDK) | 1/1 | `present` | `0.00` observed: PayPal did not return its own fee on the refund. |
| `total_refunded_amount` | `refunds` `$.seller_payable_breakdown.total_refunded_amount.value` | Required (SDK) | 1/1 | `present` | What makes partial-refund tracking possible. |
| `status` | `refunds` `$.status` | Optional, read-only (SDK) | 1/1 | `present` | - |
| `created_at` | `refunds` `$.create_time` | Optional (SDK) | 1/1 | `present` | - |

## 5. Dispute

**Every row here is `unanswered`.** `POST /v1/customer/disputes` accepts creation but rejects a card-funded capture with `TRANSACTION_NOT_ELIGIBLE`, and both fixtures were card-funded, so no dispute record exists to observe. The declared column is complete and comes from PayPal's own OpenAPI document, so the shape is known even though the behaviour is not. **None of these rows may be read as `absent`** - that would be a claim about PayPal that our evidence does not support.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `dispute_id` | `disputes` `$.items[].dispute_id` | Optional, read-only (OpenAPI) | 1/1 | `present` | - |
| `status` | `disputes` `$.items[].status` | Optional, read-only (OpenAPI) | 1/1 | `present` | `WAITING_FOR_SELLER_RESPONSE` observed. |
| `lifecycle_stage` | `disputes` `$.items[].dispute_life_cycle_stage` | Optional, read-only (OpenAPI) | 1/1 | `present` | `INQUIRY` observed - the pre-claim stage. |
| `dispute_state` | `disputes` `$.items[].dispute_state` | Optional, read-only (OpenAPI) | 1/1 | `present` | `REQUIRED_ACTION`. Note the list endpoint filters on this: a default query returns the dispute, but `dispute_state=OPEN_INQUIRIES` does not. |
| `amount` | `disputes` `$.items[].dispute_amount.value` | Required (OpenAPI) | 1/1 | `present` | - |
| `currency` | `disputes` `$.items[].dispute_amount.currency_code` | Required (OpenAPI) | 1/1 | `present` | - |
| `reason` | `disputes` `$.items[].reason` | Optional (OpenAPI) | 1/1 | `present` | `MERCHANDISE_OR_SERVICE_NOT_RECEIVED`. |
| `channel` | `disputes` `$.items[].dispute_channel` | Optional, read-only (OpenAPI) | 1/1 | `present` | `INTERNAL` - raised through PayPal rather than as a card chargeback. |
| `created_at` | `disputes` `$.items[].create_time` | Optional, read-only (OpenAPI) | 1/1 | `present` | - |
| `updated_at` | `disputes` `$.items[].update_time` | Optional, read-only (OpenAPI) | 1/1 | `present` | - |
| `seller_response_due_date` | `disputes` `$.items[].seller_response_due_date` | Optional, read-only (OpenAPI) | 1/1 | `present` | 20 days out. Drives the awaiting-evidence lifecycle state of `mission.md` P4. |
| `buyer_identity` | `disputes` `$.items[].disputed_transactions[].buyer.payer_id` | **not declared** | 1/1 | `present` | - |
| `seller_identity` | `disputes` `$.items[].disputed_transactions[].seller.merchant_id` | **not declared** | 1/1 | `present` | - |
| `capture_linkage` | `disputes` `$.items[].disputed_transactions[].buyer_transaction_id` | **not declared** | 1/1 | `present` | **Not our capture id.** The buyer's side of the transaction has its own identifier, so matching a dispute to a capture needs a join we have not established. Filtering by `disputed_transaction_id` does accept the capture id. |

## 6. Disbursement

**Every row here is `unanswered`.** Under `DELAYED`, release is `POST /v1/payments/referenced-payouts-items`, and both referenced-payouts endpoints answer `403` for this app - they need partner approval and a BN code. Under `INSTANT` the seller is paid at capture and there is no separate disbursement record to read.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `disbursement_id` | none | **not declared** | no path | `unanswered` | **the referenced-payouts endpoints answer 403 for this app, so no release could be made or read.** Under `DELAYED`, release is `POST /v1/payments/referenced-payouts-items`. |
| `status` | none | **not declared** | no path | `unanswered` | **no release could be made; referenced payouts answer 403.** |
| `released_at` | none | **not declared** | no path | `unanswered` | **no release could be made; referenced payouts answer 403.** PayPal auto-releases held funds after 28 days with no instruction, so absence of a release does not mean funds are stuck. |
| `capture_linkage` | none | **not declared** | no path | `unanswered` | **no release could be made; referenced payouts answer 403.** Declared as `reference_id` on the payout item, with `reference_type: TRANSACTION_ID`. |

## 7. FX conversion

**The reason this phase ran first.** `mission.md` P3 forbids substituting an estimate for a provider record, so the model may only carry what PayPal actually supplies. Rows are gated on a real conversion existing: an amount that was never converted is not conversion evidence.

On the count: `validation.md` C4 asks for "all eight `fx_conversion` fields" while `requirements.md` section 7 enumerates nine items. The nine are listed here. The eight are the PayPal-sourced ones; `is_derived` is our own provenance flag and was never going to come from a provider. Covering nine satisfies the check either way.

| Canonical field | PayPal path | Declared | Observed | Verdict | Condition / note |
|---|---|---|---|---|---|
| `source_amount` | `captures` `$.seller_receivable_breakdown.net_amount.value`<br>gated on `$.seller_receivable_breakdown.exchange_rate.value` | Required (SDK) | 1/4 gated | `present-but-unreliable` | **only on capture-cross-currency.** **The net, not the gross.** PayPal deducts fees in the transaction currency and converts the remainder: 82.23 EUR net * rate = the reported 89.59 USD. Converting the gross instead is wrong by the whole fee - 14.46 USD on this fixture. |
| `source_currency` | `captures` `$.seller_receivable_breakdown.exchange_rate.source_currency` | Optional (SDK) | 1/4 | `present-but-unreliable` | **only on capture-cross-currency.** |
| `target_amount` | `captures` `$.seller_receivable_breakdown.receivable_amount.value` | Required (SDK) | 1/4 | `present-but-unreliable` | **only on capture-cross-currency.** Rounded to the cent while the rate is not. Compare against this figure, never a recomputation. |
| `target_currency` | `captures` `$.seller_receivable_breakdown.exchange_rate.target_currency` | Optional (SDK) | 1/4 | `present-but-unreliable` | **only on capture-cross-currency.** |
| `rate` | `captures` `$.seller_receivable_breakdown.exchange_rate.value` | Optional (SDK) | 1/4 | `present-but-unreliable` | **only on capture-cross-currency.** 15 decimal places. Parse from the string into `Decimal` and store whole. |
| `timestamp` | none | **not declared** | no path | `absent` | **No declared source on any conversion structure.** Read the reporting ledger's `T0200` event, which lags about a day, or derive from the capture time and flag `is_derived`. |
| `fee` | none | **not declared** | no path | `absent` | **No conversion fee anywhere in the transaction APIs.** The residue of net times rate minus the reported settlement is rounding, not a fee. |
| `provider` | none | **not declared** | no path | `absent` | Set by the ingestion connector. |
| `is_derived` | none | **not declared** | no path | `absent` | Our own provenance flag. |

## Decisions

Every `absent` and every `present-but-unreliable` field carries one of four decisions (`plan.md` Group 6, `validation.md` C8). A field left undecided fails the build of this document, not merely its review.

| Kind | Means |
|---|---|
| `derive` | We can compute the value. It is written with **`is_derived` true** and may never be presented as provider evidence (`tech-stack.md` section 2, `mission.md` P3). |
| `limitation` | We cannot have the value. The entry states what the product therefore cannot do. |
| `no_action` | The conditional availability is correct for the domain. Nothing is derived and nothing is lost; the entry says why the condition is right. |
| `connector_metadata` | Not a PayPal field at all, and in this table only because `requirements.md` section 7 lists it. |

17 fields need a decision: 2 `connector_metadata`, 1 `derive`, 9 `limitation`, 5 `no_action`.

| Field | Verdict | Decision | `is_derived` | Detail |
|---|---|---|---|---|
| `capture.gross_amount` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `capture.net_amount` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `capture.paypal_fee` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `capture.settlement_amount` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `platform_fee.amount` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `platform_fee.currency` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `platform_fee.payee` | `present-but-unreliable` | `limitation` | no | **A capture loses its entire `seller_receivable_breakdown` while pending, and a dispute puts it there.** The disputed fixture reverted from `COMPLETED` to `PENDING` and the breakdown vanished - fee, net and platform fee with it. The SDK model documents this ("not available for transactions that are in pending state") but it had never been observed. So these values must be captured at ingestion and retained; re-reading later can return nothing, and the absence is not evidence that no fee was charged. |
| `platform_fee.settlement_reference` | `absent` | `limitation` | no | Partner fees arrive as a single next-day consolidated bank payment, and no per-transaction settlement reference is exposed. The per-transaction fee comparison is in scope; reconciling the aggregate is settlement-account work, deferred by `mission.md` section 7. A `platform_fee` row should still be able to carry a settlement reference later. |
| `fx_conversion.source_amount` | `present-but-unreliable` | `no_action` | no | Populated only where a conversion occurred, which is right: a same-currency transaction has no conversion and must carry no `fx_conversion` row at all. Nothing is derived and nothing is lost. |
| `fx_conversion.source_currency` | `present-but-unreliable` | `no_action` | no | Populated only where a conversion occurred, which is right: a same-currency transaction has no conversion and must carry no `fx_conversion` row at all. Nothing is derived and nothing is lost. |
| `fx_conversion.target_amount` | `present-but-unreliable` | `no_action` | no | Populated only where a conversion occurred, which is right: a same-currency transaction has no conversion and must carry no `fx_conversion` row at all. Nothing is derived and nothing is lost. |
| `fx_conversion.target_currency` | `present-but-unreliable` | `no_action` | no | Populated only where a conversion occurred, which is right: a same-currency transaction has no conversion and must carry no `fx_conversion` row at all. Nothing is derived and nothing is lost. |
| `fx_conversion.rate` | `present-but-unreliable` | `no_action` | no | Populated only where a conversion occurred, which is right: a same-currency transaction has no conversion and must carry no `fx_conversion` row at all. Nothing is derived and nothing is lost. |
| `fx_conversion.timestamp` | `absent` | `derive` | **yes** | Prefer the reporting ledger's `T0200` conversion event, which carries its own `transaction_initiation_date` and is provider evidence. It lags about a day; where reporting has not caught up, fall back to the capture's `create_time` with **`is_derived` true**. The field therefore carries a provider value sometimes and a derived one otherwise, which is exactly what `is_derived` exists to record. |
| `fx_conversion.fee` | `absent` | `limitation` | no | No conversion fee can be reported. Substituting `paypal_fee` is the substitution `mission.md` P3 forbids, and the arithmetic residue is PayPal's rounding. Consequence: readme section 13 Scenario 3 covers an incorrect conversion **value**, never an incorrect conversion **fee**. |
| `fx_conversion.provider` | `absent` | `connector_metadata` | no | Set by the connector, which knows which provider it called. |
| `fx_conversion.is_derived` | `absent` | `connector_metadata` | no | Our own provenance flag; the mechanism the decisions above rely on. |

**Two derivations exist, and both are labelled.** `fx_conversion.timestamp` and the same-currency case of `capture.settlement_amount` are the only values this phase authorises computing, and each must carry `is_derived`. Everything else is either a stated limitation or a condition that is correct as it stands. No monetary value is derived from an undocumented formula - the `capture.paypal_fee` row explains why that mattered.

---

## Pending answers

The 4 `unanswered` fields get an interim handling and the fixture that would settle them - **not** a decision. A decision implies the evidence is in; for these it is not, and recording a limitation would overstate what we know.

| Field | What would answer it | Interim handling |
|---|---|---|
| `disbursement.disbursement_id` | partner approval and a BN code, which the referenced-payouts endpoints require; they answer 403 for this app | Model the disbursement states from the declared contract but mark nothing required, and do not infer that a seller was paid from a capture's `COMPLETED` status. Under `DELAYED` that status is reached while funds are still held. |
| `disbursement.status` | partner approval and a BN code, which the referenced-payouts endpoints require; they answer 403 for this app | Model the disbursement states from the declared contract but mark nothing required, and do not infer that a seller was paid from a capture's `COMPLETED` status. Under `DELAYED` that status is reached while funds are still held. |
| `disbursement.released_at` | partner approval and a BN code, which the referenced-payouts endpoints require; they answer 403 for this app | Model the disbursement states from the declared contract but mark nothing required, and do not infer that a seller was paid from a capture's `COMPLETED` status. Under `DELAYED` that status is reached while funds are still held. |
| `disbursement.capture_linkage` | partner approval and a BN code, which the referenced-payouts endpoints require; they answer 403 for this app | Model the disbursement states from the declared contract but mark nothing required, and do not infer that a seller was paid from a capture's `COMPLETED` status. Under `DELAYED` that status is reached while funds are still held. |

Both fixtures need the same thing: **a wallet-funded capture**, which requires a sandbox personal account to approve an order in a browser. That one fixture would answer eleven rows, and would also let a cross-currency refund and payout settle the two unobserved conversion sites. It is the highest-value follow-up this phase produces and it is cheap.

---

## Declared but not observed

The highest-value output of this phase (`plan.md` Group 5 step 4): fields PayPal's contract promises that our sandbox never delivered. Each gets a line on what its absence means for the canonical model.

**Scoped deliberately.** The contract declares 1148 paths and we observed 356, so the raw difference runs to hundreds - most of it alternative payment methods and optional reporting blocks nested under objects that never appeared at all. Listing those would bury the findings. This section lists the fields whose **parent object was observed**: PayPal returned the enclosing structure and left this member out. That is the set where an absence is informative. The full difference is derivable from the two artifacts at any time.

Fields whose parent object was observed: **126**, from a raw difference of 717.

### balances

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.balances[].primary` | Optional | Which balance is primary. With one currency on the account there is nothing to disambiguate; it matters for a multi-currency account. |

### captures

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.network_transaction_reference` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.processor_response` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.seller_receivable_breakdown.paypal_fee_in_receivable_currency` | Optional | The capture fee in settlement currency. Would have been the closest thing to an FX-side fee; not populated, and not declared as a conversion fee in any case. |
| `$.seller_receivable_breakdown.platform_fees[].payee.email_address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.supplementary_data.related_ids.authorization_id` | Optional | Capture to authorization linkage. Unobserved because our captures had no separate authorization step. |
| `$.supplementary_data.related_ids.capture_id` | Optional | Present only on records other than the capture itself. |

### disputes

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.items[].buyer_response_due_date` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.items[].dispute_asset` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |

### orders

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.payer.address.address_line_1` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.address.address_line_2` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.address.admin_area_1` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.address.admin_area_2` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.address.postal_code` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.birth_date` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.phone` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payer.tax_info` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.payment_source.apple_pay` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.bancontact` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.blik` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.card` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.eps` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.giropay` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.google_pay` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.ideal` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.mybank` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.p24` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.address.address_line_1` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.address.address_line_2` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.address.admin_area_1` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.address.admin_area_2` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.address.postal_code` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.attributes` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.birth_date` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.business_name` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.experience_status` | Optional, read-only | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.phone_number` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.phone_type` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.stored_credential` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.paypal.tax_info` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.sofort` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.trustly` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.payment_source.venmo` | Optional | An alternative funding instrument. Unobserved because both fixtures were card-funded; one row per instrument PayPal declares, none of them exercised. Not an API absence. |
| `$.processing_instruction` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.purchase_units[].id` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].invoice_id` | Optional | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.purchase_units[].items` | Optional | Line items. Their absence means basket-level reconciliation is not available from PayPal and must come from the marketplace side. |
| `$.purchase_units[].most_recent_errors` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payee.email_address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payment_instruction.payee_pricing_tier_id` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payment_instruction.payee_receivable_fx_rate_id` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payment_instruction.platform_fees[].payee.email_address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.authorizations` | Optional | Authorizations. Our fixtures captured directly, so the authorize-then-capture lifecycle is unobserved. |
| `$.purchase_units[].payments.captures[].custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.purchase_units[].payments.captures[].invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.purchase_units[].payments.captures[].network_transaction_reference` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].processor_response` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].seller_receivable_breakdown.paypal_fee_in_receivable_currency` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.captures[].seller_receivable_breakdown.platform_fees[].payee.email_address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].acquirer_reference_number` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.purchase_units[].payments.refunds[].invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.purchase_units[].payments.refunds[].payer` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.net_amount_breakdown` | Optional, read-only | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.net_amount_in_receivable_currency` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.paypal_fee_in_receivable_currency` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].seller_payable_breakdown.platform_fees[].payee` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].payments.refunds[].status_details` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping.address.address_line_2` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping.email_address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping.options` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping.phone_number` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping.trackers` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].shipping.type` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.purchase_units[].supplementary_data` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |

### refunds

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.acquirer_reference_number` | Optional | Acquirer reference on a refund. Would help trace a refund at the card network; not populated. |
| `$.custom_id` | Optional | A caller-supplied external reference, as invoice_id. This is the intended place to carry a marketplace order id into PayPal and have it come back. |
| `$.invoice_id` | Optional, read-only | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.seller_payable_breakdown.net_amount_breakdown` | Optional, read-only | **A conversion record on refunds, and an array of them.** Unanswered rather than absent: our refund was same-currency. A refund against the EUR capture would answer it, and Phase 3 must not assume one conversion per record. |
| `$.seller_payable_breakdown.net_amount_in_receivable_currency` | Optional | The refund's net in settlement currency. Same cause as above - no conversion occurred. |
| `$.seller_payable_breakdown.paypal_fee_in_receivable_currency` | Optional | Refund fee in settlement currency. Same cause. |
| `$.seller_payable_breakdown.platform_fees[].payee` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.status_details` | Optional | The reason behind a non-terminal status. Absent because every fixture reached a terminal state; it will matter for the PENDING and delayed lifecycle states of mission.md P4. |

### transactions

| Declared path | Declared | What its absence means |
|---|---|---|
| `$.transaction_details[].cart_info.item_details[].adjustment_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].basic_shipping_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].checkout_options` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].discount_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].extra_shipping_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].gift_wrap_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].handling_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].insurance_amount` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].invoice_number` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].item_code` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].item_options` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].tax_amounts` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.item_details[].tax_percentage` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].cart_info.paypal_invoice_id` | Optional | A caller-supplied external reference. We did not set one, so its absence says nothing about PayPal. The canonical model may rely on it only if the marketplace sets it at capture time. |
| `$.transaction_details[].cart_info.tax_inclusive` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].payer_info.address.line2` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.payer_name.full_name` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.payer_name.middle_name` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.payer_name.prefix` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.payer_name.suffix` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].payer_info.phone_number.extension_number` | Optional | Buyer detail on a reporting record. Unobserved on the single indexed transaction, which is the account's own opening balance credit and has no buyer. |
| `$.transaction_details[].shipping_info.address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].shipping_info.method` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].shipping_info.secondary_shipping_address` | Optional | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `$.transaction_details[].transaction_info.annual_percentage_rate` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.bank_reference_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.credit_promotional_fee` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.credit_term` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.credit_transactional_fee` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.discount_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.insurance_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.invoice_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.other_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.payment_method_type` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.payment_tracking_id` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.sales_tax_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.shipping_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.shipping_discount_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.shipping_tax_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |
| `$.transaction_details[].transaction_info.tip_amount` | Optional | An optional reporting amount or reference. Unobserved on the one indexed record; Transaction Search had not caught up with any fixture. |

---

## Observed but not declared

The reverse gap, and the direction nobody plans for: fields PayPal returned that **neither** of its own published contracts declares. `validation.md` has no check for this because nothing anticipated it.

| Record type | Observed path | Observed | What it means |
|---|---|---|---|
| `disputes` | `$.items[].disputed_transactions` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `disputes` | `$.items[].disputed_transactions[]` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `disputes` | `$.items[].disputed_transactions[].buyer` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `disputes` | `$.items[].disputed_transactions[].buyer.payer_id` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `disputes` | `$.items[].disputed_transactions[].buyer_transaction_id` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `disputes` | `$.items[].disputed_transactions[].seller` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `disputes` | `$.items[].disputed_transactions[].seller.merchant_id` | 1/1 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `orders` | `$.purchase_units[].payee.display_data` | 4/4 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
| `orders` | `$.purchase_units[].payee.display_data.brand_name` | 4/4 | Declared optional and not populated for our fixtures. The canonical model must not depend on it without a fixture that exercises it. |
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

