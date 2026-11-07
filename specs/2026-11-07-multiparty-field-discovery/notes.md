# Phase 0b — Multiparty field discovery · Notes

**Branch:** `phase-0b/multiparty-field-discovery`
Fixtures: `./fixtures.md` · Direction: `specs/multiparty-direction.md`
Output: `specs/paypal-fields.md`

This phase re-established the observed column under PayPal's **multiparty**
model. Phase 0 measured a first-party integration, which is not what the
product targets, so its observations govern nothing here.

**This file stands alone.** Findings from Phase 0 that remain true are restated
below rather than cross-referenced, so nobody has to open a superseded folder
to understand the current position.

---

## 1. What carries over from Phase 0, unchanged

These are properties of PayPal's contract or of the API's behaviour, not of the
integration model, so they survive the change intact.

- **No conversion structure anywhere declares a timestamp or an FX fee**, across
  all four declared conversion sites and both source grades. The declared
  column is identical under either model.
- **PayPal spells money more than one way.** `{currency_code, value}` in Orders
  v2, Payments v2 and Reporting v1; `{currency, value}` in Payouts v1; and
  `balances[].currency` is a bare string sitting beside money objects that use
  `currency_code`. An adapter must match both envelopes and must not assume a
  key named `currency` is one.
- **`float` appears zero times** across all 373 generated model pages and 369
  model modules. Every monetary field is a string carrying its own currency.
- **A `403` is never evidence about a field**, only about the caller's
  permissions — and a scope granted after a token is minted does not appear in
  that token.
- **A `5xx` is not reliably transient.** A malformed request produced one
  repeatably.
- **The reporting APIs are eventually consistent**, roughly a day behind, and
  share one snapshot. They are still the only observed source of a conversion
  timestamp, via `T0200` ledger events that carry their own
  `transaction_initiation_date` and link to the capture by
  `paypal_reference_id`.
- **`Prefer: return=representation` has no effect on a read.** It gates
  financial detail on write responses only; a capture `GET` is byte-identical
  with and without it.
- **PayPal returns fields its own contracts do not declare**, and stops
  returning some of them later. Retain raw payloads.
- **Refund-to-capture linkage is derived.** No capture id exists on a refund;
  the only link is a URL inside `links[rel=up]`.

## 2. What Phase 0 got right for the wrong reason

**"Compare against the reported settlement amount, never a recomputation."**
Phase 0 reached this rule and justified it as a half-cent rounding artefact.
The rule is right. The reason was wrong, and the stakes are far larger.

PayPal **deducts fees in the transaction currency and converts the remainder**.
On the cross-currency fixture:

```
gross  EUR 95.50
fees   EUR  3.72 paypal_fee + EUR 9.55 platform_fee
net    EUR 82.23
rate   1.089477173902071
net   * rate = 89.58770800996729833  ->  reported USD 89.59   (rounding, 0.0023)
gross * rate = 104.04507010764778050 ->  wrong by USD 14.46
```

Phase 0 could not have seen this: it had no fees, so `gross == net` and the two
computations coincided. An engine recomputing from gross would not produce
cent-level noise — it would misstate every cross-currency settlement by the
entire fee, which no rounding tolerance would catch.

## 3. What multiparty changed in the observed column

Four Phase 0 verdicts are contradicted outright. None of these is a change in
PayPal's behaviour; they are what a different integration model returns.

| Field | Phase 0, first-party | Phase 0b, multiparty |
|---|---|---|
| `capture.paypal_fee` | `absent` | **present** — 3.98 on 100.00 |
| `platform_fee.amount` | `unanswered` | **present** — 10.00, with an explicit payee |
| `payment.payer_identity` | `unanswered` | **present** — full buyer identity |
| 10 dispute fields | `unanswered` | **present** — a real dispute record |

Phase 0's `capture.paypal_fee` row carried a warning that the verdict was
"potentially false of production" and needed re-verification. It was right to
carry it, and the re-verification has now happened.

## 4. Findings this phase produced

### 4.1 A disputed capture loses its entire financial breakdown

Opening a dispute reverted the capture from `COMPLETED` to **`PENDING`** and
removed `seller_receivable_breakdown` altogether — gross, net, `paypal_fee` and
`platform_fees` with it.

