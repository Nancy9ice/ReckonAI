# Block E — the AI investigation agent

> Phase 12 ([[roadmap]]), kept **entire** by the replan's R11 with nothing
> reduced. The model is Claude Haiku 5.5 by [[tech-stack]] §5's dated
> 2026-11-10 amendment, as a setting rather than a constant.
>
> 2026-11-10. Branch `block-e-investigation-agent`, off `main` at `3dd17ac`.

## 1. Scope

**In:** `reckonai/agent/` — read-only retrieval tools, the Phase 10 engine
exposed as a recompute tool, the Tool Runner driver, and the structured report
model with ranked causes, per-cause cited ids, per-cause confidence and the
`insufficient_evidence` branch. The `investigation` and `investigation_cause`
tables. `settings.agent_provider`, `settings.agent_model` and both API keys. A
provider seam with an Anthropic and an OpenAI implementation (D12). A citation
resolver. A `python -m` entry point. The four [[tech-stack]] §5.2 audit
constraints, which apply to both providers.

**Out, with owners:** the investigation detail view and its evidence trail
(Block F); prioritisation scoring, approve/reject and the decision record
(Phase 13, Block F); scoring the agent against the manifest, including the
`insufficient_evidence` branch on both models (Block G); LLM field-name
mapping (cut by R11); any HTTP route or queued job type for investigations
(Block F — see D8).

**Not re-derived here.** Block C's handoff
(`specs/2026-11-09-matching-classification/requirements.md` §4) is taken as
given: `reckonai.reconcile.run(session, tenant_id)` is a plain function over a
session with no clock and no windowing and returns **every** finding regardless
of any window; exceptions are counted through `exceptions_only()`; the
`Finding` JSON contract is frozen. This block reads that output and explains
it. **It changes no rule and adds no classification.**

## 2. Decisions

### D1. Two tables, append-only, causes as rows

`investigation` carries one row per attempt: the exception it is about
(`order_external_ref` plus `code`), `provider`, `model_requested`,
`model_returned`, `downgraded`, `stop_reason`, `verdict`, the summarized
reasoning, and the two timestamps. `investigation_cause` carries one row per
ranked candidate cause with its `rank`, description, `confidence` and
`cited_refs`.

`provider` is stored next to the model id rather than inferred from it (D12).
Two providers' model ids are not a single namespace, and Block G compares
across them — a column is what makes "which provider produced this report" a
filter rather than a string prefix match.

Causes are rows rather than JSONB because the exit criterion "every cited id
resolves to a real record" should be checkable in SQL rather than by parsing a
document, and because Block F's evidence trail renders citations one at a time.

Append-only for the same reason `reconciliation_exception` is: a second
investigation of the same exception writes a second row, and the difference
between them is the record that the verdict changed.

`run_id` is nullable. An investigation is about an exception, not about a run,
and the CLI can be pointed at an order that no stored run covered.

### D2. `insufficient_evidence` is a verdict, not an absent report

`verdict` is one of `reported`, `insufficient_evidence` or `failed`, checked in
the database.

- `reported` — at least one candidate cause, each with cited ids.
- `insufficient_evidence` — the agent reached a conclusion that it cannot tell,
  which [[mission]] P5 calls a correct and expected answer. It may carry causes
  it considered and rejected; it is not required to.
- `failed` — no conclusion was reached. A refusal lands here (D5).

The branch is representable in the output contract rather than something the
model has to work around, which is [[tech-stack]] §5.2's requirement. A report
with zero causes and a `reported` verdict is refused by a `CHECK`, so "I found
nothing" cannot be filed as a conclusion.

### D3. Tools retrieve and recompute; they never take a figure

Per [[tech-stack]] §5.2 the tools are read-only retrieval plus deterministic
calculators: order, items and sellers; payments, captures, refunds and
disputes; seller ledger entries and disbursements; the applicable commission
and refund rules; the retained raw payload for a named record; and
**`recompute`**, which calls `reckonai.reconcile.run` and returns its findings.

**No tool parameter is a monetary amount.** Every parameter is a record
reference or a tenant-scoped filter. This is how [[mission]] P1 is enforced
structurally rather than by instruction: the agent cannot feed the engine a
number it invented, because there is nowhere to put one. The arithmetic the
report rests on is `reconcile.run`'s, byte for byte.

**No tool mutates financial state** ([[mission]] P6). The agent's terminal
action is writing its report. It enqueues nothing and moves nothing. The tool
module opens no write transaction and imports nothing from `reckonai.jobs`.

### D4. The evidence gap is produced by withholding at the tool boundary

`InvestigationTools` takes a `withhold` set of record kinds. A withheld kind
returns empty from its tool while the database is untouched.

So the exception under investigation stays a **real** exception, classified by
the real classifier from the real rows — only the agent's reach is narrowed.
The alternatives were a seventh seeded fault and a second tenant, both
rejected: the manifest is Block G's ground truth and has already been stale
twice, and the clean baseline carries Phase 11's false-positive gate.

