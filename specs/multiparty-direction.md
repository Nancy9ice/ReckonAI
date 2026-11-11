# Product direction — PayPal Multiparty, and what it changes

**Status:** direction decided 2026-11-06. **Spec amendments applied 2026-11-07**
— see §6 for what landed and what is still waiting. **Verification is still
outstanding:** [[roadmap]] Phase 0b, scoped in §8, and nothing in §8.2 may be
designed around until it runs.

The direction was first recorded on the Phase 0 branch and deliberately not
acted on there, because
`2026-11-05-paypal-field-discovery/validation.md` §5 requires a canonical-model
amendment to be carried forward rather than applied inside the phase that found
it. Phase 0 is now merged and the amendments have been made on their own branch.

---

## 1. The decision

ReckonAI targets marketplaces on **PayPal Complete Payments Platform for
Marketplaces and Platforms** — PayPal's multiparty model — and must support
**both** disbursement modes, `INSTANT` and `DELAYED`.

The rejected alternative was the first-party merchant-of-record model, where the
marketplace collects into its own account and disburses to sellers through the
Payouts API. That is a real marketplace architecture, and it is the one Phase 0's
fixtures exercised. But this product is built for the PayPal product
specifically, and multiparty is PayPal's marketplace offering. Where `product_idea.md`
describes the merchant-of-record model, the readme is what is wrong.

**This is Phase 0 succeeding, not failing.** `plan.md` Group 8 step 2 reserves a
slot for precisely this finding: "state plainly whether the canonical model
survives contact with the real API, or needs amendment. If it needs amendment,
say so here." Phase 0 ran first so that this would surface before Phases 2 and 3
built on it. It surfaced.

## 2. Responsibility boundary

The change is one of responsibility, not of scope.

| Party | Owns |
|---|---|
| **PayPal** | Processes payments, routes each seller's share, **remits the marketplace's platform fee to the marketplace**, executes refunds and disbursements, provides lifecycle information |
| **Marketplace** | Orders, sellers, contractual commission rules, the internal seller ledger, its own financial records |
| **ReckonAI** | Reconciles the two, identifies inconsistencies, investigates exceptions, recommends corrective actions |

PayPal calls this arrangement a **connected integration**, and it is the phrase
its documentation keys liability and capability statements to. Two of those
statements constrain what we may conclude:

- **Dispute and chargeback liability sits with the seller**, not the
  marketplace. Whether the marketplace is exposed is governed by its own seller
  terms, so any exposure we report rests on a marketplace rule and is never
  inferred from a PayPal record alone.
- **The platform fee is remitted, not merely collected.** It reaches the
  marketplace as a single next-day consolidated settlement into a bank account,
  not per transaction and not as a PayPal balance. [[mission]] §7 splits the
  resulting checks: the per-transaction fee comparison is in scope, the
  aggregate settlement reconciliation is deferred.

**None of PayPal's money movement is duplicated.** That was already the rule —
[[mission]] P6, "Nothing moves money" — so nothing has to be removed. No payment
processing, payout execution or refund execution was ever built: at the time of
this decision the repository holds four Phase 0 probe scripts and the specs, and
no application code. The direction changed at the cheapest possible moment.

## 3. The two disbursement modes

| Mode | Money flow | PayPal evidence to retrieve | Reconciliation validation |
|---|---|---|---|
| `INSTANT` | Seller receives funds at capture | Capture and seller-level transaction records | Compare expected seller entitlement against the actual seller transaction amount and status |
| `DELAYED` | Payment captured; seller funds held until release, or until PayPal releases them automatically after **28 days** | Capture, the delayed-disbursement indicator, the referenced payout and the disbursement status | First validate expected entitlement against the captured transaction. **Then** validate the release instruction and the actual disbursement |

**Entitlement validation is mode-independent.** It runs against the captured
transaction in both modes. `DELAYED` adds a second stage rather than replacing
the first, so the engine carries one entitlement path plus a conditional
disbursement stage.

`disbursement_mode` is already in the declared contract Phase 0 extracted — enum
`INSTANT` / `DELAYED`, on `CapturedPayment` and on
`purchase_units[].payment_instruction` — and is unobserved in every Phase 0
fixture. See `specs/paypal-fields.md`.

## 4. The lifecycle, and the false-positive rule

Five states, of which **2 through 5 are non-error states**:

1. Payment captured.
2. Seller funds held by PayPal.
3. Disbursement requested by the marketplace.
4. Disbursement completed.
5. Disbursement failed, or still pending.

