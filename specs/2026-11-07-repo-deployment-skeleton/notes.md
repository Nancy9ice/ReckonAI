# Phase 1 — Repo and deployment skeleton · Notes

Findings recorded as they are established, per `plan.md` Group 0 and
`validation.md` §4. Dated, because several are facts about a third-party
account that can change.

---

## Group 0 — prerequisite gate

### Step 5 — local toolchain · 2026-11-07 · PASS

| Tool | Version | Note |
|---|---|---|
| `uv` | 0.10.12 (Homebrew) | `plan.md` Group 1 installs against this |
| Docker Engine | 29.3.1 | Daemon was not running at first check; started |
| Docker Compose | v5.1.1 | `compose.yaml` targets the `docker compose` subcommand, not the legacy `docker-compose` binary |
| `gh` | authenticated as `Nancy9ice` | needed by `validation.md` C8 to read the CI conclusion |
| Remote | `https://github.com/Nancy9ice/ReckonAI.git` | CI in `plan.md` Group 6 runs here |

**Local PostgreSQL installations — and the hazard they create.** Re-checked
2026-11-07, correcting a first reading. `postgres --version` reports **17.9**,
but that is only whichever version leads `PATH`: Homebrew has
**`postgresql@16`, `@17` and `@18`** installed, and `brew services` lists all
three as stopped. So a local 16 does exist and is a genuine fallback if Docker
misbehaves, which the first reading of this line denied.

**The real finding is the inverse of a missing version — it is three present
ones.** Nothing is listening on 5432 right now, so `compose.yaml` can bind it
directly and the non-default port `plan.md` Group 4 hedged about is unnecessary.
But a stray `brew services start postgresql@17` would then bind 5432 ahead of
the compose container, and the test suite would pass against the wrong major
version with no error anywhere. That is exactly the parity failure
`requirements.md` D3 exists to prevent, and it is silent.

**Mitigation, folded into Group 4 step 2:** a session-scoped fixture asserts the
server's major version is 16 and fails with whatever it actually found. It costs
one fixture, it runs wherever the suite points, and on Render it doubles as the
cheapest possible confirmation that `postgresMajorVersion: "16"` took effect
(D12).

Docker's two existing containers — `my-app-container` and `admiring_golick`,
both exited two weeks ago with no port bindings — are unrelated leftovers and
conflict with nothing.

### Step 4 — Render Cron Jobs · 2026-11-07 · AVAILABLE

Confirmed available on the workspace plan. This closes the one item
[[tech-stack]] §11 left open.

**Consequence, recorded so Phase 8 inherits it rather than rediscovering it:**
the fallback §11 named — a scheduled trigger inside the worker loop — is
**dropped**. Phase 8 declares a real Render Cron Job in `render.yaml`, and that
invocation is the third connection `requirements.md` D6 budgets for.

### Step 2 — connection cap · 2026-11-07 · 100, flat across the Basic tier

Read from Render's compute-plans reference. The cap is **not** a function of
RAM below Pro-8gb:

| Instance | Plan ID | RAM | Max connections |
|---|---|---|---|
| Basic-256mb | `0.1c-256mb` | 256 MB | 100 |
| Basic-1gb | `0.5c-1g` | 1 GB | 100 |
| Basic-4gb | `2c-4g` | 4 GB | 100 |
| Pro-4gb | `1c-4g` | 4 GB | 100 |
| Pro-8gb | `2c-8g` | 8 GB | 200 |

**Render reserves 10 of the 100** for direct and internal use — the
connection-pooling documentation states PgBouncer's default pool size as
`max_connections - 10` for that reason — so the application's budget is about
**90**. `requirements.md` D6's peak of roughly 21 sits well inside it.

**Corrected from an earlier assumption in this phase.** The 100 figure was first
recorded as a property of an 8GB-RAM tier. It is not: 8GB is Pro-8gb and allows
200, and 100 applies to every Basic tier. The number D6 uses is unchanged, but
the reason is, and it means **D6 does not need re-deriving if the tier changes**.

### Step 2 — Blueprint field findings · 2026-11-07

