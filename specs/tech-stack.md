# ReckonAI — Tech Stack

> **Status:** Constitution. Locked decisions with rationale.
> Adding a dependency that is not listed here requires amending this document.
> Governing principles are in [[mission]] §5.

---

## 1. Decision summary

| Layer | Choice | Locked |
|---|---|---|
| Language (backend) | Python 3.12 | Yes |
| Package manager | uv (`pyproject.toml` + `uv.lock`) | Yes |
| API framework | FastAPI + Uvicorn | Yes |
| Validation | Pydantic v2 | Yes |
| ORM / migrations | SQLAlchemy 2.0 (typed) + Alembic | Yes |
| Database | PostgreSQL 16 | Yes |
| Background jobs | Postgres-backed queue (no Redis) | Yes |
| Scheduling | Render Cron Jobs | Yes |
| LLM | Claude Opus 5.5 (`claude-opus-5-5`) | Yes |
| LLM orchestration | Anthropic Python SDK, beta Tool Runner | Yes |
| Frontend | React 18 + TypeScript + Vite | Yes |
| Data grid | AG Grid / AG Studio | Yes |
| Payments | PayPal REST APIs + webhooks (sandbox) | Yes |
| PayPal API context | APIMatic Context Plugin (dev-time) | Yes |
| Hosting | Render | Yes |
| Tests | pytest | Yes |
| Timeline / scheduling UI | Bryntum | Optional, only if free |

---

## 2. Money representation — the most important section

This implements [[mission]] P1 and P2 and overrides any convenience elsewhere.

**Never use `float` for a monetary value, anywhere, at any layer.**

| Concern | Rule |
|---|---|
| In Python | `decimal.Decimal` only. No `float`, no `round()` on floats. |
| In Postgres | `NUMERIC(20, 6)` for amounts; `NUMERIC(20, 10)` for FX rates. |
| Over JSON | Serialize amounts as **strings**, not JSON numbers — JSON numbers are IEEE-754 doubles and will silently lose precision in the browser. |
| Shape | Every amount is a pair: `(amount: Decimal, currency: str)`. No bare amounts in any signature, column pair, or API field. |
| Rounding | Rounding happens once, at an explicitly named boundary, with a stated mode (`ROUND_HALF_UP`) and the currency's exponent. Never implicitly. |
| Comparison | Two amounts of different currencies raise an error rather than compare. |

A single `Money` value object enforces this, and arithmetic on it refuses
mixed-currency operands. Allocation of a refund across sellers uses a
largest-remainder method so the parts always sum exactly to the whole — no
rounding residue is created or discarded.

**FX:** a conversion record stores source amount/currency, target
amount/currency, rate, timestamp, fee, provider, and a reference to the source
payload. Per [[mission]] P3, a rate we computed ourselves is tagged as derived
and is never written into a field that implies PayPal supplied it.

PayPal supplies the rate and both amounts, but **no conversion fee and no
conversion timestamp**. Three rules follow:

- `fee` stays `NULL`. **Absent, never zero** — the §9.6 rule against defaulting
  money applies with particular force, and `paypal_fee` is not a conversion fee.
- `timestamp` records whether its value came from the provider or was derived.
- **Compare against PayPal's reported settlement amount; never recompute it.**
  PayPal rounds the settlement and leaves the rate unrounded, so `gross * rate`
  disagrees with the reported figure on every converted transaction. Store rates
  at full precision; `NUMERIC(20, 10)` above is the floor, not a target.

Field-level evidence: `specs/paypal-fields.md`.

---

## 3. Backend

**uv is the package manager.** Dependencies are declared in `pyproject.toml`
and pinned in a committed `uv.lock`. Every environment — local, CI, the Render
web service and the Render worker — installs with `uv sync --frozen` so the
lockfile is authoritative and the web service and worker provably run identical
dependency trees. `uv run` is the entry point for commands (`uv run pytest`,
`uv run alembic upgrade head`, `uv run uvicorn ...`); scripts never assume an
activated virtualenv.

