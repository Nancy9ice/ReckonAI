# UI refresh - requirements

Written 2026-11-11. Branch `ui-refresh`. A presentation change plus one
read-only API route. No classifier, engine, agent or decision-table change.

## 1. Why

A finance reader opening the queue sees six counts and one grid. Volume (how
many) is visible; value (how much) is only in a column that must be sorted to
read. The goal is to see both at a glance, in a sleeker look (violet, dark
sidebar, rounded cards, coloured pills) taken from the reference mock-up the
operator supplied.

## 2. Scope

1. **Shell.** Left sidebar (Exceptions, Coverage, Past checks) replaces the top
   tabs; page header with title and the Run reconciliation button. Violet tokens
   in `styles.css`.
2. **KPI cards that carry volume and value.** Each card: a count and the value
   at risk, per currency.
3. **By-issue-type panel.** One bar per issue type: length is value at risk,
   label is count. Clicking a bar filters the grid to that type.
4. **Grid.** Issue and certainty as coloured pills; a value bar behind the
   Impact cell, scaled within its own currency.
5. **Triage tabs with counts** above the grid: Needs a decision, Decided, All.
6. **Drawer** restyled; the investigation reads as an "AI investigation" card
   (probable cause, confidence chip, evidence, recommended next step) using the
   data it already has.

## 3. Rules that bind

- **Money is never summed across currencies** (P3: no FX rate is invented). A
  total is shown per currency; there is no grand total.
- **No money arithmetic in the browser.** Sums come from a new read-only route,
  `GET /api/summary`, computed with `Money` (which refuses mixed currencies).
- **"At risk" is the sum of `impact`** of the open exceptions in the group, in
  that currency. It is the engine's impact figure, not a loss estimate.
- Wording lives in `frontend/src/copy.ts`. The queue's problem column keeps its
  id `type`. The open-row highlight stays a style keyed to the row id.
- A confidence chip shows the agent's own stored figure; it is never computed
  here, and an uninvestigated row has none (not zero).
- `api/` stays free of money-moving tables; `/api/summary` reads the same
  newest-finding rows as `/api/exceptions` and writes nothing.
- No emojis anywhere. Icons are inline SVG.

## 4. Out of scope

Seller financial position view (R13), logins, changing any figure the engine
produces, any change to decisions or investigations.

## 5. Done means

- `npm run build` passes; `uv run pytest` passes after `demo_data --reset`;
  ruff check and strict mypy pass.
- `/api/summary` totals equal the sums of `/api/exceptions` impacts, per
  currency and per type (a test).
- The refreshed queue is looked at in a real browser on the seeded data.
- `validation.md` records what was and was not seen.
