# Phase 0 — PayPal field discovery · Notes

> **HISTORICAL RECORD. Superseded 2026-11-07 — do not build from this.**
>
> This phase measured a **first-party** integration. The product targets
> PayPal's **multiparty** model (`specs/multiparty-direction.md`), and several
> observations below are contradicted under it — `capture.paypal_fee`,
> `payer_identity`, `platform_fee` and the dispute rows among them.
>
> The live record is **`specs/2026-11-07-multiparty-field-discovery/notes.md`**,
> which restates everything here that is still true, so you never need this
> file to build. `specs/paypal-fields.md` is generated from Phase 0b's
> artifacts, not these.
>
> This file and its artifacts are kept because they are the accurate record of
> what Phase 0 measured, and because the direction change was itself a Phase 0
> finding. Nothing reads them.

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

---

## Group 3 — the observed column, captured 2026-11-06

### What was produced, and how it is reproducible

| Artifact | Contents |
|---|---|
| `artifacts/raw/<record_type>.json` | redacted raw responses, one file per record type |
| `artifacts/observed.json` | per record type, every field path with populated counts and a sample value |

Two scripts, run in sequence. `scripts/probe_paypal.py` hits the API and writes
unredacted output **outside the working tree**; `scripts/derive_observed.py`
(added by this group) reads that output, writes the redacted copies and derives
`observed.json`. It touches no network and no credentials.

`derive_observed.py` is not in `plan.md`, and the reason it exists is worth
stating. Group 3 steps 3 and 4 require redaction and summarisation of seven
payloads into a field table. Done by hand that is precisely the
hand-transcription `validation.md` §6 item 4 makes a phase failure, and it would
have defeated `requirements.md` §3.2, which wants the observed column
machine-produced and re-runnable. It is a second disposable instrument under the
same §4 rules as the probe: a flat script, no SDK, no abstraction.

The commands are recorded in `fixtures.md` under "Re-running the probe".

### Coverage — seven record types probed, every one HTTP 200

| Record type | Samples | Terminal states sampled |
|---|---|---|
| `captures` | 2 | same-currency, cross-currency |
| `orders` | 2 | same-currency, cross-currency |
| `refunds` | 1 | partial refund |
| `payouts` | 2 | `SUCCESS` and `DENIED` |
| `disputes` | 1 (list envelope) | no dispute record exists |
| `transactions` | 1 | reporting list |
| `balances` | 1 | reporting list |

Sampling two terminal states for payouts was a direct consequence of the
"Payout fees" finding above, which showed a single state can look
self-consistent and still be meaningless. It paid off immediately — see F1.

`observed.json` carries a `required_record_types` block judging each of the five
in-scope record types of `requirements.md` §3.1 on a **witness field**, not on
whether a payload came back. The distinction matters: `GET
/v1/customer/disputes` answers `200` with `items: []`, which populates the
envelope's own `links` and would otherwise read as dispute coverage. Four of the
five are `observed`; disputes is `not_observed` with the reason recorded inline.

### Redaction, and what it deliberately does not touch

Two mechanical rules, so the split is not a per-field judgement call: a value is
an identifier if its key is `id`, ends in `_id`, or is `account_number`; a value
is PII if its key is in an explicit list, or is `name` directly under a
party-shaped container. The second clause is why redaction is path-aware rather
than key-aware. Payouts carry `items[].errors.name`, whose value is
`RECEIVER_COUNTRY_NOT_ALLOWED` — an error code and the only machine-readable
account of why a disbursement failed. A key-name rule would have destroyed it.
HATEOAS hrefs keep their route with id segments replaced by `{id}`, because the
shape of `links[rel=up]` *is* the evidence for refund-to-capture linkage.

An empty value is never redacted, so "present but empty" survives as a distinct
fact from "absent". `observed.json` counts the two separately
(`populated_on`, `present_empty_on`, `absent_on_labels`). Conflating them is how
an `absent` verdict gets manufactured out of an explicit empty, which is the
error D1 is most exposed to.

Amounts, currencies, rates, fees, timestamps, statuses and error codes are kept
in full, per `plan.md` Group 3 step 3.

### F1 — correction: `time_processed` is populated on FAILED payout items too

`fixtures.md` recorded that `transaction_id` and `time_processed` are "both
populated on a successful item and `null` on a failed one". The observed data
shows that is right about `transaction_id` and **wrong about
`time_processed`**:

| Field | Populated on | Verdict basis |
|---|---|---|
| `items[].transaction_id` | 2 of 4 items, success only | `present-but-unreliable`, condition "successfully processed item" |
| `items[].activity_id` | 2 of 4 items, success only | same condition; not previously recorded at all |
| `items[].time_processed` | **4 of 4 items, both states** | `present` |
| `items[].errors` | 2 of 4 items, failure only | `present-but-unreliable`, condition "failed item" |

`fixtures.md` has been corrected. PayPal stamps a processing time on an item it
*attempted*, whether or not the attempt succeeded, which is a different and more
useful fact: an item's timestamp cannot be used to infer that it paid out.
Nothing but running the probe over both terminal states would have caught this —
the original claim was made from the successful batch and a plausible inference.

`errors.details` and `errors.links` are present-but-empty arrays even on the
failed items, which is exactly the distinction the redaction rules preserve.

### F2 — the same refund has different fields depending on how you read it

`platform_fees` appears on the refund when read **through the order**, and not
when read from the refund endpoint:

| Read path | `seller_payable_breakdown` members |
|---|---|
| `GET /v2/payments/refunds/{id}` | `gross_amount`, `paypal_fee`, `net_amount`, `total_refunded_amount` |
| `GET /v2/checkout/orders/{id}` → `purchase_units[].payments.refunds[]` | the same four **plus `platform_fees[]`** (`USD 0.00`) |

One record, two endpoints, different field sets. This is a new class of finding
and it bears on both remaining columns:

1. **For Group 5**, field presence is a property of the *read path*, not only of
   the record type. A verdict of `absent` must name the endpoint it was observed
   against, or it is ambiguous. `platform_fees` is `present-but-unreliable` with
   the condition "on a refund read through the order, not through the refund
   endpoint".
2. **For Phase 7**, an ingestion client that reads refunds from the refund
   endpoint alone will never see `platform_fees`, and a marketplace operator's
   platform fee is not a field to lose silently. The order view is the richer
   source for refunds.

The order view is also where `amount.breakdown` appears as an empty object, and
where the embedded capture repeats `seller_receivable_breakdown` in full,
including the `exchange_rate` on the cross-currency order. So the FX evidence is
reachable by two paths, which is worth knowing if Phase 7 ingests orders rather
than captures.

### F3 — the two money envelopes, now confirmed across six record types

The earlier finding was made from one payout batch. `observed.json` records the
currency-key spelling per field path, and the split is clean:

| Spelling | Record types | Money paths |
|---|---|---|
| `currency_code` | orders, captures, refunds, transactions, balances | 26 |
| `currency` | **payouts only** | 4 |