Two rules follow from this and are not negotiable:

- **`requirements.txt` is not used.** If a deployment target needs one, it is
  generated from the lock (`uv export`), never hand-maintained alongside it —
  two sources of dependency truth is how a worker silently diverges from the
  web service it must agree with.
- **`uv.lock` is committed and conflicts are resolved by re-locking**, not by
  hand-editing. A lockfile edited by hand is no longer a record of a resolved
  dependency graph.

**Python 3.12 + FastAPI + Pydantic v2 + Uvicorn.** FastAPI is already the
project's stated direction; Pydantic v2 gives us one schema layer shared by
request validation, the canonical model, and the LLM's structured output
contracts.

**SQLAlchemy 2.0 with typed `Mapped[]` declarative models, plus Alembic.**
Reconciliation is relational-join work across orders, items, ledger entries,
platform fees and disbursements; raw SQL for that volume of joins becomes
unmaintainable inside a week.
Alembic is non-optional because the canonical schema will change during Phase 2
and 3 and the database must stay reproducible.

**PostgreSQL 16.** One datastore. `NUMERIC` for exact decimals, `JSONB` for
retained raw source payloads ([[mission]] P7), and real transactions so a
dedupe check and the financial write it guards commit or fail together.

---

## 4. Background jobs and event processing

**Decision: a Postgres-backed job queue. No Redis, no broker.**

```
FastAPI (Render web service) ─┐
                              ├──> PostgreSQL ── jobs
Worker (Render worker)       ─┤                ├─ processed_events
                              │                └─ canonical records
Render Cron Job              ─┘
```

Implementation:

- A `jobs` table claimed with `SELECT ... FOR UPDATE SKIP LOCKED LIMIT n`,
  giving safe concurrent claiming without a broker.
- Columns for `attempts`, `run_after`, `last_error` to support retry with
  exponential backoff.
- A `processed_events` table keyed on the provider's event id as a **unique
  constraint** — the database, not application logic, is what makes duplicate
  webhook delivery impossible.
- Render Cron Jobs trigger scheduled reconciliation runs and periodic backfills
  that recover missed events.

**Why this over Redis + ARQ/Celery:** the decisive reason is correctness, not
simplicity. Idempotency must be transactional with the financial write it
protects. With the dedupe marker in Postgres and the ledger write in Postgres,
one `COMMIT` covers both. Split across Redis and Postgres, a crash between the
two leaves a marker claiming an event was processed when its financial effect
was rolled back — a silent lost refund. Fewer moving parts on Render is a real
but secondary benefit.

**Accepted cost:** no queue dashboard, and polling latency of a second or two on
event pickup. Neither matters at our volumes. If throughput ever demands a real
broker, the job interface is narrow enough to swap behind.

---

## 5. AI investigation layer

**Model: Claude Opus 5.5 — `claude-opus-5-5`.**
1M context, 128K max output, $4 / $20 per million input / output tokens.
Chosen because exception investigation is multi-step evidence reasoning, which
is exactly where the strongest model pays for itself; at a handful of
investigations per demo the cost is immaterial.

**Secondary model: Claude Haiku 5.5 — `claude-haiku-5-5`** ($0.10 / $0.50 per
MTok) for the high-volume, low-judgment task of suggesting marketplace→canonical
field mappings. Per [[mission]] P1 those suggestions are advisory and must pass
deterministic validation before activation.

### 5.1 Request configuration

