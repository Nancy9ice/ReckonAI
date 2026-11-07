# Phase 1 — Repo and deployment skeleton · Requirements

**Branch:** `phase-1/repo-deployment-skeleton`
**Roadmap phase:** [[roadmap]] Phase 1 · 0.5 day
**Primary output:** a deployable spine — FastAPI, Postgres, Alembic, pytest, CI,
and a Render Blueprint provisioning web service, worker and managed Postgres.

---

## 1. Goal

**A deployable spine, so deployment is never a last-day problem.**

Phase 1 is the first phase that writes application code. Nothing before it did:
Phases 0 and 0b produced `specs/paypal-fields.md` plus four disposable PEP 723
scripts under `scripts/`, and no `pyproject.toml` exists yet.

The deliverable is not a feature. It is the proof that every environment this
project will use — local, CI, the Render web service and the Render worker — can
install the same dependency tree, reach the same database, apply the same
migrations and run the same tests. Every later phase assumes that and none of
them re-proves it.

**What makes this phase worth a half day rather than an hour** is the
*deployed* half. A FastAPI app with a `/health` route is twenty minutes. A
`/health` route answering `200` from a Render URL, behind a Blueprint, with a
worker alive beside it and migrations applied against managed Postgres, is the
thing the roadmap's exit criteria actually ask for — and it is the thing that is
expensive to discover broken on day six ([[roadmap]] §Risks).

## 2. Why this runs now

[[roadmap]] Phase 1 depends on nothing, and is sequenced first among the
implementation phases for one reason: deployment risk is front-loaded
deliberately, exactly as Phase 0 front-loaded PayPal risk.

[[tech-stack]] §11 made that concrete on 2026-11-07. The account's Render
workspace is on the **Hobby** plan, where Background Workers do not exist, free
Postgres expires 30 days after creation, and a free web service spins down after
15 minutes idle. All three would have broken the topology the specs assume, and
all three were found by a plan check rather than by a failed deploy. $50 of
Render credits resolve it — a Starter web service, a Starter worker and a Basic
Postgres, roughly $20 a month, about two and a half months of runway.

So the plan-constraint exit criterion is **largely already satisfied**, and both
of the facts §11 left open were answered by the account holder on 2026-11-07:

- **The Postgres connection cap is 100, and below Pro-8gb it does not depend on
  which compute tier we pick.** Checked against Render's compute-plans reference
  on 2026-11-07: Basic-256mb, Basic-1gb and Basic-4gb all cap at 100, as does
  Pro-4gb. The limit first rises at Pro-8gb, to 200, which is far outside the
  credit budget. **Render reserves 10 of the 100 for direct and internal use**,
  so the application's real budget is about 90. This matters for a reason beyond
  the number: D6's allocation does not need re-deriving if the database lands on
  a smaller Basic tier than planned, because the cap is flat across the tier.

  **The cap being flat does not make the tiers equivalent.** Each Postgres
  backend costs a few MB of RAM, so a 256MB instance that is *permitted* 100
  connections will feel D6's peak of roughly 21 far more than a 1GB one will.
  The connection limit is not the constraint to choose a tier on; available
  memory is. Record the tier actually chosen in `notes.md` (`plan.md` Group 0
  step 2) so a later connection or latency problem can be read against it.
- **Render Cron Jobs are available on the plan.** §11 left this as the one
  genuinely open item and named the fallback: a scheduled trigger inside the
  worker loop. The fallback is not needed. **Phase 8 declares a real Render Cron
  Job**, and the third component of the connection budget is a real short-lived
  process rather than a hypothetical one.

What Phase 1 still owes is the `plan:` identifier as Render's Blueprint
reference spells it, and confirmation that the plan carries no expiry window.

## 3. In scope

### 3.1 The roadmap exit criteria, restated

- `/health` returns `200` from the deployed Render URL, not just locally.
- An empty Alembic migration applies cleanly against the Render database.
- `pytest` runs green in CI with at least one real assertion.
- The worker process starts, logs, and stays up.
- `uv.lock` is committed, and every environment installs with
  `uv sync --frozen` ([[tech-stack]] §3).
- Render plan constraints confirmed: Background Workers available on the current
  tier, and the Postgres plan has no expiry window inside the project's life
  ([[tech-stack]] §11).
- Connection pool sizes set explicitly per component, not left at defaults.

### 3.2 What that means in files

