# Phase 0 — PayPal field discovery · Notes

**Branch:** `phase-0/paypal-field-discovery`
Scope: `./requirements.md` · Task groups: `./plan.md` · Merge gate: `./validation.md`

---

## Group 0 — Prerequisite gate: PASSED 2026-11-05

Run on 2026-11-05. `plan.md` Group 0 is a hard stop: all four prerequisites in
`requirements.md` §5 must hold before Group 1 begins.

**Status: 3 of 4 hold.** Prerequisites 1, 3 and 4 were satisfied on 2026-11-05.
Prerequisite 2 remains open for a mechanical reason recorded below. No
implementation group has been started. Nothing has been committed.

Also records the `validation.md` M1 manual check, since M1 is the judgement call
on whether the plugin returns real context.

### Gate results

| # | Prerequisite | Result | Evidence |
|---|---|---|---|
| 1 | APIMatic Context Plugin installed and returning PayPal context | **Pass, with an amendment to what it supplies** — see "Prerequisite 1" below | Installed 2026-11-05 as `paypal@context-plugins-local` (user scope) from `github:paypaldev/server-sdk-context-plugin-preview`. Required installing Node first (none was present); Node 26.13.0 via Homebrew. |
| 2 | `paypal-sandbox` MCP server reachable | **Fail — token not in the process environment** | Credentials now exist in a local, gitignored `.env`, but the MCP server expands `${PAYPAL_SANDBOX_ACCESS_TOKEN}` from Claude Code's own process environment at startup and never reads `.env`. Root cause below. |
| 3 | Sandbox credentials available to a local script as env vars | **Met 2026-11-05** | Client id and secret both present and loadable by a local script; base URL `https://api-m.sandbox.paypal.com`. Verified without printing any value. |
| 4 | PayPal developer sandbox account with API access | **Met** | Token acquired, `expires_in` about 9 hours. **The account was replaced on 2026-11-06**; the scope counts and permission map recorded further down describe the superseded account. On the current account — merchant `C24NAWELTTHXJ`, app `APP-9C108748UB224713D` — all eight endpoint paths this phase needs are permitted, and card processing is additionally enabled, which is what made Group 2's captures possible. |

### Prerequisite 1 — installed, but it is not a field-shape oracle

This changes Group 4 and must be read before anyone starts it.

The plugin is **skills that teach an agent to use the APIMatic-generated PayPal
Server SDK**, one set per language (csharp, java, php, python, ruby,
typescript). It is not an MCP server and it answers no queries. Its own README:
"A plugin whose skills teach a coding agent to install and use the
APIMatic-generated PayPal Server SDK."

**It contains no PayPal API field names at all.** A grep across every markdown
file in the plugin for `seller_receivable_breakdown`, `paypal_fee`,
`exchange_rate`, `gross_amount`, `net_amount`, `settlement`, `purchase_units`,
`payout_batch`, `dispute_id`, `/v2/payments` and `/v1/reporting` returns **zero
matches**. The skills are deliberately generic, written with placeholders
(`{Model}`, `{optional_attr}`) and repeated instructions to "take the real names
from your SDK source".

So `requirements.md` §1 and `plan.md` Group 4 step 1 — "use the APIMatic Context
Plugin to establish the declared response shape: field names, types,
optionality" — **cannot be done from the plugin itself.** The plugin instead
names where the declared contract actually lives:

- **pip distribution `paypal-server-sdk`, version `2.4.0`**; import package
  `paypalserversdk`.
- `doc/models/*.md` — one page per model, carrying a field table, types and a
  Default column. `doc/models/containers/` covers union types.
- Each model class's `_names` (Python attribute to **API property name**),
  `_optionals` (which may be omitted), `_nullables`, and `from_dictionary`
  (the definitive answer on how a field is parsed).

**Amendment for Group 4:** the declared column comes from the generated SDK's
model documentation and model classes, with the plugin as the guide to reading
them. This is a stronger source than prose API docs, not a weaker one — it is
machine-readable and version-pinned. The requirement's intent is met; its stated
mechanism is not. `requirements.md` §3.1 should be amended to say so.

Two mechanical consequences for Group 4:

1. Key `declared.json` on the **API property name** from `_names`, not the
   Python attribute name. The generator renames fields (`mtype` for a property
   called `type`, `person_type` for `personType`), and `observed.json` comes from
   raw JSON. Keying on the Python name would make the two artifacts undiffable,
   which is exactly what `plan.md` Group 4 step 2 requires them to be.
2. The SDK does not need to become a project dependency to be read. Installing
   it into the project would pull Phase 1's scaffold forward, against
   `requirements.md` §4. Fetch it to a scratch location and read its generated
   docs there.

### M1 — the plugin test, and what it returned

`validation.md` M1 asks for a PayPal response shape and a judgement on whether a
substantive answer comes back. Run after `/reload-plugins` loaded 48 skills
(8 per language, 6 languages).

**Question put to `paypal:python-getting-started`:** "What are the field names,
types and optionality of a PayPal capture response — specifically
`seller_receivable_breakdown`, `paypal_fee` and `exchange_rate`?"

