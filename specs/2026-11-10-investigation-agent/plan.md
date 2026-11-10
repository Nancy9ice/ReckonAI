# Block E — plan

> Task groups, run in order. Each is one pass: implementation and tests written
> together, per the replan's R2. One commit for the block.
>
> Groups 1-3 are the substrate the model never sees. Group 4 is the first API
> call. Groups 5-7 are the audit constraints and the two criteria that carry
> the block. Group 8 closes out.
>
> **Load the `claude-api` skill before Group 4.** The request configuration in
> [[tech-stack]] §5.1 was derived from real API behaviour and several of its
> rows are 400s rather than preferences.

## 0. Amend `tech-stack.md` for the second provider

- §1: the LLM and orchestration rows, dated 2026-11-10 (D12). A runtime
  dependency absent from §1 requires amending §1 **first**, so this precedes
  Group 1 rather than following it.
- §5: a provider seam, and §5.1's table recorded as **Anthropic-specific**.
  §5.2's audit constraints explicitly apply to both providers.
- State plainly that the no-fallback decision is **not** reversed.

## 1. Dependencies, settings and the package

- Add `anthropic` and `openai` to `pyproject.toml` runtime dependencies,
  each commented with the §1 row that locks it (D11, D12). `uv sync`.
- `settings.agent_provider: Literal["anthropic", "openai"] = "anthropic"`,
  `settings.agent_model: str = "claude-haiku-5-5"`, and
  `anthropic_api_key` / `openai_api_key` as `SecretStr | None` — optional for
  the same reason `marketplace_base_url` is, and asserted at the point of use
  (D5).
- Add both keys to `.env.example` with placeholder values, alongside the
  PayPal block.
- Test: an `openai` provider whose model id still names a Claude model is
  refused at construction. The provider and the model have to agree, or the
  stored audit trail would claim a model the request never reached.
- `reckonai/agent/__init__.py`. Add `src/reckonai/agent` to `[tool.mypy]`
  `files`, and name it in the purity gate as a package that legitimately holds
  a session (D9).
- Test: the default model is `claude-haiku-5-5` and is read from settings, not
  from a constant in the agent module.

## 2. The two tables

- `reckonai/models/investigation.py`: `Investigation` (`tenant_id`, nullable
  `run_id`, `order_external_ref`, `code`, `provider`, `model_requested`,
  `model_returned`, `downgraded`, `stop_reason`, `verdict`,
  `summarized_reasoning`, `started_at`, `finished_at`) and
  `InvestigationCause` (`investigation_id`, `rank`, `description`,
  `confidence`, `cited_refs`, resolution outcome).
- `provider` is a checked vocabulary too, for the same reason `verdict` is.
- `verdict` is a checked vocabulary, not free text — the same reasoning as
  `RUN_STATUSES`. `code` is pinned to `ExceptionCode` by duplicated literals
  plus a test, the same pattern as `EXCEPTION_CODES`, because `models/` must
  not import the pure `recon/` package.
- `CHECK`: a `reported` verdict requires at least one cause, so "I found
  nothing" cannot be filed as a conclusion (D2).
- `UNIQUE (investigation_id, rank)`, so two causes cannot claim one rank.
- Register both in `reckonai/models/__init__.py` —
  `tests/test_model_registry.py` fails if either is missing.
- Alembic revision. `uv run alembic upgrade head` then `downgrade base` clean,
  no autogenerate drift.

## 3. The tools, read-only, with the withhold set

- `reckonai/agent/tools.py`: `InvestigationTools(session, tenant_id,
  withhold=frozenset())`, exposing the [[tech-stack]] §5.2 list — order, items
  and sellers; payments, captures, refunds, disputes; ledger entries and
  disbursements; commission and refund rules; the raw payload for a named
  record; and `recompute`, calling `reckonai.reconcile.run`.
- Every tool filters by `tenant_id`. **No parameter is a monetary amount** —
  references and filters only (D3).
- Retrieval returns the fields a decision needs. A full raw payload is fetched
  only by explicit record reference (D7).
- `withhold` empties the named kinds. `recompute` is never withheld (D4).
- `reckonai/agent/citations.py`: the resolver. Every `evidence_ref` shape the
  classifier emits resolves; `platform_fee.external_id` is **rejected** rather
  than resolved, because it is rebuilt on every seed (D6).
- Test: **no tool can write.** Enumerate the exposed tools and assert each
  leaves the session with no pending changes and no `INSERT`/`UPDATE`/`DELETE`
  issued. Phase 12's fourth exit criterion.
- Test: the module imports nothing from `reckonai.jobs`.
- Test: every `evidence_ref` on every finding from the seeded dataset resolves.
- Test: `withhold={"ledger"}` empties the ledger tool and leaves F1 classified
  exactly as before.