| Setting | Value | Why |
|---|---|---|
| Thinking | `thinking={"type": "adaptive", "display": "summarized"}` | `budget_tokens` is removed and returns 400. **`display` must be set explicitly** — it defaults to `"omitted"`, which returns thinking blocks with empty text and leaves us no record of how a conclusion was reached. See §5.2. |
| Effort | `output_config={"effort": "medium"}` | Set explicitly even though it matches the Opus 5.5 default, so the value is auditable and survives a change of default. Starts at the cheaper level deliberately; raise only on measured evidence. See the granularity watch-item in §5.2. |
| Tool choice | `{"type": "auto"}` + `strict: true` on each tool | Forced `tool_choice` (`any` / `tool`) returns 400 on Opus 5.5. `strict` guarantees schema-valid tool arguments. |
| Max tokens | `max_tokens=64000` | Required. Streaming default for long output; an unset value is a latent truncation failure. |
| Streaming | Yes, with `.get_final_message()` | Long investigations otherwise risk HTTP timeouts. |
| Eager input streaming | **Off** | Our tool inputs are record ids — it buys nothing and shifts validation to us, where a tolerant parser can return a *silently truncated* input. Unacceptable on a tool that drives a financial recompute. |
| Sampling params | **Never sent** | `temperature`, `top_p`, `top_k` return 400 on this model. See the reproducibility note in §5.2. |
| Refusal handling | `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"` | A `stop_reason: "refusal"` arrives as HTTP 200 — always check `stop_reason` before reading content. **Subject to the disclosure rule in §5.2.** |
| Prompt caching | Cache the system prompt and tool definitions | Identical across investigations. Note caches are model-scoped, so a fallback forfeits the cache. |
| Context handling | Append-only. No compaction, no context editing. | See §5.2. |

### 5.2 Audit constraints on the model layer

These exist because ReckonAI is an auditable financial product, not because the
API requires them. They are as binding as anything in §2.

**Reasoning must be recorded.** Every investigation persists the summarized
thinking alongside the report. It is a *summary*, never the raw chain of thought,
which no model exposes — so it is stored as supporting context and is never
presented as the authoritative cause. The cited source records are the
authority ([[mission]] P5).

**A model swap must be disclosed, never silent.** We persist the model id from
every response and compare it against the model we requested. If a refusal
fallback served the investigation, the stored report is flagged as downgraded
and surfaced that way in the UI. The fallback ran without the prior thinking
blocks, which are bound to the original model — an investigation produced that
way has materially weaker reasoning behind it, and a finance team is entitled to
know which ones they are. An unflagged downgrade would make the audit trail
lie, which is worse than a failed investigation.

**The agent loop is append-only.** Opus 5.5 enforces the preserved-thinking
check on accounts created on or after 2026-08-31: editing earlier turns
invalidates thinking blocks and can return a 400. Independently, trimming
earlier tool results to save context would be deleting the evidence trail to
save tokens. If an investigation outgrows its context, bound the evidence it
retrieves — do not clear what it has already read.

**The AI layer is not reproducible, and nothing should imply otherwise.**
Sampling parameters are gone, so the same exception investigated twice can
produce differently-worded reports and differently-ranked causes. This is the
reason [[mission]] P1 exists: every *number* comes from the deterministic engine
in §6, which is byte-for-byte reproducible and unit-tested. The model's output
is an explanation over fixed figures, and the UI must not present it as a
recomputation of them.

**Watch evidence granularity at `medium` effort.** Lower effort produces fewer,
more-consolidated tool calls. That is the intended saving, but it trades against
something this product needs: each retrieval should be a discrete, citable step,
and an investigation that reaches its conclusion in two broad calls is harder to
audit than one that reaches it in six narrow ones. The specific signal to watch
is a report whose cited record ids thin out while its conclusions stay
confident. If Phase 15's harness shows root-cause accuracy or evidence
completeness dropping, raise effort to `high` and re-measure — that is a
measured decision, not a default to drift back to.

**Cost exposure sits in evidence volume, not model choice.** At $4 / $20 per
MTok the model is not the risk; loading raw JSONB payloads into a 1M-token
context repeatedly is. Retrieval tools return the fields needed for the decision
and fetch a full raw payload only when explicitly asked for one.

**Haiku 5.5 has no server-side refusal fallback.** A refused field-mapping
suggestion must fail closed — the administrator maps the field by hand. It must
never fall through to an unvalidated guess ([[mission]] P1).