**Do not treat a delayed disbursement as an unpaid seller obligation merely
because the seller has not received the funds yet.** Held funds are the expected
intermediate state of a `DELAYED` transaction, not a marketplace failure to pay.

This is not presentational. An engine that classifies states 2 and 3 as
discrepancies turns **every** `DELAYED` transaction into an exception, breaching
the hardest exit criterion in [[roadmap]] Phase 11 — "the clean Phase 4 baseline
produces zero exceptions" — by design rather than by bug. It is the same class of
error as recomputing `gross * rate` and reporting PayPal's cent-rounding as a
discrepancy (`2026-11-05-paypal-field-discovery/notes.md`, Group 6 blast radius
2). Two distinct ways to fail one gate, both now known in advance.

These states are the provider-backed form of the non-error lifecycle states
[[mission]] P4 requires and [[roadmap]] Phase 3 must enumerate.

**State 2 has a fuse.** PayPal disburses held funds automatically after 28 days,
with no marketplace instruction. So a transaction can move from state 2 to state
4 without ever passing through state 3, and the absence of a release instruction
does not mean the funds are stuck. An engine that treats state 3 as a required
predecessor of state 4 will mis-model every auto-released transaction, and one
that reports held funds as overdue without accounting for the fuse will raise an
exception that resolves itself.

The release itself is `POST /v1/payments/referenced-payouts-items` — see §8.1.
**ReckonAI never sends it** ([[mission]] P6); it may observe that a release is
due, overdue, or approaching the 28-day fuse, and recommend one.

## 5. What reconciliation validates

1. Marketplace orders against PayPal payments and captures.
2. Expected seller entitlements against actual seller-level financial
   transactions. **Both modes.**
3. Expected marketplace commissions against PayPal partner fee records.
4. PayPal refunds against marketplace refund and seller ledger adjustments.
5. Dispute-related financial changes against marketplace records.
6. Expected seller obligations against actual PayPal disbursement records and
   statuses. **`DELAYED` only.**
7. Original transaction currencies and available FX conversion information
   against marketplace financial records.

Pending and delayed transactions are distinguished from genuine discrepancies
per §4. All financial calculation stays deterministic; the agent investigates and
explains and never computes a financial conclusion ([[mission]] P1).

**Commission validation gets stronger under this direction.** In the
merchant-of-record model the marketplace's commission was implicit — whatever it
kept — with no provider figure to check it against. Under multiparty, PayPal's
partner fee record is independent evidence, so validation 3 becomes a real
comparison. This promotes `platform_fees` from a low-priority unobserved row to a
**core MVP dependency**.

## 6. What this changes in the specs

**Applied 2026-11-07** on branch `direction/multiparty-specs`. The
contract-derived amendments are in; the access-dependent ones wait on §8.

| Document | Change | State |
|---|---|---|
| `product_idea.md` | Integration model in §2 and the status block; both modes in §1; mode-specific comparison targets in §3.2; chargeback liability in §3.3; conversion fee removed from §3.5, §5.3 and §13; two-source comparison in §6.1; fee reversal in §6.2; five new exception categories in §6.3; disbursement states in §6.4; §11.1 and §11.2 rewritten | **Applied** |
| [[mission]] | Integration model in §2; the four-system table and the new §3.1 responsibility boundary; capability 2 in §4; disbursement states under P4; the release call under P6; platform fee scope split in §7 | **Applied** |
| [[roadmap]] | Phase 0 marked complete with its first-party limit; **Phase 0b added**; Phase 3 gains `platform_fee` and `disbursement`; Phases 4 and 5 require multiparty-shaped fixtures; Phase 7 gains the client rules; Phases 10 and 11 gain the fee and false-positive criteria; budget and risks updated | **Applied** |
| [[tech-stack]] | FX rules in §2; multiparty access mechanics and client rules in §8; the SDK condition in §8.1; money envelopes in §9.3; seller attribution and derived refund linkage in §9.5 | **Applied** |
| All four | Phase *findings* removed from the root specs, which now state rules and point at `specs/paypal-fields.md` and the phase directory | **Applied** |
| `product_idea.md` §6.1, [[roadmap]] Phase 7 | Whether entitlement validation is implementable at all depends on §8 question 2 | **Waiting on Phase 0b** |
| [[roadmap]] Phase 3 | The exact records representing held funds and release depend on §8 question 3 | **Waiting on Phase 0b** |

