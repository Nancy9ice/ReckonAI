# Phase 1 — Repo and deployment skeleton · Validation

**Branch:** `phase-1/repo-deployment-skeleton`
Scope: `./requirements.md` · Task groups: `./plan.md`

Merge-readiness is **demonstrated by commands that pass or fail**, not asserted.
Anything that cannot be automated is listed in §4 as an explicit manual check
with a stated reason.

Phase 1 splits its gate in two, per `requirements.md` D4. §3's checks run and
must pass **before** the merge. §4's deployed checks run **after** it, and the
phase is not complete until they do. Phase 2 may begin once §3 passes; it may
not begin while any §4 check is failing.

---

## 1. Exit criteria mapped to checks

Every roadmap Phase 1 exit criterion, and how it is proven.

| # | Roadmap exit criterion | Check | Type | Gate |
|---|---|---|---|---|
| E1 | `/health` returns 200 from the deployed Render URL, not just locally | §4 M2 | Manual | Post-merge |
| E2 | An empty Alembic migration applies cleanly against the Render database | §4 M3 | Manual | Post-merge |
| E2a | The same migration applies and reverses cleanly in CI and locally | C5, C6 | Auto | Pre-merge |
| E3 | `pytest` runs green in CI with at least one real assertion | C7, C8 | Auto | Pre-merge |
| E4 | The worker process starts, logs, and stays up | C9 local, §4 M4 deployed | Mixed | Both |
| E5 | `uv.lock` committed; every environment installs with `uv sync --frozen` | C1, C2, C3 | Auto | Pre-merge |
| E6 | Render plan constraints confirmed — workers available, no Postgres expiry window | §4 M1, M5 | Manual | Group 0, recorded |
| E7 | Connection pool sizes set explicitly per component | C10, C11 | Auto | Pre-merge |

Two criteria carry an extra note.

**E2 is deliberately proven three times** — locally against compose, in CI
against a service container, and on Render. Only the third satisfies the
criterion as written, but the first two are what make a future migration failure
visible before it reaches a deployed database.

**E6 was largely answered before this phase started.** [[tech-stack]] §11 records
the Hobby-plan check of 2026-11-07 and the credit resolution, and both facts §11
left open were answered the same day: **Cron Jobs are available** on the plan,
and the **Postgres connection cap is 100** — flat across the Basic tier, less the
10 Render reserves, so about 90 for the application (`requirements.md` §2). M1
records those rather than discovering them and confirms the one item still open,
that the plan carries no expiry window. M5 is now a recorded answer rather than
an investigation.

---

## 2. Checks the specs require beyond the exit criteria

These are not in the roadmap's list. They enforce standing rules from
CLAUDE.md and [[tech-stack]], and a Phase 1 that passed §1 while failing these
would be putting a convention violation into the first commit of the codebase.

| # | Rule | Source | Check |
|---|---|---|---|
| S1 | No emoji or pictographs anywhere in the codebase | [[tech-stack]] §13 | C12 |
| S2 | No `requirements.txt`, hand-maintained or generated | [[tech-stack]] §3 | C4 |
| S3 | No credential-shaped string in a tracked file | [[tech-stack]] §10 | C13 |
| S4 | No `float` in a money path | [[tech-stack]] §2 | Not applicable — no money path exists until Phase 2, which owns the gate. C14 asserts the absence rather than pretending to test it. |

---

## 3. Automated checks · pre-merge

Run in order, from a clean checkout of the branch. Every one must exit 0.

### C1 — A frozen install works from scratch

```bash
rm -rf .venv && uv sync --frozen
```

Exits 0. This is the claim [[tech-stack]] §3 makes about every environment, and
it is checked from an empty state rather than an already-warm one.

### C2 — The lockfile is authoritative

```bash
uv sync --frozen && git diff --exit-code uv.lock
```

A frozen sync must not modify the lockfile. A non-empty diff means a dependency
was added to `pyproject.toml` without re-locking, which is the drift that lets a
worker and a web service install different trees.

### C3 — The package imports

```bash
uv run python -c "import reckonai; print(reckonai.__version__)"
```

Proves the src layout and the editable install are wired, not just that the
dependencies resolved.

### C4 — No requirements.txt is tracked

```bash
test -z "$(git ls-files | grep -E '(^|/)requirements.*\.txt$')"
```

Exits 0 only when the set is empty. `requirements.md` D8.

### C5 — The baseline migration applies

```bash
docker compose up -d --wait
uv run alembic upgrade head
uv run alembic current
```

`alembic_version` exists and holds the baseline revision. Nothing else is
created, because the baseline is empty by design (`requirements.md` D5).

### C6 — The baseline migration reverses

```bash
uv run alembic downgrade base
uv run alembic upgrade head
```

Both exit 0. A first revision that cannot be reversed is a first revision that
was never tested, and every later phase inherits that.

