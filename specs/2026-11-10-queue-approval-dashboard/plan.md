# Block F — plan

Implementation and tests in one pass per group. Run the suite after each group;
commit once per group. See `requirements.md` for the reasoning behind each.

## 1. Decision and scoring tables
- Migration: `exception_decision` (append-only; tenant, exception id,
  `approved|rejected`, approver, note, decided_at, optional investigation id).
  Register in `models/__init__.py`.
- `recon/priority.py` (pure, purity gate + strict mypy): four normalised
  factors, fixed weights, factor breakdown returned. Unit tests incl. empty
  confidence and mixed currencies.

## 2. Read API
- `reckonai/api/`: tenant dependency; `GET /api/runs`, `/api/runs/{id}`,
  `/api/exceptions` (latest run, scored, effective decision, latest verdict),
  `/api/exceptions/{id}` (cause list, cited refs, resolved flags, withheld).
- Latest-per-exception query on the existing index. Money as strings + currency.

## 3. Decision route
- `POST /api/exceptions/{id}/decision` with required approver; `409`-free,
  append-only, returns the new effective state. Test: no money-moving import,
  session holds only the decision row.

## 4. Queued investigation
- `investigate` job type in `jobs/handlers.py` calling `agent.investigate()`.
- `POST /api/exceptions/{id}/investigate` -> `202` + job id; `GET` job status.
  Duplicate click yields one job. Failed investigation is a recorded failure.

## 5. Frontend scaffold
- `frontend/` Vite + React 18 + TS + AG Grid; typed API client; money
  formatter; no arithmetic. FastAPI mounts `dist/` and a SPA fallback that never
  shadows `/api`, `/sim`, `/health`.

## 6. Views
- Exception queue (group by code/seller, sort by score, filter, confirmed vs
  suspected badge, decision state).
- Investigation detail: causes ranked, confidence, cited records with
  resolved/stable, withheld shown as unavailable, run-investigation button,
  approve/reject with approver field.
- Lean run summary grid with click-through to that run's exceptions.

## 7. Deploy configuration
- `render.yaml`: Node build step, agent credential + provider on the worker.
- CI: frontend typecheck + build.

## 8. Close out
- Browser pass over the seeded dataset; `validation.md` filled with evidence;
  update `CLAUDE.md` and the replan State section; PR.
