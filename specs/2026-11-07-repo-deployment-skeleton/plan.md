# Phase 1 — Repo and deployment skeleton · Plan

**Branch:** `phase-1/repo-deployment-skeleton`
Scope and decisions: `./requirements.md` · Merge gate: `./validation.md`

Each group leaves the repo in a working state and ends with one commit, so any
group can be reverted on its own. Do not start a group until the previous one's
exit condition holds.

Groups 1–7 run pre-merge. Group 8 runs **after** the merge, per
`requirements.md` D4, and the phase is not complete until it passes.

---

## Group 0 — Prerequisite gate · no commit · CLOSED 2026-11-07

Not implementation. Five facts to establish before anything is written, because
each one changes a file in a later group.

**All five hold. Findings and the provisioning decisions are in `notes.md`**;
`requirements.md` D11–D13 carry the three that needed deciding rather than
merely recording. The steps below are kept as written, so a later reader can see
what was asked as well as what came back.

1. **Render workspace access and credits.** Confirm the $50 of credits are
   applied to the workspace and that the plan permits Background Workers.
   [[tech-stack]] §11 records the Hobby-plan finding of 2026-11-07 and its
   resolution; confirm the resolution actually landed rather than assuming it.
2. **The exact Postgres plan identifier, and the tier.** The connection limit is
   already settled at **100**, and it is flat across the Basic tier, so it does
   not move if the tier does (`requirements.md` §2). What remains is the
   `plan:` string. Render's reference gives each instance two spellings — a
   display name (`Basic-1gb`) and a plan id (`0.5c-1g`) — and picking the wrong
   one is precisely what fails a Blueprint apply, so read the Blueprint
   reference for which field takes which, rather than inferring from the
   instance table. Record the chosen tier, its `plan:` string and the
   `postgresMajorVersion` key in `notes.md`.
3. **No expiry window.** Confirm the chosen Postgres plan does not expire inside
   the project's life, which is the second half of the plan-constraint exit
   criterion. A free instance expires 30 days after creation with a 14-day grace
   period; a paid one does not. Record which plan and what the answer is.
4. **Render Cron Jobs — answered: available.** Confirmed by the account holder
   on 2026-11-07, which closes the one item [[tech-stack]] §11 left open. Record
   it in `notes.md` so Phase 8 inherits it, along with the consequence: **the
   worker-loop timer fallback is dropped**, Phase 8 declares a real Cron Job in
   `render.yaml`, and that invocation is the third connection
   `requirements.md` D6 budgets for.
5. **Local toolchain.** `uv` installed, Docker running, `gh` authenticated
   against the repository's remote.

**Exit:** all five recorded in `notes.md`. Three are already answered — the
credit resolution, the 100-connection cap and Cron Jobs availability — and are
being written down rather than discovered; the `plan:` identifier and the
expiry answer are read from Render.
**If blocked:** if credits did not land or workers are unavailable, stop and
report. Do not proceed with an in-process worker thread as a substitute — that
is a different architecture from [[tech-stack]] §4 and §11, and adopting it
silently would make the specs wrong.

---

## Group 1 — Python project scaffold and lockfile · commit

1. Create `pyproject.toml`: project name `reckonai`, `requires-python = ">=3.12,<3.13"`,
   src layout with the package at `src/reckonai`.