Payouts v1 is the sole outlier, and the inconsistency is internal to one
provider. Also note `balances[].currency` is a *third* thing: a bare currency
string beside money objects that use `currency_code`, within the same response.
The Phase 6/7 adapter needs to match both spellings and must not assume a key
named `currency` is a money envelope.

### F4 — both reporting endpoints are one stale snapshot

`/v1/reporting/transactions` and `/v1/reporting/balances` returned the **same**
refresh timestamp, `2026-11-06T01:29:59Z` — earlier than every fixture
(captures 03:04, payouts 03:27) and roughly three hours before the probe ran.
So the reporting subsystem is a single periodic snapshot rather than two
independently lagging endpoints, and neither had caught up.

Consequences:

- Transaction Search still reports `total_items: 1`, the opening balance credit
  `T1900`. `balances` still shows USD 5000.00 untouched. **Reconciliation driven
  off either reporting endpoint would have reported a sandbox with no activity
  while five real transactions existed.** The v2 resource endpoints are the
  authoritative source; this confirms the earlier Transaction Search finding and
  extends it to balances.
- The hope recorded earlier — that Transaction Search might carry the capture
  fee the capture omits — is not resolved and now looks unlikely:
  `transaction_info` has **no `fee_amount` key at all** on the one indexed
  record, even with `fields=all`. Previously this was noted as
  `fee_amount: null`. Either way Transaction Search supplied no fee evidence,
  and the `paypal_fee` caveat above stands unchanged for Group 6.
- `balances` does answer the settlement currency directly and unambiguously: one
  balance, USD. That is better evidence of the settlement currency than
  inferring it from `receivable_amount` per capture.

### F5 — card-funded orders carry no payer, now machine-recorded

`observed.json` has **no `$.payer` path on orders at all**, and
`$.payment_source.card` populated on both. This confirms the inference made
while establishing the dispute blocker, and it is now recorded as observation
rather than a note.

For Group 5: if the canonical model expects payer identity on a payment
([[tech-stack]] §3), that field's verdict is conditional on funding instrument,
not on record type. For Group 6 it is a limitation with a clear statement —
PayPal supplies no buyer identity on a card-funded capture, so a marketplace
cannot be reconciled to a PayPal buyer account for those transactions.

A related oddity: the refund's `payer` block carries the **merchant's** email
and `merchant_id`, not the buyer's. `payer` on a refund is not buyer identity,
and a mapping that assumes it is will attribute the merchant as the payer.

### F6 — what Group 5 cannot get from `observed.json` alone

By construction an absent field has **no row** in `observed.json`: the probe
records what the wire carried, and a field PayPal never sent has no path to
record. `paypal_fee` and `platform_fees` on captures are absent in exactly this
way — they are simply missing from the capture payloads, not present-and-null.

So the `absent` verdicts of `specs/paypal-fields.md` cannot be produced from
`observed.json`; they come from `declared.json` minus `observed.json`, which is
Group 4's output and the C7 gap section. This is not a defect in either artifact
— it is why `plan.md` keeps the two columns in separate files and why Group 5
depends on both. Group 5 must not read "no row" as "not checked".

### Group 3 status against its exit condition

`plan.md` Group 3 verify: "`observed.json` has a non-empty entry for every
record type that Group 2 could create." Group 2 created captures, a refund and
payout batches; all three have populated entries, as do orders, transactions and
balances. Disputes, the one fixture Group 2 could not create, is recorded
`not_observed` with its reason rather than left blank or marked absent —
`validation.md` §5 is explicit that those are different facts.

Q1 and Q3 stay answered, now from committed evidence rather than a transcript:
`exchange_rate` populated on 1 of 2 captures, condition `cross-currency`, rate
`1.089477173902071` at full precision as a string.

---

## Group 4 — the declared column, captured 2026-11-06

### What was produced

`artifacts/declared.json`, 1148 field paths across all seven record types,
generated by `scripts/extract_declared.py`. Paths use the same `$` and `[]`
syntax as `observed.json`, so the two artifacts diff mechanically — which is the
whole point of the artifact and what makes the C7 gap section derivable rather
than asserted.

```bash
SDK=/tmp/sdk/PayPal-Python-Server-SDK          # outside the working tree
SPECS=/tmp/specs/paypal-rest-api-specifications

git clone --filter=blob:none --branch main \
  https://github.com/paypal/PayPal-Python-Server-SDK "$SDK"
git -C "$SDK" checkout tags/2.4.0     # b1460117a5618fb1a5b36b3ea0b1a49e124220c3
git clone --filter=blob:none --depth 1 \
  https://github.com/paypal/paypal-rest-api-specifications "$SPECS"

uv run scripts/extract_declared.py --sdk "$SDK" --openapi "$SPECS" \
  --out specs/2026-11-05-paypal-field-discovery/artifacts/declared.json
```

Re-running changes only `generated_at`. Neither clone is in the working tree,
nothing is installed, and the SDK is never imported (`requirements.md` §4).

**Both halves of the SDK have to be read, not just one.** The generated Python
classes carry `_names`, `_optionals` and `_nullables` but **no read-only
information at all**; the `doc/models/*.md` tables carry the `Read-only` tag but
name fields by their Python attribute. So optionality and the API property name
come from the classes, the read-only tag from the doc pages, and the two are
joined on the model class name.

### Amendment — disputes and payouts have a machine-readable contract after all

`plan.md` Group 4 step 3 and `requirements.md` §3.1 both say the declared shape
for disputes and payouts must come from PayPal's prose REST reference, marked as
prose-sourced because prose "can drift from the deployed API without any
signal".

**PayPal publishes official OpenAPI 3.0.3 documents for both**, in
`paypal/paypal-rest-api-specifications`, including
`openapi/customer_disputes_v1.json` (Disputes v1.11) and
`openapi/payments_payouts_batch_v1.json` (Payouts v1.9). These were used instead
of prose, pinned to commit `90e8041ffe02d80c452d2b476bedd59a8d219bdc` of
2026-04-07.

This is strictly better evidence than prose on the exact axis §3.1 objects to:
it is machine-extracted and version-pinned, so a drift is a diff rather than an
unsignalled change. It is **not** equivalent to grade 1, because it is not the
contract the SDK we tested generates, and a published spec can still lag the
deployed API — which it demonstrably does, see G1 below. So it is recorded as
its own third grade, `openapi_specification`, and every disputes and payouts row
carries it. `requirements.md` §3.1 and `plan.md` Group 4 step 3 are amended to
match; no prose reference was used anywhere in this phase.

The SDK's lack of coverage is confirmed rather than assumed: **zero** model
files matching `payout`, **zero** controller references to
`/v1/customer/disputes` or `/v1/payments/payouts`, and the single
dispute-shaped file, `dispute_category.py`, is an enum with no `_names` map and
therefore no fields.

### Keying on API property names was not a theoretical concern

`plan.md` step 4 warns that the generator renames fields and that keying on the
Python attribute name would make the two artifacts undiffable. Four distinct
renames are in the contract we extracted:

| Record type | API property path | Python attribute |
|---|---|---|
| orders | `$.payment_source.apple_pay.card.type` | `mtype` |
| orders | `$.payment_source.p24` | `p_24` |
| transactions | `...payer_info.address.line1` | `line_1` |
| transactions | `...payer_info.address.line2` | `line_2` |