```
pyproject.toml              project metadata, dependencies, tool config
uv.lock                     committed; the authoritative dependency graph
.python-version             3.12
compose.yaml                local Postgres 16
render.yaml                 the Blueprint: web + worker + database
alembic.ini
.github/workflows/ci.yml
src/reckonai/
  __init__.py
  config.py                 Pydantic Settings, env-var only
  logging.py                dotted-event-name log setup
  db.py                     engine factory taking explicit pool parameters
  app.py                    FastAPI app, /health and /health/ready
  worker.py                 the worker loop
  migrations/               Alembic env.py and versions/
tests/
  conftest.py
  test_health.py
  test_migrations.py
  test_conventions.py       the no-emoji gate
```

### 3.3 Two routes, not one

`/health` is **liveness** and touches no database. `/health/ready` is
**readiness** and executes `SELECT 1`. Render's health check points at
`/health`.

This is a decision, not an accident, and the reason is sharper than separation
of concerns. Render restarts a service whose health check keeps failing. Point
that check at a route that opens a database connection and the two failure modes
feed each other: connections are exhausted, the check fails, Render restarts the
service, the fresh process immediately tries to build its pool, the check fails
again. **A restart does not add connections, so the loop does not converge** —
and the window in which it happens is exactly the window in which someone is
trying to read the dashboard to find out why.

**Revised 2026-11-07.** This decision was first justified by the cold start a
restart would cost during a judged demo. That was overstated: a Starter instance
does not spin down — only the free tier does — so a restart costs tens of
seconds, not a wake-up. The restart loop above is the real argument and the one
to keep.

The database's reachability is still proven — by `/health/ready`, by the
migration applying in pre-deploy, and by the test suite — just not by the thing
that decides whether to kill the process.

### 3.4 The no-emoji gate

CLAUDE.md and [[tech-stack]] §13 ban emoji and pictographs across the whole
codebase. Phase 1 is the first phase with a test suite, so it is the first
chance to make that rule enforced rather than remembered, and the gate costs
one test.

It bans pictographs, not non-ASCII: typographic punctuation, box-drawing
characters and currency symbols stay legal, and any non-ASCII character
occurring in real marketplace or seller data must never be stripped. The test
therefore matches Unicode pictographic ranges, not `str.isascii()`.

Phase 2 adds the equivalent gate for `float` in the money path
([[roadmap]] Phase 2). Phase 1 does not pre-build it, because there is no money
path yet to grep.

## 4. Out of scope

Each of these belongs to a named later phase. Pulling one forward would make
Phase 1 a day and a half and leave it shaped by guesses the later phase exists
to replace.

| Deferred | Owner |
|---|---|
| The `jobs` table, `FOR UPDATE SKIP LOCKED` claiming, retry and backoff, `processed_events` | Phase 8 |
| `Money`, the allocation helper, `tenant` / `seller` / `order` / `commission_rule` | Phase 2 |
| Any canonical financial table, `raw_payload`, the lifecycle enum | Phase 3 |
| The React + TypeScript + Vite app and a Render static site | Phase 14 |
| Any PayPal client code, and any PayPal environment variable in `render.yaml` | Phase 7 |
| A Render Cron Job declaration | Phase 8 |
| Authentication, roles, `tenant_id` filtering | Phases 2 onward, per [[tech-stack]] §10 |

Two further exclusions worth naming because they look like Phase 1 work:

- **The `scripts/` probe harness stays exactly as it is.** Those four files
  carry PEP 723 inline metadata and are run standalone; they are not added to
  `pyproject.toml`, not imported by the package, and not covered by the test
  suite. Phase 0's validation check C1 asserts the probe runs with no
  `pyproject.toml` present — that check is now historical, because Phase 1
  creates one, and `specs/2026-11-05-paypal-field-discovery/validation.md` C1
  should be read as satisfied at the time rather than re-run.
- **The worker does no work.** In Phase 1 it starts, configures logging, opens
  its own small pool, logs one line per tick and stays up. Giving it a job to
  claim before Phase 8 defines the `jobs` table means designing that table here,
  which is the mistake Phase 0 exists to prevent, one layer down.

## 5. Decisions

Recorded with rationale so they are not relitigated mid-build. D1–D4 were put
to the user on 2026-11-07 and answered; the rest follow from the specs.