2. Runtime dependencies, and only these — each is already locked in
   [[tech-stack]] §1, and anything not listed there needs that document amended
   first: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`,
   `sqlalchemy`, `alembic`, `psycopg[binary]`.
3. Dev dependencies as a `uv` dev group: `pytest`, `httpx` (for FastAPI's test
   client), `ruff`.
4. Create `.python-version` containing `3.12`.
5. Configure `ruff` in `pyproject.toml` — line length, lint rule selection,
   `src = ["src", "tests"]`. Keep the rule set small and real; a large selection
   enabled on an empty repo produces noise that gets suppressed rather than
   fixed.
6. Run `uv sync` and **commit `uv.lock`**. Then run `uv sync --frozen` and
   confirm it is a no-op.
7. Create `src/reckonai/__init__.py` carrying the package version only.

**Verify:** `uv sync --frozen` exits 0 and `git diff --exit-code uv.lock`
is clean. `uv run python -c "import reckonai"` exits 0.
**Commit:** `chore(phase-1): scaffold the python project and commit the lockfile`

---

## Group 2 — Configuration, logging, the engine, and the FastAPI app · commit

**Resequenced 2026-11-07, while starting it.** `db.py` moved here from Group 3,
because Group 2's `/health/ready` reads through the engine and Group 3 cannot
supply it afterwards. The real dependency order is
`config.py` → `db.py` → `app.py` → Alembic, so Group 3 is now Alembic alone.
Each group still stands on its own, which is the property the resequencing
protects.

1. `src/reckonai/config.py` — a `pydantic-settings` `Settings` model reading
   `DATABASE_URL`, `LOG_LEVEL`, `ENVIRONMENT`. No default for `DATABASE_URL`: a
   missing database URL must fail at startup with a named error, not fall back to
   localhost and then fail confusingly in a deployed environment. No credential
   is ever logged, and `Settings.__repr__` must not expose the URL
   ([[tech-stack]] §10).
2. `src/reckonai/logging.py` — one `configure_logging()` that sets a plain
   greppable format on the root logger. Dotted event names per
   `requirements.md` D9. No emoji, no pictographs, and no structured-logging
   dependency ([[tech-stack]] §1 and §13).
3. `src/reckonai/app.py` — the FastAPI application.
   - `GET /health`: liveness. Returns `200` with a small JSON body. **Touches no
     database** (`requirements.md` §3.3).
   - `GET /health/ready`: readiness. Executes `SELECT 1` through the engine and
     returns `200`, or `503` with a reason if the database is unreachable.
     It never returns `200` on a failed probe.
   - A lifespan handler that calls `configure_logging()` and disposes the engine
     on shutdown.
4. Amounts and monetary types appear nowhere in this group. There is nothing to
   serialize yet, so there is no JSON encoder decision to make here — Phase 2
   owns it when `Money` exists ([[tech-stack]] §2).

**Verify:** `uv run uvicorn reckonai.app:app` starts and `curl -s -o /dev/null
-w '%{http_code}' localhost:8000/health` prints `200`.
**Commit:** `feat(phase-1): add the fastapi app with liveness and readiness routes`

---

## Group 3 — Alembic and the baseline migration · commit

Steps 1 and 2 moved to Group 2 — see the note there.

1. `alembic init` into `src/reckonai/migrations`, then edit it down:
   - `env.py` reads the URL from `Settings`, never from `alembic.ini`. The
     `sqlalchemy.url` key in `alembic.ini` stays empty so a stale value cannot
     point a migration at the wrong database.
   - `env.py` uses a `NullPool` engine. A migration is a short-lived process.
   - Remove the generated file's commentary and any scaffolded content that
     does not apply; per [[tech-stack]] §13 this includes anything a tool emits
     that breaks the conventions, emoji included.
   - Set `target_metadata = None` for now, with a comment pointing at Phase 2,
     which introduces the first `MetaData`.
4. Generate the first revision **empty**: `alembic revision -m "baseline"` with
   no autogenerate. `upgrade()` and `downgrade()` both `pass`, and the docstring
   says why it is empty and which phase adds the first table
   (`requirements.md` D5).
5. Confirm reversibility by hand against the local database once Group 4 has one:
   `upgrade head`, `downgrade base`, `upgrade head`.

**Verify:** `uv run alembic upgrade head` against the compose database creates
`alembic_version` and nothing else. `uv run alembic downgrade base` is clean.
**Commit:** `feat(phase-1): add the database engine with explicit pools and an empty baseline migration`

---

## Group 4 — Local Postgres and the test suite · commit

1. `compose.yaml` — one service, `postgres:16`, with a named volume and a
   healthcheck. **Bind 5432**: checked 2026-11-07, nothing is listening on it
   and all three Homebrew Postgres services are stopped (`notes.md`), so the
   non-default port this step previously hedged about is unnecessary.
   Credentials are local-only development values and are not secrets; say so in
   a comment so nobody later mistakes them for a leak, and keep them out of
   `.env` so the gitignore rules stay meaningful.
2. `tests/conftest.py` — a fixture providing a database URL from the
   environment, with a clear skip or hard failure when it is absent. Decide
   which and state it in the fixture: a silent skip means a green CI run that
   tested nothing, so prefer a hard failure in CI and an explanatory skip
   locally, keyed on an environment variable CI sets.

   **A session-scoped fixture then asserts the server's major version is 16**,
   by reading `SHOW server_version` (or `current_setting('server_version_num')`)
   and failing with the version it actually found. This is not belt-and-braces.
   This machine has `postgresql@16`, `@17` and `@18` installed via Homebrew, all
   currently stopped; a stray `brew services start postgresql@17` would bind
   5432 ahead of the compose container and the whole suite would pass against
   the wrong major version. That is precisely the parity failure
   `requirements.md` D3 exists to prevent, and it fails **silently** — the one
   failure mode worth paying a fixture for. The guard runs in CI and on Render's
   database too, where it is the cheapest possible check that
   `postgresMajorVersion: "16"` actually took effect (D12).
3. `tests/test_health.py` — FastAPI test client. Assert `/health` returns `200`
   and its body shape; assert `/health/ready` returns `200` against a reachable
   database. These are the "at least one real assertion" the exit criterion asks
   for, and a status-code assertion on a route that genuinely touches Postgres
   is a real one.
4. `tests/test_migrations.py` — run `upgrade head`, assert `alembic_version`
   holds the expected revision, run `downgrade base`, then assert
   `alembic_version` holds **zero rows**. This is the automated form of Group 3
   step 5.

   **Corrected 2026-11-07, from running it.** This step previously said to
   assert the table set is back to empty. It never will be: Alembic keeps the
   `alembic_version` table across `downgrade base` and only deletes the row.
   Asserting an empty schema would fail against correct behaviour, which is the
   worst kind of test to write into a migration suite.
5. `tests/test_conventions.py` — the no-emoji gate over tracked files
   (`requirements.md` §3.4). Match Unicode pictographic ranges, not
   non-ASCII: typographic punctuation, box-drawing characters and currency
   symbols must pass. Walk `git ls-files` rather than the working tree, so build
   output and `.venv` are out of scope by construction.

   **Two traps, the first found by hitting it on 2026-11-07.** Do not test
   `unicodedata.category(ch) == "So"`: box-drawing characters are category `So`,
   and a draft check written that way flagged the six in [[tech-stack]] §4's
   topology diagram, which §13 permits by name. The usable test is
   Unicode's `Extended_Pictographic` property, and `unicodedata` does not expose
   it — so use explicit codepoint ranges rather than adding the `regex`
   package, which is not in [[tech-stack]] §1 and would need that document
   amended. Second, this repository's own `specs/` are tracked files and are
   full of legitimate non-ASCII; the gate must pass over them unchanged, which
   makes them a useful fixture rather than an exclusion.
6. Add a pool-allocation assertion: the web and worker engines expose the pool
   sizes Group 3 set, and their sum stays under the cap Group 0 recorded. This
   is what turns "pool sizes set explicitly" from a claim into a check.

**Verify:** `docker compose up -d` then `uv run pytest` is green, with at least
the four test modules collected.
**Commit:** `feat(phase-1): add local postgres, the pytest suite and the no-emoji gate`

---

## Group 5 — The worker process · commit

1. `src/reckonai/worker.py` with a `python -m reckonai.worker` entrypoint.
2. The loop: configure logging, build the worker engine with its own pool, then
   loop — log one line per tick at a sane interval, and nothing else. It claims
   no jobs, because the `jobs` table does not exist until Phase 8
   (`requirements.md` §4).
3. Handle `SIGTERM` and `SIGINT` so Render's deploy and restart cycle shuts the
   process down cleanly rather than having it killed. Dispose the engine on the
   way out.
4. A crash must exit non-zero so Render restarts it, and the exception must
   reach the log before the process dies. A worker that exits `0` on an
   unhandled error looks healthy and is not.
5. One log line on startup naming the component, the environment and the pool
   size it claimed. That line is what Group 8 looks for in the Render dashboard.

**Verify:** `uv run python -m reckonai.worker` logs a startup line, then tick
lines, and is still running after 60 seconds. `SIGTERM` exits `0` after logging
shutdown.
**Commit:** `feat(phase-1): add the worker process with its own connection pool`

---

## Group 6 — CI · commit

1. `.github/workflows/ci.yml`, on push and on pull request.
2. One job: check out, install `uv`, `uv sync --frozen`, start a `postgres:16`
   service container, `ruff check`, `uv run alembic upgrade head`,
   `uv run pytest`.
3. `uv sync --frozen` and not `uv sync`. A plain sync in CI would quietly
   re-resolve and the lockfile would stop being authoritative, which is the
   exact drift [[tech-stack]] §3 exists to prevent.
4. Assert the lockfile is unchanged after the sync, so a dependency added
   without re-locking fails CI instead of installing.
5. Cache the `uv` cache directory, keyed on `uv.lock`.
6. Pin the Postgres service container to `16` to match compose and Render.

**Verify:** the workflow runs green on this branch. `gh run list --branch
phase-1/repo-deployment-skeleton` shows a successful conclusion.
**Commit:** `ci(phase-1): run ruff, migrations and pytest against postgres 16`

---

## Group 7 — The Render Blueprint · commit

1. `render.yaml` declaring exactly three components ([[tech-stack]] §11), with
   the values Group 0 settled (`notes.md`). **`region: frankfurt` on all
   three** — per `requirements.md` D11, and irreversible on the database.
   - A **web service**: Python runtime, Starter plan, build
     `pip install uv && uv sync --frozen`, start
     `uv run uvicorn reckonai.app:app --host 0.0.0.0 --port $PORT`,
     `healthCheckPath: /health`, and `preDeployCommand:
     uv run alembic upgrade head` per `requirements.md` D7.
   - A **worker**: Starter plan, same build command, start
     `uv run python -m reckonai.worker`. No pre-deploy command — the web
     service owns migrations, and two components racing to migrate is the
     problem D7 avoids.
   - A **database**: `plan: 0.5c-1g` (Basic-1gb — a **plan id**, never the
     display name `Basic-1gb`, which fails the apply),
     `postgresMajorVersion: "16"` set explicitly because Render otherwise
     provisions the latest and the choice is irreversible, and
     `ipAllowList: []` for internal-only access per `requirements.md` D13.
     Leave `diskSizeGB` unset: it can be increased but never decreased.
2. `DATABASE_URL` on both services via `fromDatabase`, so the credential is
   never written into a tracked file ([[tech-stack]] §10 and
   `requirements.md` D10). `LOG_LEVEL` and `ENVIRONMENT` as literal values.
   **No PayPal variables** — Phase 7 adds those, and declaring them empty now
   invites them being filled in the dashboard and forgotten.
3. No Cron Job declaration. Phase 8 owns it, informed by Group 0 step 4.
4. No static site. `requirements.md` D1.
5. Add a short deployment section to `readme.md`: apply the Blueprint, the three
   components it creates, where `DATABASE_URL` comes from, and the local
   quickstart (`docker compose up -d`, `uv sync --frozen`,
   `uv run alembic upgrade head`, `uv run pytest`).
6. Parse-check the file locally — `uv run python -c "import
   yaml,sys; yaml.safe_load(open('render.yaml'))"` or equivalent. Render has no
   local validator, so this catches a syntax error but not a wrong key; the real
   validation is the apply in Group 8.