**Agent loop:** the Anthropic SDK's beta Tool Runner —
`@beta_tool`-decorated functions passed to `client.beta.messages.tool_runner(...)`,
driven with `runner.until_done()`. We do not hand-write the tool loop, and we do
not use the Claude Agent SDK (a different product, for coding agents).

**The agent's tools are read-only retrieval plus deterministic calculators:**

- fetch order, order items and sellers
- fetch payments, captures, refunds, disputes
- fetch seller ledger entries and payout items
- fetch the applicable commission and refund rules
- **recompute expected entitlement** — calls the same engine code as §6, so the
  agent cannot produce a number the engine would not produce
- fetch the retained raw source payload for any record

**No tool mutates financial state.** Per [[mission]] P6 the agent's terminal
action is writing an investigation report plus a *proposed* resolution that a
human must approve.

**Output contract:** the report is a Pydantic model returned via structured
outputs (`output_config.format`), with ranked candidate causes, per-cause cited
record ids, a confidence value, and a required `insufficient_evidence` branch so
[[mission]] P5 is representable rather than something the model has to work
around.

---

## 6. Reconciliation engine

Not a framework choice — a structural constraint.

The engine is **pure Python functions with no I/O**: records in, expected
financial position and classified exceptions out. No database handle, no HTTP
client, no clock reads inside the calculation. This is what makes it unit
testable against hand-computed fixtures, and it is why the AI agent can safely
call it as a tool.

Both historical and event-driven reconciliation call the *same* engine over the
*same* canonical model. There is no second code path for live events.

---

## 7. Frontend

**React 18 + TypeScript + Vite**, with **AG Grid / AG Studio** as the primary
surface for the exception queue, seller financial positions and reconciliation
results. AG Grid is doing real work here — grouping, pivoting and drill-down over
financial rows — not decoration.

Amounts arrive as strings and are formatted with `Intl.NumberFormat` using the
record's own currency code. The frontend never does monetary arithmetic; if a
number needs computing, the backend computes it.

**Bryntum** stays optional. Include it only for a payout/dispute timeline if
core work is already finished — it is not permitted to displace any phase in
[[roadmap]].

---

## 8. PayPal integration

Sandbox throughout. PayPal REST APIs plus webhooks, using the PayPal MCP
toolkit where its exposed capabilities genuinely fit: transaction retrieval,
dispute retrieval, refunds, disbursement data, merchant insights.

**The integration model is multiparty** (amended 2026-11-06). We read as the
**platform** in a PayPal *connected integration*, not as the merchant, and both
disbursement modes are supported. That changes access as much as content:

| Need | Mechanism |
|---|---|
| Read a seller's transactions | `PayPal-Auth-Assertion`: an unsigned JWT (`alg: none`), platform client id as `iss`, the seller's `payer_id` or `email` in the payload |
| Platform fee on an order | `purchase_units[].payment_instruction.platform_fees`; seller onboarded with `PARTNER_FEE` |
| Delayed disbursement | `payment_instruction.disbursement_mode: DELAYED`; seller onboarded with `DELAY_FUNDS_DISBURSEMENT` |
| Release held funds | `POST /v1/payments/referenced-payouts-items`, `reference_id` = capture id, `reference_type: TRANSACTION_ID`. **We never call this** ([[mission]] P6) |

Capabilities are granted **per seller at onboarding**, not as account-level
switches, so the onboarding path determines which of these exist at all.
[[roadmap]] Phase 0b verifies them; until it does, none may be designed around.

Two standing rules, both from [[mission]] P3:

1. **Verify fields exist before designing around them.** The toolkit may not
   expose full settlement, balance or FX detail. [[roadmap]] Phase 0 exists
   specifically to find out before the canonical model is built.
2. **Where PayPal does not supply a figure, record the gap as a limitation.**
   Never infer, estimate or fill it.

Five client rules, all of them non-obvious enough to be worth locking here:

- **Field availability depends on the read path, not only the record type.**
  Read refunds through the order, which carries fields the refund endpoint
  omits.
