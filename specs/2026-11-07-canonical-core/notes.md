# Phase 2 — Canonical core · Notes

Findings, recorded gate results, and anything the implementation met that the
specs did not predict. Scope: `./requirements.md` · Plan: `./plan.md` · Gate:
`./validation.md`

---

## Group 0 — Prerequisite gate · CLOSED 2026-11-07

All four facts hold.

| # | Fact | Result |
|---|---|---|
| 1 | Branch cut from `origin/main`, not from `docs/phase-1-complete` | **Pass.** `origin/main` is an ancestor of `HEAD`. Phase 1's PR #8 is merged at `6e63dbc` |
| 2 | Local database up and `DATABASE_URL` exported | **Pass**, after starting Docker — see below |
| 3 | `alembic current` reports the baseline revision | **Pass.** `92be96b3c343 (head)` |
| 4 | Deployed service healthy **before** the phase changes anything | **Pass.** `/health/ready` `200`, `/health` `200` |

**On fact 1.** The branch was first cut from `docs/phase-1-complete` while that
branch was still unmerged, then reset onto `origin/main` once PR #8 landed. The
reset is why fact 1 is checked rather than assumed: a Phase 2 branch based on an
unmerged documentation branch would have carried its commit into this phase's
history and made the eventual PR diff misleading.

**On fact 2.** The Docker daemon was not running, so
`docker compose up -d --wait` failed with
`failed to connect to the docker API at unix:///Users/.../docker.sock`. Starting
Docker Desktop and waiting for the daemon resolved it in about eight seconds.
Worth noting only because the error names a socket path rather than saying the
daemon is down, which reads like a configuration fault and is not one.

**On fact 4.** This reading is the reason Group 9's identical check means
anything. `/health/ready` runs `SELECT 1`, so a `200` after the migration shows
the service came back with the new schema; without the before-reading it would
show only that the service worked at some point.