**All three demo scenarios survive.** `product_idea.md` §13 Scenario 1 is validation 4,
Scenario 2 is validation 3, Scenario 3 is validation 7 — subject to the
established constraint that Scenario 3 must inject an incorrect conversion
**value**, never a conversion **fee**, because PayPal declares no FX fee
anywhere.

## 7. Unresolved — needs a decision

**The simulated-data boundary.** The direction permits "clearly labelled
simulated fixtures only where necessary". [[mission]] P3 and `CLAUDE.md` forbid
fabricating a financial value. These coexist only with an explicit line, and the
proposed one is:

> Simulated data may populate the demo marketplace, and may stand in for a
> PayPal endpoint our credentials cannot reach, labelled as simulated at the
> record level. A simulated value may **never** enter a reconciliation result as
> provider evidence, never be cited by the agent as a PayPal record, and never
> populate an `fx_conversion` field. Where a PayPal figure is unreachable, the
> gap is a limitation, not a fixture.

**Adopted 2026-11-11** by `specs/2026-11-11-live-ingestion/requirements.md` D4, enforced in data by a record-level simulated marker. (Originally: not adopted until confirmed.) Without it, "where necessary" is the opening P3
exists to close.

## 8. Verification still needed — Phase 0b

[[tech-stack]] §8 rule 1 — "verify fields exist before designing around them" —
applies to this decision as much as to any field. The questions below are
[[roadmap]] Phase 0b's scope.

### 8.1 Settled from documentation, 2026-11-07 — do not re-derive

Found while amending the specs. Each is documented by PayPal but **none is
observed**, so each still needs a fixture before anything depends on it.

| Mechanic | What PayPal states |
|---|---|
| `DELAYED` release | `POST /v1/payments/referenced-payouts-items`, `reference_id` set to the capture id, `reference_type: TRANSACTION_ID`. This is the disbursement record under `DELAYED` |
| Automatic release | Held funds "are automatically disbursed to the seller after 28 days" — a hard fuse that fires with no marketplace instruction |
| Fee incidence | PayPal "deducts fees from the seller's funds" |
| Platform fee settlement | Partner fees settle **once daily, consolidated**, into a bank account, not a PayPal balance. A fee-receiving API-caller account cannot hold a PayPal balance |
| Capability grant | Per seller **at onboarding**: `PARTNER_FEE` for platform fees, `DELAY_FUNDS_DISBURSEMENT` for delayed disbursement. The simpler onboarding flow excludes Platform Fee, Delayed Disbursement, Partner Reporting and the Disputes API outright |
| `PayPal-Auth-Assertion` | An unsigned JWT (`alg: none`), platform client id as `iss`, the seller's `payer_id` or `email` in the payload, trailing period |
| Dispute liability | "In connected integration, merchants assume financial liability and handle disputes and chargebacks" — the **seller** bears it, not the marketplace |
| Partner fee cost detail | Pricing Transaction report: **monthly**, SFTP, approved partners on gross-billing or revenue-share only. Carries `PRICING_FEE_TYPE: CURRENCY_CONVERSION` and FX markup columns, amounts in **integer cents** |

**One correction this produced.** An earlier reading of this direction held that
the Payouts API is not used for seller settlement under multiparty. That is
wrong: referenced payouts **are** the `DELAYED` release mechanism.

**And one narrowing.** "No FX fee exists anywhere" should read "none in the
transaction APIs". A partner-level FX markup does exist, in the monthly report.
It is PayPal's cost to the partner rather than a per-transaction conversion fee
in a seller's settlement, so the `fx_conversion.fee` verdict is unchanged — but
the claim needed bounding.

### 8.2 Open questions — **all answered 2026-11-07 by Phase 0b**

The table below is the state before Phase 0b ran, kept because it records what
was unknown and why. **Every question is now answered**; the answers and the
evidence are in `specs/2026-11-07-multiparty-field-discovery/notes.md`, and the
verdicts they produced are in `specs/paypal-fields.md`.

In brief: platform fees and delayed disbursement are both reachable; a platform
can read a seller's records with `PayPal-Auth-Assertion`; the release is
`POST /v1/payments/referenced-payouts-items` and produces a record PayPal
publishes no contract for; FX behaves normally under a seller payee except that
the conversion applies to the **net**; commission is reversed automatically on
both refunds and lost disputes; and the fee is taken **at capture** in both
modes. The one probe still outstanding — whether the fee reaches the platform
through a readable API — is a reporting lag, not a blocker.