Read from Render's Blueprint specification. Four of these would have failed or
silently misconfigured the first apply in Group 8.

1. **`plan:` takes plan IDs, not display names.** `0.5c-1g`, not `Basic-1gb`.
   This is the ambiguity `plan.md` Group 0 step 2 was written to resolve, and it
   is resolved: use the plan id column above.
2. **Omitting `plan:` defaults a new database to `0.1c-256mb`.** So the tier is
   chosen by writing it down, not by leaving it out.
3. **`postgresMajorVersion` defaults to the latest supported version and cannot
   be changed after creation.** [[tech-stack]] §1 locks PostgreSQL 16, so the
   Blueprint sets `postgresMajorVersion: "16"` explicitly. Omitting it would
   provision 17 or later and break parity with compose and CI on the one axis
   `requirements.md` D3 exists to protect — and it could not then be fixed
   without destroying the database.
4. **`ipAllowList` omitted on a new database allows all IPs.** An empty list
   blocks all external connections, leaving internal Render traffic only. This
   is a live decision rather than a default to accept, given
   [[tech-stack]] §10 and that the database will hold marketplace financial
   records.

**Fields that cannot be changed after creation**, so the first apply has to be
right: `name`, `region`, `postgresMajorVersion`, `databaseName`, `user`. Group 8
is therefore not a step to improvise through.

Two confirmations of decisions already taken:

- **`preDeployCommand` is supported on paid plans**, for workers as well as web
  services. `requirements.md` D7 holds, and the web service is the only
  component given one.
- **`connectionPool` defaults to `none`.** Correct for this topology — PgBouncer
  is for a database needing more simultaneous connections than its plan allows,
  and D6's peak of 21 against 90 is the opposite situation.

### Step 1 — credits and Background Workers · 2026-11-07 · PASS

Confirmed in the Render dashboard: the $50 of credits are applied to the
workspace, and Background Workers are available as an instance type. This is the
condition [[tech-stack]] §11 identified as failing on the Hobby plan, and it is
now resolved rather than merely planned for.

### Step 3 — expiry window · 2026-11-07 · NONE

The chosen Postgres is a paid Basic tier (below), not the free tier, so the
30-day expiry and 14-day deletion grace period [[tech-stack]] §11 warns about do
not apply. The second half of roadmap exit criterion E6 is satisfied.

### Step 2 — the provisioning decisions · 2026-11-07

Settled with the account holder. The first two cannot be changed after the
database is created.

| Decision | Value | Reversible? |
|---|---|---|
| Region, all three components | `frankfurt` | **No** |
| Postgres major version | `"16"` | **No** |
| Postgres instance | Basic-1gb, plan id `0.5c-1g` | Yes, upward |
| `ipAllowList` | empty list — internal only | Yes |
| `connectionPool` | `none` (the default) | Yes |
| `diskSizeGB` | omitted, so the plan default applies | Increase only |

**Region: Frankfurt.** Closest Render region to West Africa, where the project
is built and demoed — roughly 100-150ms against 250-300ms from Oregon, on every
dashboard interaction in a live demo. It costs some latency on the backend's
calls out to PayPal's US-hosted sandbox, which is background work nobody
watches. The interactive path was chosen over the batch path deliberately.

**Basic-1gb rather than Basic-256mb.** Not a connections decision — every Basic
tier caps at 100. A Postgres backend costs a few MB of RAM, so 256MB is cramped
once D6's ~21 connections are open, and the symptom would be slow queries during
Phase 11 rather than a clear error. The cost consequence is recorded below.

**`ipAllowList` empty — internal Render traffic only.** The deciding factor is
that **the repository becomes public at hackathon submission**. `render.yaml` is
committed, so an allowlisted CIDR would publish which IP may reach a database
whose name, user and region sit in the same file. Credentials still gate it, but
it is a stale artefact that outlives the project and that nobody remembers to
remove. An empty list is safe to publish and **fails closed**: an IP added in
the dashboard for one debugging session is removed again by the next Blueprint
sync. Access to deployed data goes through Render's dashboard psql, which
authenticates as the account holder rather than opening a network path, and
`validation.md` M3 is doable that way.

