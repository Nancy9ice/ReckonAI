# Eval harness: plan

One pass per group, tests with the code, commit per group.

## 1. The deterministic scorer
- `reckonai/eval/deterministic.py`: findings and the manifest in, the measures out,
  and a table. Under strict mypy.
- Tests: passes on the seeded data; fails when a finding is dropped, when an
  exception is raised on a clean order, when an impact or currency is wrong.

## 2. The agent scorer
- `reckonai/eval/agent.py`: investigate F1, F2, F3, F4 and F6, then the two
  evidence-removed cases; score verdict, manifest-reference coverage and fabricated
  citations; keep the cause text for the reader.
- Pure scoring function, tested with stand-in results: a `reported` verdict on a
  negative case fails; a missing manifest reference fails; a fabricated one fails.

## 3. The command
- `python -m reckonai.eval [--agent] [--json PATH]`: one rolled-back session,
  localhost-only guard, model and call count printed first, exit 1 on a failed gate,
  exit 2 on a missing key.

## 4. Run it and publish
- Run the deterministic half and the agent half once each. Record the output and a
  reader's judgement of each cause in `validation.md`, as they came, misses included.

## 5. Documents and review
- Readme: a measured table replacing the "unmeasured" limitation row; `CLAUDE.md`,
  the replan State and the roadmap say the eval exists and the rest of Block G does
  not.
- `/code-review medium`; findings worked; PR.