| # | Question | Unblocks | State |
|---|---|---|---|
| 1 | Is `platform_fees` reachable in sandbox, with a seller onboarded for `PARTNER_FEE`? | validation 3 | open |
| 2 | Can the platform read a seller's capture via `PayPal-Auth-Assertion`, and what fields return? | validation 2, both modes | open — **highest risk** |
| 3 | What do the held-funds and release records actually contain, and what statuses do they carry? | validation 6, the §4 states | mechanism known (8.1), payload unobserved |
| 4 | Is the consolidated daily settlement visible through any API, or only the monthly report? | the deferred settlement check ([[mission]] §7) | open |
| 5 | How does FX behave when the payee is a seller in another currency? | validation 7 | open |
| 6 | Does a refund reverse the platform fee, and is the reversal automatic or instructed? | validation 4, `product_idea.md` §6.2 | partly answered: it is **instructed** on the refund call and PayPal must enable it. Whether it can be omitted entirely is unobserved |
| 7 | Is the platform fee taken at **capture** or at **release** under `DELAYED`? | Phase 3's fee timing, Phase 11's matching | **open, and not answerable from documentation.** PayPal states neither |
| 8 | What happens to the platform fee when a dispute or chargeback occurs? | dispute reconciliation | open; undocumented |

Question 2 carries the highest risk. Under `INSTANT` the seller's funds land in
the seller's own PayPal account, and PayPal's own wording is "seller-level
transaction records **where accessible**". If the platform cannot read them,
validation 2 is not implementable as specified and the MVP narrows to what the
platform can see of its own fees.

Questions 7 and 8 were added on 2026-11-07. Both are undocumented.

### 8.3 The blocker was the app, and it is now cleared

Probed 2026-11-07. The original sandbox app was a **merchant** app: 33 scopes,
not one of them partner, platform or referral. That single fact produced every
block we hit, and a seller account alone would not have helped — without
partner scopes the seller could not have been onboarded or charged a platform
fee.

A **business platform** sandbox account was created the same day, and its app
carries **42 scopes including four partner scopes**:
`customer/partner`, `customer/partner-referrals/readwrite`,
`partners/merchant-accounts/readwrite`, and `payments/partnerfee`.

Every previously blocked call now works:

| Call | Merchant app | Platform app |
|---|---|---|
| `POST /v2/customer/partner-referrals` | `403 NOT_AUTHORIZED` | `400 MISSING_REQUIRED_PARAMETER` — permitted |
| `GET /v1/payments/referenced-payouts-items/{id}` | `403 NOT_AUTHORIZED` | `404 INVALID_RESOURCE_ID` — permitted |
| Order carrying `platform_fees` | `422 PLATFORM_FEES_NOT_SUPPORTED` | **`201`** |
| Order carrying `disbursement_mode: DELAYED` | `422 DELAYED_DISBURSEMENT_NOT_SUPPORTED` | **`201`** |
| A real seller onboarding referral | not possible | **`201`**, returning a seller `action_url` |

**`PayPal-Auth-Assertion` was already confirmed working and enforced** on the
merchant app: asserting our own merchant id returned `200`, while a bogus payer
id, an empty one and a malformed header each returned `401`. The asserted
identity is validated rather than ignored, so the cross-seller read path exists
in principle.

**Both credential pairs are kept.** The platform app is `PAYPAL_CLIENT_ID`;
the original merchant app stays as `PAYPAL_MERCHANT_CLIENT_ID`, because Phase
0's fixtures belong to account `C24NAWELTTHXJ` and would return `404` under any
other credentials — `fixtures.md` records that happening once already, and
`validation.md` C2 re-probes those ids.

**A `201` on order creation is not evidence that capture works.** Phase 0's own
lesson applies: success at one stage says nothing about the next. Whether the
platform fee is actually taken, what the held-funds records look like, and
whether a release succeeds are all still unobserved, and are Phase 0b's work.

### 8.4 Seller onboarded, and question 2 is answered — 2026-11-07

The existing merchant account `C24NAWELTTHXJ` was onboarded as the **seller**
under platform `G2K2FVVLETKYG`. Using it rather than creating a new business
account keeps Phase 0's fixtures in the same account they were seeded in.

Two notes on getting there, both costly to rediscover:

- **`products` must be `PPCP`, and products cannot be combined.** A referral
  requesting `EXPRESS_CHECKOUT` produced an `action_url` that dead-ends at
  `Cannot GET /unifiedonboarding/entry`; `PPCP` builds the URL correctly.
  Requesting both returns `400 INVALID_PARAMETER_SYNTAX`, "Requested multiple
  products are not supported together."