**What came back:** SDK identity, package layout, install commands, a
placeholder legend and a seven-step integration workflow. **Not one PayPal API
field name.** The skill does not address response shapes, and the loaded skill
list confirms why: all 48 skills are SDK mechanics (authentication, calling
endpoints, client initialization, configuration, error handling, getting
started, models, testing). **No PayPal API resource has a skill** — nothing for
payments, captures, refunds, disputes, payouts or FX.

**M1 verdict: the plugin returns substantive context, but not of the kind the
declared column needs.** It is a guide to reading the SDK, and on that it is
precise and useful. It is not an API-contract oracle. Judged against M1 as
written ("ask it for a PayPal response shape"), it fails; judged on whether it
returns real context rather than an error or a generic reply, it passes. M1's
wording needs amending to match what the plugin is.

The skill also supplied a trap worth recording:

> `README.md` and `doc/` ... **It is not in the installed package** —
> `site-packages/paypalserversdk/` holds the importable module and nothing else,
> so `pip install` never puts `doc/` on disk.

So `pip install paypal-server-sdk` would **not** yield the declared column.

### The declared column is obtainable, and here is exactly how

Verified 2026-11-05 by cloning the SDK to a scratch location (not the project —
see the Group 4 amendment above):

```bash
git clone --filter=blob:none --branch main \
  https://github.com/paypal/PayPal-Python-Server-SDK
git checkout tags/2.4.0
```

Tag `2.4.0` exists and matches the plugin's pinned version; `pyproject.toml` at
that tag reads `version = "2.4.0"`, `name = "paypal-server-sdk"`. Taking the tag
rather than `main` is mandatory — the skill warns the branch keeps moving after a
release and "nothing in the tree will tell you". **This is the answer to open
question Q2**: the declared contract is pinned to an SDK version, so Q2 becomes
"does SDK 2.4.0 match the sandbox API version", answerable only once
prerequisite 3 lands.

`doc/models/` holds **373 pages**, one per model, each with a field table of
`Name | Type | Tags | Description`, where Tags carries `Required`, `Optional`
and `Read-only`. That is precisely the declared shape Group 4 needs, already
machine-parseable.

### Scope gap — the SDK covers 3 of the 5 record types in scope

This is the most consequential finding so far and it changes
`requirements.md` §3.1.

Controllers present at tag 2.4.0: `orders`, `payments`, `subscriptions`,
`transaction_search`, `vault`, `o_auth_authorization`.

| Record type (`requirements.md` §3.1) | Declared source in SDK 2.4.0 |
|---|---|
| Transactions / captures | **Yes** — `orders`, `payments`, `transaction_search` |
| Refunds | **Yes** — `payments` |
| Settlement and FX | **Yes** — `seller-receivable-breakdown.md`, `exchange-rate.md`, `net-amount-breakdown-item.md` |
| Disputes | **No** — no controller. The only match in `models/` is `dispute_category.py`, an enum on orders. |
| Payouts | **No** — no controller, and **zero** models matching `payout`. |

A grep of the whole import package for `disputes` or `payouts` endpoint paths
returns nothing. **The PayPal Python Server SDK does not cover the Disputes or
Payouts APIs at all.**

Consequence: the declared column for disputes and payouts cannot come from this
SDK. It has to come from PayPal's REST API reference directly (Disputes v1,
Payouts v1), which is a different kind of source — prose documentation, not a
generated contract — and that difference should be recorded per-row in
`paypal-fields.md` rather than hidden. Group 2 still creates the fixtures and
Group 3 still captures observed data for both; only the declared half is
affected.

### FX — the declared contract already falls short of the canonical model

The central question Q1, answered on the declared side before any sandbox call.

`seller_receivable_breakdown` declares: `gross_amount` (Required),
`paypal_fee`, `paypal_fee_in_receivable_currency`, `net_amount`,
`receivable_amount`, `exchange_rate` (Optional, **Read-only**), `platform_fees`.

`ExchangeRate` declares exactly three fields, all Optional `str`:
`source_currency`, `target_currency`, `value` (the rate).

Against the eight `fx_conversion` fields in `requirements.md` §7:

| Canonical field | Declared source | Note |
|---|---|---|
| source currency | `exchange_rate.source_currency` | direct |
| target currency | `exchange_rate.target_currency` | direct |
| rate | `exchange_rate.value` | direct |
| source amount | `gross_amount` | sibling field, not part of `ExchangeRate` |
| target amount | `receivable_amount` | sibling field, not part of `ExchangeRate` |
| fee | **no direct source** | `paypal_fee` and `paypal_fee_in_receivable_currency` exist, but neither is declared as an FX *conversion* fee |
| timestamp | **not declared at all** | `ExchangeRate` carries no temporal field anywhere |
| provider | **not declared** | constant for us; derived |

So **`fx_conversion.timestamp` has no source even in the declared column**, and
no FX conversion fee is declared as such. That is a stronger result than an
observed absence: no sandbox run can change it, so these two are decided now
and Group 6 can write their decisions without waiting for credentials. Both must
be either derived-and-labelled (`is_derived`, per [[tech-stack]] §2 and
[[mission]] P3) or declared as limitations. A timestamp taken from the capture's
own `create_time` is a derivation, not provider evidence, and must be labelled
as such.

