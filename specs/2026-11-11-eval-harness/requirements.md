# Eval harness (the Phase 15 eval only)

> A partial revival of Block G, which was skipped on 2026-11-11 (`CLAUDE.md`).
> Only the eval is built. The scripted demo, the deployed rehearsal and the
> limitations audit stay skipped. Scope, and "publish results as they are",
> decided with the operator, 2026-11-11. Measures: [[mission]] section 6.

## 1. Scope

`python -m reckonai.eval` reconciles the seeded dataset and prints one table
against the fault manifest (`sim/manifest.py`: six faults, 12 clean orders).
`--agent` adds the model-dependent rows and spends model credit.

Out: a provider comparison (Anthropic has no credit), the demo script, the
rehearsal, an in-app page, CI, and any change to the seeded data.

## 2. Decisions

**D1. Two halves, one gating and one reporting.** The deterministic half is free,
exact and reproducible, so it **gates** (non-zero exit). The agent half depends on
a model, so it **reports**, with provider, model id and date, and never fails the
command.

**D2. The deterministic half reuses `tests/test_recon_gate.py`'s comparisons.**
Those tests already assert detection, zero false positives, exact impact and
manifest-evidence coverage. The eval returns the same checks as numbers and a table.
It is not a second source of truth: the manifest is read, never edited to fit.

**D3. Root cause is scored by what can be checked, then read by a person.**
Free text cannot be matched exactly and an LLM judge would be the model marking its
own work. A fault passes if the verdict is `reported`, the causes together cite
**every reference the manifest names** for it, and none is fabricated. The output
prints each cause beside the manifest's description. **A passing row can still be
wrong in a way only a reader sees**, so the published results add a reader's
judgement, labelled as such.

**D4. Two evidence-removed cases**, because one observation is not a property: F1
with the ledger withheld, and F1 with every record kind withheld. The correct answer
to both is `insufficient_evidence`; a `reported` verdict is a failure (the model
repeating a conclusion it cannot support).

**D5. The eval leaves nothing behind.** One transaction, rolled back; it reseeds
inside it. It refuses a `DATABASE_URL` that is not localhost, because it deletes and
re-creates the demo tenant before rolling back.

**D6. The model is the environment's** (`AGENT_PROVIDER`, `AGENT_MODEL`), printed at
the top. The agent half calls the model seven times (five faults, two negatives) and
says so before it starts. One draw per case; no statistical claim.

## 3. Context

- F5 (ORD-0017) is a late refund and the correct answer is `awaiting_evidence`, not
  an exception; the agent is not asked to investigate it.
- The deployed pair is `openai` / `gpt-4.1`; the Anthropic provider has never run.
- Known before running: F4 passed on production, F3 (FX) came back "no clear
  cause" once, F2 and F6 were never investigated.

## 4. Limitations to record

Agent results are one draw on one provider; root cause is checked by evidence and
read by a person, not proven; no model comparison; the eval needs a local database
and a model key for `--agent`.