- **The reporting APIs are eventually consistent and share one snapshot.** The
  v2 resource endpoints are authoritative for recent activity. Reporting is
  still read — it carries conversion timestamps and per-event fees the resource
  endpoints do not — and joins on `transaction_id` and `paypal_reference_id`.
- **A `403` is never recorded as an absent field.** It describes our
  permissions, and a scope added after a token is minted does not appear in that
  token.
- **A `5xx` is not reliably transient**, so blind retry is wrong.
- **Retain every raw payload.** The contract is neither a complete nor a stable
  description of what arrives.

Each rests on evidence in `specs/paypal-fields.md` and the Phase 0 notes.

Webhook handling: verify signatures where supported, dedupe on event id via the
unique constraint in §4, process idempotently, and backfill on a schedule to
recover anything missed.

### 8.1 APIMatic Context Plugin — how PayPal knowledge enters the build

The **APIMatic Context Plugin for PayPal** is how the coding agent reaches
accurate PayPal API context while writing the integration, instead of relying on
model recall or hand-searching the API reference. It needs no PayPal
credentials.

**Corrected 2026-11-05, after installing it.** The plugin does not itself carry
endpoints, response shapes or field names. It is 48 skills — eight each for
csharp, java, php, python, ruby and typescript — teaching how to use the
APIMatic-*generated* PayPal Server SDK, and no PayPal API resource has a skill.
What it supplies is the SDK's identity (`paypal-server-sdk`, pinned version,
source repository) and precise instructions for reading the contract out of the
generated source. The field names live in the SDK's `doc/models/*.md` and model
classes, not in the plugin. See
`specs/2026-11-05-paypal-field-discovery/notes.md`.

It is **development-time context, not a runtime dependency and not a build
artifact.** Nothing we ship imports it; no generated client is committed from
it. It changes what the agent knows while writing Phases 0, 7 and 9, and that is
its whole function.

Two consequences worth stating, because they are easy to get wrong:

- **The generated SDK contract is the *declared* side. The sandbox is *observed*
  reality. They are not the same thing, and the gap between them is the point.**
  The contract tells us a settlement or FX field exists in the API; only a
  sandbox call tells us whether it is actually populated for our transactions.
  Phase 0 exists to compare the two, and [[mission]] P3 forbids treating a
  documented field as evidence that a value was returned. (Corrected 2026-11-05:
  this previously said the plugin *is* the declared contract. It is the route to
  it, at a pinned SDK tag.)
- **Money typing stays ours.** Because we hand-write the client rather than
  generating one, we control deserialization: PayPal returns amounts as JSON
  strings and they are parsed straight into `Decimal`, never through `float`
  (§2). No generated deserializer gets to make that choice for us.

  **Confirmed against the real contract, 2026-11-05.** `float` appears zero
  times across all 373 generated model pages; every monetary field is
  `Money{currency_code: str, value: str}`. PayPal declares money as strings
  carrying their own currency, exactly as §2 requires, so this rule needs no
  amendment to survive contact with the API.

  One consequence worth stating, because it cuts against the sentence above:
  since the generated deserializer hands over strings and we would parse them to
  `Decimal` either way, the money-typing argument for hand-writing the client is
  weaker than it looked when written. It is not void — the SDK covers neither
  Disputes nor Payouts, so those calls are hand-written regardless. Whether
  Phase 7 uses the SDK for the three covered record types is an open decision,
  recorded in `specs/2026-11-05-paypal-field-discovery/notes.md`, and it carries
  a submission dimension as well as an architectural one.

  The decision stays open, but it now carries a condition: **a generated client
  must retain the raw payload and diff it against the model.** PayPal returns
  fields its own published contracts do not declare, and a generator discards
  what it does not know, silently. Without the diff, the integration inherits
  whatever the generator left out.