One documented condition to carry into the table as a `present-but-unreliable`
verdict with its stated condition (D5): the declared description of
`seller_receivable_breakdown` reads "This is not available for transactions that
are in pending state."

### Finding — the falsy-drop hazard is real, but does NOT touch money

The plugin's `python-models` skill describes APIMatic's generated deserializer:

> For a plain scalar optional, `from_dictionary` tests truthiness rather than key
> presence, so a present `0` or `""` lands as absent.

Confirmed in the real generated source at tag `2.4.0`
(`paypalserversdk/models/money.py`):

```python
value = dictionary.get("value") if dictionary.get("value") else None
```

An earlier draft of this note warned that a zero `paypal_fee` would be silently
dropped. **That was wrong, and the SDK source is the correction.** Every
monetary field PayPal declares is `Money`, whose `currency_code` and `value` are
both `str`. A zero fee arrives as the string `"0.00"`, which is truthy in
Python, so it survives the test. There is no zero-fee hazard. `float` appears
**zero times across all 373 declared model pages** — 104 money-typed field
occurrences, no numeric money anywhere.

That is a positive finding for [[tech-stack]] §2: PayPal declares money exactly
as our rules require it, as strings carrying their own currency code. The probe
parses those straight into `Decimal` with no float ever existing, and the
canonical model's money typing needs no amendment to accommodate PayPal.

Where the hazard **is** real: the 31 `int`-typed declared fields. The ones that
could matter are the pagination counters — `total_items`, `total_pages`, `page`,
`maximum_items`. A `total_items` of `0`, meaning a genuinely empty result set,
deserializes as absent rather than as zero. That is a Phase 7 pagination concern
(an empty page becoming indistinguishable from an unreported count), not a
financial-evidence one, and it is not this phase's problem.

**Group 1's probe still must not use the SDK**, for the unchanged reason in
`requirements.md` §4 and D2: the probe is a disposable instrument that must
report what the wire actually carried, and any deserializer between the wire and
the observed column is an unneeded interpreter of the evidence. `plan.md`
Group 1 already specifies raw `httpx`. No change needed there.

### Caveat on depending on this plugin

Its README states: "This repo holds the Server SDK Context Plugin for the
hackathon. It is not an official long term supported PayPal product and may be
removed at any time." The plugin is a convenience, not a dependency. Capturing
the declared column into the committed `declared.json` (Group 4) is what makes
this phase's output survive the plugin disappearing.

### How PayPal-specific knowledge enters the build, and where it persists

Written because this is easy to get wrong in both directions: treating the
dev-time tools as unimportant, or treating them as a runtime dependency.

**Neither tool is part of the product. Both matter to building it.** The
APIMatic plugin is development-time context for Phases 0, 7 and 9
([[tech-stack]] §8.1), not a Phase 0 throwaway. The thing that is genuinely
disposable is the probe script (`requirements.md` §4). Nothing we ship imports
either tool.

PayPal-specific knowledge reaches the build through four channels. **This phase
exists to convert the transient ones into committed artifacts:**

| Knowledge | Source | Persisted as |
|---|---|---|
| Field names, types, optionality | SDK `2.4.0`, commit `b1460117a5618fb1a5b36b3ea0b1a49e124220c3`, reached via the plugin | `artifacts/declared.json` |
| Which fields are actually populated | Live sandbox, via the probe script | `artifacts/observed.json` plus retained raw payloads |
| The gap between them, with verdicts and decisions | Synthesis of the two columns | `specs/paypal-fields.md` |
| Marketplace-to-canonical mapping | Phase 6 | Data, not code ([[tech-stack]] §9.1) |

**Not** through model recall. [[roadmap]] Phase 0 names that explicitly as the
failure mode this phase prevents: without a real contract source the phase
degenerates into "model recall and manual doc searching". Anything asserted
about PayPal that is not traceable to one of the four rows above is
unsubstantiated, whoever or whatever asserted it.

After this phase the knowledge lives in the repository. That is what makes the
plugin's possible disappearance survivable, and it is why D3 insists the
artifacts are committed rather than the table being written from memory.

### The two things called "the agent" are not the same thing

Clarified 2026-11-05 after the prerequisite 2 downgrade raised the question.

| Layer | What it is | On the runtime path |
|---|---|---|
| `paypal-sandbox` MCP server | Dev-time. Lets the *coding* agent query PayPal while writing Phases 0, 7, 9 | No |
| APIMatic Context Plugin | Dev-time. Supplies SDK and contract context while writing those phases | No |
| Phase 7 PayPal client | Hand-written, ingests into the canonical tables | Yes |
| Phase 12 investigation agent | Claude API with our own read-only tools and the Phase 10 engine as a recompute tool ([[tech-stack]] §5.1, [[roadmap]] Phase 12) | Yes |

**The investigation agent never calls PayPal.** It reads canonical records that
Phase 7 already ingested, plus retained raw payloads. Downgrading prerequisite 2
therefore costs the product nothing — the MCP server was never on the runtime
path.

**A PayPal MCP server must never be given to the investigation agent.** Not
merely unnecessary: prohibited. That toolkit exists to *perform* operations —
creating orders, refunds, payouts. Exposing it to a language model would hand it
a money-moving tool, against [[mission]] P6 and [[roadmap]] Phase 12's exit
criterion that "no tool can mutate financial state". Phase 13's approval gate is
where anything that acts belongs.

