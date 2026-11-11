# UI refresh - validation

Evidence recorded 2026-11-11, local only (seeded dev database, built frontend
served by uvicorn, worker running). `[x]` means shown by the command or
observation named; `[ ]` means not proven.

## 1. Gates

- [x] `uv run pytest`: 2055 passed, after `demo_data --reset`.
- [x] `ruff check`, strict `mypy` (now including the new summary code), and
  `npm run build` (runs `tsc --noEmit`) pass.
- [x] The numeric-coercion grep over `frontend/src` still matches only the
  existing grid height code in `columns.tsx` and a comment in `money.ts`. Bar
  lengths are drawn by CSS from server strings (`calc(var(--s) * 100%)`).

## 2. What was built

- `GET /api/summary` (read-only): open, confirmed, suspected, undecided and
  decided buckets, each a count plus value at risk per currency, and `by_type`
  with a per-currency `shares` figure (share of the largest type in that
  currency) already sorted largest first. Computed from `queries.exceptions`, so
  it cannot disagree with the queue. Sums use `Money`, which refuses mixed
  currencies.
- Violet tokens, dark sidebar, page header with the Run reconciliation button.
- KPI cards (count and per-currency value), "Where the value is" panel (click an
  issue to filter), triage tabs with counts, issue pills, a value bar in the
  Difference column (the existing priority "impact" factor), and the drawer's AI
  section as a card with the agent's own confidence chip.

## 3. Observed in Chrome (seeded data, after one run-now)

- [x] Cards read: 5 open, 5 confirmed, 0 suspected, 5 awaiting a decision,
  12 checked clean; value at risk EUR 15.00 and $159.16 on separate lines, no
  grand total.
- [x] The panel lists the five issue types with the manifest's figures
  ($90.00, EUR 15.00, $32.16, $27.00, $10.00); clicking one filtered the grid to
  that row and showed a clearing chip; tabs recounted to 1.
- [x] Grid shows pills and value bars; the open row keeps its left-bar highlight;
  the drawer shows the AI investigation card.
- [x] `test_summary_matches_the_queue_per_currency_and_never_mixes`: totals equal
  the sums of `/api/exceptions` impacts, per currency and per type.

## 4. Not proven

- [ ] The probable-cause card with the confidence chip was **not seen**: no
  investigation was run (each is a paid call). Only the "not looked at yet" state
  was observed.
- [ ] Not seen on the deployed site, on a phone width, or in dark mode.
- [ ] Bars are scaled within a currency, so a EUR 15.00 bar and a $90.00 bar can
  both be full length. The panel says so; a finance reader may still misread it.
- [ ] The Coverage and Past checks views were re-themed by tokens only and were
  not re-inspected.
- [ ] The "Needs a decision" tab is now the default, so Decided rows are hidden
  until the tab is chosen. Not reviewed by a finance user.
- [ ] Issue pill tints are a hash of the code, not a meaning; colour carries no
  severity.
