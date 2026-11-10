# Block E — validation

> 1,789 tests pass (113 of them Block E's), `uv run mypy` clean over `engine`,
> `recon`, `jobs` and now `agent`, `uv run ruff check` clean, migration clean up
> and down with no autogenerate drift.
>
> **No test makes an API call**, to either provider. Section 3's live runs are
> the only model calls in the block, and they are run by hand.
>
> **Both model-dependent criteria are met on `gpt-4.1`, not on the Anthropic
> default** - that account has no credit balance. `claude-haiku-5-5` is
> unexercised, and §5 records it as unproven rather than assumed.

## 1. Phase 12's exit criteria

| # | Criterion | How it is proven | State |
|---|---|---|---|
| 1 | Scenario 1 is investigated end to end and names the missing ledger adjustment, citing the refund and ledger record ids | Wiring: `test_agent_investigate.py::test_scenario_1_is_investigated_end_to_end`. The model's own answer: §3, run on `gpt-4.1` | **met, on the OpenAI path** |
| 2 | Every cited id resolves to a real record — no fabricated references | `test_agent_tools.py::test_every_evidence_ref_on_the_whole_dataset_resolves` over every finding on the seeded dataset; `unresolved_refs` persisted per cause; a fabricated id surfaced by `test_a_fabricated_citation_is_surfaced` | **met** |
| 3 | An exception with evidence deliberately removed returns `insufficient_evidence` rather than a guess | Instrument: `test_withholding_reaches_the_tools_and_the_row`. The model's own answer: §3, run on `gpt-4.1` | **met, after a real defect was fixed** - see §7 finding 3 |
| 4 | No tool can mutate financial state | `test_agent_tools.py::test_no_tool_writes_to_the_session` calls **every** tool and asserts the session holds nothing new, dirty or deleted; `test_the_tools_do_not_import_the_job_queue` | **met** |
| 5 | `stop_reason` is checked before response content is read | `test_agent_providers.py::test_a_refusal_discards_a_report_the_response_still_carried`, on both providers | **met** |
| 6 | The summarized reasoning is persisted, and the model id is stored and compared — a fallback produces a report flagged as downgraded | `test_agent_store.py::test_a_report_round_trips_losing_no_field` and `test_a_mismatched_model_is_stored_as_downgraded` | **met** |
| 7 | Both providers satisfy every §5.2 constraint and return one report shape | `test_openai_returns_the_same_report_shape`, `test_openai_runs_tools_and_appends_every_turn`, and the Anthropic equivalents | **met** |

**Criterion 5 is proven by the discarding, not by the reading order.** The stub
returns a refusal *and* a parsed report — a shape that should never occur. An
implementation that read content first would file that report as a conclusion,
so asserting `report is None` catches the mistake where asserting "a refusal
returns no report" would pass against a provider that happened to find nothing
parsed.

**Criterion 6 needs reading precisely rather than ticking.** Haiku 5.5 has no
server-side refusal fallback and D12 added no cross-provider fallback, so
**nothing in the MVP can set `downgraded`**. That is not an exemption — the
stored-and-compared columns are what prove the claim, and the flag is exercised
by a stub rather than by a real fallback. `tech-stack.md` §5 calls this the
honest version of the rule, and the row is met on that reading. A `CHECK`
refuses a `downgraded` row that has no `model_returned` to compare, so the flag
cannot be set on a row with no evidence for it.

## 2. The four audit constraints

`tech-stack.md` §5.2 is as binding as the money rules, so each is checked
directly rather than inferred from the request.

| Constraint | Check |
|---|---|
| Reasoning is recorded | `test_the_summarized_reasoning_is_collected` and `test_the_reasoning_is_persisted`; `display` asserted explicitly in the request, since its default `"omitted"` returns empty thinking text |
| A model swap is disclosed, never silent | §1 criterion 6, plus `test_the_downgrade_flag_is_not_taken_from_the_caller` — it is derived from the two ids, so a caller cannot assert it wrongly |
| The loop is append-only | `test_the_loop_is_never_compacted` (the runner's `compact_before_next_turn` is never called and no `context_management` is sent) and `test_openai_runs_tools_and_appends_every_turn` (each request's messages are a prefix of the next) |
| The AI layer is not reproducible | `test_the_request_sends_no_parameter_that_returns_400`; every report assertion is on structure and cited ids, never on wording |

The last row is why no test asserts a sentence. Two investigations of one
exception can produce differently-worded reports and differently-ranked causes,
and a test pinning prose would fail for the wrong reason. Every *number* comes
from `recompute`, which is `reconcile.run` and already unit-tested.

Also asserted on the Anthropic request: `max_tokens=64000`, `stream=True`,
`output_config={"effort": "medium"}`, `tool_choice` `auto`, `strict` and
`eager_input_streaming: false` on every tool, caching on the system prompt, and
the absence of `temperature`, `top_p`, `top_k`, `budget_tokens` and
`fallbacks`.

**One §5.1 row became a test of its own.** The exception under investigation is
asserted *not* to appear in the cached system prompt
(`test_the_system_prompt_is_cached_and_the_exception_is_not`). Caching is a
prefix match, so interpolating the order reference into the system prompt would
forfeit the cache on every investigation — which is the entire saving §5.1
claims.

## 3. The two live runs

**Not yet run.** Both need `ANTHROPIC_API_KEY` in the environment of the shell
that runs them; the application reads the process environment only and has no
`.env` fallback, by `reckonai/config.py`'s stated decision.

```
uv run python -m reckonai.demo_data --ensure          # commits the dataset
uv run python -m reckonai.agent.investigate --fault F1
uv run python -m reckonai.agent.investigate --fault F1 --withhold ledger
uv run python -m reckonai.demo_data --reset          # REQUIRED afterwards
```

**The reset is not optional**, and this cost a suite-wide failure to discover.
`--ensure` commits, and the test suite requires a database with no committed
marketplace rows: `test_migrations.py`'s downgrade to `base` cannot run against
committed tenant rows. Six failures and 43 errors, none of them about the agent.
`demo_data`'s own docstring warns that `--ensure` "is not for a developer's test
database"; the warning is easy to read past while setting up a demo.

`assert_no_committed_marketplace_rows` now names this as the first of its two
causes and prints the `--reset` command, so the failure explains itself rather
than pointing at a collection-order bug. See §8 debt 3.

**Run 2026-11-10 on `gpt-4.1`, not on the Anthropic default.** The Anthropic
account has no credit balance (`400 invalid_request_error: Your credit balance
is too low`), so the measured default could not run; the operator chose to
evidence these two criteria on the OpenAI path instead. `gpt-5` and
`gpt-5-mini` both returned `404: Your organization must be verified`, so
`gpt-4.1` is what the key can reach. **`claude-haiku-5-5` remains
unexercised** — see §5.

#### Criterion 1 — Scenario 1 end to end

```
uv run python -m reckonai.agent.investigate --fault F1 \
    --provider openai --model gpt-4.1
```

| Field | Value |
|---|---|
| verdict | `reported` |
| model requested / returned | `gpt-4.1` / `gpt-4.1-2025-04-14` |
| `downgraded` | false |
| `stop_reason` | `stop` |
| causes | 1, confidence 1.00 |
| cited refs | `0CE88977WM977540P` (refund), `LED-ORD-0013-vendor-bellweather-comm`, `LED-ORD-0013-vendor-bellweather-sale` (ledger), `6AP63246WF1540214` (capture) |
| unresolved citations | **none** |
| unstable citations | **none** |
| wall time | ~7 s |

> Cause 1: "Neither the refund debit nor the commission reversal relating to
> PayPal refund 0CE88977WM977540P are present in the seller's ledger. The
> platform fee refund reversal (3.00 USD) is confirmed in PayPal records but
> absent in the ledger, resulting in an overstated seller position."

**Met.** It names the missing ledger adjustment, cites the refund and the
ledger records as the criterion requires, and every citation resolves. It also
cited the capture rather than the fee id, unprompted, and correctly described
the fee as a *reversal* — the distinction §6.2 records.

#### Criterion 3 — evidence deliberately removed

```
uv run python -m reckonai.agent.investigate --fault F1 --withhold ledger \
    --provider openai --model gpt-4.1
```

| Field | Value |
|---|---|
| verdict | **`insufficient_evidence`** |
| `withheld` | `["ledger"]` |
| causes | **0** |
| wall time | ~6 s |

> "The PayPal refund (0CE88977WM977540P) of 30.00 USD was found and platform
> fees show both the original commission and a reversal upon refund. However,
> the ledger entries required to determine if a refund debit or commission
> reversal was actually posted in the seller's ledger are **unavailable**, so
> it is not possible to definitively establish why the seller's position is
> overstated."

**Met — on the second attempt, and the first attempt is the more important
result.** See §7 finding 3: the first run returned `reported` at confidence
1.00 asserting "No seller ledger entries exist", which was a defect in the
tool layer rather than in the model. The same model declines once the
distinction is made available to it.

**Why these are hand-run and not in CI.** A paid call per test run is not
acceptable and CI has no key. A live test skipped on a missing key would make a
green CI run cover nothing, which this project already treats as the worse
failure — deliberately, for the database-backed tests. So the deterministic
parts are tested offline against stubs and the two model-dependent criteria are
evidenced here once.

**Budget: two calls, both on the Anthropic default.** The OpenAI path ships
unit-tested and not live-run; Block G scores it (D12).

### 3a. The OpenAI provider's recorded configuration

Recorded here because no section of `tech-stack.md` describes it, and §5.1 is
Anthropic-specific — a reader would otherwise assume that table applies.

| Setting | Value | Why |
|---|---|---|
| API | Chat Completions, `client.chat.completions.create` | The tool loop is hand-written; this SDK has no equivalent of the beta Tool Runner |
| Loop | Hand-written, **append-only** | Each assistant turn and each tool result is appended; nothing earlier is rewritten or dropped (§5.2) |
| Structured report | `response_format` `json_schema` with `strict: true`, schema generated from `InvestigationReport` | Generated rather than hand-written, so both providers are provably asked for the same shape |
| Tools | `strict: true`, `additionalProperties: false`, every property a required string | Same registry as the Anthropic path; a hallucinated argument is a schema error |
| Tool choice | `auto` | Matches the Anthropic path; forced tool use is not used on either |
| Refusal | `finish_reason == "content_filter"`, read **before** content | No fallback. The investigation is recorded as failed |
| Reasoning summary | **None available** | This API returns none, so `summarized_reasoning` is NULL on this path |
| Iteration ceiling | 40 turns | A runaway guard, not an audit requirement. Failing at a known bound is more legible than failing at the context limit |
| Sampling | Not sent | Not a 400 here as it is on Haiku 5.5, but omitted for the same reason: §5.2 says the layer is not reproducible and nothing should imply otherwise |

**The reasoning-summary row is a real difference between the providers, stated
rather than hidden.** §5.2's "reasoning must be recorded" holds on this path
only in the weaker sense that there is nothing to record. Block G's comparison
has to account for it, and `test_openai_carries_no_reasoning_summary` pins it
so it cannot be forgotten.

## 4. Gates

- `uv run pytest` — 1,789 pass. No test makes an API call.
- `uv run mypy` — clean over `engine`, `recon`, `jobs` and `agent`.
- `uv run ruff check` — clean.
- `uv run alembic upgrade head` then `downgrade -1` then `upgrade head` — clean,
  no autogenerate drift.
- The purity gate passes, with `agent/` recorded in
  `tests/test_money_purity.py` as deliberately outside it rather than slipping
  through.
- `tests/test_model_registry.py` passes with both new models registered, and
  the two tables recorded in `TABLES_WITHOUT_EVIDENCE` with their reasoning.
- No emojis. No `float` column anywhere in the schema.
- No test randomisation and no `-n auto`.

## 5. What this block does not prove

1. **That the model takes the `insufficient_evidence` branch reliably.** §3
   proves the branch is reachable and correct on one withheld case. One
   observation is not a rate.

   **And §7 finding 3 raises the stakes on this considerably.** The first
   attempt at that case confabulated at confidence 1.00, and the cause was a
   tool returning `[]` where it meant "denied". The branch is reachable when
   the tool layer tells the truth about what it is withholding; how often a
   model takes it when evidence is merely *thin* rather than explicitly marked
   unavailable is still unmeasured, and is the harder question. **Block G
   should score the branch against thin evidence as well as withheld
   evidence**, because only the second is now signposted.
2. **That `medium` effort retrieves enough evidence.** The signal to watch is a
   report whose cited ids thin out while its conclusions stay confident. §3's
   token counts are the baseline; Block G re-measures and may raise effort.
3. **That the Anthropic default works against the live API.** This inverted
   on the day: the Anthropic account had no credit, so `claude-haiku-5-5` was
   never called and **the OpenAI path is the one with live evidence.** Four of
   its five §5.1 request settings are therefore asserted only against a stub.
   Whoever tops up the account should re-run §3's two invocations without
   `--provider` and append the results; the code path is unchanged.
4. **That the agent handles an exception shape the seeded dataset lacks.** Six
   faults are investigable. The engine's named raises — mixed currency, an
   unresolvable commission rule — classify correctly and have never been handed
   to the agent.
5. **Anything about cost at volume.** Four investigations is not a cost model.
   For what it is worth, each completed run took 6-8 seconds on `gpt-4.1`.
6. **That a real downgrade is flagged.** Nothing can downgrade, so the flag is
   exercised only by a stub. See §1's note on criterion 6.

## 6. Corrections this block made to earlier documents

1. **The `reported`-needs-a-cause rule cannot be a `CHECK`.** `plan.md` Group 2
   asked for one. The causes live in another table and Postgres forbids a
   subquery in a `CHECK`, so it is enforced in `reckonai.agent.store`, which
   writes both tables in one transaction, and pinned by test. A deferred
   constraint trigger was the alternative and was rejected: it would put a
   second financial rule in a second language for a condition one function
   already owns. The model records why it is absent.
2. **"Cite the capture" is incomplete.** Block C's handoff and this block's D6
   both say to cite the capture rather than `platform_fee.external_id`.
   Scenario 1's order carries a **refund reversal** fee, which has no
   `capture_id` at all — its parent is the refund. The stable reference is the
   fee's parent, whichever that is, and `fetch_platform_fees` returns
   `fee_kind` so the two are never added together (`reckonai.models.fees` warns
   that adding a reversal to a capture fee doubles the commission). Found by a
   test written against `capture_ref` failing.
3. **Resolution and stability are two questions, not one.** D6 says the
   resolver "rejects" a fee id. Rejecting it outright would have been wrong
   twice: a fee id *is* a real record, so reporting it as fabricated is false,
   and the classifier's own findings cite fee ids deliberately — so the
   whole-dataset citation test would fail. `Citation` carries `resolved` and
   `stable` separately, and the exit criterion and the citation rule are
   measured independently.
4. **`confidence` is `NUMERIC(4, 3)`, not a float.** D1 and the model's first
   draft used a float, reasoning that it is not money and so not covered by the
   `float` ban. `tests/test_models_schema.py::test_no_column_is_a_binary_float`
   is broader — it refuses a binary float anywhere in the schema — and caught
   it. Exempting it was the alternative; three decimals is ample, exact storage
   keeps Block G's cross-provider comparison free of representation drift, and
   the gate keeps no exceptions.
5. **§5.1's table gained a scope note.** It is now marked Anthropic-specific,
   with this document's §3a named as the OpenAI path's record. Without that,
   the natural reading was that `output_config` and adaptive thinking applied
   to both providers.

## 7. Seven things found by running it

**Three of these came from the live calls and could not have come from
anywhere else.** Findings 1, 2 and 3 below were each invisible to a stub,
because a stub accepts whatever schema it is handed, returns whatever model id
it is told to, and cannot tell the agent anything the tool layer does not say.
They are the argument for §3's two hand-run calls being worth their cost.

1. **OpenAI's strict mode rejects a `$ref` carrying sibling keywords**, and
   Pydantic emits exactly that for an enum field with a docstring:

       Invalid schema for response_format 'investigation_report':
       context=('properties', 'verdict'), $ref cannot have keywords
       {'description'}

   Fixed by inlining `$defs` in the OpenAI adapter rather than by changing
   `agent/report.py`. The report model is the seam between the providers, and
   bending it to one provider's schema dialect is how the two stop being
   comparable - the adaptation belongs in the adapter.
   `test_the_strict_schema_carries_no_refs` pins it.

2. **The downgrade flag fired on every OpenAI response, as a false positive.**
   A request for `gpt-4.1` is served by `gpt-4.1-2025-04-14`, and a plain
   inequality called that a model swap. It is the same model named more
   precisely. [[tech-stack]] §5.2's flag exists to tell a finance team their
   reasoning came from a weaker model, and **a flag that fires every time is
   noise rather than evidence - the same failure as never firing.** Now
   prefix-matched, with `claude-haiku-5-5` served by `claude-sonnet-5-5` still
   a downgrade. Invisible to the Anthropic path, which returns the requested
   id exactly.

3. **A withheld record returned `[]`, which the agent read as "none exist".**
   The most serious finding in the block. On the first run of criterion 3 the
   agent returned `reported` at **confidence 1.00**, asserting "No seller
   ledger entries exist to debit the seller for refund ... The refund and
   commission reversal occurred at PayPal but are missing from the
   marketplace's books." It had been denied those records, not shown their
   absence.

   **The defect was mine, not the model's.** `[]` made "you may not see the
   ledger" identical to "the ledger has no entries", so the only reading
   available was the confabulated one. This is precisely the distinction
   [[mission]] P3 protects for money - an absent value and a measured zero are
   different claims - applied to evidence, and I collapsed it in the tool
   layer while quoting P3 elsewhere in the same file.

   A withheld kind now raises `Withheld`, which `registry.run_as_json` turns
   into an explicit `{"unavailable": true, "record_kind": ...}` marker telling
   the model it cannot conclude anything about existence. Prompt rule 5 says
   the same. **The same model then returned `insufficient_evidence` with zero
   causes**, naming the unavailable records.

   Two things follow. **The stub test encoded the bug**: it asserted
   `fetch_seller_ledger_entries() == []`, which is why nothing failed. It now
   asserts the marker, and the stub provider calls `run_as_json` rather than
   `call` so it takes the path the real providers take. And **this is evidence
   about tool design, not about model capability** - Block G should not read
   the first attempt as a `claude-haiku-5-5`-versus-`gpt-4.1` result, because
   any model given an empty list would have had the same two readings and the
   confabulated one reads better.

4. **`demo_data.reset`'s delete list had fallen behind the schema by six
   tables.** A real `--reset` failed on `fk_investigation_tenant_id_tenant`.
   Block E's two tables were missing and so were **Block D's four**, which had
   never fired because nothing committed a run row outside the rolled-back
   suite. Fixed, and
   `test_demo_data.py::test_reset_empties_every_tenant_scoped_table` now
   derives the expected set from `Base.metadata`, so a table added later is
   covered whether or not anyone remembers that function.

5. **Two of my own test modules read ambient environment state.** The settings
   tests passed only while no agent key was configured and failed the moment
   one was exported - three failures, none about anything that had changed in
   the code. One store test asserted `investigation` was globally empty after
   a rollback, which is only true on a database no live run has touched. Both
   are now hermetic. A test whose result depends on the developer's shell is a
   test that will fail in CI for reasons nobody can reproduce locally.

## 7a. Four things found by building it

1. **Structured outputs and the Tool Runner compose, through `output_format`
   rather than `output_config["format"]`.** `plan.md` Group 4 left this open
   and allowed a final-turn workaround. None is needed: `tool_runner` accepts
   `output_format=<Pydantic model>` and `until_done()` returns a
   `ParsedBetaMessage` whose `parsed_output` is the validated report. Putting
   the model under `output_config["format"]` type-checks as a raw JSON schema
   and silently does not parse.
2. **A missing credential printed a traceback.** `provider_for` asserts the key
   and sat outside the CLI's `ConfigurationError` handler, so the one failure
   most likely to happen was also the least legible. Found by running the CLI
   before the key was set; fixed, with a regression test.
3. **A stub that records the request can make an append-only test vacuous.**
   The OpenAI provider appends to one `messages` list across turns, so a stub
   storing the reference made every recorded request show the final state —
   and the assertion that request *n* is a prefix of request *n+1* compared a
   list with itself and passed regardless. The stub now snapshots each turn.
   The test failing is what exposed it.
4. **`InvestigationTools` caches the assembled order cases per instance, and
   that is load-bearing.** `load_order_cases` reads every order, payload,
   capture, refund, fee and ledger entry for the tenant. An agent makes many
   tool calls per investigation, and rebuilding that per call would make
   retrieval cost grow with the model's curiosity. One investigation also then
   sees one consistent snapshot, so two tools cannot disagree because records
   moved between them.

## 8. Debts this block hands on

1. **No HTTP route and no job type** (D8). Block F owns the next deploy, the
   dashboard and the queued form. `investigate()` is already separated from the
   CLI so a job handler can call it without reaching through `main`.
2. **A latest-per-exception read is not built.** The tables are append-only, so
   Block F's investigation detail wants the newest row per
   `(order_external_ref, code)` — the same shape as Block D's debt 2. The index
   `ix_investigation_latest_per_exception` is in place for it.
3. ~~**The live demo needs seed, run, reset in that order.**~~
   **Discharged in this block, not handed on.** Kept here with its reasoning
   because the hazard still exists; what changed is that it now explains
   itself.

   *The collision.* Three reasonable choices are mutually incompatible. The
   agent CLI is a separate process and can only see **committed** rows, so
   running it by hand needs `demo_data --ensure`, which commits. The test
   suite **never** commits — the `session` fixture rolls everything back.
   And `tests/test_migrations.py` downgrades the schema to `base`, which
   committed rows break against `ondelete=RESTRICT`. **Block E is the first
   block that needed committed rows to be exercised at all**; every prior one
   could be validated entirely inside the rolled-back suite.

   *Why it was worth more than a note.* `assert_no_committed_marketplace_rows`
   already caught it — correctly, and before Postgres produced an error naming
   nothing. But its message offered only the collection-order cause, because
   that was the only cause when Block B wrote it. So the diagnosis sent a
   reader hunting a pytest ordering bug that was not there, while the fix was
   one unrelated command. It also does not fail as one clean assertion: 6
   failures and 43 errors, among them
   `test_order_dependence_guard.py::test_the_guard_passes_when_nothing_is_committed`
   — the guard's own self-test inverting, which is the least useful thing
   available to also be broken.

   *The fix.* The guard's message now names both causes, seeding first because
   it is the one with a one-command undo, and prints that command. Both causes
   and the fix are asserted by
   `test_order_dependence_guard.py::test_the_guard_fails_when_rows_exist`, and
   the message was verified against the **real** failure rather than only
   against the faked count. The explanation now lives where the failure
   happens, which is the only mechanism in this project with a track record —
   prose in a `validation.md` is how Block B's five mypy errors became 32.

   *What remains.* The hazard itself: seed, run, reset is still the local
   procedure. **Block G owns the rehearsal** and is the next thing to run the
   full demo locally; if that proves awkward, the fix is a separate database
   for live runs rather than a louder message. The deployed database is seeded
   once and never reset, so none of this reaches the demo.
4. **`fetch_disputes` resolves captures through the order case twice.** It
   reads `case.captures` for ids and the ORM rows separately. Harmless at
   eighteen orders and one query either way; worth folding together if the
   tool surface grows.
5. **The mypy gate is not widened, and that is a decision rather than an
   omission.** Block B's `validation.md` §8 recorded "five mypy errors outside
   `engine/`"; the true figure was 25 when checked during this block, and that
   section is now corrected with the reasoning.

   **None of them is a bug**, which is the part worth carrying: 17 are bare
   `dict`/`list` annotations and 5 are missing annotations, neither of which
   can change behaviour, and the four substantive ones were each assessed as
   correct code — a `Decimal.as_tuple().exponent` union the caller has already
   narrowed, a nullable composite factory Phase 2 established by test, and the
   `Mapped[X]`-versus-Protocol conflict D9 documents.

   So the case for widening the gate to `money/`, `connector/` and `paypal/`
   is **preventative**: those are money paths, and a genuinely wrong
   annotation there would not be caught because nothing looks. It is ~16
   mechanical fixes, mostly `paypal/replay.py`'s bare generics. Deliberately
   not done here — the hours went to the live investigations, which are an
   exit criterion where this is hygiene.

   **Fixing the errors is not what resolves it; the gate is.** Block B fixed
   32 of 39 and recorded five, and it returned to 25 because nothing failed.
   Whoever widens it should also decide `sim/` explicitly — out, in my view:
   it is the fixture generator, deliberately independent evidence against the
   engine, and `seed.verify()` recomputes every stated figure, which is a
   stronger check than types.