**`diskSizeGB` left unset on purpose.** It may be increased but never decreased,
so the plan's own default is the right starting point; over-provisioning here is
a one-way door for no benefit.

---

## Amendment this phase owes to a constitution document

[[tech-stack]] §11 states the topology costs "about $20 a month" and that $50 of
credits covers "roughly two and a half months". Both assumed Basic-256mb.
With Basic-1gb at about $19 the topology is about **$33 a month**, so the credits
cover roughly **1.5 months** — still far beyond a one-week project, but the
figures in §11 are now wrong as written and were amended with this phase.

---

## Pre-merge gate · PASSING 2026-11-07

Every check in `validation.md` §3 run against this branch. CI run `42412103402`
on `phase-1/repo-deployment-skeleton` is green in 33 seconds.

| Check | Result |
|---|---|
| C1 frozen install from an empty `.venv` | PASS |
| C2 lockfile authoritative | PASS |
| C3 package imports, src layout wired | PASS |
| C4 no `requirements.txt` tracked | PASS |
| C5 baseline migration applies | PASS |
| C6 baseline reverses and re-applies | PASS |
| C7 suite green locally (45 tests) | PASS |
| C8 suite green in CI (45 tests) | PASS |
| C9 worker starts, logs, stays up, exits 0 on `SIGTERM` | PASS |
| C10/C11 pools explicit, budget fits | PASS |
| C12 no pictographs, both directions | PASS |
| C13 no private key in a tracked file | PASS |
| C14 money path absent, not untested | PASS |

**What remains is `validation.md` §4**, which cannot run before the merge by
`requirements.md` D4: applying the Blueprint, the deployed `/health` and
`/health/ready` responses, the baseline migration against Render's database, and
the worker's log stream. Phase 1 is **not complete** until those pass.

### Findings from Groups 1-7

Recorded because each was a wrong assumption caught cheaply, which is the same
value Phases 0 and 0b produced.

1. **SQLAlchemy resolves to 2.1 on an open constraint**, while
   [[tech-stack]] §1 locks 2.0. Pinned `<2.1`; taking 2.1 would have been a
   silent amendment to a locked decision.
2. **Render's `DATABASE_URL` uses a scheme SQLAlchemy cannot use as given.**
   `postgres://` is rejected outright by SQLAlchemy 2.0 and `postgresql://`
   selects psycopg2, which this project does not install. `config.py` normalises
   both to `postgresql+psycopg://`. Left alone this would have surfaced as an
   opaque failure during the Group 8 deploy.
3. **`plan.md` had Groups 2 and 3 in the wrong order** - Group 2's readiness
   route reads through the engine that Group 3 was to create. `db.py` moved into
   Group 2.
4. **Alembic keeps `alembic_version` across `downgrade base`** and empties it.
   `plan.md` Group 4 had said to assert an empty schema, which would have failed
   against correct behaviour.
5. **Alembic's revision template emits pre-3.10 typing**, which the project's
   `UP` lint rules flag, so every future migration would have arrived
   lint-failing. The template was rewritten and proved with a throwaway
   revision.
6. **The no-emoji gate flagged its own fixtures.** Four-digit `\uXXXX` escapes
   became literal pictographs on the way to disk, and the violation was
   invisible until the file was tracked, because the gate walks `git ls-files`.
   Fixtures are now built with `chr()`. This is the gate working, and it is the
   argument for CI over local runs.
7. **A readiness-gated health check cannot converge.** Verified by stopping
   Postgres: `/health` stayed `200` while `/health/ready` returned `503`. Had
   Render's `healthCheckPath` pointed at readiness, connection pressure would
   restart a service whose restart adds no connections.

---

## Group 0 — gate status · CLOSED 2026-11-07

All five prerequisites hold. `plan.md` Group 1 may start.

| Step | Status |
|---|---|
| 1 — credits, Background Workers | PASS |
| 2 — plan identifier, connection cap, Blueprint fields | PASS |
| 3 — no expiry window | PASS |
| 4 — Render Cron Jobs | PASS, available |
| 5 — local toolchain | PASS |