`recompute` is withheld-aware in one direction only: it returns findings, which
are verdicts rather than evidence, so it is never suppressed. Withholding it
would test whether the agent can work without the engine, which is not the
question.

### D5. Haiku 5.5, as a setting, with no fallback and nothing silent

`settings.agent_model` defaults to `claude-haiku-5-5`, so Block G scores both
models on the same faults with no code change. Both API keys are optional in
settings and asserted at the point of use, the same shape as
`marketplace_base_url` — the web service and the worker both read `Settings`
and neither needs one.

**`stop_reason` is checked before any response content is read.** A
`stop_reason: "refusal"` arrives as HTTP 200, and Haiku 5.5 has **no
server-side refusal fallback** — the `betas` / `fallbacks` row of
[[tech-stack]] §5.1 has no equivalent here. A refusal therefore stops, and is
persisted as `verdict: failed` with its `stop_reason`: no report, no cause
list, nothing presented as a conclusion.

`model_requested` and `model_returned` are both stored and compared, and
`downgraded` is set where they differ. Nothing in the MVP can set it, because
nothing downgrades at all — which is the point. The columns are what *prove*
that, and an unflagged downgrade would make the audit trail lie.

Request configuration is [[tech-stack]] §5.1 exactly, and it is unusually
specific because it was derived from real API behaviour: adaptive thinking with
`display` set explicitly, `output_config` effort `medium` set explicitly,
`max_tokens=64000`, streaming with `.get_final_message()`, eager input
streaming off, **no sampling parameters at all**, prompt caching on the system
prompt and tool definitions, `tool_choice` `auto` with `strict: true` per tool.
`budget_tokens` is a 400 here.

*Limitation to record in the readme:* a refused investigation is not retried on
a second model; it is reported as failed.

### D6. Every citation is resolved, and the resolution is stored

A resolver takes a cited reference and answers whether it names a real record
for this tenant. `investigation_cause.cited_refs` is stored with the resolution
outcome, so an unresolvable citation is visible in the row rather than
discovered by a reader.

An investigation whose report cites an id that resolves to nothing is persisted
with that fact recorded and surfaced as such. It is not silently repaired and
not discarded: the exit criterion is that no citation is fabricated, and the
only way to hold that honestly is to measure it.

**Cite the capture, never `platform_fee.external_id`.** The latter is built
from a payload UUID and changes on every seed, so it can name fee evidence but
cannot be used to find it again. The resolver rejects it, which is what keeps
the rule enforced rather than remembered.

### D7. The loop is append-only, and the context is bounded by narrow tools

No compaction, no context editing, no trimming of earlier tool results
([[tech-stack]] §5.2). Haiku 5.5 runs the same preserved-thinking check on
accounts created on or after 2026-08-31, so editing earlier turns can return a
400 — but the binding reason is that trimming a tool result is deleting the
evidence trail to save tokens.

Where an investigation would outgrow its context, the **evidence it retrieves**
is bounded instead. Retrieval tools return the fields a decision needs; a full
raw payload is fetched only when explicitly asked for by record. [[tech-stack]]
§5.2 puts the cost exposure in evidence volume rather than model choice, and
this is the mechanism.

One watch item is carried verbatim rather than solved here: at `medium` effort
the signal to watch is **a report whose cited ids thin out while its
conclusions stay confident**. Block G re-measures and may raise effort to
`high`. That is a measured decision, not a default to drift back to.

### D8. A `python -m` entry point, and no route

`python -m reckonai.agent.investigate`, taking a tenant plus an order reference
or a fault id, and an optional `--withhold`. The same invocation shape as
`reckonai.jobs.schedule`.

Block F owns the next deploy and the dashboard, so it adds the HTTP route and
can make it a queued job then. A synchronous route over a multi-minute model
call is the wrong shape, and this block's job is to prove the agent works at
all.

### D9. `reckonai/agent/` is outside the purity gate and inside strict mypy

It holds a `Session`, a clock and a network client — exactly what the engine
and the classifier refuse. It is the second such package after `jobs/`, and it
is added to `[tool.mypy]` for the same reason: the absence of one gate is why
the other matters more.

The purity gate must be told about it explicitly, so that `agent/` importing
`reconcile` does not read as the engine acquiring a session.

### D10. One kind of confidence only

The replan's R11 distinguishes three things these specs call confidence. Only
the second is in scope: **a candidate cause in an investigation report, with
its cited ids**. The field-mapping confidence is cut; the queue-ranking
confidence is Block F's. `investigation_cause.confidence` is the second one and
nothing else reads it.

### D11. `anthropic` is added to `pyproject.toml`

[[tech-stack]] §1 already locks "Anthropic Python SDK, beta Tool Runner", so
the dependency needs adding rather than the document amending. The beta Tool
Runner is used as §5.2 specifies — `@beta_tool`-decorated functions passed to
`client.beta.messages.tool_runner(...)`, driven with `runner.until_done()`. We
do not hand-write the tool loop, and we do not use the Claude Agent SDK, which
is a different product.

`output_config.format` and `tools` compose in one request, so the structured
report comes back as the final message's first text block. The runner's
signature is verified against the installed SDK rather than assumed.

