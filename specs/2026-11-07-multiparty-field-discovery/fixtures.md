# Phase 0b — Multiparty sandbox fixtures

**Branch:** `phase-0b/multiparty-field-discovery`
Direction: `specs/multiparty-direction.md` · Phase 0: `specs/2026-11-05-paypal-field-discovery/`

Fixture ids created in the PayPal sandbox under the **multiparty** model, so
the observed column covers what Phase 0 could not: platform fees, disbursement
modes, and a seller-level view. Every id below was confirmed retrievable.

Sandbox base URL: `https://api-m.sandbox.paypal.com`
Seeded: 2026-11-07

---

## The accounts, and which credentials reach what

Three sandbox accounts are now in play. Getting this wrong is the quickest way
to misread a `401` as a finding.

| Role | Account | Credentials |
|---|---|---|
| **Platform** (the marketplace) | `G2K2FVVLETKYG` | `PAYPAL_CLIENT_ID` / `_SECRET`, 42 scopes including 4 partner scopes |
| **Seller** | `C24NAWELTTHXJ` | `PAYPAL_MERCHANT_CLIENT_ID` / `_SECRET`, 33 scopes, no partner scopes |
| **Buyer** | payer `2L4BAW5QJ2MSU` | none; approves in a browser |

The seller is the same account Phase 0 used as its merchant, onboarded under
the platform. Reusing it keeps Phase 0's fixtures reachable in the account they
were seeded in, so `validation.md` C2 still runs.

**Reading a record depends on who asks.** Verified against Phase 0 capture
`8RW04003P5846784X`:

| Identity | Result |
|---|---|
| merchant profile, the account that owns it | `200` |
| platform profile, no assertion | **`401`** |
| platform profile, asserting the seller | `200` |

So `scripts/probe_paypal.py` takes `--profile {platform,merchant}` and
`--as-seller`, and every result records the `read_as` identity. A `401` without
that context is unreadable.

## Onboarding

The seller was onboarded through a partner referral. Two things cost time and
are worth not rediscovering:

- **`products` must be `PPCP`.** A referral requesting `EXPRESS_CHECKOUT`
  produced an `action_url` that dead-ends at `Cannot GET
  /unifiedonboarding/entry`. Requesting both returns `400`, "Requested multiple
  products are not supported together."
- **The `action_url` is single use.**

All six requested features were granted, reported as scope URIs under
`oauth_integrations[].oauth_third_party[].scopes` rather than as the feature
names used in the request — so reading the `capabilities` array alone suggests
wrongly that nothing was granted:

`payments/partnerfee`, `payments/delay-funds-disbursement`,
`reporting/search/read`, `customer/merchant-integrations/read`,
`payments/refund`, `payments/payment/authcapture`.

## Created fixtures

All three are USD 100.00 with a USD 10.00 platform fee, payee the seller, and
PayPal wallet as the funding source. They differ only where stated, which is
what makes them comparable.

| Fixture | Order id | Capture id | Mode | Funded by |
|---|---|---|---|---|
| Instant, card in wallet | `4NP885630E9766607` | `10X728766N112511N` | `INSTANT` | card inside the wallet |
| Delayed, card in wallet | `9PV67634T4050864W` | `5JS430140P0246744` | `DELAYED` | card inside the wallet |
| Instant, balance | `03Y10939VN407604U` | `48L03973A5528824C` | `INSTANT` | PayPal balance |

Every capture returned the same breakdown:

```json
"seller_receivable_breakdown": {
  "gross_amount":  { "currency_code": "USD", "value": "100.00" },
  "paypal_fee":    { "currency_code": "USD", "value": "3.98"   },
  "platform_fees": [ { "amount": { "currency_code": "USD", "value": "10.00" },
                       "payee":  { "merchant_id": "G2K2FVVLETKYG" } } ],
  "net_amount":    { "currency_code": "USD", "value": "86.02"  }
}
```

