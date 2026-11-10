# Eval harness: validation

Mergeable when the gates are evidenced here. The agent results are **published as
they came**, misses included, and do not gate. The scoring rule in
`requirements.md` D3 was fixed before the run and was **not changed after seeing
it**; where it looks wrong, that is said below and left for a later run.

## 1. Gates

- [x] `python -m reckonai.eval` prints one table and exits 0 on the seeded data.
- [x] **6 of 6** faults classified as the manifest says, **6 of 6** impacts exact
  (amount and currency), currency kept in 6 of 6, every manifest reference cited
  in 6 of 6, **0 false positives on the 12 clean orders** (including ORD-0005 and
  ORD-0008), and 20 of 20 cited records exist (2 are fee ids that will not survive
  a reseed).
- [x] The scorer fails when it should: a dropped finding, a planted exception on a
  clean order, a wrong impact, a wrong currency, a fabricated reference and a
  missing manifest reference each turn the gate red (`tests/test_eval.py`).
- [x] The eval leaves the database as it found it: before and after a run the dev
  database held 1 tenant, 18 orders, 1 run, 6 exceptions, 0 investigations.
- [x] It refuses a non-local `DATABASE_URL`, and `--agent` stops with a named error
  before any call when no key is set (tests).
- [x] Full suite, `ruff` and strict `mypy` pass (see section 5).

## 2. Agent results (reported, not gated)

Run once on 2026-11-11: provider `openai`, model `gpt-4.1`, seven model calls.
**One draw per case.**

| Case | Verdict | Manifest refs cited | Fabricated | Automatic result |
|---|---|---|---|---|
| F1 missing ledger adjustment | reported | 0 of 3 | 0 | miss |
| F2 commission mismatch | reported | 1 of 3 | 0 | miss |
| F3 FX discrepancy | reported | 2 of 3 | 0 | miss |
| F4 duplicate ledger entry | reported | 2 of 3 | 0 | miss |
| F6 payout mismatch | reported | 0 of 3 | 0 | miss |
| F1, ledger withheld | insufficient_evidence | - | 0 | pass |
| F1, everything withheld | insufficient_evidence | - | 0 | pass |

**The automatic score is 2 of 7, and 0 of 5 on the faults.** The two cases that
pass are the evidence-removed ones: the agent said it could not tell, and did not
repeat a conclusion it could not support.

### A reader's judgement (mine, not automated)

Read against the manifest's description. This is a person's reading of one draw.

| Case | Is the stated cause right? | Note |
|---|---|---|
| F1 | Yes | Names the missing debit and commission reversal on the refund and says the position is overstated. Cites the refund under PayPal's id (`0CE88977WM977540P`), where the manifest names the marketplace's (`RFND-0013-A`): the same refund, a different id, so it is scored as missing |
| F2 | Yes | 30.00 retained against a 20.00 rule, confirmed by PayPal's 20.00 platform fee, which it cites |
| F3 | Right direction, hedged | Finds 262.00 against PayPal's 294.16 and says the difference is "likely" an inconsistent FX rate. It does not name a stale rate and still reports 0.95 confidence on a hedged cause |
| F4 | Yes | Both ledger entries cited, the 90.00 double credit stated |
| F6 | Right figures, thin evidence | 120.00 against 135.00 EUR, a 15.00 shortfall. It states what PayPal disbursed but does not cite the release record it rests on |

### What this says, and does not

- **The strict rule was too strict for what it was meant to catch.** It requires
  the generic order and item ids and the manifest's own spelling of an id, so it
  marks a correct cause as a miss. It also caught something real: on F6 the cause
  rests on a release record it never cites, and on F3 it says "likely" and is 0.95
  sure. A fairer rule (the load-bearing record cited, any id for the same record
  accepted) is a change for a later run, **not applied here**, so that the figures
  above are the ones the rule produced.
- **The agent does not echo a conclusion it cannot support.** Both
  evidence-removed cases passed, which had been observed only by hand before.
- **One draw is fragile.** The same model returned "no clear cause" on F3 in an
  earlier hand run and "cause found" here. No claim is made about a rate.
- **Not shown:** any other provider (Anthropic has never run), whether this
  repeats, and whether a different prompt or tool design would cite more. The
  reader's judgement is one person's.
- **Cost:** seven calls on `gpt-4.1`, about two minutes.

## 3. Documents

- [x] Readme limitation row replaced with these figures.
- [x] `CLAUDE.md`, the replan State and the roadmap updated.
- [x] `/code-review medium` run; 10 findings, all worked (table below).

## 4. Known gaps

The eval is not in CI (the deterministic half could be added in one line); the
agent half is a manual command; the demo script, the deployed rehearsal and the
readme limitations audit remain skipped.

## 5. Code review

`/code-review medium`: 10 findings, all real, all fixed, each with a test.

| Finding | Outcome |
|---|---|
| An exception in the agent half lost the free half's `--json` and flipped the exit code | Fixed: the agent half is contained; the results file is written in a `finally`; test makes the agent half raise and checks exit 0 and the file |
| An outage or refusal was scored as a wrong answer, and would have been published as a model-quality figure | Fixed: a `failed` verdict is **no draw**, neither pass nor miss, shown as "NO DRAW" and excluded from the count |
| An evidence-removed case passed on the verdict alone | Fixed: it must also carry no fabricated citation and no stated cause |
| False positives counted only on the 12 listed clean orders | Fixed: an exception on any order the manifest does not name as a fault is a false positive, listed or not |
| Duplicate references counted once per occurrence | Fixed: distinct references; a test doubles every citation and the count holds |
| Call count hardcoded in the banner | Fixed: derived from `planned_cases()` |
| A convoluted exception check rebuilt a tuple to test membership | Fixed: reads the flag the classifier stores |
| The local-only guard refused a socket URL | Fixed: no host means a local socket. **It still cannot see a remote database tunnelled to localhost**; stated in the code |
| The engine was not disposed on an error | Fixed: `finally` |
| The table said "fee ids" for a count of any unstable reference | Fixed: wording |

The fixes do not change the published figures in section 2: both evidence-removed
cases had no stated cause and no fabricated citation, so they pass under the
stricter rule too (checked against the saved run).

## 6. Regression

On a clean database: 1961 passed; `ruff` and strict `mypy` clean. 22 tests for
the eval itself, including the corruptions that must turn the gate red.