**D1 — Backend only.** No frontend scaffold, no static site in `render.yaml`.
Every Phase 1 exit criterion is backend; Phase 14 owns the UI and is already the
only full day in the plan. Node tooling and a second CI job are not paid for by
anything this phase has to prove.

**D2 — CI is GitHub Actions.** Nothing in [[tech-stack]] names a CI provider, so
this fills a genuine gap. One workflow on push and pull request: `uv sync
--frozen`, `ruff check`, `alembic upgrade head` against a Postgres 16 service
container, then `pytest`. The migration step is in CI and not only on Render
because "an empty migration applies cleanly" should be a check that runs on
every future change, not a thing someone remembers to do by hand.

**D3 — Local Postgres is Docker Compose, Postgres 16.** `compose.yaml` runs the
same major version as the Render database and the CI service container, so a
migration cannot pass in one and fail in another on a version difference. The
alternative — pointing local tests at the Render database — adds a fourth and
fifth connector against a Basic plan's connection cap ([[tech-stack]] §11) and
means a local test run that truncates a table is operating on the deployed
database. Tests may create and drop schemas freely against compose; they may
never do so against Render.

**D4 — The Blueprint tracks `main`; the deployed checks are a post-merge gate.**
`render.yaml` is applied against `main`. Phase 1 merges once every check that can
run pre-merge has passed, and `validation.md` §4 then runs the deployed-URL
checks and the phase is marked complete only when they pass. The honest cost is
stated rather than hidden: for the length of one merge, the deployed criteria are
unproven. The alternative — provisioning the Blueprint against this branch,
proving `/health` live, then switching Render back to `main` — proves everything
pre-merge at the cost of a manual dashboard switch afterwards and a window where
the Blueprint's tracked branch does not match the repository's. Preview
environments were rejected outright: they provision their own Postgres per pull
request and would spend the credits that are funding the real topology.

**D5 — The first migration is empty, deliberately.** Phase 2 creates the first
tables. Phase 1's migration exists to prove the Alembic wiring — `env.py`
reading `DATABASE_URL`, `script_location`, the `alembic_version` table being
created on the Render database — and an empty revision proves all of that
without inventing a schema Phase 2 will replace. `alembic downgrade base` then
`upgrade head` must also be clean, so the first revision is reversible from the
outset rather than retrofitted.

**D6 — Pool sizes are explicit arguments, not defaults and not globals.**
`db.py` exposes an engine factory whose pool parameters are required, and each
entrypoint passes its own.

**Revised 2026-11-07, against a confirmed cap of 100 connections** (§2):

| Component | Pool | Overflow | Peak |
|---|---|---|---|
| Web service | 10 | 5 | 15 |
| Worker | 3 | 2 | 5 |
| Cron Job invocation | `NullPool` | — | 1 per invocation |

Peak around 21 with a couple of Cron Jobs overlapping, against an effective 90
once Render's 10 reserved connections are taken out (§2). The remaining headroom
is deliberate rather than timid: a `psql` session during a demo, a pre-deploy
migration, and Phase 9's concurrent ingestion all draw on the same cap.

**The honest consequence of a 100-connection cap is that exhaustion is no longer
the live risk, and this decision's justification changes with it.** SQLAlchemy's
defaults — `pool_size=5, max_overflow=10`, fifteen per component — would total
forty-five across three components and fit comfortably. So the reason for
explicitness is not that defaults would break anything today. It is that a
connection budget nobody wrote down cannot be checked when Phase 9 adds
concurrency, and that exit criterion E7 requires it regardless.

The check that therefore carries the weight is `validation.md` **C10** — the
factory refuses to build an engine without pool parameters, so a default is
impossible by construction — and not C11's sum-under-cap arithmetic, which 100
connections makes trivial to pass.

A Cron Job gets `NullPool` because it is a short-lived process that opens one
connection and exits; a pool would hold connections open for a process that is
about to die ([[tech-stack]] §11). Now that Cron Jobs are confirmed available,
that is a real component rather than a precaution.

**D7 — Migrations run in `preDeployCommand`, never in the web start command.**
Starter is a paid plan, so Render's pre-deploy command is available. Putting
`alembic upgrade head` in the start command means every instance races to
migrate on every restart; with one instance today that is survivable and with
two it is a lock contention bug that appears under load, which is the worst time
to find it. Pre-deploy also gives the right failure mode: a bad migration blocks
the deploy outright instead of half-applying across restarting instances.