## 4. The report model and the provider seam

- `reckonai/agent/report.py`: the Pydantic report — ranked causes, each with a
  description, a confidence and its cited ids, plus the `verdict` of D2.
  **Shared by both providers**; it is the seam (D12).
- `reckonai/agent/providers/base.py`: the protocol. One method, taking the
  tools and the prompt, returning the report plus the audit facts — provider,
  model returned, stop reason, summarized reasoning.
- `reckonai/agent/providers/anthropic.py`: `@beta_tool` functions passed to
  `client.beta.messages.tool_runner(...)`, `runner.until_done()`. Request
  configuration is [[tech-stack]] §5.1 exactly: adaptive thinking with
  `display` explicit, `output_config` effort `medium` explicit,
  `max_tokens=64000`, streaming with `.get_final_message()`, eager input
  streaming off, no sampling parameters, caching on the system prompt and tool
  definitions, `tool_choice` `auto` plus `strict: true`. Verify the runner
  accepts `output_config` against the installed SDK; if it does not, the final
  turn requests the structured report and the choice is recorded in
  `validation.md` rather than left implicit.
- `reckonai/agent/providers/openai.py`: the same contract over OpenAI's own
  tool-calling loop and structured outputs. **Its configuration is recorded in
  `validation.md`, not inherited from §5.1** — that table is Anthropic-specific
  and several of its rows are 400s there rather than preferences.
- One system prompt, shared: the read-only contract, that every cause cites
  real record ids, that `insufficient_evidence` is a correct answer, and that
  the figures come from `recompute` and are never recalculated.
- Test: the Anthropic request kwargs carry every §5.1 value and **no**
  `temperature`, `top_p`, `top_k` or `budget_tokens`, asserted against a stub
  client.
- Test: both providers, driven by stubs through the same scripted tool
  sequence, produce the same report shape. That equivalence is what makes
  Block G's comparison meaningful.

## 5. The audit constraints

- `stop_reason` is read **before** any content block (D5), on **both**
  providers. A refusal or policy stop persists `verdict: failed` with the stop
  reason and no causes.
- `model_returned` from the response is stored and compared against
  `model_requested`; `downgraded` is set where they differ. `provider` is
  stored alongside it.
- The summarized thinking is persisted as supporting context, never as the
  authoritative cause.
- The loop appends. No compaction, no context editing, no trimming of earlier
  tool results (D7).
- `reckonai/agent/store.py`: persist the investigation and its causes in one
  transaction, with each citation's resolution recorded.
- Tests, all against a stubbed runner so CI makes no API call: a `refusal`
  stop reason yields `failed` and no report; a mismatched model id sets
  `downgraded`; a report round-trips through both tables losing no field; an
  unresolvable citation is persisted as unresolved rather than dropped.
- Test: the message list only ever grows — asserted on the stub's recorded
  turns, not by inspection.

## 6. The CLI

- `reckonai/agent/investigate.py`, a `python -m` entry point taking a tenant
  plus `--order` or `--fault`, and `--withhold`, plus `--provider` to override
  the setting for one run. Same shape as `reckonai.jobs.schedule`.
- It selects the exception through `exceptions_only()`, never `len(findings)`.
- An absent key for the selected provider fails with a named error naming that
  provider, not a traceback.
- Test: `--fault F1` resolves to the right order and code from the manifest.

## 7. The two criteria that carry the block

Run by hand against the seeded database with a real key, and the output pasted
into `validation.md`. CI covers the deterministic parts and makes no call.

**Two live calls total, both on the Anthropic default.** The operator's budget
is two investigations, so the OpenAI path ships unit-tested and not live-run —
recorded as a limitation, and Block G is what scores it (D12). Prompt tuning
happens against the stub, never against the live API.

- **Scenario 1 end to end.** `--fault F1` names the missing ledger adjustment
  as the cause and cites the refund and ledger record ids. Every cited id
  resolves.
- **The evidence gap.** The same exception with `--withhold ledger` returns
  `insufficient_evidence` rather than a guess.
- Record the token counts and wall time for both, since Block G needs a
  baseline and §5.2 puts the cost exposure in evidence volume.

## 8. Close out

- `uv run pytest`, `uv run mypy` clean over `engine`, `recon`, `jobs` and now
  `agent`, `uv run ruff check`, migration clean up and down.
- `validation.md`: the six exit criteria with how each was proven, the two
  recorded transcripts, what this block does not prove, and the debts.
- `readme.md` limitations: a refused investigation is reported as failed and
  not retried on a second model **or a second provider**; and the OpenAI path
  is implemented but not measured.
- Update `CLAUDE.md` and the replan's State section: Block E complete, Block F
  next.
- `/code-review medium` before the PR, as Block D did. Nine of nine were real
  there, so the findings are worked rather than triaged.