The SDK model documents the mechanism ("not available for transactions that are
in pending state") but it had never been observed. Three consequences:

1. **Capture status is mutable after the fact.** An ingestion that treats
   `COMPLETED` as terminal is wrong.
2. **Fee evidence must be captured at ingestion and retained.** Re-reading a
   disputed capture returns nothing, and that absence is not evidence that no
   fee was charged. This is the sharpest argument yet for `mission.md` P7.
3. It is why `capture.paypal_fee` is `present-but-unreliable` rather than
   `present`: 3 of 4 captures, absent on the disputed one.

### 4.2 The platform fee is taken at capture, in both disbursement modes

Two orders identical apart from `disbursement_mode` produced identical
breakdowns — same `paypal_fee`, same `platform_fees`, same `net_amount`. PayPal
documents this nowhere. The fee is assessed at capture; only the movement of
the seller's funds differs.

### 4.3 A `DELAYED` capture reads `COMPLETED` while the seller is unpaid

The `INSTANT` and `DELAYED` captures are structurally identical apart from
`disbursement_mode`, and both report `status: COMPLETED`. **Capture status
cannot be used to infer that a seller was disbursed.** This is the
false-positive trap `multiparty-direction.md` §4 predicted, now evidenced
rather than argued.

### 4.4 Platform fee reversal on a refund is automatic and pro rata

A refund of 30.00 against a 100.00 capture returned **3.00** of the 10.00
commission — exactly proportional — with **no instruction sent**.

An explicit instruction was attempted first and refused:
`422 PLATFORM_FEE_NOT_ENABLED`, "The API Caller account is not setup to be able
to process refunds with `platform_fees`." So what requires enablement is
**overriding** the amount; the default reversal happens regardless.

This contradicts what was written into `readme.md` §3.3 and §6.2 during the
direction amendment — that the reversal is "not automatic" and that a refund
leaving commission untouched "may be correct rather than wrong". **Both are
wrong and need correcting**, and the error mattered: Phase 10 would have
expected commission to survive a refund.

### 4.5 The fee did not vary across funding instruments, but the API does not say which was used

Three captures of the same amount and currency all returned `3.98`, two funded
by a card inside the wallet and one from the PayPal balance. But all three
carry an **identical** `payment_source.paypal` block, with nothing recording
the instrument. That two were card-funded is known only from outside the data.

So the supportable claim is narrow, and the durable consequence is that
**nothing in the canonical model may branch on funding instrument**, because
PayPal does not expose it.

### 4.6 Buyer identity lives on the order, not the capture

The order carries `payer` with `address`, `email_address`, `name` and
`payer_id`, and the capture response carries `payment_source.paypal` including
`account_status: VERIFIED`. `GET /v2/payments/captures/{id}` carries neither.
Phase 7 must read the order for buyer identity — the same read-path lesson
Phase 0 learned for `platform_fees` on refunds.

### 4.7 The platform cannot see its seller's disputes

Reading disputes as the platform with a seller assertion returns `403`; the
seller's own credentials return `200`. The onboarding referral requested
`PAYMENT`, `REFUND`, `PARTNER_FEE`, `DELAY_FUNDS_DISBURSEMENT`,
`ACCESS_MERCHANT_INFORMATION` and `ADVANCED_TRANSACTIONS_SEARCH` — **no dispute
feature**. Dispute reconciliation is a named MVP capability, so the referral
needs dispute scopes and the seller must be re-onboarded with them.

### 4.8 A dispute does not name the capture id

The dispute record's `disputed_transactions[].buyer_transaction_id` is the
**buyer's** side of the transaction, not our capture id. Filtering the list by
`disputed_transaction_id` does accept the capture id, so a join exists, but the
returned record cannot be matched on its face. Phase 11 needs that join
established.

The list endpoint also filters by state in a way worth knowing: a default query
returns the dispute, `dispute_state=UNDER_PAYPAL_REVIEW` returns it, and
`dispute_state=OPEN_INQUIRIES` does not — despite the lifecycle stage being
`INQUIRY`.

### 4.9 The release works, and it was never blocked

An earlier reading of a `403` concluded that referenced payouts were
unavailable to this app. **That was wrong, and the mistake is instructive**: the
`403` came from the *list* form, `GET /v1/payments/referenced-payouts-items
?reference_id=`, and was generalised to the whole API without testing the
others. Probing each shape separately:

| Call | Result |
|---|---|
| `GET item/{id}` | `404 INVALID_RESOURCE_ID` — permitted |
| `POST item` | `400 VALIDATION_ERROR` on a malformed id — permitted |
| `GET list ?reference_id=` | `403 FORBIDDEN` — the only blocked shape |

This is the same error the phase warns about, committed by the phase itself: a
`403` is evidence about one endpoint and one caller, never about an API.

The release succeeded. `POST /v1/payments/referenced-payouts-items` with
`reference_id` set to the capture id returned a record with `item_id`,
`processing_state.status: SUCCESS`, `payout_transaction_id`, `payout_amount`
and `payout_destination`. **It is idempotent per reference** — a second attempt
answers `422 PAYOUT_ALREADY_COMPLETED_FOR_REFERENCE`, so a retry cannot
double-pay.

`payout_amount` is `86.02`, the capture's **net**. Fees were already taken at
capture, confirming the fee-timing answer of §4.2 from a second direction.

### 4.10 Releasing funds changes nothing on the capture

Read immediately before and after the release:

| | before | after |
|---|---|---|
| `status` | `COMPLETED` | `COMPLETED` |
| `disbursement_mode` | `DELAYED` | `DELAYED` |
| breakdown | gross, net, `paypal_fee`, `platform_fees` | identical |

**So a capture cannot tell you whether the seller was paid.** Phase 11 must
join every `DELAYED` transaction to its disbursement record, or it will report
unpaid sellers as paid and vice versa — silently, because nothing in the
capture looks wrong.

### 4.11 The disbursement record has no contract, and no timestamp

**PayPal publishes nothing for referenced payouts** — no SDK model, no path in
any of its OpenAPI documents. Confirmed by searching both. So the single record
that proves a seller was paid is known from observation alone, and a change to
it would arrive with no warning. `declared.json` records this as a third source
grade, `undeclared_by_paypal`, rather than leaving the declared column
mysteriously empty.

It also carries **no usable timestamp**: `time_updated` is the integer `0`, and
`last_response_code` likewise. When a seller was paid must therefore be
recorded at ingestion or taken from the reporting ledger, and an ingestion time
must never be presented as a provider timestamp.

### 4.12 A lost dispute claws back the commission, and costs the seller PayPal's fee

The dispute was resolved by accepting the claim as the seller, which refunds
the buyer in full. `POST /v1/customer/disputes/{id}/accept-claim` returned
`200`.

**The capture's breakdown came back.** It had vanished while the capture was
`PENDING`; once the capture moved to `REFUNDED` the full breakdown returned,
with its **original** values — gross 100.00, `paypal_fee` 3.98,
`platform_fees` 10.00, net 86.02. The capture is not adjusted by the reversal;
the refund records carry it. So the disappearance is tied to the `PENDING`
state and is recoverable afterwards, but not during.

**PayPal generated two refund records for one logical reversal:**

| Refund | Status | gross | paypal_fee | platform_fees | net |
|---|---|---|---|---|---|
| `1GJ79007MD105063Y` | **FAILED** | 86.02 | 0.00 | 0.00 | 86.02 |
| `56U34857F7216691V` | **COMPLETED** | 100.00 | 0.00 | **10.00** | 90.00 |

Both report `total_refunded_amount` 100.00. **Summing refund records would
double-count**: 86.02 plus 100.00 against a 100.00 capture. Phase 11 must
filter on status, and a failed refund attempt is a record that exists and must
not be treated as money moved.

**The commission is clawed back in full.** `platform_fees` 10.00 on the
completed refund, so the marketplace does not keep its fee on a reversed
transaction. Combined with §4.4, the rule is uniform: PayPal reverses
commission proportionally on an ordinary refund and fully on a full reversal,
without being asked either time. Phase 10's expected position must assume it.

**The seller bears PayPal's processing fee.** Follow the money on the seller's
side: credited 86.02 at capture, debited 90.00 on the refund, leaving the
seller **3.98 down** — exactly the `paypal_fee`, which the refund returns as
`0.00`. So a lost dispute costs a seller the original processing fee on top of
the full reversal, and an entitlement calculation that nets a dispute to zero
is wrong by that fee.

**The dispute did not close.** It moved to `status: UNDER_REVIEW`,
`dispute_state: UNDER_PAYPAL_REVIEW`, still at lifecycle stage `INQUIRY`, even
after the seller accepted. So accepting a claim is not a terminal state, and
the financial effect lands before the dispute record settles — a reconciliation
keyed on dispute closure would miss money that has already moved.

## 5. Still unanswered

| Question | Why |
|---|---|
| Whether the consolidated daily fee settlement is visible via any API | Only a monthly SFTP partner report is documented |
| What the `403` list endpoint would give | Not needed: reading an item by id works, and the capture links to it |

No canonical field is left `unanswered`.

## 6. Status

`specs/paypal-fields.md` is regenerated from multiparty evidence only: 69
canonical rows, 56 `present`, 6 `present-but-unreliable`, 7 `absent` and
no `unanswered`. `scripts/check_fields_doc.py --all` passes, including C2
reproducibility against a fresh probe.

Phase 0's directory is left as the record of that phase, accurate for the
model it measured. Nothing in this phase reads it.