The trap to expect: during Phase 12, when the agent needs a PayPal fact that is
not in the canonical tables, reaching for a live PayPal tool will look like the
obvious fix. The correct fix is to widen Phase 7's ingestion, or to return
`insufficient_evidence` ([[mission]] P5, [[roadmap]] Phase 12). An investigation
agent that can call the provider directly is also one whose evidence trail no
longer matches the stored records it cites.

### Root cause of prerequisite 2

The plugin's MCP definition
(`~/.claude/plugins/synced/.../.mcp.json`) declares:

```json
{
  "mcpServers": {
    "paypal-sandbox": {
      "type": "sse",
      "url": "https://mcp.sandbox.paypal.com/sse",
      "headers": { "Authorization": "Bearer ${PAYPAL_SANDBOX_ACCESS_TOKEN}" }
    }
  }
}
```

`PAYPAL_SANDBOX_ACCESS_TOKEN` is unset, so the client opens the SSE stream with
an empty bearer token and the server drops the connection. This presents as
`ECONNRESET` but is an authentication failure, not a network or server fault.

Both endpoints were confirmed reachable from this machine with no credentials
sent:

- `https://mcp.sandbox.paypal.com/sse` returns `HTTP 401`
- `https://api-m.sandbox.paypal.com/v1/oauth2/token` returns `HTTP 401`

A `401` from a live TLS endpoint means DNS, routing and TLS are all fine.
**Prerequisites 2 and 3 therefore have a single shared cause: no sandbox
credentials exist in this environment.** Prerequisite 1 is independent of them.

### Group 0 step 4 — API version and base URL, as recorded

**Base URL:** `https://api-m.sandbox.paypal.com`

**There is no single sandbox API version to record, and that is the honest
answer.** PayPal versions per resource in the URL path, and returns no API
version header on a response. The version of the contract we are testing is
therefore a set, one entry per record type, and it is what the declared column
must be matched against for Q2:

| Record type | Path version |
|---|---|
| Orders | `/v2/checkout/orders` |
| Captures, refunds, authorizations | `/v2/payments/...` |
| Disputes | `/v1/customer/disputes` |
| Payouts | `/v1/payments/payouts` |
| Transaction search, balances | `/v1/reporting/...` |

### Endpoint permission map — the evidence path is open

Probed 2026-11-05 with deliberately non-existent resource ids, reading the
status code only: `404` or `422` means authenticated and permitted (the id
merely does not exist), `403` means the app lacks the permission.

| Endpoint | Status | Verdict |
|---|---|---|
| `GET /v2/payments/captures/{id}` | 404 | **Permitted** — this is the FX and settlement evidence path |
| `GET /v2/checkout/orders/{id}` | 404 | **Permitted** |
| `GET /v2/payments/refunds/{id}` | 404 | **Permitted** |
| `GET /v2/payments/authorizations/{id}` | 404 | **Permitted** |
| `GET /v1/customer/disputes` | 200 | **Permitted** — returned data |
| `GET /v1/payments/payouts/{id}` | 404 | **Permitted** |
| `GET /v1/reporting/transactions` | 200 | **Permitted** — returned data |
| `GET /v1/reporting/balances` | 200 | **Permitted** — returned data |

**All eight endpoint paths this phase needs are permitted.** Every record type
in `requirements.md` §3.1 is reachable, and so is the settlement and FX
evidence. There is no permissions-shaped gap left to confound a verdict: from
here, an `absent` verdict means PayPal did not populate the field, not that our
app could not ask.

Q1, the phase's central question, is answerable:
`seller_receivable_breakdown.exchange_rate` lives on captures, and captures are
permitted. Disputes and payouts are both permitted, which matters because §3.1
requires all five record types and the SDK declares neither of those two.

Transaction Search was initially blocked and was enabled during the gate run;
the history is recorded below under "Transaction Search", because the sequence
matters for reading the earlier probe output. `/v1/reporting/balances` gives the
settlement currency directly, which is a better source than inferring it from
`seller_receivable_breakdown` fields per capture.

### Finding — PayPal uses two different money envelopes across its APIs

Found 2026-11-05 during Group 2, by probing a real payout batch whose amounts
the probe reported as **zero** when six were plainly present.

| API | Money shape |
|---|---|
| Orders v2, Payments v2 | `{"currency_code": "USD", "value": "40.00"}` |
| **Payouts v1** | `{"currency": "USD", "value": "40.00"}` |

The key is `currency`, not `currency_code`. Same concept, different field name,
same provider. The probe's money walker matched only `currency_code`, so every
payout amount was silently missed: `money_values = 0` on a batch carrying a
65.00 total, two item amounts and two item fees.

**Consequences, in order of importance:**

1. **It is a normalization requirement for Phase 6 and 7, not a curiosity.** Any
   code that reads PayPal money by looking for `currency_code` will silently
   skip every payout amount. Silently, because the field is simply absent — no
   error, no type mismatch, just nothing found. A reconciliation engine that
   loses payout fees is wrong in exactly the way this product exists to detect.
2. **It argues for the canonical model carrying one money shape** and the
   provider adapter mapping both spellings onto it ([[tech-stack]] §9.3 already
   anticipates composite money fields needing care).