### C7 — The test suite is green locally

```bash
docker compose up -d --wait
uv run pytest -q
```

Among what this proves, one assertion is worth naming because it guards a
**silent** failure rather than a loud one: a session fixture checks the server's
major version is **16**. Three Homebrew Postgres versions are installed on this
machine (`notes.md`), so a stray `brew services start postgresql@17` would bind
5432 ahead of the compose container and the suite would pass against the wrong
major version — the parity failure `requirements.md` D3 exists to prevent.
The same fixture is the cheapest check that `postgresMajorVersion: "16"` took
effect on Render (D12), since it runs wherever the suite points.

### C8 — The test suite is green in CI

```bash
gh run list --branch phase-1/repo-deployment-skeleton --limit 1 \
  --json conclusion --jq '.[0].conclusion'
```

Prints `success`. The exit criterion says *in CI*, so a local green run does not
satisfy it — CI is where the frozen install, the migration and the tests are
proven against a machine that holds none of this developer's state.

**And the suite must contain a real assertion**, which the criterion names
explicitly. `tests/test_health.py` asserting `200` from a route that opens a
Postgres connection is that assertion. A smoke test that imports a module and
asserts nothing would pass C7 and C8 while proving nothing, and would fail this
criterion on its intent.

### C9 — The worker starts, logs, and stays up

```bash
uv run python -m reckonai.worker > /tmp/worker.log 2>&1 &
sleep 30
grep -q 'reckonai.worker.start' /tmp/worker.log
grep -q 'reckonai.worker.tick' /tmp/worker.log
kill -0 $!            # still alive
kill -TERM $!         # and shuts down cleanly
```

Three separate claims, and all three matter: it started, it is logging, and it
did not exit. A process that logs once and dies passes a naive check.

### C10 — Pool sizes are explicit

```bash
uv run pytest tests/test_pools.py -q
```

Asserts that the engine factory requires its pool parameters — constructing an
engine without them raises rather than defaulting — and that the web and worker
entrypoints pass the allocation in `requirements.md` D6. SQLAlchemy's silent
default of fifteen connections per component is what this prevents.

### C11 — The pool budget fits the plan

Part of the same test module. The sum of the declared peaks stays under the
effective cap — 100 less Render's 10 reserved connections — with headroom for a
`psql` session, a pre-deploy migration and an overlapping Cron Job invocation.

**This check is weaker than it looks, and the weakness is worth stating.** At 90
usable connections even SQLAlchemy's defaults across three components — forty-five
— would pass it, so C11 cannot catch the mistake it was written to catch.
**C10 is the check that does**, because the engine factory refuses to build
without explicit pool parameters, which makes a default impossible rather than
merely unlikely. C11 stays because it is nearly free, and because it fails if
nobody recorded a cap at all — a real way to lose track of the budget before
Phase 9 adds concurrency ([[tech-stack]] §11).

### C12 — No emoji or pictographs

```bash
uv run pytest tests/test_conventions.py -q
```

Walks `git ls-files` and matches Unicode pictographic ranges. Typographic
punctuation, box-drawing characters, currency symbols and non-ASCII characters
in data must all pass; this gate bans pictographs, not non-ASCII
([[tech-stack]] §13). Confirm both directions once, by hand: insert a pictograph
and watch the test fail, then insert an em-dash and watch it pass. A gate that
has only ever been seen passing has not been tested.

### C13 — No credential-shaped string in a tracked file

```bash
git ls-files -z | xargs -0 grep -nE \
  'A[A-Za-z0-9_-]{20,}|EK[A-Za-z0-9_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY|postgres(ql)?://[^ ]*:[^ @]*@' \
  --exclude='*.md' || true
```

Reviewed by eye and must contain no real value. Carried forward from Phase 0's
C9, with the Postgres URL pattern added because this is the first phase with a
database URL to leak. `compose.yaml`'s local development credentials are the one
expected match and are not secrets — `requirements.md` D10 and `plan.md`
Group 4 step 1 say so in the file itself.

### C14 — The money path is absent, not untested

```bash
test -z "$(git ls-files 'src/**/*.py' | xargs grep -l 'Decimal\|NUMERIC' )"
```

Exits 0, confirming Phase 1 introduced no monetary code. This is not a money
check — it is the statement that there is nothing to check yet, so that nobody
reads Phase 1's green suite as evidence that the `float` rule is enforced. Phase
2 introduces `Money` and the grep gate that enforces it
([[roadmap]] Phase 2), and **this check is deleted then**, not inverted.

---

## 4. Manual checks · post-merge

Each is manual for a stated reason. Record the result of every one in
`notes.md`, with the date.

### M1 — Render plan and workers · recorded in Group 0

Confirm in the Render dashboard that the credits are applied, the plan permits
Background Workers, and the chosen Postgres plan does not expire inside the
project's life. Record the chosen instance tier and its `plan:` string next to
the 100-connection cap, so the budget and the thing it is drawn against sit in
one place.