**The constraint this imposes lands on Phases 2 and 3, not here.** Pre-deploy
runs while the *old* code is still serving traffic, so a destructive migration —
dropping or renaming a column the running instance still selects — breaks that
instance for the length of the overlap window. The discipline is
expand-then-contract: add the new column, deploy the code that writes both,
then drop the old one in a later migration. Phase 1's baseline is empty so
nothing is at stake yet, which makes this the cheap moment to write it down
rather than the moment Phase 3 reshapes `platform_fee`.

**D8 — No `requirements.txt`, generated or otherwise.** Render's Python runtime
is driven by a build command, and `pip install uv && uv sync --frozen` is that
command. [[tech-stack]] §3 bans a hand-maintained `requirements.txt` outright
and permits a generated one only where a target demands it. Render does not
demand it, so none exists, and `validation.md` checks for its absence.

**D9 — Log lines carry a dotted event name.** `reckonai.worker.tick`,
`reckonai.health.ready`, not prose and not pictographs. [[tech-stack]] §13's
reasoning is that a financial system's logs get grepped and parsed; a dotted
name is the greppable form of that. Plain stdlib `logging` with a configured
format — no structured-logging dependency, which is not in
[[tech-stack]] §1 and would need the document amended to add.

**D10 — Secrets stay in Render environment variables.** `DATABASE_URL` comes
from `fromDatabase` in the Blueprint so it is never written down. Anything else
secret is declared `sync: false`, which puts the key in version control and the
value in the dashboard. `.env` stays gitignored, `.env.example` stays the
template, and Phase 0's rule that no credential-shaped string reaches a tracked
file carries forward into this phase's validation unchanged
([[tech-stack]] §10).

## 6. Context a later reader needs

**What the repository already contains.** `specs/` with the five constitution
documents and two completed phase directories; `scripts/` with four PEP 723
scripts; `.env.example`, `.gitignore`, `CLAUDE.md`, `readme.md`. No Python
package, no `pyproject.toml`, no tests, no CI, no deployment configuration.

**The connection budget is comfortable, not tight.** Three Render components talk
to one Postgres instance, and [[tech-stack]] §11 states plainly that exhausting
connections takes down reconciliation and the API together. At the confirmed
100-connection cap that outcome is remote, so D6 is now about keeping the budget
legible for Phase 9 rather than about averting an imminent outage — and §3.3's
readiness split removes the one path by which connection pressure could escalate
into a restart loop.

**Phase 8's dependency on a Phase 1 finding is already satisfied.** Whether
Render Cron Jobs exist on this plan was the open question, and the answer is
yes (2026-11-07). Phase 8 declares a real Render Cron Job, the worker-loop timer
fallback is dropped, and the cron invocation is a genuine third draw on D6's
budget rather than a line item for something that might not exist.

**Nothing in this phase moves money, and nothing in it can.** There is no PayPal
client, no ledger and no balance. [[mission]] P6 is satisfied vacuously here,
and is restated only so the phase is not read as exempt from it.

## 7. Risks

**The Blueprint's first apply is the step most likely to fail**, and it fails in
the dashboard rather than in a test. Plan identifiers, the `runtime` key and
`preDeployCommand` availability are all things Render's own reference is
authoritative on, and `plan.md` Group 0 reads that reference rather than
trusting recall. A wrong `plan:` string is a failed apply, not a silently
degraded service, so it is loud — which is the good case.

**Credits are finite.** Roughly two and a half months at about $20 a month, from
2026-11-07. That is far beyond the project's life, but it is not unlimited, and
provisioning a second Postgres for previews or leaving a duplicate service
running would eat it. One Blueprint, three components.

**Deferring the deployed checks past the merge is a real exposure**, accepted
under D4. The mitigation is that `validation.md` §4 is a gate on marking the
phase complete, not a formality — if `/health` does not answer `200` from Render,
Phase 1 is not done, and Phase 2 starting is not permission to leave it that way.

## 8. Related specs

- [[mission]] — what we are building and the principles that bind it
- [[tech-stack]] — §3 (uv), §4 (the queue this phase prepares for), §11
  (hosting, the plan check, the connection budget), §13 (no emoji)
- [[roadmap]] — Phase 1's exit criteria, and the phases that depend on them
- `./plan.md` — the task groups
- `./validation.md` — the merge gate