**Setup is a prerequisite for Phase 0**, not an optional extra — see
[[roadmap]] Phase 0. The APIMatic resource page covers installation; the
hackathon also attaches a free subscription tier to having used the plugin, and
a longer tier for winning teams that covers SDKs and a documentation portal for
*our own* API. Those are prize benefits to revisit after the build, not inputs
to it.

---

## 9. Canonical field mapping

How a marketplace's fields become canonical fields. This is the connector's
core mechanism and the subject of [[roadmap]] Phase 6.

### 9.1 The mapping is data, not code

A mapping is a **versioned, per-tenant row** in `connector_mapping`: a JSONB
document validated by a Pydantic schema, with `status` (`draft` / `active`),
`activated_by` and `activated_at`.

It is versioned because reconciliation must stay reproducible. Every canonical
record stores the `mapping_version` that produced it, so a historical
reconciliation run can be re-derived exactly even after the mapping changes.
Without that, editing a mapping silently rewrites the meaning of past results
([[mission]] P7).

Per canonical field, an entry declares: source endpoint, source JSON path,
transform, required flag — plus `provenance` and `confidence` (§9.2).

### 9.2 How a mapping is produced — deterministic first, LLM last

Three passes, in this order. The ordering is the design: the cheap, explainable
passes run first and the model only sees the residue.

1. **Exact match.** Source field name equals canonical name, or matches after
   normalising case and separators. Provenance `exact`.
2. **Curated synonym dictionary.** A checked-in table of known equivalences —
   `vendor_id`→`seller_id`, `order_ref`→`order_id`, `item_total`→`item_amount`.
   This is version-controlled project knowledge that improves with every
   marketplace we meet, and it covers most of what the readme's examples show.
   Provenance `synonym`.
3. **LLM suggestion for whatever remains.** Claude Haiku 5.5 (§5), given field
   names, types, descriptions and sample values. Provenance `llm`, with a
   confidence value.

Then, always: **human review.** An administrator sees every entry with its
provenance and confidence, and corrects what is wrong. Provenance becomes
`human` on edit.

**A mapping cannot be activated by the model.** Activation is a human action,
recorded with who and when. An `llm`-provenance entry that no human has
confirmed is reviewable but not activatable.

### 9.3 Money and composite fields — where this gets subtle

**Most canonical fields map one-to-one. Monetary fields do not.** An amount is
meaningless without its currency (§2), so a money mapping resolves *two*
sources: an amount path and a currency path — or one path with an embedded
currency that the transform splits.

This is the single most likely place for a mapping to be quietly wrong: map
`item_total` to an amount and forget the currency, and the system will compare
numbers across currencies and report confident nonsense. The validation gate
therefore treats an amount without a resolved currency as a **hard failure**,
not a warning.

**One provider can spell money more than one way, and PayPal does.** It uses
both `{currency_code, value}` and `{currency, value}` across its APIs, and also
has a plain currency string named `currency`. So a provider adapter matches
every declared envelope spelling and **must not assume a key named `currency` is
a money envelope** — treating a bare string as one produces an amount with no
value, silently. Where a source states amounts as integer minor units, that is
scaled on the way in, never parsed as a decimal string.

Transforms are a closed, named set — decimal parsing, currency extraction,
timestamp with timezone, enum translation. Not arbitrary expressions: a mapping
is configuration and must not become an execution surface.

### 9.4 The validation gate

Deterministic, blocking, and run against sampled live records before a mapping
can be activated. This is what [[mission]] P1 means for mapping — AI suggests,
code decides.

- Every **required** canonical field is mapped.
- Source and canonical types are compatible.
- Every money field resolves both amount **and** currency (§9.3).
- The mapping applies to N sampled records with **zero** parse errors.
- Declared relationships hold in sampled data — an `order_item`'s order
  reference actually resolves to a sampled order (§9.5).
- No two canonical fields draw from the same source path unless explicitly
  declared as intentional.

Failure blocks activation and names the offending field. A mapping never
half-activates.

### 9.5 Relationships

