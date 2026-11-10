# Block E — validation

> What has to be true before this merges. Written before the work, so the bar
> is set rather than described afterwards. Each row gets its evidence filled in
> as it is proven.

## 1. Phase 12's exit criteria

| # | Criterion | How it will be proven | State |
|---|---|---|---|
| 1 | Scenario 1 is investigated end to end and names the missing ledger adjustment, citing the refund and ledger record ids | A real run of `--fault F1`, transcript in §3 | pending |
| 2 | Every cited id resolves to a real record — no fabricated references | The resolver, plus a test that every `evidence_ref` on every seeded finding resolves, plus §3's run showing zero unresolved | pending |
| 3 | An exception with evidence deliberately removed returns `insufficient_evidence` rather than a guess | A real run of `--fault F1 --withhold ledger`, transcript in §3 | pending |
| 4 | No tool can mutate financial state | Enumerate the exposed tools; assert each leaves no pending changes and issues no write; assert `agent/tools.py` imports nothing from `reckonai.jobs` | pending |
| 5 | `stop_reason` is checked before response content is read | A stubbed `refusal` yields `verdict: failed`, no causes, and the content is never touched | pending |
| 6 | The summarized reasoning is persisted with the report, and the model id is stored and compared — a fallback produces a report flagged as downgraded | Round-trip test through both tables; a stubbed mismatched model id sets `downgraded` | pending |
| 7 | Both providers satisfy every §5.2 constraint and return one report shape | Stubbed runs of each through the same scripted tool sequence, asserted equal in shape (D12) | pending |

**Criterion 6 needs reading precisely rather than ticking.** Haiku 5.5 has no
server-side refusal fallback, so **nothing can set `downgraded` in the MVP**
because nothing downgrades at all. That is not an exemption from the rule — the
stored-and-compared columns are what prove the claim, and the flag is exercised
by a stub rather than by a real fallback. [[tech-stack]] §5's amendment calls
this the honest version of the rule, and the row is met on that reading.

**Criterion 1's citation must be the capture where fee evidence is involved**,
never `platform_fee.external_id`, which is rebuilt from a payload UUID on every
seed. The resolver rejecting it is what makes criterion 2 hold across reseeds
rather than only today.

## 2. The four audit constraints

[[tech-stack]] §5.2 is as binding as the money rules, so each is checked
directly rather than assumed from the request configuration.

| Constraint | Check |
|---|---|
| Reasoning is recorded | The summarized thinking is non-empty and persisted; `display` is set explicitly in the request, since its default `"omitted"` returns thinking blocks with empty text |
| A model swap is disclosed, never silent | §1 criterion 6 |
| The loop is append-only | The stub's recorded turns only grow; no compaction, no context editing, no trimming of earlier tool results |
| The AI layer is not reproducible | No `temperature`, `top_p`, `top_k` or `budget_tokens` in the request; the report is asserted on *structure* and on cited ids, never on wording |

The last row is why no test asserts a specific sentence. Two investigations of
one exception can produce differently-worded reports and differently-ranked
causes, and a test pinning prose would fail for the wrong reason. Every
*number* in the report comes from `recompute`, which is byte-for-byte
reproducible and already unit-tested.

Also checked: the request carries `output_config` effort `medium`, `max_tokens`
`64000`, streaming with `.get_final_message()`, eager input streaming off, and
caching on the system prompt and tool definitions.

**Row 7 is the second provider.** All four constraints are checked against the
OpenAI path too, because they are properties of an auditable financial product
rather than of an SDK (D12). §5.1's request table is **not** reapplied there —
it is Anthropic-specific, and several of its rows are 400s on that API rather
than preferences. The OpenAI configuration actually sent is recorded in §3a.

### 3a. The OpenAI provider's recorded configuration

To be filled in: the model id, how the structured report is requested, how the
tool loop terminates, and where its stop reason is read. Recorded here because
no section of `tech-stack.md` describes it and a reader would otherwise assume
§5.1 applies.

## 3. The two recorded transcripts

To be filled in from the real runs, with the invocation, the verdict, the
ranked causes with their confidences and cited ids, the citation resolution
outcome, and the token counts and wall time.

```
uv run python -m reckonai.agent.investigate --fault F1
uv run python -m reckonai.agent.investigate --fault F1 --withhold ledger
```

**Why these are hand-run and not in CI.** A paid call per test run is not
acceptable and CI has no key, and a live test skipped on a missing key would
make a green CI run cover nothing — which this project already treats as the
worse failure, deliberately, for the database-backed tests. So the
deterministic parts are tested offline against a stubbed runner and the two
model-dependent criteria are evidenced here once.

## 4. Gates

- `uv run pytest` — the suite passes. **No test makes an API call**, to either
  provider.
- `uv run mypy` — clean over `engine`, `recon`, `jobs` and now `agent`.
- `uv run ruff check` — clean.
- `uv run alembic upgrade head` then `downgrade base` — clean both ways, no
  autogenerate drift.
- The purity gate still passes, with `agent/` named as a package that
  legitimately holds a session rather than slipping through.
- `tests/test_model_registry.py` passes with both new models registered.
- No emojis anywhere, and no `float` on any monetary value.
- **No test randomisation and no `-n auto`.** The suite is order-dependent and
  passes because pytest collects alphabetically.
- `/code-review medium` run and its findings worked, as Block D did.

## 5. What this block will not prove

Stated so Block G inherits the question rather than discovering it.

1. **That the model takes the `insufficient_evidence` branch reliably.** This
   block proves the branch is reachable and correct on one withheld case. One
   observation is not a rate, and a cheaper model is likelier to confabulate a
   cause that reads well. Block G scores that branch specifically, on both
   models.
2. **That `medium` effort retrieves enough evidence.** The signal to watch is a
   report whose cited ids thin out while its conclusions stay confident. The
   token counts in §3 are the baseline; Block G re-measures and may raise
   effort to `high`.
3. **That the agent handles an exception shape the seeded dataset does not
   contain.** Six faults are investigable here. The engine's named raises —
   mixed currency, an unresolvable commission rule — classify correctly and
   have never been handed to the agent.
4. **Anything about cost at volume.** Two investigations is not a cost model.
5. **That the OpenAI path works against the live API.** It is implemented and
   unit-tested against a stub; the operator's budget was two live calls and
   both went to the measured Anthropic default. Block G runs it for real.

## 6. Debts this block will hand on

To be filled in. Expected, from the decisions:

1. **No HTTP route and no job type** (D8). Block F owns the next deploy, the
   dashboard and the queued form.
2. **A latest-per-exception read is not built.** The tables are append-only, so
   Block F's investigation detail needs the newest row per
   `(order_external_ref, code)` — the same shape as Block D's debt 2, and the
   index should be added when the query is written rather than guessed now.
