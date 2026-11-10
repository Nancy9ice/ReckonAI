# Block E — validation

> 1,783 tests pass (104 of them Block E's), `uv run mypy` clean over `engine`,
> `recon`, `jobs` and now `agent`, `uv run ruff check` clean, migration clean up
> and down with no autogenerate drift.
>
> **No test makes an API call**, to either provider. Section 3's two live runs
> are the only model calls in the block, and they are run by hand.

## 1. Phase 12's exit criteria

| # | Criterion | How it is proven | State |
|---|---|---|---|
| 1 | Scenario 1 is investigated end to end and names the missing ledger adjustment, citing the refund and ledger record ids | Wiring: `test_agent_investigate.py::test_scenario_1_is_investigated_end_to_end` plus `test_agent_tools.py::test_scenario_1_is_reachable_through_the_tools`. The model's own answer: §3 | **pending the live run** |
| 2 | Every cited id resolves to a real record — no fabricated references | `test_agent_tools.py::test_every_evidence_ref_on_the_whole_dataset_resolves` over every finding on the seeded dataset; `unresolved_refs` persisted per cause; a fabricated id surfaced by `test_a_fabricated_citation_is_surfaced` | **met** |
| 3 | An exception with evidence deliberately removed returns `insufficient_evidence` rather than a guess | Instrument: `test_withholding_reaches_the_tools_and_the_row`, plus `test_withholding_does_not_change_what_the_classifier_finds`. The model's own answer: §3 | **pending the live run** |
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
marketplace rows: `tests/conftest.py::assert_no_committed_marketplace_rows`
fails, `test_migrations.py`'s downgrade to `base` cannot run against committed
tenant rows, and `test_order_dependence_guard.py` fails. Six failures and 43
errors, none of them about the agent. `demo_data`'s own docstring warns that
`--ensure` "is not for a developer's test database"; the warning is easy to
read past while setting up a demo.

To be filled in from the runs: the invocation, the verdict, the ranked causes
with their confidences and cited ids, the citation resolution outcome, and the
token counts and wall time.

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

- `uv run pytest` — 1,783 pass. No test makes an API call.
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
   observation is not a rate, and a cheaper model is likelier to confabulate a
   cause that reads well. **Block G scores that branch specifically, on both
   models and now both providers.**
2. **That `medium` effort retrieves enough evidence.** The signal to watch is a
   report whose cited ids thin out while its conclusions stay confident. §3's
   token counts are the baseline; Block G re-measures and may raise effort.
3. **That the OpenAI path works against the live API.** Implemented and
   unit-tested against a stub; the operator's budget was two live calls and
   both went to the measured Anthropic default.
4. **That the agent handles an exception shape the seeded dataset lacks.** Six
   faults are investigable. The engine's named raises — mixed currency, an
   unresolvable commission rule — classify correctly and have never been handed
   to the agent.
5. **Anything about cost at volume.** Two investigations is not a cost model.
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

## 7. Four things found by running it

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
3. **The live demo needs seed, run, reset in that order.** §3's note. If Block
   F wants investigations visible in the deployed dashboard, the deployed
   database is seeded and never reset, so this tension is local only — but a
   developer running the agent locally must reset afterwards or the suite
   fails in six places that say nothing about the agent.
4. **`fetch_disputes` resolves captures through the order case twice.** It
   reads `case.captures` for ids and the ORM rows separately. Harmless at
   eighteen orders and one query either way; worth folding together if the
   tool surface grows.