### D12. OpenAI is a second selectable provider, and `tech-stack.md` is amended

**Operator decision, 2026-11-10, taken after Block E's spec was drafted.**
`settings.agent_provider` selects `anthropic` or `openai`; one investigation
uses one provider, start to finish.

The reasoning is the same one that made the model a setting in the first place.
[[tech-stack]] §5 says the strongest-model argument is "asserted rather than
measured", and Block G's eval is the instrument that settles it. Being able to
vary the *provider* on the same six faults widens that measurement at no cost
to the MVP path, since the default is unchanged.

**What this amends, dated in the documents themselves:**

- **§1** — the LLM row and the orchestration row. A second provider is a
  locked-decision change, and `pyproject.toml` carries the rule that a runtime
  dependency absent from §1 requires amending §1 first.
- **§5** — a provider seam, with §5.1's request configuration recorded as
  **Anthropic-specific**. That table is not portable: adaptive thinking,
  `output_config.effort`, `budget_tokens` being a 400 and the preserved-thinking
  check are all Claude API behaviours derived from real responses. The OpenAI
  path gets its own recorded configuration rather than inheriting that one.

**What does not change, and this is the load-bearing part.** Every [[tech-stack]]
§5.2 audit constraint applies to both providers, because they are properties of
an auditable financial product rather than of an SDK:

- reasoning is persisted whatever produced it
- the model id is stored and compared against the one requested
- the loop is append-only; nothing is compacted or trimmed
- a refusal or policy stop is read **before** content and becomes
  `verdict: failed`
- no tool mutates financial state, and no tool takes a monetary amount

**The report model is shared, and is the seam.** Both providers return the same
Pydantic report; only the transport differs. A provider that cannot produce the
structured report fails the investigation rather than returning a looser shape
— otherwise the two providers' reports would not be comparable, and comparing
them is the entire reason for having both.

**Still no fallback between them.** D5's no-fallback decision stands and is
*not* reversed by having a second provider available: a refusal ends the
investigation. Cross-provider fallback was offered to the operator and
declined. This keeps `downgraded` meaning exactly what §5.2 says it means, and
keeps a refusal honestly reported as a failure.

*Limitation to record:* the OpenAI path is implemented and unit-tested but is
not the measured default; Block G is what scores it against Claude.

## 3. Context worth not re-deriving

- **`reconcile.run` returns every finding regardless of any window**, so the
  agent is not limited to what a scheduled run covered. Block D's windowed run
  wraps the same function and filters afterwards; this block does not filter.
- **Count through `exceptions_only()`.** `awaiting_evidence` is a finding and
  not an exception. An agent offered `len(findings)` as the exception count
  would investigate a lifecycle state as though it were a discrepancy.
- **Three cross-source linkages are claims made in `reconcile.py`, not
  columns**, and a fourth was added after Block D's review. A finding resting
  on a refund-to-capture link carries `EvidenceBasis.DERIVED_LINKAGE`, and the
  report must not present a derived linkage as a provider-reported fact.
- **The dispute-versus-refund shape is undecidable from the records**, which is
  why a dispute-backed refund is `awaiting_evidence` rather than a guess. The
  agent explains that state; it does not resolve it.
- **Scenario 1 is F1**, a refund PayPal performed that the seller ledger does
  not record. Its expected cause is the missing ledger adjustment, and its
  citations are the refund and the ledger records.

## 4. The risk this block accepts, named so Block G measures it

Phase 12's hardest criterion is `insufficient_evidence` — returning "I cannot
tell" instead of assembling a plausible cause from thin evidence. That is
precisely where model capability shows, and a cheaper model is likelier to
confabulate a cause that reads well.

This block proves the branch is **reachable and correct on one withheld case**.
It does not prove the model takes it reliably. **Block G must score that branch
specifically on both models**, not only overall detection accuracy. D5's
`settings.agent_model` is what makes that a configuration change rather than a
rewrite.

## 5. What this block hands forward

- **The `investigation` and `investigation_cause` tables** are Block F's read.
  A latest-per-exception query will want the same shape as Block D's debt 2:
  the newest row per `(order_external_ref, code)`.
- **`settings.agent_provider` and `settings.agent_model`** are Block G's
  switches, across both models and both providers.
- **The citation resolver** is what Block G's root-cause scoring counts
  against, and it already stores per-cause resolution.
- **The withhold set** is the instrument for Block G's
  `insufficient_evidence` scoring, not just this block's single test.

## Related

- [[roadmap]] Phase 12 — the six exit criteria
- [[tech-stack]] §5, §5.1, §5.2 — the model, the request configuration, the
  four audit constraints
- [[mission]] P1, P3, P5, P6 — no LLM arithmetic, no fabricated value, evidence
  or silence, humans hold the money
- `specs/2026-11-08-replan/requirements.md` R11 — what was cut, what was not,
  and the three confidences
- `specs/2026-11-09-matching-classification/requirements.md` §4 — Block C's
  handoff
- `specs/2026-11-09-queue-scheduled-run/validation.md` §8, §9 — three
  corrections and three debts
