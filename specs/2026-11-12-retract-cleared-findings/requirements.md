# Retract cleared findings — scope, decisions, context

## 1. Problem

A run writes a row per finding and nothing for an order that comes out clean.
The queue and Coverage show the newest row per `(order, code)`, so that row
stayed the newest for ever. A fixed fault, or a refund whose PayPal record
arrived late, kept showing as open. Seen on the deployed demo (S4 stayed "on
hold" after PayPal's refund was delivered and a second run completed) and
reproduced locally on the seeded ORD-0017 with the real engine.

## 2. Decisions

- **D1. Retract only on positive evidence of a re-check.** A run records how many
  orders it covered, not which, and absence of a row alone cannot mean "checked
  and clean" (mission P3). What is known: a completed run examines every order
  whose `placed_at` is inside its window. So a finding is retracted if a
  **completed** run that **started after** the finding's run has a window
  **containing the order** and did not write the finding again.
- **D2. Everything that reads the queue changes together.** The rule lives in
  `_newest_exception_rows`'s cross-run branch, so the queue, summary, coverage and
  CSV cannot disagree. A specific run (`run_id`) is unchanged: Past checks shows a
  run as it left it.
- **D3. Errs toward leaving a finding up.** Orders a run re-examined only because
  new evidence arrived (placed outside its window) are not counted; a failed or
  running run counts for nothing; a row keyed by PayPal's id (an unmatched PayPal
  payment, no marketplace order) cannot be placed in a window and is kept.
- **D4. Decisions are untouched.** They are append-only and keyed by
  `(order, code)`. A retracted finding leaves the queue and the Decided tab; every
  decision stays in the decision log.
- **D5. The `basis` text** returned by `/api/coverage` says what is now true.

- **D6. Later means "started after the first run finished".** Comparing start
  times let a run that overlapped the first retract on data it never saw
  (found in code review). A running run has no `finished_at` and retracts
  nothing.
- **D7. One definition of open.** The rule and the query live in
  `reckonai/findings.py`; the queue, summary, Coverage, CSV and the nightly
  `auto_investigate` sweep all call it, so the sweep cannot pay for an
  investigation of a finding the queue no longer shows (found in code review).

## 3. Not in scope

- Recording which orders a run covered (would remove the window inference).
- Retracting unmatched PayPal payments (the 40-odd "on hold" items keyed by
  PayPal's id on the demo). They need a marketplace order to be placed.

## 4. Known follow-ups (from code review, not done)

- **Age after a fault reappears.** `first_found` takes the earliest run ever, so
  a fault found day 1, retracted day 5 and reintroduced day 30 still ages from
  day 1 and ranks too high.
- **Cost per request.** The rule adds two small queries to each queue-related
  call; fine while run history is small, worth caching if it grows.
- **The decisions of a retracted finding** remain in the log but are no longer
  reachable from the queue; a "Resolved" view would show them.
