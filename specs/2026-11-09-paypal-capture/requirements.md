# PayPal capture — decisions

> The replan's **R16**, which moved the capture ahead of Block B and retired
> Block A's synthetic provider side. Branch `paypal-capture`.
>
> 2026-11-08/09. Supersedes Block A's dataset: that directory's
> `validation.md` §2 describes four sellers and invented FX figures, and is
> kept as the record of what Block A did rather than amended.

## 1. Scope

Capture the demo dataset's PayPal side from the sandbox into committed
fixtures; replay them into the canonical model; re-point the marketplace seed
at the captured figures; load the result on deploy, with one live order as the
demo's opening flourish.

Out of scope: matching and classification (Block C), the entitlement engine
(Block B), the queue and Cron Job (the rest of old Block D).

## 2. Decisions

### D1. Card-funded orders, so the capture runs unattended

A PayPal-wallet order needs a buyer to approve it in a browser — Phase 0b's
fixtures list the buyer as "none; approves in a browser". Eighteen orders
would have meant eighteen manual approvals, which would have forced either a
smaller dataset or a half-synthetic one.

An order created with `payment_source.card` authorises server to server.
Verified: `4111111111111111` and `5555555555554444` capture outright;
`4032035728516179` is rejected as an invalid number.

*The cost, named:* the dataset's orders are card-funded and carry no buyer
identity at all. §4.5 already established that **nothing in the canonical
model may branch on funding instrument**, and the API does not record which
was used, so this changes no reconciliation logic. It does mean
`payment.payer_reference` is `NULL` throughout.

### D2. Two sellers, each mapped one-to-one onto a real account

The connector resolves `seller_id` from `payee.merchant_id`. A dataset seller
sharing an account with another would have every one of its orders attributed
to the wrong seller — and seller attribution is the join the whole
reconciliation rests on.

Only two sandbox accounts can process cards, so the dataset has two sellers.
`vendor-citrine` and `vendor-dunlin` are gone and their orders redistributed.

*What that costs:* dunlin existed to prove the engine does not flag a missing
platform fee for a seller never granted `PARTNER_FEE`. That case survives as
an engine unit test rather than as seeded data. It was never one of D4's two
named non-faults.

*An inconsistency this corrected:* an earlier plan onboarded a third seller
for citrine while routing dunlin's orders through aurora's account — which
produces exactly the misattribution it refused for citrine. Caught by the
operator asking why a new account was needed at all.

### D3. EUR orders to USD-settling sellers, not the reverse

A conversion needs the order's currency to differ from the payee's account
currency. The original plan used a German seller receiving USD orders. That
account's `PPCP_CUSTOM` vetting is **`NEED_MORE_DATA`** where the US accounts
are `SUBSCRIBED`, so it cannot process cards at all —
`PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING`.

Onboarding granted all six partner features and still left this: a referral
that *requests* a feature is not proof the product was vetted.

So the direction is inverted. A US marketplace taking EUR orders is at least
as realistic as the reverse, and it works unattended today.

### D4. Every fault is marketplace-side

The capture pushed eighteen **correct** orders. All six faults are errors in
the marketplace's own books, measured against PayPal's honest record — which
is the only shape available now that the provider data is real and cannot be
bent.

The single capture-side choice is F5: `ORD-0017` is deliberately not refunded
on PayPal's side, so the marketplace's refund genuinely has no provider
counterpart. That is R10's `awaiting_evidence` case rather than a
discrepancy.

Two faults changed shape as a result:
- **F3** is the marketplace recording its EUR sale in USD at a stale rate —
  262.00 against a real settlement of 294.16.
- **F6** is the ledger crediting 120.00 EUR where the entitlement is 135.00
  and PayPal released in full.

### D5. The platform fee is read from the capture-time snapshot

**A capture's `platform_fees` is not stable across a partial refund.**
Reproducible on fresh orders, and it differs by seller: `C24NAWELTTHXJ` keeps
its original fee, while `E4FUU2CX86ALC` has the capture breakdown replaced by
the *reversal* amount — ORD-0013 reports `3.00 / net 107.00` where at capture
it was `11.00 / net 99.00`. Stable on re-read minutes later, so not a race.

Reading the later value would understate what PayPal took by 8.00 and invent
a discrepancy. So each fixture stores `order_at_capture` beside the readback,
and the ingester takes the fee from the former.

Same family as §4.1, where a disputed capture loses its whole breakdown.