Keyed on the Python name, those four rows would never have matched
`observed.json`, which is built from raw JSON. `declared.json` keys on the API
name and keeps the Python name alongside under `python_attr` with a `renamed`
flag, since Phase 7 needs it if it ever uses the SDK.

### The diff, per record type

| Record type | Declared | Observed | Both | Declared only | Observed only |
|---|---|---|---|---|---|
| captures | 69 | 43 | 43 | 26 | 0 |
| refunds | 61 | 30 | 30 | 31 | 0 |
| orders | 697 | 105 | 104 | 593 | **1** |
| payouts | 85 | 56 | 54 | 31 | **2** |
| disputes | 31 | 7 | 7 | 24 | 0 |
| transactions | 188 | 43 | 42 | 146 | **1** |
| balances | 17 | 16 | 16 | 1 | 0 |

The declared-only counts are large for orders and transactions because the
contract enumerates every alternative payment method and every optional
reporting block. Group 5's gap section should not list all 593 order paths; the
meaningful subset is declared fields whose **parent object was observed**, which
is a join Group 5 can compute from these two files. Noted here so it is a
decision rather than an oversight.

### G1 — PayPal returns fields its own contract does not declare

Three, and this is the direct answer to Q2:

| Record type | Observed path | Sample | Declared? |
|---|---|---|---|
| orders | `$.purchase_units[].supplementary_data.risk.risk_level_indicator` | `HIGH_RISK` | **no** |
| payouts | `$.items[].links[].encType` | `application/json` | **no** |
| payouts, transactions | `$.links[].encType` | `application/json` | **no** |

`risk_level_indicator` is the sharper of the two. The SDK *does* declare
`supplementary_data.risk` as a `RiskSupplementaryData` object — but that model
contains only `customer.ip_address`. So the gap is a missing member inside a
declared object, not a missing object, which is the kind of divergence a
deserializer hides: APIMatic drops unknown keys into nothing by default, so code
built on the SDK model would never see `risk_level_indicator` and nothing would
error.

`encType` is undeclared in both source grades — absent from the SDK's
`LinkDescription` and from the OpenAPI link-description schemas — yet PayPal
sends it on every Payouts v1 and Reporting v1 link. Two independent published
contracts both omit a field the API actually returns.

**Q2 is therefore answered: no, not exactly.** The SDK contract at tag 2.4.0 and
the sandbox API agree on every field we observed except these three, and the
disagreement runs in the direction that matters least for this phase (extra
data, not missing data) but most for Phase 7's choice of client: a generated
deserializer silently discards what the contract does not declare.
`requirements.md` §4 leaves that choice to Phase 7; this is the evidence it
should weigh. It also vindicates the Group 1 rule against putting a
deserializer between the wire and the artifact — had the probe used the SDK,
all three of these fields would have been invisible and `observed.json` would
have been quietly wrong.

In the other direction, PayPal's **own** OpenAPI document declares
`$.total_items` and `$.total_pages` on a payout batch, and the API returned
neither. The published spec overstates the payload.

### G2 — FX evidence is declared in four places, and we observed one

This is the most consequential finding of the group. Every declared conversion
site in PayPal's contract:

| Record type | Declared path | Observed? |
|---|---|---|
| captures | `$.seller_receivable_breakdown.exchange_rate` | **yes**, cross-currency only |
| orders | `...payments.captures[].seller_receivable_breakdown.exchange_rate` | **yes**, cross-currency only |
| refunds | `$.seller_payable_breakdown.net_amount_breakdown[].exchange_rate` | **no** |
| payouts | `$.items[].currency_conversion.exchange_rate` | **no** |

Two were unobservable from the fixtures Group 2 created, and for a reason worth
stating plainly: **the refund and the payouts were same-currency.** The partial
refund was taken against the USD capture, not the EUR one, and both payout
batches were USD to USD. So these are not absences in the API — they are gaps in
our fixture coverage, and `plan.md`'s own warning that Group 2 is the only group
that can fail in an interesting way applies more broadly than to captures alone.

Consequences:

- For Group 5, `refunds.net_amount_breakdown` and
  `payouts.items[].currency_conversion` are **not `absent`**. They are
  declared-but-not-observed with a known cause, in the same category as the
  dispute rows: `validation.md` §5 is explicit that "unanswered" and "absent"
  are different facts.
- `refunds.seller_payable_breakdown.net_amount_breakdown` is an **array** of
  conversion entries, where a capture carries a single `exchange_rate` object.
  A refund can therefore declare more than one conversion. Phase 3's canonical
  model should not assume one conversion per record.
- Phase 7 and Phase 11 gain a second and third place to look for conversion
  evidence. A cross-currency refund and a cross-currency payout batch would
  settle both rows, and both are cheap to create now that the account processes
  cards. Recommended before Phase 11, not required for this phase.

### G3 — no declared conversion record anywhere carries a timestamp

`requirements.md` §7 settled this for the SDK's `ExchangeRate`. It now holds
across **all four** declared conversion sites and **both** source grades: no
field whose name contains `time`, `date`, `timestamp` or `_at` exists inside any
conversion structure in PayPal's published contract. The payouts
`currency_conversion` adds `from_amount` and `to_amount` but, like
`ExchangeRate`, no temporal field and no conversion-fee field.

So `fx_conversion.timestamp` has **no declared source in PayPal's contract at
all**, not merely none in the SDK. No sandbox run can produce one, and no
further probing can change the answer. Under [[mission]] P3 the only honest
outcomes are a labelled `is_derived` value or a stated limitation, and Group 6
can now decide that on settled evidence rather than on one model's shape.

The same holds for a distinct FX conversion fee. `paypal_fee` and
`paypal_fee_in_receivable_currency` are declared on a capture's
`seller_receivable_breakdown`, but neither is declared *as* the fee for the
conversion, and the payouts conversion structure declares no fee at all.
Treating either as an FX fee remains the substitution P3 forbids.

### G4 — the money-envelope split is declared, not just observed

F3 recorded that Payouts v1 spells the currency key `currency` while every other
API uses `currency_code`. The contract declares that split: payouts'
`from_amount.currency` and `batch_header.amount.currency` against captures'
`amount.currency_code`. So it is deliberate on PayPal's side and permanent, not
a sandbox quirk, and the Phase 6/7 adapter must handle both spellings as a
contract-level requirement.

### Group 4 status against its exit condition

`plan.md` Group 4 verify: "`declared.json` covers every record type in
`observed.json`, carries a provenance block, and is keyed on API property
names."

- All seven record types in `observed.json` have a declared entry.
- The provenance block names the APIMatic plugin and its actual role, the SDK
  repository with tag `2.4.0` and commit
  `b1460117a5618fb1a5b36b3ea0b1a49e124220c3`, the OpenAPI repository with commit
  `90e8041ffe02d80c452d2b476bedd59a8d219bdc`, which files were read from each,
  and the source grade per record type.
- Keys are API property names, with the four renames above proving the
  distinction is load-bearing.