3. **It is an argument for probing rather than reading documentation.** The
   declared contract describes each API correctly in isolation; nothing in it
   says "these two disagree". The inconsistency is only visible when you hold
   two real responses side by side, which is what the observed column is for.

Fixed in `scripts/probe_paypal.py` by matching either key and recording which
one was used, so `observed.json` will carry the spelling per field path rather
than flattening the difference away.

### ANSWERED — Q1 and Q3. PayPal does supply real conversion evidence.

2026-11-06, from capture `3VG831163X0730421` (EUR 95.50, cross-currency).
This is the central question of the phase.

```json
"seller_receivable_breakdown": {
  "gross_amount":      { "currency_code": "EUR", "value": "95.50" },
  "net_amount":        { "currency_code": "EUR", "value": "95.50" },
  "receivable_amount": { "currency_code": "USD", "value": "104.05" },
  "exchange_rate": {
    "source_currency": "EUR",
    "target_currency": "USD",
    "value": "1.089477173902071"
  }
}
```

**Q3 resolved:** a cross-currency payment *can* be created in sandbox, with no
second funding source — a card-funded order in EUR against a USD-settling
account. **Q1 resolved:** the sandbox does expose settlement and FX detail, on
captures.

Consequences:

- [[mission]] P3 is satisfiable. We have a provider conversion record and never
  need to substitute a public FX rate. The quote endpoints noted below as a trap
  stay unused.
- readme §13 Scenario 3 is viable as written, not narrowed.
- The rate carries **15 decimal places** (`1.089477173902071`). It must be
  parsed as `Decimal` from the string and stored at full precision; a `float`
  would lose digits, and rounding it before the comparison in [[roadmap]]
  Phase 11 would manufacture a discrepancy.
- **The receivable is rounded; the rate is not.** Computed with `Decimal`:

  ```
  95.50 * 1.089477173902071 = 104.04507010764778050
  PayPal receivable_amount  = 104.05
  difference                =  -0.00492989235221950
  ```

  So PayPal rounds the product to the cent. **The engine must compare against
  the reported `receivable_amount`, not against its own recomputation of gross
  times rate**, or every cross-currency transaction shows a half-cent exception
  that is purely a rounding artefact. This is a concrete instance of
  [[mission]] P1: the provider's figure is the evidence, and our arithmetic is
  for checking the marketplace against it, not for second-guessing PayPal's own
  settlement line. Record the tolerance decision in Phase 11 explicitly rather
  than discovering it as noise.

### Field presence on captures — same-currency versus cross-currency

Observed across both fixtures. This is the `observed.json` content for captures
in miniature, and it is what Group 5's verdicts rest on.

| Field | Same-currency | Cross-currency | Verdict under D1 |
|---|---|---|---|
| `gross_amount` | yes | yes | `present` |
| `net_amount` | yes | yes | `present` |
| `receivable_amount` | **no** | yes | `present-but-unreliable` — only on cross-currency |
| `exchange_rate` | **no** | yes | `present-but-unreliable` — only on cross-currency |
| `paypal_fee` | **no** | **no** | `absent` |
| `paypal_fee_in_receivable_currency` | no | no | `absent` |
| `platform_fees` | no | no | `absent` |

Two of these need comment.

**`receivable_amount` and `exchange_rate` are the textbook D5 case.** The
declared contract marks both Optional; observation shows the condition exactly —
they appear when, and only when, a conversion occurred. `requirements.md` D5
anticipated this: "a field present only on cross-currency transactions is useful
information, not a failure." Group 5 must state that condition on the row.

**`paypal_fee` absent on captures is the uncomfortable finding, and it needs a
caveat Group 6 must carry.** Under D1 the verdict is `absent`, judged on
observation. But the refund fixture *does* carry `paypal_fee` (as `"0.00"`), so
PayPal clearly emits the field, with an explicit zero, when it has a value. The
likely explanation is that this sandbox assessed no fee on these captures.

That makes `absent` true of our observation and **potentially false of
production**, which is a different thing from every other `absent` in this
phase. Do not let Phase 7 infer "PayPal does not return capture fees." The
honest record is: absent in sandbox on card-funded captures, cause not
established, must be re-verified against production or a fee-bearing sandbox
transaction before the canonical model treats capture fees as unavailable. This
is the one row where our evidence is weaker than the format suggests.

### Finding — refunds use a different breakdown name, and link rather than reference

From refund `3435955072478500X`:

```json
"seller_payable_breakdown": {
  "gross_amount":          { "currency_code": "USD", "value": "30.00" },
  "paypal_fee":            { "currency_code": "USD", "value": "0.00" },
  "net_amount":            { "currency_code": "USD", "value": "30.00" },
  "total_refunded_amount": { "currency_code": "USD", "value": "30.00" }
}
```

Two structural facts that bear on Phase 3 and Phase 7:

1. **A capture carries `seller_receivable_breakdown`; a refund carries
   `seller_payable_breakdown`.** Different names for the analogous structure,
   with different members — a refund has `total_refunded_amount` and no
   `receivable_amount` or `exchange_rate`. Mapping code cannot share one path.
