"""The eval: [[mission]] section 6's measures, scored against the fault manifest.

`python -m reckonai.eval` reconciles the seeded dataset and prints one table;
`--agent` adds the model-dependent scores and spends model credit.

Two halves, kept apart on purpose:

- `deterministic` is free, exact and reproducible, and it **gates**: the command
  exits non-zero if a seeded fault is missed or a clean order is flagged.
- `agent` depends on a model, so it **reports**. It is published with the
  provider, the model id and the date, and it never fails the command.

Everything runs in one transaction that is rolled back, so the eval can be run
against a database someone is using and leaves it as it found it.
"""