Field mapping alone is insufficient — the connector must also know how records
relate across endpoints: item to order, item to seller, payment to order, ledger
entry to transaction, disbursement to obligation.

Two relationships are load-bearing enough to name here:

- **Seller attribution is the `payee` on the PayPal purchase unit.** Under the
  multiparty model (§8) that identifies the seller, not the marketplace, and it
  is the join the whole reconciliation rests on.
- **Refund to capture is derived, not provider-supplied.** PayPal exposes no
  capture identifier on a refund; the link exists only inside a URL. A derived
  linkage is recorded as derived and falls under the confidence rules below.

Where a direct identifier exists, it is declared and validated as above. Where
one does not, a relationship may be **inferred** from other evidence, and then:

- the inference carries confidence and provenance, and
- it is surfaced as **evidence with a confidence attached, never as a settled
  fact** — an inferred link may not silently drive a financial conclusion.

An exception resting on an inferred relationship says so in its report.

### 9.6 Runtime behaviour

- **An unmapped source field is not lost.** It stays in the retained raw
  payload ([[mission]] P7), so a mapping gap is recoverable later.
- **A missing required field quarantines the record.** It is not defaulted and
  not skipped silently. A quarantined record is visible and countable.
- **A monetary value is never defaulted to zero.** A missing amount is a
  quarantine, not a `0`. Defaulting money is how a reconciliation system
  produces a confident wrong answer.
- **Schema drift is detected, not absorbed.** The observed source schema is
  hashed at ingestion; a change marks the mapping `stale` and raises it for
  review rather than letting a renamed field map to the wrong place.

## 10. Security and controls

- Credentials in Render environment variables; never in the repo, never logged.
- Every table carrying marketplace data has a `tenant_id`, and every query is
  filtered by it.
- Role-based access, with approval of a recommended resolution as a distinct
  permission from viewing one.
- Reconciliation runs and investigation reports are append-only and timestamped.
- Raw payloads retained for audit ([[mission]] P7).

---

## 11. Hosting

Render: one web service (FastAPI), one worker (job queue consumer), one managed
PostgreSQL instance, and Cron Jobs for scheduled runs. APIMatic is not part of
the deployed system — it is development-time context only (§8.1).

**The job queue is not a separate service.** `jobs` and `processed_events` are
tables inside the same managed PostgreSQL instance as the canonical financial
records. That co-location is the design, not a shortcut — it is what lets a
dedupe marker and the financial write it guards share one transaction (§4).

**Connection budget.** Three Render components connect to one Postgres
instance — web service, worker, and each Cron Job invocation — and small managed
plans cap total connections well below what three default SQLAlchemy pools would
claim. Pool sizes are set explicitly per component, and a Cron Job opens a short
-lived connection rather than a pool. Exhausting connections would take down
reconciliation and the API together.

**Plan constraints to confirm in [[roadmap]] Phase 1, not on the last day:**
whether Background Workers and the required Postgres plan are available on the
account's current Render tier, and whether a free-tier Postgres instance has an
expiry window that lands inside the project's life. Both are cheap to check on
day one and expensive to discover on day six.

**Checked 2026-11-07, and both would have failed.** The account's workspace plan
is **Hobby**, Render's free tier, where:

- **Background Workers do not exist.** Free instance types cover web services
  only; workers start at Starter, around $7 a month.
- **Free Postgres expires 30 days after creation**, with a 14-day grace period
  before the data is deleted, and has no backups.
- A free web service also **spins down after 15 minutes idle** and takes about
  a minute to wake — which would both break an in-process worker and open a
  judged demo on a blank page.

**Resolved by $50 of Render credits.** **So §11 stands as written** — separate
web service and worker, a Postgres that does not expire, and the
three-component connection budget above is the real one.

**Costs amended 2026-11-07**, in [[roadmap]] Phase 1 Group 0, once the Postgres
tier was actually chosen. The topology is a Starter web service, a Starter
worker and a **Basic-1gb** Postgres (plan id `0.5c-1g`) — about **$33 a month**,
so the credits cover roughly **1.5 months**. That is far beyond the project's
life. This paragraph previously said about $20 a month and two and a half
months, which assumed Basic-256mb; 256MB is cramped once the connection budget
above is open, and the extra memory was judged worth the shorter runway.

