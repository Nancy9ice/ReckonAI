# Block F — validation

Mergeable when every box is evidenced in this file, with the command or
screenshot that proved it. A merge deploys, so §3 comes before the merge.

## 1. Phase 13 exit criteria
- [ ] Approve records decision + approver; test proves no ledger, refund or
      payout write and no money-moving import on the path (P6).
- [ ] Reject recorded with equal weight: same table, same fields, same read.
- [ ] Decisions are append-only; a second decision supersedes by read, the first
      row survives. A `CHECK`/trigger or test refuses update and delete.
- [ ] Confirmed and suspected are visibly distinct in API and UI.
- [ ] Score uses all four factors; breakdown returned; mixed-currency impact is
      ranked within currency, never converted. Pure-module tests pass under
      purity gate and strict mypy.

## 2. Phase 14 exit criteria (browser pass, Chrome, seeded dataset)
- [ ] Queue groups, sorts and filters; the 5 manifest exceptions appear and the
      clean orders do not.
- [ ] Detail shows cause, evidence records and recommendation; approve/reject
      reachable and persists across reload.
- [ ] Every amount shows its own currency; grep of `frontend/src` finds no
      arithmetic on amount strings and no `parseFloat`/`Number(` on money.
- [ ] A withheld kind renders "unavailable", not an empty list.
- [ ] Layout usable at laptop width; no emojis anywhere (hard rule).

## 3. Deployed environment (before merge)
- [ ] Node build works in Render's Python runtime (or the build step is moved
      and recorded). Otherwise the deploy fails; this is checked on a branch
      preview or settled by reading Render's runtime docs, and noted here.
- [ ] `render.yaml` carries the agent credential (`sync: false`),
      `AGENT_PROVIDER`, `AGENT_MODEL`; key set in the dashboard.
- [ ] Post-merge: `/`, `/health`, `/health/ready`, the dashboard URL and
      `/api/exceptions` respond; a queued investigation completes against the
      deployed worker. State which provider/model proved it.

## 4. Regression gates
- [ ] Full suite passes in order (not randomised); `ruff`, strict `mypy`,
      purity gate, frontend typecheck and build in CI.
- [ ] Zero exceptions on the fifteen clean purchase units unchanged.
- [ ] Seed -> run -> reset sequence documented if the UI needs committed rows.
- [ ] `/code-review medium` run; findings tabulated here with fixes.

## 5. Records to leave
- Corrections to earlier documents, debts handed to Block G, and anything that
  is proven only offline stated plainly, not rounded up.