2. **There is no `capture_id` field on a refund.** The link back to the original
   capture exists only as a HATEOAS link:

   ```
   rel=self  /v2/payments/refunds/3435955072478500X
   rel=up    /v2/payments/captures/8RW04003P5846784X
   ```

   `requirements.md` §3.1 asks for "linkage back to the original capture" on
   refunds. The answer is: **it exists, but only by parsing a URL out of
   `links[rel=up]`.** The refund's top-level keys are `amount`, `create_time`,
   `id`, `links`, `note_to_payer`, `payer`, `seller_payable_breakdown`,
   `status`, `update_time` — no capture reference among them.

   For Phase 7 this means refund-to-capture linkage is derived from a link href,
   and `total_refunded_amount` is what makes partial-refund tracking possible
   (readme §13 Scenario 1 depends on exactly this). A derived linkage should be
   recorded as such rather than presented as a provider-supplied foreign key.

### Finding — Transaction Search is not real-time

After creating two captures and a refund, `GET /v1/reporting/transactions` over
a 28-day window still returned `total_items: 1` — only the account's opening
balance credit (`T1900`), with `fee_amount: null`. The new transactions had not
been indexed.

This matters operationally, not just for field discovery. A reconciliation run
driven off Transaction Search will not see recent activity, and the lag is
unbounded as far as we can tell from one observation. [[roadmap]] Phase 9's
event-driven ingestion and Phase 8's scheduled backfill should both treat
Transaction Search as an eventually-consistent source and the v2 resource
endpoints (`/v2/payments/captures/{id}`) as the authoritative one.

It also means the fee question above cannot be answered from Transaction Search
yet. Re-probe once the window has caught up; `fee_amount` there may carry what
the capture omits.

### RESOLVED — the payout denial, and a refuted hypothesis

The new account's payout attempt (batch `PGHX46EKCCH3Q`) denied with a
**populated** `errors` object:

```json
{"name": "RECEIVER_COUNTRY_NOT_ALLOWED",
 "message": "As the recipient's country can't receive payments, this
             transaction is not allowed.",
 "information_link": "https://developer.paypal.com/docs/api/payments.payouts-batch/#errors"}
```

**The cause is receiver-side: the recipient's country cannot receive payouts.**
The receiver, `sb-0ugzd53471943@personal.example.com`, was created under the
previous sandbox setup.

**An earlier hypothesis in this file was wrong.** On the previous account, every
payout denied with `errors` empty, and that was recorded as probably a
sender-side restriction: Payouts not enabled on the business account. The cause
is receiver-side. What settled it was not better reasoning but a different
*account* — the new merchant surfaces the error the old one withheld.

Two durable lessons from the denial, both worth more than the fixture:

- **`items[].errors` is populated when PayPal has a mapped reason, so Phase 7
  must read it.** The field is not decorative and not always empty. A client
  that ignores it discards the only machine-readable account of why a
  disbursement failed.
- **Two accounts, same request, different diagnostics.** The previous sandbox
  account denied identical requests with `errors` empty; this one names
  `RECEIVER_COUNTRY_NOT_ALLOWED`. Error verbosity is therefore
  account-dependent. An integration cannot assume the diagnostics it sees in one
  environment are what it will get in another, and a product that explains
  failures to a finance team must degrade gracefully when the reason is absent.

### What the product can and cannot say about a failed payout

Bearing directly on what the Phase 12 agent may claim.

**When `errors` is populated**, the product reports the provider's own reason and
cites it. `RECEIVER_COUNTRY_NOT_ALLOWED` is provider evidence and may be
presented as the cause.

**When `errors` is empty** — which we observed on a different account for the
same request — the product reports *that* the payout was denied, cites
`batch_status` and `transaction_status`, and stops. It does not assemble a
plausible cause from context. That is `insufficient_evidence`
([[roadmap]] Phase 12), and it is the honest answer.

The distinction matters because the failure is silent in the worst way: the
status fields are always present and always look authoritative, so there is
nothing to signal that the reason is missing except the absence of the `errors`
object itself.

Compounding this: payouts have **no declared contract either**, because the SDK
has no payouts controller (see "Scope gap" above). So the `errors` field is a
case where neither column of `paypal-fields.md` can be filled from a contract —
only from observation, and the observation differs by account.

Phase 7 should retain the raw payout payload regardless, so that a change in
what PayPal populates is detected rather than assumed away.

### Payout fees — they reconcile on success, and are unreliable on denial

Three batches, all USD 65.00 over two items (40.00 and 25.00), same request
shape:

| Batch | State | `batch_header.fees` | Sum of `payout_item_fee` | Agree? |
|---|---|---|---|---|
| `LY9LEES5KP2WN` (previous account) | `DENIED` | 0.00 | 1.30 | no |
| `PGHX46EKCCH3Q` (current account) | `DENIED` | 1.30 | 0.00 | no, inverted |
| **`55LJJ7SBMXHY6`** (current account) | **`SUCCESS`** | **0.50** | **0.50** | **yes** |

**On a batch that actually processes, the batch fee equals the sum of the item
fees.** The two fields are two views of one quantity, and they agree. The
disagreement seen earlier is an artefact of the `DENIED` state, where PayPal
populates one field or the other inconsistently — on one account the items, on
another the header.