One incidental re-verification: `float` appears **zero** times in all 373
`doc/models` pages and zero times across the 369 model modules, so
`requirements.md` §7's claim about the money rules holds at the tag we pinned.

---

## Group 5 — the field availability table, 2026-11-06

### What was produced

`specs/paypal-fields.md`, the phase's primary output: 73 canonical field rows
across seven entities, a declared-but-not-observed section of 108 entries, and
a reverse gap section. Generated by `scripts/build_fields_doc.py` from the two
artifacts; re-running is byte-identical.

```bash
uv run scripts/build_fields_doc.py \
  --artifacts specs/2026-11-05-paypal-field-discovery/artifacts \
  --out specs/paypal-fields.md
```

**No verdict in that document was typed by hand.** The script holds the mapping
from canonical field to PayPal path, which is judgement and is versioned for
review; every verdict, count and condition is computed from `observed.json`. C6
asks that no field be marked `present` without observed support, and generating
the table is what makes that true by construction instead of by inspection.

| Verdict | Rows |
|---|---|
| `present` | 47 |
| `present-but-unreliable` | 9 |
| `absent` | 6 |
| `unanswered` | 11 |

**Superseded the same day.** `capture.platform_fee` was reclassified from
`absent` to `unanswered` during Group 6, making the live counts 47 / 9 / 5 / 12.
See "The migration guide, and what it actually settled". The table above is left
as the Group 5 record; `specs/paypal-fields.md` is authoritative.

### The canonical field set had to be derived, and this is the basis

`requirements.md` §7 says the canonical model's PayPal-sourced fields come from
[[tech-stack]] §3 and [[roadmap]] Phase 3. **Neither enumerates them.**
`tech-stack` §3 is the backend stack, §9 is the mapping mechanism, and roadmap
Phase 3 names the tables but lists fields only for `fx_conversion` — because
Phase 3 is the phase that designs them, informed by this one.

So the field set in the table is derived, and from the specs rather than from
taste:

- the six entities named in `requirements.md` §7 and roadmap Phase 3;
- per entity, the "fields of interest" that `requirements.md` §3.1 itself
  enumerates — ids, amounts, currencies, fees for captures; capture linkage and
  partial-refund shape for refunds; rate, conversion fee, settlement currency
  and conversion timestamp for settlement and FX; status, amount and lifecycle
  states for disputes; batch and item structure for payouts;
- the nine `fx_conversion` fields §7 lists;
- the Phase 3 exit criteria, which require every amount to carry a currency and
  every record to link to its raw payload.

**Phase 3 owns the final list.** Any field it adds that is not in this table has
not been checked against PayPal, and adding one is a reason to re-run this
phase's probe rather than to assume. That is stated in the table itself.

A smaller discrepancy worth recording: `validation.md` C4 asks for "all eight
`fx_conversion` fields" while §7 enumerates nine. The eight are the
PayPal-sourced ones; `is_derived` is our own provenance flag. All nine have
rows, which satisfies the check on either reading.

### The fourth verdict, and why it is not a hedge

`requirements.md` D6 allowed three verdicts. Eleven rows cannot honestly take
any of them.

Ten dispute fields have no observation because no dispute record could be
created, and `payment.payer_identity` has none because both fixtures were
card-funded. Under a literal reading of D1 those are `absent` — and `absent` is
a claim about PayPal that our evidence does not support. Publishing it would be
the exact error this file keeps warning about: a fixture gap or a permissions
gap masquerading as a finding about the API. `validation.md` §5 had already
drawn the distinction for the FX row — "those are different facts and must not
be conflated" — and had already used the word `unanswered` for it.

So D6 and C5 are amended to admit `unanswered`, constrained: it requires a
named unexercised condition and a statement of what would answer it, and it may
never be used where a record was sampled and the field was simply missing.
Every `unanswered` row in the table carries an owner-actionable next step. The
boundary is now a checkable rule for Group 7, written into C5.

### Where the line fell, case by case

The interesting work in this group was deciding `absent` against `unanswered`,
so the calls are recorded rather than left implicit.

| Field | Verdict | Why that side of the line |
|---|---|---|
| `capture.paypal_fee` | `absent` | The captures completed and settled. Nothing about the condition is known to be unexercised — `notes.md` records the cause as not established. A record that should have carried it did not. |
| `capture.platform_fee` | `absent` | Same: the capture's breakdown was returned and this member was left out. |
| `fx_conversion.timestamp` | `absent` | Not declared anywhere in PayPal's contract, across four conversion sites and both source grades. No fixture could ever produce it. |
| `fx_conversion.fee` | `absent` | As above. |
| `payment.payer_identity` | `unanswered` | Both orders were card-funded and a card-funded order carries no payer. The condition is known and nameable. |
| all ten dispute fields | `unanswered` | No dispute record exists to observe. |

`capture.paypal_fee` is the uncomfortable one and the table says so on the row.
It is `absent` under D1, and that verdict is **potentially false of
production** in a way no other `absent` in this table is. Phase 7 must not read
it as "PayPal does not return capture fees".

### The gap section is scoped, and that was a decision

`plan.md` Group 5 step 4 wants every declared-but-not-observed field to carry a
line on what its absence means. The raw difference is 1148 declared paths
against 300 observed. Writing 848 lines, most of them about `payment_source`
alternatives nested under objects that never appeared, would have buried the
findings and produced a section nobody reads.

The section therefore lists the **108** fields whose parent object *was*
observed — PayPal returned the enclosing structure and left this member out.
That is the set where an absence carries information. The full difference stays
derivable from the two artifacts, and the scoping is stated in the document so
it is a decision a reviewer can reject rather than an omission they have to
notice.

The three highest-value entries in it: `refunds.net_amount_breakdown` and
`payouts.items[].currency_conversion`, the two unobserved conversion sites from
G2, and `transactions.transaction_info.fee_amount`, which is the field that
would have answered the capture-fee question and is absent from the indexed
record entirely.

### Group 5 status against its exit condition

`plan.md` Group 5 verify: "no blank verdict cells; every field from §7 appears."

- 73 rows, every one carrying a verdict from the allowed set, verified
  mechanically against `observed.json`: no row marked `present` or
  `present-but-unreliable` without observed support, every
  `present-but-unreliable` row stating its condition, every `unanswered` row
  stating its reason, no blank detail cell.
- All six §7 entities have sections, and all nine `fx_conversion` fields have
  rows.
- Every observed count in the document is reproducible from `observed.json`,
  including the gated rows, which report `min(field, gate)` and name the gate.

One deliberate construct to flag for Group 7's checker: a few `fx_conversion`
rows are **gated**. A capture's `gross_amount` is populated on both captures,
but it is the *source amount of a conversion* only where a conversion occurred,
so those rows report the narrower count and name the gate in the row. Without
the gate the table would have claimed `present` for conversion evidence that
exists on one capture out of two. The checker must apply the same rule or it
will read a gated row as a mismatch.

---

## Group 6 — decisions for absent fields, 2026-11-06

### What was produced

Two new sections in `specs/paypal-fields.md`, generated from the same script:

- **Decisions** — all 14 `absent` and `present-but-unreliable` fields, each with
  one of four kinds: 4 `limitation`, 2 `derive`, 6 `no_action`,
  2 `connector_metadata`.
- **Pending answers** — the 12 `unanswered` fields, with an interim handling and
  the fixture that would settle each.

(Counts revised the same day; see "The migration guide, and what it actually
settled" below. `capture.platform_fee` moved from a decision to a pending
answer.)

A field left undecided now **fails the build** of the document rather than its
review: the generator exits with the offending field names. Verified by removing
a decision and confirming the refusal. That is `plan.md` Group 6's verify step
made mechanical.

### Why `unanswered` rows get handling and not decisions

`validation.md` C8 asks for a derivation or a declared limitation for every
`absent` and `present-but-unreliable` field. The `unanswered` rows are
neither. Recording a limitation for them would overstate what we know — a
limitation says the product cannot do something, and for a dispute field we do
not know that; we know we have not looked. So they get an interim handling and a
named fixture instead, in a separate section, and the distinction is stated in
the document.

### The one derivation we refused, and why that matters

`capture.paypal_fee` looks derivable as `gross_amount - net_amount`. It is not,
and settling that was the most consequential call in this group.

PayPal's contract gives **only boilerplate `Money` descriptions** for
`net_amount`, `paypal_fee` and `platform_fees` — "The currency and amount for a
financial transaction, such as a balance or payment due" — and **never states
the formula** relating them. So `net = gross - paypal_fee` is our assumption,
not PayPal's statement.

The decisive argument is simpler than the formula, and it survived the later
investigation below: **an omitted field is not an implied zero.** PayPal emits
`paypal_fee` as an explicit `0.00` on the refund of this same capture, so it
states a zero fee when it means one. Inferring `0.00` from silence would be
inventing a financial value, which is what [[mission]] P3 forbids and what P1
keeps out of the agent's hands.

The decision is a limitation: capture fees are **unknown, never zero**
([[tech-stack]] §9.6 already forbids defaulting money to zero), a fee-inclusive
entitlement check must exclude them and say so, and the row must be re-verified
against production before Phase 10 treats it as settled.

Only two derivations are authorised by this phase, and both carry `is_derived`:
`fx_conversion.timestamp` from the capture's `create_time`, and the
same-currency case of `capture.settlement_amount` from `net_amount`. No
monetary value is derived from an undocumented formula.

### Blast radius 1 — readme §13 Scenario 3 narrows, and the half that survives is the important one

Scenario 3 is *Currency conversion discrepancy*: the customer pays EUR,
settlement occurs in USD, and "the marketplace records an expected settlement
using an **incorrect conversion value or fee**".

| Scenario 3 step | Status |
|---|---|
| Customer pays in EUR | **Supported** — observed, EUR 95.50 |
| PayPal records the original transaction | **Supported** |
| Settlement occurs in USD | **Supported** — `receivable_amount` USD 104.05 |
| Marketplace records an incorrect conversion **value** | **Supported** — rate `1.089477173902071` is real provider evidence |
| Marketplace records an incorrect conversion **fee** | **Not supported** |
| Engine compares recorded conversion evidence | **Supported**, with the caveat below |
| AI explains the discrepancy | **Supported** |

**The fee half of Scenario 3 cannot be demonstrated.** There is no
PayPal-declared FX conversion fee at any of the four conversion sites, so there
is nothing to compare a marketplace's claimed conversion fee against. If the
demo injects an incorrect conversion *fee*, the engine has no provider figure to
contradict it and the agent's only honest answer is
`insufficient_evidence` — which is a correct behaviour to show but not the
exception story the scenario intends.

**The fault injected in Scenario 3 must therefore be an incorrect conversion
value or rate, not an incorrect conversion fee.** `requirements.md` §7
anticipated exactly this outcome: "If PayPal does not supply it, Scenario 3
narrows to what PayPal does supply and the gap is stated openly." It is stated
here. The readme is the product document and is **not** edited on this branch;
narrowing it is a decision for whoever builds the demo, carrying this note.

### Blast radius 2 — Phase 11's false-positive gate is the real exposure

[[roadmap]] Phase 11's exit criteria include "**The clean Phase 4 baseline
produces zero exceptions** — the false-positive gate, and the hardest criterion
in this document."

**A naive implementation fails that gate on every cross-currency transaction.**
From our own fixture, computed with `Decimal`:

```
95.50 * 1.089477173902071 = 104.04507010764778050
PayPal receivable_amount  = 104.05
residue                   =  -0.00492989235221950
```

PayPal rounds the settlement to the cent and leaves the rate unrounded. An
engine that recomputes `gross * rate` and compares it against the reported
settlement produces a half-cent discrepancy on clean data — a false positive on
every converted transaction, and the hardest criterion in the roadmap breached
by arithmetic rather than by a bug in the matching.

Four requirements for Phase 11 follow, and they are not optional:

1. **Compare against PayPal's reported `receivable_amount`**, never against a
   recomputation of gross times rate. The provider's figure is the evidence
   ([[mission]] P1); our arithmetic checks the marketplace against it, not
   PayPal against itself.
2. **Record the rounding tolerance explicitly**, as a stated decision rather
   than a constant discovered while chasing noise. The residue above is the
   worked example.
3. **Store the rate at full precision as `Decimal` from the string.** Fifteen
   decimal places were observed; rounding the rate before the comparison
   manufactures a discrepancy larger than the one being measured.
4. **Do not use the conversion timestamp as a matching key or a staleness
   signal.** It is derived from `create_time` and flagged `is_derived`; keying
   on it would let a derived value drive a financial conclusion.

Two further Phase 11 consequences from earlier groups: refund-to-capture
matching rests on a **derived** linkage parsed out of `links[rel=up]`, since no
`capture_id` field exists on a refund, and **dispute matching is unavailable
entirely** until a dispute fixture exists.

### Blast radius 3 — which `fx_conversion` fields cannot come from PayPal

`plan.md` Group 6 step 2 asks for this list explicitly. Of the nine fields
`requirements.md` §7 enumerates:

| Field | From PayPal? |
|---|---|
| `source_amount` | **Yes**, on a converted capture only |
| `source_currency` | **Yes**, on a converted capture only |
| `target_amount` | **Yes**, on a converted capture only |
| `target_currency` | **Yes**, on a converted capture only |
| `rate` | **Yes**, on a converted capture only |
| `timestamp` | **No** — derived from `create_time`, `is_derived` true |
| `fee` | **No** — no source anywhere; a declared limitation |
| `provider` | Not PayPal's to give; set by the connector |
| `is_derived` | Not PayPal's to give; our own provenance flag |

**Two of the eight PayPal-sourced fields cannot be populated from PayPal:
`timestamp` and `fee`.** The remaining five are available and real, but only
where a conversion actually occurred — which is correct, because a
same-currency transaction has no conversion and should carry no `fx_conversion`
row at all.

So [[mission]] P3 is satisfiable: we never need to substitute a public FX rate,
and the two gaps are a labelled derivation and a stated limitation rather than
an estimate dressed as evidence. The quote endpoints noted earlier as a trap
stay unused.

### The single highest-value follow-up

