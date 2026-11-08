# Phase 3 — Canonical financial events and raw payload store · Notes

**Branch:** `phase-3/canonical-events`
Scope: `./requirements.md` · Groups: `./plan.md` · Gate: `./validation.md`

Findings and gate results, written as they happen rather than at the end.

---

## Group 0 — Prerequisite gate · 2026-11-08

All five facts established before anything was written.

| # | Fact | Result |
|---|---|---|
| 1 | Phase 2 closed, `origin/main` at `4e25c3f`, branch cut from it | Pass |
| 2 | Compose database up, `DATABASE_URL` exported | Pass |
| 3 | `uv run alembic current` reports `b5f8485e802e (head)` | Pass |
| 4 | Phase 2's gates green before this phase changes anything | Pass — 123 passed across `test_models_schema`, `test_money_purity`, `test_conventions`, `test_migrations` |
| 5 | Deployed service healthy | Pass — `/health` `200`, `/health/ready` `200` |

**One environment finding.** `DATABASE_URL` was unset for non-interactive
shells, so the database-backed tests would have skipped rather than run. It is
now exported from `~/.zshenv`, which is the file a non-interactive zsh reads —
`~/.zshrc` is not read, so an export there would have left the same silent skip
in place.