**A second and more serious point: the fee quoted on a denied batch is not the
fee that would have been charged.** On the current account, the denied batch
reported a 1.30 total; the successful batch, for identical amounts, charged
**0.50**. So denied-batch fee figures are not predictive and must not be used as
an estimate of the cost of a retry.

Consequences for the deterministic engine (Phases 10 and 11):

- **On a successful batch**, `batch_header.fees` and `sum(payout_item_fee)`
  may be reconciled against each other, and a mismatch is a real exception.
- **On a failed batch**, do not compare them and do not treat either as the
  cost of the attempt. No fee was charged; the figures present are not a
  settlement record. Record them as raw payload and exclude them from any fee
  total.
- **Never carry a denied batch's fee forward as the expected fee of a retry.**
  Observed to be wrong by a factor of 2.6 on our own data.

Note how this was found. Two denied batches on two accounts gave two mutually
contradictory pictures, and a single account would have produced one of them
looking entirely self-consistent. It took a *successful* batch to show that
neither denied-state reading was meaningful. Group 3 should sample more than one
record per type, and more than one terminal state per type, wherever it can.

### Disputes — creatable in principle, blocked by card funding in practice

Tested 2026-11-06 against the real captures, not placeholder ids.

| Payload | Result |
|---|---|
| `buyer_transaction_id` = capture `8RW04003P5846784X` | 400 `TRANSACTION_NOT_ELIGIBLE` |
| `seller_transaction_id` = same capture | 400 `INVALID_BUYER_TRANSACTION_ID` |
| `buyer_transaction_id` = FX capture `3VG831163X0730421` | 400 `TRANSACTION_NOT_ELIGIBLE` |
| both ids supplied | 400 `TRANSACTION_NOT_ELIGIBLE` |

The endpoint requires `buyer_transaction_id`; supplying only
`seller_transaction_id` is rejected for the missing field. Supplying a real
capture id as `buyer_transaction_id` changes the error to
`TRANSACTION_NOT_ELIGIBLE` — so **the id is recognised and the refusal is on
eligibility**, not on form.

PayPal does not state the eligibility criterion. What we can observe is that
these captures were card-funded and carry **no `payer` at all**:

```
capture top-level keys: amount, create_time, final_capture, id, links, payee,
  processor_response, seller_protection, seller_receivable_breakdown, status,
  supplementary_data, update_time
order payer: null
order payment_source keys: ['card']
```

So there is no PayPal buyer account behind the transaction. A customer dispute
is a buyer-initiated instrument, and with no buyer identity on the record the
ineligibility is consistent — but the criterion is inferred from the absence of
`payer`, not stated by PayPal, and should be read that way.

**To unblock:** a wallet-funded capture, which requires a sandbox personal
account to approve the order in a browser. The same account would also unblock
the payout, whose recipient must sit in a permitted country.

**Note for Group 3**, since it bears on more than the dispute row: a card-funded
capture has no `payer`. If the canonical model expects payer identity on a
payment ([[tech-stack]] §3), that expectation does not survive a card
transaction, and the field's verdict depends on funding instrument rather than
on record type. Worth a stated condition on the row.

An earlier revision of this file claimed merchants cannot open a dispute through
the API at all. That was wrong — the endpoint exists and accepts creation. The
constraint is transaction eligibility, as recorded above.

One Phase 7 caution observed while establishing that: a partially-formed dispute
payload drew a `500 INTERNAL_SERVER_ERROR` rather than a validation message. An
ingestion client therefore cannot treat `5xx` from this API as purely transient
and safe to retry; a malformed request can produce one indefinitely.

### Trap — this app can read exchange rate QUOTES. They are not evidence.

Two scopes in the granted list:

```
https://uri.paypal.com/services/pricing/exchange-currency/read
https://uri.paypal.com/services/pricing/quote-exchange-rates/read
```

These read **prospective pricing quotes**, not records of a conversion that
happened. Populating any `fx_conversion` field from them would be precisely the
substitution [[mission]] P3 forbids: "do not estimate, infer, or substitute a
public FX rate for a provider conversion record." That a quote comes from PayPal
rather than a public feed does not make it a conversion record.

Recorded here because of when it will be tempting. If Group 3 finds
`seller_receivable_breakdown.exchange_rate` unpopulated, these endpoints will
look like a sanctioned PayPal-sourced fallback. They are not. The correct
outcome in that case is an `absent` verdict with a declared limitation, or a
clearly labelled `is_derived` value — never a quote presented as settlement
evidence. Any future use of these endpoints belongs to a different feature
(pricing display), not to reconciliation evidence.

### Prerequisite 2 reclassified as non-blocking

Decided 2026-11-05. The MCP server contributes nothing to the merge gate: E1 is
the APIMatic plugin, and every other check in `validation.md` is script- or
document-based. D4 already states that the script is the source of record and
the MCP server is for exploration. Its bearer token expires about every nine
hours and cannot be refreshed from inside a session, because the server expands
`${PAYPAL_SANDBOX_ACCESS_TOKEN}` from Claude Code's process environment at
startup. Blocking the phase on a recurring manual step that produces no
committed evidence is a cost with no return.