### D6. The payee comes from the readback, not the snapshot

The order creation response omits `purchase_units[].payee` entirely. An
ingester reading only the snapshot resolves no seller at all.

### D7. Refunds are read through the order

The refund endpoint returns `seller_payable_breakdown: null`; the order
carries it, including the automatic pro-rata fee reversal. This is the
read-path rule Phase 7 inherited, now confirmed against real responses.

### D8. A conversion row only where a rate is reported, and its source is the net

`fee` stays `NULL`: PayPal publishes no conversion fee and the `CHECK`
refuses a zero (P3). **The source is the net, not the gross** — confirmed with
real figures, `138.00 × 1.089477173902071 = 150.35`, where the gross would
give 163.42, "wrong by the whole fee".

### D9. A platform fee's identifier is synthesised, and visibly so

PayPal gives a platform fee no id — just `{amount, payee}` — but
`platform_fee.external_id` is `NOT NULL` and its unique constraint is what
makes replay idempotent. The key is `pf-{parent_id}-{index}`, stable across
runs. The prefix exists so nobody looks for it in a PayPal dashboard.

### D10. `paypal_fee` is `NULL` on a capture and a measured `0.00` on a refund

PayPal omits the field entirely on a capture and reports `"0.00"` explicitly
on a refund. Those are different claims and both are stored as given.

The engine needs neither: `net_amount` is reported directly, so the seller's
receipt is known without reconstructing a fee. Where an investigation must
*explain* the gap, `gross − platform_fee − net` is computed on the fly and
labelled derived, never persisted beside its own inputs.

### D11. `--ensure` on deploy; `--live` as a flourish that cannot fail

`preDeployCommand` runs `alembic upgrade head` then
`python -m reckonai.demo_data --ensure` — deterministic, no outbound call,
idempotent. It is there because Phase 14's dashboard is a static site that
renders what the API returns: deployed and reachable with an empty database
demonstrates nothing.

**Capturing from PayPal must never run on a deploy.** A sandbox `5xx` in a
pre-deploy command fails the deploy and takes the service down — the failure
mode R6 removed. A blueprint test enforces it.

`--live` creates one real order and ingests it through the *same*
`ingest_document` path as a fixture, and returns 0 on any failure. That is
R15's "whether or not that call succeeded", and it is the operator's chosen
shape: the live order flows to the dashboard, with the fixtures underneath.

### D12. The sim's `/releases` endpoint is removed

It served `disbursement` rows, which are PayPal's and now come from fixtures.
`mission.md` §3 does list marketplace release records — which captures it
instructed PayPal to disburse — but there is no canonical table for them, only
PayPal's `disbursement`. Serving provider data as the marketplace's would be
worse than not serving it. The field map is kept, so the endpoint returns the
moment that table exists.

## 3. Open, and deliberately not decided here

**`RATE_SCALE = 10` against a 15-decimal rate.** `fx_conversion.rate` is
`NUMERIC(20, 10)` and its own docstring claims rates are "stored at full
precision". PayPal's observed rate is `1.089477173902071`. It changes no cent
at our magnitudes — 6, 10 and 15 dp all give 150.35 — but the docstring and
the schema disagree. A one-line change plus a migration.

**`disbursement.released_at`.** The release record carries `time_updated`,
where §4.11 and Phase 3 both say no timestamp exists. Whether that is a
provider release time or a record-update time is unestablished, and writing it
on a guess is the substitution the column was built to refuse. Left `NULL`.

**Sandbox uses one fixed FX rate**, `1.089477173902071`, on every EUR order.
Convenient for determinism; it means the FX scenario rests on a constant.

## 4. What this hands forward

- **Block B** — the worked example is captured, not written: `100/10/90` and
  `60/6/54`. `validation.md` §2 has every clean figure.
- **Block C** — the provider side is real. ORD-0008's one-cent artefact is
  PayPal's own arithmetic, and the false-positive gate is measured against it.
- **Block F** — `--ensure` already runs on deploy, so debt 5 is closed.
- **Block G** — the manifest's expected classifications are unchanged; its
  impact figures are re-derived in `validation.md` §3.

## Related

- `specs/2026-11-08-replan/requirements.md` — R6, R15, R16
- `specs/2026-11-08-block-a-marketplace-connector/` — the dataset this supersedes
- `specs/2026-11-07-multiparty-field-discovery/notes.md` — §4.1, §4.5, §4.11
- `specs/paypal-fields.md` — §7, the convert-the-net rule
