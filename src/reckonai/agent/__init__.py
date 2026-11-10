"""The AI investigation agent: explain an exception, citing real records.

Phase 12, kept entire by the replan's R11. This package is what turns a
classified exception into an evidence-backed report - ranked candidate causes,
each citing the record ids it rests on, each carrying a confidence, with
`insufficient_evidence` as a first-class answer rather than a failure mode
(specs/mission.md P5).

**It computes nothing.** Every figure in a report arrives from
`reckonai.reconcile.run`, which is the same deterministic path Block D's
scheduled run uses. specs/mission.md P1 is enforced structurally rather than by
instruction: no tool in `tools.py` accepts a monetary amount, so there is
nowhere for the model to put a number it invented. The agent explains figures;
it does not produce them.

**It is read-only.** No tool mutates financial state, nothing is enqueued and
nothing moves money (P6). The terminal action is writing a report plus a
*proposed* resolution that a human approves.

**It is outside the purity gate and inside strict mypy**, the second package
after `reckonai.jobs` to be both. It holds a session, a clock and a network
client - exactly what `engine` and `recon` refuse - and the absence of one gate
is why the other matters more here, not less. `tests/test_money_purity.py`
records why it is excluded rather than leaving it to inference.

**Two providers, one report.** `settings.agent_provider` selects Anthropic or
OpenAI; both return the same Pydantic report, and that shape is the seam
(specs/tech-stack.md section 5, amended 2026-11-10). There is **no fallback
between them**: a refusal ends the investigation and is reported as failed,
which is section 5's decision and is not reversed by a second provider being
available.
"""

from __future__ import annotations