See the §5 amendment for why this is not the degraded path that was originally
rejected: the objection then was an empty observed column producing false
`absent` verdicts, and that objection does not apply now.

### Transaction Search — enabled, after a propagation delay

Sequence, because it affects how the earlier probe output should be read:

1. First probe: 29 scopes, no reporting scope, both `/v1/reporting/*` endpoints
   `403 NOT_AUTHORIZED`.
2. Setting enabled on the sandbox app. Re-probed immediately with a freshly
   minted token: still 29 scopes, still `403`. The enablement had not
   propagated.
3. Probed again shortly after: **30 scopes**, now including
   `https://uri.paypal.com/services/reporting/search/read`, and both endpoints
   returning `200`.

**A fresh token is required to observe a permission change.** A scope added
after a token is issued does not appear in that token's grant, so re-probing
with a cached token would have shown a false negative indefinitely. The gate
scripts mint a new token on every run for this reason.

**The lesson worth carrying to Group 3:** a `403` is not evidence about the API.
Had the observed column been captured during step 1 or 2, every Transaction
Search field would have been recorded `absent` under D1 — a true statement about
our app's permissions at that moment, and a false one about PayPal. That is a
permissions artefact masquerading as a finding, and it is the same class of
error as treating a declared field as evidence. Before Group 3 captures
anything, confirm every endpoint returns something other than `403`; the
permission map above is that confirmation.

### Token expiry, and why the probe does not use the MCP token

The minted token's `expires_in` is 32242 s, about 9 hours. The value in `.env`
will therefore go stale within a working day, and when it does the MCP server
fails with `ECONNRESET` again — identical in appearance to the original fault,
because an empty or rejected bearer makes the SSE stream drop. Re-mint rather
than debugging the connection.

This is why `plan.md` Group 1 step 2 has the probe acquire its own token from the
client id and secret, which do not expire. The probe keeps working after the
MCP server's token has died, and the committed evidence never depends on a
credential with a nine-hour life.

### Credential hygiene (the one gate item that passes)

No credential-shaped string exists in any tracked file: a `git grep` for
`client_secret`, `client_id`, `access_token` and `Bearer ` across the tracked
tree returns nothing. This satisfies the credential half of `validation.md` C9
and must stay true — the repo has **no `.gitignore`**, so one is needed before
any credential file is created alongside the working tree.

### Not recorded, because it cannot be observed

Group 0 step 4 asks for the sandbox API version and base URL. The base URL is
known from the plugin definition and PayPal's published sandbox host
(`https://api-m.sandbox.paypal.com`), but **the API version actually served to
this account has not been observed** and is deliberately left blank rather than
assumed. Writing a version number taken from documentation into the observed
record would be exactly the declared-for-observed substitution this phase exists
to prevent (`requirements.md` D1, [[mission]] P3). It is filled in on the first
successful authenticated call.

### What unblocks this phase

Owner is the user for all of it (`requirements.md` §5).

1. ~~Install an APIMatic Context Plugin exposing PayPal context.~~ **Done**
   2026-11-05. Run `/reload-plugins` or restart `claude` to load its skills into
   a session; VS Code needs a window reload. Amendment above still stands.
2. ~~Create or locate a PayPal developer sandbox app.~~ **Done** 2026-11-05.
   Credentials live in a local, gitignored `.env`. Prerequisites 3 and 4 met.
3. **Export `PAYPAL_SANDBOX_ACCESS_TOKEN` in the shell that launches `claude`**
   — still outstanding, and the only remaining gate item. Putting it in `.env`
   is not sufficient: the MCP server reads Claude Code's process environment at
   startup. Source it from a file outside the repo, then relaunch. This is
   exactly the separation `requirements.md` §5 predicted between prerequisites 2
   and 3, confirmed in practice: the script authenticates while the MCP server
   does not.
4. ~~Add a `.gitignore`.~~ **Done** 2026-11-05, with a `.env.example` template.
5. ~~Enable Transaction Search on the sandbox app.~~ **Done** 2026-11-05. All
   eight endpoint paths now permitted; see the permission map above.

Re-run the Group 0 gate after that. Groups 1 and 2 can then proceed in either
order (`plan.md` sequencing notes).

### Why the phase was held until the gate passed

Recorded so the delay is legible, and so the distinction survives into later
phases where the same temptation recurs.

`requirements.md` §5 records that this phase was deferred rather than attempted
degraded. Between the first gate run and the gate passing, the two groups that
produce the phase's actual evidence could not have run:

- Group 2 needs live sandbox access to create fixture transactions. With no
  credentials there are no fixtures, so the observed column has no subject.
- Group 3's observed column would have been empty, which under
  `requirements.md` D1 marks **every** canonical field `absent` — a false
  finding that reads identically to the real one this phase exists to detect.
  This is the specific harm the deferral avoided, and it is why an empty result
  was never acceptable as an answer.

Group 1 was writable throughout, since its verify step is only `--help` exiting
0, but was held because `plan.md` forbids starting a group before the previous
group's exit condition holds, and a probe that has never authenticated is
unverified scaffolding.

**The gate passed on 2026-11-05** with prerequisites 1, 3 and 4 met and
prerequisite 2 reclassified as non-blocking on the reasoning above. Groups 1 and
2 are both open and may run in either order.