**Manual because** it is account state in a third-party dashboard, with no API
this project is wired to read. **Mostly answered already** — [[tech-stack]] §11
plus §1's note above — so this confirms a resolution rather than repeating a
discovery. The expiry window is the one part genuinely still open.

### M2 — `/health` answers 200 from the deployed URL

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/health
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/health/ready
```

Both print `200`. The first is roadmap exit criterion E1 as written; the second
is the deployed proof that Postgres is actually connected, which `/health`
deliberately does not test (`requirements.md` §3.3).

**Manual because** it needs a provisioned Blueprint and a live URL, which do not
exist until the apply in `plan.md` Group 8. Automatable later as a smoke check
once the URL is stable, and worth doing before Phase 14's deploy.

### M3 — The baseline migration applied against the Render database

Confirm from the pre-deploy command's log output, and by inspecting
`alembic_version` on the Render database, that the baseline revision is applied
there. This is exit criterion E2 proven where the criterion means it.

**Manual because** it reads the deployed database and the deploy log. Note that
a *successful* pre-deploy is also the evidence that `preDeployCommand` is
available on the plan, which `requirements.md` D7 depends on.

### M4 — The worker is up on Render

In the worker service's log stream, find the `reckonai.worker.start` line and
subsequent tick lines, and confirm the service is still running at least ten
minutes after deploy. A worker that starts and dies produces the start line too,
which is why the elapsed check is part of this one.

**Manual because** it reads the Render log stream. This is the deployed half of
exit criterion E4; C9 is the local half.

### M5 — Render Cron Jobs · answered: available

**Answered 2026-11-07**, which closes the one item [[tech-stack]] §11 left open.
What remains is clerical: record it in `notes.md` so Phase 8 inherits it, and
record that the worker-loop timer fallback is therefore dropped.

**Manual because** it is a plan capability with no API this project reads.
**Not a blocker for Phase 1** — it is a finding Phase 8 consumes, and Phase 8
now consumes a yes.

---

## 5. What is explicitly not validated here

Stated so a reader does not mistake a green Phase 1 for more than it is.

- **No financial correctness.** There is no `Money`, no engine, no allocation and
  no amount. Phases 2 and 10 own that, and Phase 2's exit criteria carry the
  mixed-currency and rounding-residue tests.
- **No job queue semantics.** The worker claims nothing. `FOR UPDATE SKIP
  LOCKED`, retry with backoff and the `processed_events` unique constraint are
  Phase 8's exit criteria, including the two-workers-never-claim-the-same-job
  test.
- **No PayPal access.** Phase 1 adds no client and no PayPal credential. Phases
  0 and 0b established field reality; Phase 7 consumes it.
- **No load or concurrency testing.** The pool allocation is checked as a
  declared budget against a recorded cap, not under load. That is the right
  scope here, and Phase 9 is where concurrent ingestion first exercises it.

---

## 6. How this phase fails outright

Any one of these means Phase 1 is not merged, regardless of what else passes.

1. **A `requirements.txt` appears**, hand-maintained or generated into the tree.
   Two sources of dependency truth is the exact failure [[tech-stack]] §3 names.
2. **A credential reaches a tracked file.** Any real client secret, database
   password or private key. C13, and Phase 0's rule carried forward.
3. **A pictograph reaches the codebase.** C12. First commit of the codebase is
   the cheapest possible place to enforce it and the most expensive place to let
   it slide.
4. **The worker is folded into the web process.** A thread, a background task, or
   an `asyncio` loop inside FastAPI is a different architecture from
   [[tech-stack]] §4 and §11. If Background Workers turn out to be unavailable,
   the answer is to stop and amend the specs, not to build the substitute
   quietly.
5. **Pool sizes left at SQLAlchemy defaults.** Exit criterion E7 requires them
   set explicitly per component, and C10 enforces it by making the factory
   refuse to build an engine without them. The reason is no longer an imminent
   outage — at 90 usable connections the defaults would fit — it is that an
   unwritten budget cannot be checked once Phase 9 adds concurrency.
6. **`/health` proven only locally.** Exit criterion E1 says *from the deployed
   Render URL, not just locally*, and it says so because a locally-green
   deployment story is how deployment becomes a last-day problem — the single
   thing this phase exists to prevent.
7. **Migrations run from the web service's start command.** `requirements.md` D7.
   It works with one instance and becomes a lock-contention bug with two.

---

## 7. Related

- `./requirements.md` — scope, decisions D1–D10, and the context behind them
- `./plan.md` — the eight task groups and their commits
- `./notes.md` — where Group 0 and §4 record their findings
- [[roadmap]] Phase 1 — the exit criteria this document maps
- [[tech-stack]] §3, §4, §10, §11, §13 — the standing rules §2 enforces