Three provisioning facts worth carrying here, because two of them cannot be
changed after the database is created and all three are easy to get wrong:

- **Region is `frankfurt`** for all three components, chosen for the operator's
  latency rather than the PayPal sandbox's.
- **`postgresMajorVersion: "16"` is set explicitly.** Render otherwise
  provisions the latest major version, which would break parity with local
  compose and CI — and could not be corrected without destroying the database.
- **`ipAllowList: []`** — internal Render traffic only. An absent `ipAllowList`
  means *allow every IP* on a new database, and this repository becomes public
  at submission. Connection limits are flat at **100 across every Basic tier**
  (less 10 Render reserves), so the budget above has ample headroom.

Details and rationale: `specs/2026-11-07-repo-deployment-skeleton/notes.md`.

**Provisioning is declared in `render.yaml`**, a Render Blueprint committed to
the repository, rather than configured by hand in the dashboard. The topology
is then version-controlled and reviewable, and matches the reproducibility rule
in §3.

**Render Cron Jobs are available on the plan** (confirmed 2026-11-07). The
fallback this paragraph previously named — a scheduled trigger inside the worker
loop — is **dropped**, and [[roadmap]] Phase 8 declares a real Cron Job.

---

## 12. Rejected alternatives

Recorded so they are not relitigated mid-build.

| Rejected | Why |
|---|---|
| Redis + Celery / ARQ | Idempotency must be transactional with the financial write; see §4. |
| `float` or integer minor units for money | `float` is disqualified outright. Minor units break on 3-decimal currencies and on FX rates, and push rounding into every call site. |
| LLM-computed arithmetic | [[mission]] P1. |
| Public FX rate API as a settlement source | [[mission]] P3. Permitted only as explicitly-labelled derived context, never as evidence. |
| CSV upload as the primary ingestion path | The connector architecture *is* the product thesis. CSV stays a fallback only. |
| Claude Agent SDK | A coding-agent harness; wrong tool. The Tool Runner over our own read-only tools is what this needs. |
| MongoDB / document store | Reconciliation is relational-join work and needs exact `NUMERIC` arithmetic. |
| A second ORM-free raw-SQL layer | One data access pattern. Raw SQL permitted only for a measured hot path. |
| Poetry / PDM | uv covers resolution, locking and running, and is materially faster in CI. No reason to carry a second toolchain. |
| `pip` + hand-maintained `requirements.txt` | No real lockfile, so the worker and web service can drift apart — see §3. |

---

## 13. Code conventions

**No emojis anywhere in the codebase.** This covers source files, comments,
docstrings, log messages, test names, commit messages, CLI output and UI
strings.

Reasons, so the rule is not mistaken for taste:

- Log lines from a financial system get grepped, piped and parsed. Emojis are
  multi-byte, sometimes multi-codepoint, and render at inconsistent widths —
  they break column alignment and confuse downstream tooling.
- An investigation report or exception row may be exported to CSV or read in a
  terminal by a finance team. Plain text survives that; pictographs do not.
- Status conveyed by a pictograph is not greppable. A log line says
  `reconciliation.failed`, not a cross mark.

**This bans emoji and pictographs, not all non-ASCII.** These remain fine and
are used in this repo deliberately:

- Typographic punctuation in prose and docs: em-dashes, arrows (`→`), quotes
- Box-drawing characters in documentation diagrams
- Currency symbols, and any non-ASCII character occurring in real marketplace
  or seller data — which must never be stripped or normalised away

Applies to generated and scaffolded code too: if a tool emits an emoji into a
template, remove it rather than leaving it.

## 14. Related specs

- [[mission]] — what we are building and the principles that bind this stack
- [[roadmap]] — the order these pieces get built