Eleven of the fifteen open rows, plus both unobserved conversion sites, trace
back to one missing fixture: **a wallet-funded capture**, which needs a sandbox
personal account to approve an order in a browser.

It would answer `payment.payer_identity`, unblock dispute creation and therefore
all ten dispute rows, and the same browser-approved account would allow a
cross-currency refund and a cross-currency payout to settle
`refunds.net_amount_breakdown` and `payouts.items[].currency_conversion`. One
fixture, thirteen open questions. It is recommended before Phase 11 and is not
required for this phase to merge.

### The migration guide, and what it actually settled

Prompted 2026-11-06 by a pointer to PayPal's
[v1-to-v2 payments migration guide](https://developer.paypal.com/api/rest/integration/payments-api/v1-v2-migration),
on the question of why the fee fields were missing. It named two mechanisms.
One turned out not to apply, one changed a verdict, and the exercise is worth
recording in both directions.

**Mechanism 1: `Prefer: return=representation`. Tested, and it does not
explain anything.** The guide identifies that header as the gate on financial
detail — v2 "returns minimal responses by default", and its Pitfall 3 warns
against "reading financial fields from the response body of capture, authorize,
void, or refund calls without the `Prefer` header". That is about **write**
responses. Our observations come from a `GET`, and the breakdown was present
there, only without `paypal_fee`, so the mechanism looked unlikely but not
impossible.

Tested directly rather than argued, both captures, with and without the header:

| Capture | Header | `seller_receivable_breakdown` keys |
|---|---|---|
| same-currency | none | `gross_amount`, `net_amount` |
| same-currency | `Prefer: return=representation` | `gross_amount`, `net_amount` |
| cross-currency | none | `exchange_rate`, `gross_amount`, `net_amount`, `receivable_amount` |
| cross-currency | `Prefer: return=representation` | `exchange_rate`, `gross_amount`, `net_amount`, `receivable_amount` |

**Byte-identical.** The header has no effect on a read, so the minimal-response
mechanism is ruled out as the cause of the missing `paypal_fee`. The verdict and
the decision are unchanged, but the reasoning is now tested instead of assumed,
and the `capture.paypal_fee` row says so.

The guide did sharpen that row in one way: it claims v2 gives "full fee
transparency on every capture" and maps v1's `transaction_fee` onto
`seller_receivable_breakdown.paypal_fee`. That makes our omission **anomalous
rather than expected**, which strengthens the instruction not to conclude that
PayPal withholds capture fees and to re-verify against production.

The guide does **not** document when `paypal_fee` is populated or omitted. That
question remains open and is now explicitly a production question.

**Mechanism 2: platform fees are opt-in. This changed a verdict.** The guide
notes that marketplace fee collection "requires PayPal Complete Payments
Platform approval". Confirmed in the pinned contract itself, on
`PaymentInstruction.platform_fees`:

> An array of various fees, commissions, tips, or donations. This field is only
> applicable to merchants that been enabled for PayPal Complete Payments
> Platform for Marketplaces and Platforms capability.

And it is **caller-supplied at capture time**, through
`CaptureRequest.payment_instruction`. So a platform fee exists on a capture only
when the caller instructs one and the merchant holds that capability. This
account has neither.

**`capture.platform_fee` is therefore reclassified from `absent` to
`unanswered`**, and moved from the decisions table to the pending answers. It
was never a claim we were entitled to make: the condition under which the field
could appear was simply never exercised, which is the precise definition this
phase gave `unanswered` in Group 5. Counts move from 6 `absent` / 11
`unanswered` to 5 / 12.

This also weakens a claim made earlier in this file. Group 5 and the first cut
of Group 6 treated the refund's `platform_fees: 0.00` as evidence that PayPal
"has the data and the capture endpoint does not carry it". With the capability
disabled there was no platform fee to report anywhere, so the refund's `0.00` is
a vacuous zero. It shows the field is **emitted on that read path**, not that a
real platform fee would be reported there. `refund.platform_fee` keeps its
decision — Phase 7 must still ingest refunds through the order — but the row now
records that the evidence is weak and needs a capability-enabled account.

**An incidental find worth carrying to Phase 11.** The same model declares
`payee_receivable_fx_rate_id`:

> FX identifier generated returned by PayPal to be used for payment processing
> in order to honor FX rate (for eligible integrations) to be used when amount
> is settled/received into the payee account.

So a caller **can** pin a previously quoted FX rate to a capture. That is the
legitimate route by which a quoted rate enters a transaction, and it bears on
the "exchange rate quotes are not evidence" trap recorded earlier: if a
marketplace used this field, the settlement rate would honour an earlier quote
rather than the rate at capture time. Phase 11 should not assume a reported rate
reflects the moment of capture, and an unexplained rate difference may have this
as its cause rather than an error. We did not use the field; no fixture
exercises it.

**What this says about method.** Two mechanisms from one document: one ruled out
by a two-minute experiment, one confirmed from the pinned contract and changing
a verdict. Neither was discoverable by reading the field tables alone, which is
what this phase had been doing. The `paypal_fee` row stayed where it was for a
better reason, and the `platform_fee` row should never have been `absent`.

### The direction change this uncovered

Following the platform-fee thread to its end established that `platform_fees`
exists only in PayPal's **multiparty** model, and that the model this product
was specified against — the marketplace as merchant of record, collecting
centrally and disbursing through the Payouts API — is not the model it is being
built for.

**The product now targets PayPal Complete Payments Platform for Marketplaces,
with both `INSTANT` and `DELAYED` disbursement.** That decision, what it changes
in `readme.md`, [[mission]] and [[roadmap]], the five-state disbursement
lifecycle, the false-positive rule that comes with it, and the six questions a
multiparty probe must answer are all recorded in **`specs/multiparty-direction.md`**.

It is recorded there and **not applied**, because `validation.md` §5 forbids
amending the canonical model on this branch. It is also the answer to `plan.md`
Group 8 step 2, in the strongest form that step allows for: the canonical model
does not survive contact with the real API unchanged, and neither does the
product definition above it. Phase 0 ran first so this would surface before
Phases 2 and 3 built on it.

Everything in Groups 1 to 6 still stands; the declared column is
architecture-independent and so are most of the observed findings. What needs
re-observation is listed in `multiparty-direction.md` §9.

---

## Group 7 — validation checks, 2026-11-07

### What was produced

`scripts/check_fields_doc.py`, implementing C2 through C9 of `validation.md`.
All pass, including C1 (`probe_paypal.py --help` with no `pyproject.toml`).

```bash
uv run scripts/check_fields_doc.py --all
uv run scripts/check_fields_doc.py --reproduce /path/to/recheck.json
```

**Each check was verified able to fail.** A gate that cannot fail certifies
nothing, so twelve fault classes were injected into copies of the document and
artifacts, and every one was detected: a stripped column (C3), a deleted
canonical row (C4), an unreliable row with its condition removed and an
`unanswered` row with its pending entry removed (C5), a verdict upgraded past
its evidence and a tampered observed count (C6), a deleted gap row (C7), a
missing decision and a `derive` decision not flagged `is_derived` (C8), a
removed provenance block and a field keyed on the Python attribute name (C8a),
and an unredacted email plus a JSON float (C9).

One miss on the first run turned out to be a bug in the test, not the checker —
the mutation regex omitted the entity prefix the pending table uses, so it
silently changed nothing. Fixed by asserting that every mutation actually
applies, which is the lesson: a negative test that does not verify its own
premise is worth nothing either.

Presence parsing in the checker is a deliberate second implementation rather
than an import of `derive_observed.py`, so a bug in the deriver cannot hide
behind the checker agreeing with it.

### C2 found that PayPal's responses change over time

The first run of C2 failed, and it was right to. Re-probing the same fixtures a
day later gave different presence in **both** directions:

| Record type | Path | 2026-11-06 | 2026-11-07 |
|---|---|---|---|
| captures | `$.network_transaction_reference.*` | populated | **gone** |
| orders | `$.purchase_units[].supplementary_data.risk.risk_level_indicator` | populated | **gone** |
| transactions | `cart_info`, `shipping_info`, `payer_info.payer_name`, and 39 more | absent | **populated** |

**Two fields that were observed are no longer returned for the same records.**
One of them, `risk_level_indicator`, was the headline of G1 — a field PayPal
returns that neither of its published contracts declares. It is now also a field
that PayPal stops returning. A single observation establishes that a field *can*
arrive, never that it will.

`observed.json` now aggregates **both sessions**, with sample labels carrying an
`@date` suffix, so those fields read `1/4` and `2/4` with the condition naming
the session that saw them. That is the honest verdict for a field that comes and
goes, and it preserves the day-one evidence instead of overwriting it.

C2 itself was refined, because with multi-session evidence a field the artifact
already records as varying will differ in any single re-probe by definition. It
now fails only on a real regression — a path recorded populated on *every*
sample that is now absent, or one recorded never populated that is now
populated — and reports the varying paths rather than flagging them. See the
`validation.md` C2 refinement.

### Transaction Search caught up, and it carries a ledger view the v2 endpoints do not

The reporting snapshot moved from `2026-11-06T01:29:59Z` to
`2026-11-07T00:59:59Z` and from 1 transaction to 13. So the lag recorded in F4
is real but bounded: **roughly a day, not unbounded.** Phase 8's backfill and
Phase 9's event ingestion can treat reporting as eventually consistent with a
day-scale horizon, and the v2 resource endpoints as authoritative for anything
recent.

What it revealed is more valuable than the timing.

**1. A currency conversion is a pair of provider ledger events.** The
cross-currency capture produced two `T0200` entries, linked to it by
`paypal_reference_id`:

| Event | Amount | `transaction_initiation_date` |
|---|---|---|
| `T0200` | EUR `-95.50` | `2026-11-06T03:04:17Z` |
| `T0200` | USD `104.05` | `2026-11-06T03:04:17Z` |

Double-entry, with the source debited and the target credited, and **each
carries its own timestamp**.

**This reopens `fx_conversion.timestamp`.** G3 established that no conversion
*structure* in PayPal's contract declares a temporal field, and that remains
true of every v2 resource endpoint. But the reporting ledger stamps the
conversion *event*, which is provider evidence rather than a derivation. The
decision changes from "derive from the capture's `create_time`" to "read the
`T0200` event's `transaction_initiation_date`, falling back to a derived value
flagged `is_derived` where reporting has not caught up". The canonical field
will carry a provider value sometimes and a derived one otherwise — which is
precisely the distinction `is_derived` exists to record.

One honest caveat: on this fixture the `T0200` date is `03:04:17Z`, identical to
the capture's `create_time`. So we cannot yet show the two ever differ, only
that one is stamped on the conversion and the other is borrowed from the
capture. Under [[mission]] P3 that distinction is the whole point.

**2. The fee questions are now independently corroborated.** Transaction Search
reports `fee_amount` where a fee exists and omits it where none was charged, and
it does both within one response:

| Event | What it is | `fee_amount` |
|---|---|---|
| `T0005` | the two captures | `null` |
| `T0200` | the two conversion legs | `null` |
| `T0001` | the two payout items | `-0.25` each |

So the reporting API demonstrably reports fees, and reported none for the
captures or the conversion. That makes "no fee was assessed in this sandbox" far
more likely than "PayPal withholds capture fees", and it turns
`fx_conversion.fee` from "not declared anywhere" into "not declared **and**
not charged". Both decisions were updated to cite it. Neither verdict changed,
and both remain sandbox-only.

**3. Reporting links back to the v2 resources.** The `T0005` event's
`transaction_id` **is** the capture id, and the `T0200` events carry the capture
id as `paypal_reference_id` with `paypal_reference_id_type: TXN`. So the ledger
view and the resource view are joinable on provider-supplied identifiers, which
Phase 7 needs and which no amount of reading the contract would have shown.

### For Phase 0b and Phase 3

The reporting ledger deserves canonical modelling in its own right. It is the
only source observed to carry a conversion timestamp, a conversion as discrete
signed entries, and a per-event fee — and under the multiparty direction it is
also the most likely place to find platform fee and disbursement events. It was
treated in Groups 1 to 6 as a secondary, stale source; that was correct at the
time and is no longer.

---

# Handoff — Group 8, 2026-11-07

**Read this section alone and you have what Phases 3, 7 and 11 need.** Everything
above is the working record; this is the conclusion.

## The headline: the canonical model needs amendment, and so does the product definition

`plan.md` Group 8 step 2 asks plainly whether the canonical model survives
contact with the real API. **It does not, on two levels.**

1. **The product was specified for the wrong PayPal integration model.** The
   specs describe a marketplace as merchant of record — collecting centrally and
   disbursing to sellers through the Payouts API. The product targets PayPal's
   multiparty platform, where PayPal splits funds at capture and the marketplace
   takes a platform fee. The full decision, the two disbursement modes, the
   five-state disbursement lifecycle and the amendments required to `readme.md`,
   [[mission]] and [[roadmap]] are in **`specs/multiparty-direction.md`**. Read it
   before Phase 3.
2. **The money rules survive untouched.** `float` appears zero times across all
   373 generated model pages and all 369 model modules; every monetary field is
   `Money{currency_code, value}` with the value a string. [[tech-stack]] §2 needs
   no change.

Phase 0 ran first so that (1) would surface before Phases 2 and 3 built on it.
It surfaced. That is this phase succeeding.

## For Phase 3 — canonical event tables

**Money.** Amounts arrive as strings carrying their own currency. Parse straight
to `Decimal`, store `NUMERIC`, never let a `float` exist. Unchanged from
[[tech-stack]] §2.

**One money shape, two provider spellings.** Every API uses
`{currency_code, value}` except **Payouts v1**, which uses `{currency, value}`.
A third trap: `balances[].currency` is a bare currency string sitting beside
money objects that use `currency_code`. The adapter must match both spellings
and must not assume a key named `currency` is a money envelope.

**`fx_conversion`.** Five of the eight provider-sourced fields are real
(`source_amount`, `source_currency`, `target_amount`, `target_currency`, `rate`)
and exist **only where a conversion occurred**. Create a row only when PayPal
reports an `exchange_rate`, so an empty row can never be mistaken for a
conversion with missing fields. `timestamp` comes from the reporting ledger's
`T0200` event or is derived from the capture's `create_time` with `is_derived`
true — so the table must record which. `fee` has no source at all. The rate
carries **15 decimal places**; store it at full precision.

**A conversion can be more than one record.** A refund declares
`seller_payable_breakdown.net_amount_breakdown[]` — an **array** of conversion
entries, where a capture carries a single `exchange_rate` object. Do not model
one conversion per record.

**Refunds are not captures with a sign flipped.** A capture carries
`seller_receivable_breakdown`; a refund carries `seller_payable_breakdown`, with
`total_refunded_amount` and no `receivable_amount`. There is **no `capture_id`
field on a refund** — the only link back is `links[rel=up]`, whose href must be
parsed. Store that linkage as derived, never as a provider-supplied foreign key.

**Payout items.** `time_processed` is populated on **failed** items too, so it
proves an attempt and not a payment. `transaction_id` and `activity_id` appear
only on success. `errors` appears only on failure and is **account-dependent** —
a different sandbox account denied identical requests with it empty. Make all of
these nullable.

**Lifecycle states.** The five disbursement states in
`multiparty-direction.md` §4, plus PayPal's own `PENDING` and the
`disbursement_mode` enum (`INSTANT` / `DELAYED`, declared and unobserved).
States 2 through 5 are **non-error** states.

**Payer identity may not exist.** A card-funded order carries no `payer` at all,
and a refund's `payer` block carries the **merchant**, not the buyer. Do not make
payer identity required, and do not map a refund's `payer` to a buyer.

**Retain every raw payload.** Not a formality. Three fields were observed that
neither of PayPal's published contracts declares, and two of them stopped being
returned a day later. The contract is not a complete description of the payload,
and the payload is not stable.

**The reporting ledger deserves its own tables.** It is the only observed source
carrying a conversion timestamp, a conversion as discrete signed entries, and a
per-event fee, and it joins to the v2 resources on `transaction_id` and
`paypal_reference_id`.

## For Phase 7 — PayPal ingestion

**Decide about the SDK with this evidence in hand.** Three fields were observed
that neither the generated SDK contract nor PayPal's OpenAPI documents declare —
`supplementary_data.risk.risk_level_indicator` (`HIGH_RISK`, reconciliation-
relevant) and `encType` on HATEOAS links. APIMatic drops unknown keys, so an
SDK-based client would never have seen them and nothing would have errored. If
the SDK is used, retain raw payloads and diff against the model.

**Read refunds through the order.** `GET /v2/payments/refunds/{id}` omits
`platform_fees`; the same record inside `GET /v2/checkout/orders/{id}` carries
it. Field presence is a property of the **read path**, not only of the record
type.

**`Prefer: return=representation` matters on writes, not reads.** Tested: a
capture `GET` returns a byte-identical breakdown with and without it. Since
nothing in this product moves money ([[mission]] P6), it only matters if a write
is ever added.

**Treat reporting as eventually consistent, with a day-scale horizon.** Both
`/v1/reporting/*` endpoints share one snapshot; it was ~21 hours stale and then
caught up. The v2 resource endpoints are authoritative for anything recent.
Reporting is nonetheless the **only** source for conversion timestamps and
per-event fees.

**A `403` is never evidence about the API**, only about the app's permissions. A
scope added after a token is minted does not appear in that token — mint fresh
to observe a permission change. Tokens last about nine hours.

**A `5xx` is not reliably transient.** A malformed dispute payload drew
`500 INTERNAL_SERVER_ERROR` repeatably. Do not retry blindly.

**Read `items[].errors`.** It is the only machine-readable account of why a
disbursement failed. When absent, the honest answer is `insufficient_evidence`.

**Unverified for multiparty.** Whether the platform can read a seller's
transactions via `PayPal-Auth-Assertion` is **not established** and is the
highest-risk unknown in the direction change. See
`multiparty-direction.md` §8.

## For Phase 11 — matching and currency comparison

**Two ways to fail the false-positive gate, both avoidable.** The exit criterion
"the clean Phase 4 baseline produces zero exceptions" is breached by arithmetic
unless both are handled.

1. **FX rounding.** PayPal rounds the settlement to the cent and leaves the rate
   unrounded:

   ```
   95.50 * 1.089477173902071 = 104.04507010764778050
   PayPal receivable_amount  = 104.05
   residue                   =  -0.00492989235221950
   ```

   **Compare against the reported `receivable_amount`, never a recomputation of
   gross times rate.** Record the tolerance as an explicit decision.
2. **Delayed disbursement.** Held funds are the expected intermediate state of a
   `DELAYED` transaction, not an unmet obligation. Classifying states 2 and 3 as
   discrepancies makes every such transaction an exception.

**A reported rate may not be the capture-time rate.**
`payment_instruction.payee_receivable_fx_rate_id` lets a caller pin a previously
quoted rate. An unexplained rate difference may be that, not an error.

**Payout fees reconcile only on success.** On a `SUCCESS` batch,
`batch_header.fees` equals the sum of `payout_item_fee`. On a `DENIED` batch the
two disagree and the figure is **not** what would have been charged — observed
wrong by a factor of 2.6. Never carry a denied batch's fee forward as the
expected cost of a retry.

**Dispute matching is unavailable** until a dispute fixture exists.
**Refund-to-capture matching rests on a derived linkage**; an exception resting
on it must say so.

**Never substitute a public or quoted FX rate.** The app holds scopes that can
read exchange-rate *quotes*. A quote is not a conversion record, and using one
would be the substitution [[mission]] P3 forbids.

## What is still open

| Open item | Blocks | Owner |
|---|---|---|
| A sandbox personal/seller account | 12 `unanswered` rows, and all six multiparty questions | User |
| Whether the sandbox app can enable the Marketplaces capability | `platform_fees`, validation 3 | User |
| The simulated-data boundary in `multiparty-direction.md` §7 | Phases 4, 5 | User |
| `capture.paypal_fee` against production | Phase 10 fee handling | Phase 7 |
| A cross-currency refund and payout | the two unobserved conversion sites | Phase 0b |

One fixture — a **wallet-funded capture** — unblocks twelve of those rows and
dispute creation, and the same account enables the cross-currency refund and
payout. It is the highest-value follow-up this phase produces.

## Where the evidence lives

| Artifact | What it is |
|---|---|
| `specs/paypal-fields.md` | the field table: 73 canonical rows, 108-entry gap section, decisions, pending answers |
| `artifacts/observed.json` | what PayPal returned, two probe sessions, machine-derived |
| `artifacts/declared.json` | what PayPal declares, 1148 paths, pinned to SDK `2.4.0` and an OpenAPI commit |
| `artifacts/raw/` | the redacted responses behind all of it |
| `fixtures.md` | the sandbox ids, and the exact commands to re-probe |
| `specs/multiparty-direction.md` | the direction change and its consequences |

Every number in the table is reproducible from the artifacts, and
`scripts/check_fields_doc.py --all` proves it.