- **The `action_url` is single use.**

**All six requested features were granted.** They are reported as scope URIs
under `oauth_integrations[].oauth_third_party[].scopes`, not as the feature
names used in the request, so reading the `capabilities` array alone suggests
wrongly that nothing was granted:

| Requested feature | Granted scope |
|---|---|
| `PARTNER_FEE` | `payments/partnerfee` |
| `DELAY_FUNDS_DISBURSEMENT` | `payments/delay-funds-disbursement` |
| `ADVANCED_TRANSACTIONS_SEARCH` | `reporting/search/read` |
| `ACCESS_MERCHANT_INFORMATION` | `customer/merchant-integrations/read` |
| `REFUND` | `payments/refund` |
| `PAYMENT` | `payments/payment/authcapture` |

**Question 2 is answered, affirmatively.** The decisive cross-account test, with
the control alongside it:

| Platform reads the seller's capture | Result |
|---|---|
| without `PayPal-Auth-Assertion` | `401 NOT_AUTHORIZED` |
| with the assertion for that seller | **`200`**, full capture returned |

So a platform can read a seller's records, and **entitlement validation is
implementable as specified**. This was the largest risk in the direction change
and the one that would have forced the MVP to narrow.

The integration also reports `payments_receivable: true`, `primary_currency:
USD` and `country: US`. The `country.x=NG` seen in the onboarding URL is a
locale default, not the merchant's country.

### 8.5 What remains

1. **Record the seller in the environment:** `PAYPAL_SELLER_MERCHANT_ID` is
   `C24NAWELTTHXJ`, with its primary email in `PAYPAL_SELLER_EMAIL`. These are
   the `payee` on a purchase unit and the `payer_id` claim in the assertion.
2. **Teach `probe_paypal.py` which credentials to use.** It reads
   `PAYPAL_CLIENT_ID` only, which is now the platform. Phase 0's fixtures belong
   to the seller and answer `401` to the platform unless an assertion is sent,
   so `validation.md` C2 cannot run until the probe can select a credential
   profile and attach an assertion. This is Phase 0b's first task.
3. **Questions 1, 3, 4, 5, 6, 7 and 8 remain open** and are what Phase 0b
   observes, now that the path to them exists.

Independent of all of the above, a **wallet-funded capture** still unblocks the
twelve `unanswered` rows in `specs/paypal-fields.md` and dispute creation. It
needs only a sandbox **personal** account approving an order in a browser, and
no partner scopes at all.

## 9. What Phase 0 still supports

**Phase 0 is complete and merged** (PR #1, 2026-11-07). All eight groups landed,
including the validation gate and the handoff. Most of its findings are
architecture-independent.

**Unaffected** — the entire declared column, including the finding that no
conversion structure anywhere declares a timestamp or an FX fee; the two money
envelopes; `receivable_amount` cent-rounding; refund-to-capture linkage existing
only as `links[rel=up]`; the two fields PayPal returns but does not declare;
money as strings with no floats.

**Needs re-observation under multiparty** — the observed column, captured
first-party with `payee` set to our own merchant on every order. `platform_fees`,
`disbursement_mode` and the seller-level view are the specific gaps.

**Phase 0 completed on its own terms**, and the direction change did not block
it. Its validation gate tests internal consistency — that every verdict traces
to `observed.json`, that every absent field carries a decision — and asserts
nothing about architectural coverage. Its handoff's job was to state what the
next phase must change, which is this document. Both the field table and the
handoff record that the observed column is first-party only.

Its tooling carries straight over to Phase 0b: `probe_paypal.py`,
`derive_observed.py`, `extract_declared.py`, `build_fields_doc.py` and
`check_fields_doc.py` need new endpoints and the `Auth-Assertion` header, but no
change of approach, and the declared column needs nothing at all.

---

## Related

- [[mission]] — P1, P3, P4 and P6 all govern this; §3.1 carries the boundary
- `product_idea.md` — the document this partly superseded; amendments applied, see §6
- [[roadmap]] — Phase 0b verifies this; Phases 3, 7 and 11 consume it
- [[tech-stack]] — §8 holds the access mechanics and client rules
- `specs/paypal-fields.md` — the field table, observed first-party only
- `specs/2026-11-05-paypal-field-discovery/notes.md` — how the direction change surfaced