## What the three fixtures establish

**`paypal_fee` is present on a capture.** Phase 0 marked it `absent` and
flagged the verdict as "potentially false of production". It was an artefact of
card-funded, first-party captures. The verdict is corrected in
`specs/paypal-fields.md`.

**`net = gross - paypal_fee - platform_fees`**, exactly, in all three:
`100.00 - 3.98 - 10.00 = 86.02`. Phase 0 refused to derive `paypal_fee` from
`gross - net` because the formula was undocumented and `platform_fees` was also
absent, leaving two unknowns. All three terms are now observed and the identity
holds — so no derivation is needed, and refusing to guess it was right.

**The platform fee is taken at capture, in both disbursement modes.** The
`DELAYED` capture carries an identical `platform_fees` and `net_amount` to the
`INSTANT` one. PayPal documents this nowhere; two orders differing only in
`disbursement_mode` settled it.

**The fee did not vary across the three, and `paypal_fee` is present on a `GET`
for each** — not only in the capture response. All three returned `3.98`.

One qualification, because it is an inference rather than an observation.
**PayPal's response does not record which funding instrument was used.** All
three orders carry an identical `payment_source.paypal` block — email,
`account_id`, `account_status: VERIFIED`, name, country — with nothing
distinguishing a balance payment from a card inside the wallet. That two of
these were card-funded and one was balance-funded is known only because the
person approving them reported it.

So the supportable claim is narrow: **three wallet-funded captures of the same
amount and currency produced the same fee.** "The fee does not depend on the
funding instrument" is a reasonable reading but is not established, and nothing
in the canonical model may branch on funding instrument, because the API does
not expose it.

**A `DELAYED` capture reports `status: COMPLETED` while the seller has not been
paid.** The two captures are structurally identical apart from
`disbursement_mode`. Capture status cannot be used to infer that a seller was
disbursed, which is the false-positive trap `multiparty-direction.md` §4
predicted, now evidenced.

**Buyer identity lives on the order, not the capture.** The order carries
`payer` with `address`, `email_address`, `name`, `payer_id`, and the capture
response carries `payment_source.paypal` including `account_status: VERIFIED`.
`GET /v2/payments/captures/{id}` returns neither. Phase 7 must read the order
for buyer identity — the same read-path lesson Phase 0 learned for
`platform_fees` on refunds.

**`seller_protection` is `ELIGIBLE`** with `dispute_categories`
`["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"]`. Phase 0's card captures
were `NOT_ELIGIBLE` with no categories, so this also signals that the
transaction is disputable.

## Not yet created

| Fixture | Blocks | Why not |
|---|---|---|
| A dispute | 10 `unanswered` dispute rows | Needs the buyer to file one through the sandbox Resolution Center. The API route refused Phase 0's card captures as `TRANSACTION_NOT_ELIGIBLE`; these wallet captures are eligible, so it should now succeed |
| A `DELAYED` release | the disbursement stage, and the held-funds records | `GET /v1/payments/referenced-payouts-items?reference_id=` returns `403 FORBIDDEN` even with partner scopes. The release `POST` moves money and is not attempted without a decision |
| A cross-currency multiparty capture | FX under a seller payee | Not yet attempted |
| A refund with a platform fee reversal | whether commission reversal is automatic or instructed | Not yet attempted |

## Re-running the probe against these fixtures

```bash
uv run scripts/probe_paypal.py --profile platform --as-seller env \
  --record-type captures --id 10X728766N112511N \
  --record-type captures --id 5JS430140P0246744 \
  --record-type captures --id 48L03973A5528824C \
  --record-type orders   --id 4NP885630E9766607 \
  --record-type orders   --id 9PV67634T4050864W \
  --record-type orders   --id 03Y10939VN407604U \
  --out multiparty.raw.json
```

Phase 0's fixtures need `--profile merchant` instead; they answer `401` to the
platform without an assertion.