**Verify:** `render.yaml` parses, declares exactly three components, and
`grep -ni "paypal\|secret\|password" render.yaml` returns nothing but
`fromDatabase` wiring.
**Commit:** `feat(phase-1): declare the render blueprint for web, worker and postgres`

---

## Group 8 — Deploy and verify · post-merge · no code commit

Runs after the merge to `main`, per `requirements.md` D4. This group is where
the deployed exit criteria are actually earned.

1. Apply the Blueprint in Render against `main`. Watch the first build: a wrong
   plan identifier or an unavailable `preDeployCommand` fails here, loudly.
2. Confirm the pre-deploy command applied the baseline migration against the
   Render database — `alembic_version` present, holding the baseline revision.
   This is roadmap exit criterion E2, and it is the one that must be proven
   against **Render's** database rather than compose or CI.
3. `curl` the deployed `/health` and confirm `200`. Then `/health/ready` and
   confirm `200`, which is the deployed proof that Postgres is connected.
4. Confirm the worker is live: its startup line and tick lines in the Render
   log stream, and the service still up after ten minutes.
5. Record in `notes.md`: the deployed URL, the three component names, the
   Postgres plan and connection cap, the Cron Jobs answer from Group 0, and the
   observed first-build duration. The last one is cheap to note and useful when
   Phase 14 deploys under time pressure.
6. Run `validation.md` §4's manual checks and record each result.

**Verify:** every check in `validation.md` §1 has a recorded pass.
**Commit:** a docs commit recording the outcome —
`docs(phase-1): record the deployed environment and the plan findings`

---

## If something blocks

Phase 1 has no upstream dependency, so a block here is environmental. Two
specific ones, with the decision already made:

- **Background Workers unavailable.** Stop. Do not fold the worker into the web
  process as a thread. [[tech-stack]] §4 and §11 both specify a separate
  process, and the free tier's 15-minute spin-down would stop an in-process
  worker silently. Report it and amend the specs before building differently.
- **Render Cron Jobs unavailable.** Not a block for Phase 1 — record it and
  continue. Phase 8 absorbs it as a timer in the worker loop
  ([[tech-stack]] §11).
