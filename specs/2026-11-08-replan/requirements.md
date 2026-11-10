# Replan — decisions

> Supersedes [[roadmap]]'s sequencing for Phases 4-15. Every exit criterion in
> [[roadmap]] still binds for every phase kept here. Where a decision touches a
> principle, it names the principle and why it survives.
>
> 2026-11-08, after Phase 3. Two days, ~20 working hours, and a token budget.

## Why a replan

Phases 4-15 are **6 days** at the roadmap's own estimates, and those estimates
have held — 0, 0b, 1, 2 and 3 each landed on their half-day figure. Two days is
~20 hours.

The roadmap's cutline protects 10, 11 and 12. Add the dataset they run on
(4, 5) and the dashboard the work is judged through (14), and the protected set
alone is 3.5 days ≈ 28 hours. **The protected set does not fit.** So cutting
phases is not sufficient, and this replan has three levers: reduce the cost per
phase, merge phases that share a dataset, and cut what is not load-bearing.

## Lever 1 — process cost

Measured on this repo at Phase 3:

| | Lines |
|---|---|
| Code and tests | 10,670 |
| Per-phase process docs (`specs/2026-*/`) | 8,683 |
| `docs(...)` commits | 40 of 80 |

That ceremony was correct at a normal cadence — it is why Phase 3 could warn
Phase 4 about the `NOT NULL` payload link and SQLAlchemy's flush order, and why
the Render free-tier problem surfaced on day one instead of day six. It is not
affordable at 20 hours, and it is the only cost removable without removing
capability.

**R1. Per-phase directories stay, lean.** `requirements.md` (decisions and why)
and `validation.md` (how each exit criterion was proven) are kept — they are
what made Phase 3's handoff useful. `plan.md` is dropped: it restates the
roadmap phase and goes stale as soon as work starts. `notes.md` only for
findings not already in a root spec, kept short. Target 120-150 lines per phase,
not 450. `notes.md` was 43% of the volume above and `plan.md` 17%.

**R2. Session discipline.** One commit per block. This directory is the session
entry point — the root specs are 3,532 lines and re-reading them per session is
the largest avoidable token cost; read a root spec only where a block points at
a section. No subagents. Whole modules per pass, implementation and tests in one
write. Operator runs the suite and pastes failures only. `/code-review medium`
on Blocks B and C only. One deploy, in Block F — **spent early on Block A's
merge; see "State" below**.

## Lever 2 — merges

**R3. Phase 6 runs as a sequenced sub-block of Block A, not a separate phase.**
Nothing is removed: connector client, declarative mapping config, the §9.4
validation gate and normalization with raw payloads retained all stay, as do all
three exit criteria. We author both sides of this boundary, so the deliberately
different field names are chosen in the same pass that writes the mapping
consuming them; building them apart means a handoff document and a context
reload for a boundary we invented. Two commits and two spec entries, one
session.

*The risk this creates, named so it is guarded rather than discovered:*
controlling both sides makes a trivially-matching mapping easy to write, which
would mean mapping was never exercised. Guard: field names are deliberately
different (`vendor_id`, `order_ref`, `item_total`), plus a check that no
canonical field maps from an identically-named source field.

**R4. Phase 5 folds into Block A.** Clean and faulty records share one seed
builder and one manifest. Building them together is also the only way to be
confident the clean baseline is clean, which Phase 11's hardest criterion rests
on.

**R5. Phase 13 stays its own block.** Not folded. Prioritisation scoring over
all four `readme.md` §9.3 factors — financial impact, affected sellers, age,
confidence — an append-only decision record carrying approver and outcome, and a
visible confirmed-vs-suspected distinction. P6 is the point of the phase, not a
constraint on it.

## Lever 3 — cuts

Each names what it costs and what must be recorded. **A cut that is not recorded
as a limitation is a false claim about the product**, which P3 and P5 forbid in
reconciliation output and which the readme should not make either.

**R6. PayPal is replayed from captured sandbox responses, rather than the
reconciliation run depending on a call made during the demo.** Sandbox
throughout, as always; nothing production, ever. A capture script writes real
sandbox responses to fixtures; a replay ingester normalizes them through the
same path as live retrieval.

**This does not make the demo less live.** There is a live end-to-end demo, and
every link in it runs for real: the deployed app, the connector ingesting, the
engine classifying, the agent investigating, the dashboard, approve/reject. The
only thing R6 removes is the reconciliation run's *dependence* on an outbound
PayPal call at demo time.

Sound because the verification this protects is **already done** — Phases 0 and
0b spent a full day on it against a real platform app with partner scopes, and
`paypal-fields.md` is regenerated from multiparty evidence only. **P3 is
intact:** P3 forbids fabricating or estimating a provider figure, and a replayed
fixture is PayPal's own response, retained as the raw payload P7 requires
anyway. The values reconciled are the provider's either way; only the timing of
the call changes.

It also removes three live failure modes from a judged run: sandbox
availability, the reporting APIs' ~1-day lag, and the `5xx` that
[[tech-stack]] §8 records as "not reliably transient".

*Costs:* no live call during the demo. The capture script stays real and
runnable, so it can be shown separately in under a minute.
*Limitation to record:* PayPal records are replayed from captured sandbox
responses; the retrieval path is exercised against fixtures.

**R7. Phase 8 is minimised, not cut.** Two reasons it cannot go: `render.yaml`
already declares `reckonai-worker` as a **paid** Starter instance, in a
submission that lists Render's purpose as background processing and scheduled
reconciliation; and production **already runs two workers concurrently on every
deploy** (observed 2026-11-08, ~90s overlap on Render's rolling deploy), so
`FOR UPDATE SKIP LOCKED` correctness is the condition the deployed system is
already in, not a hypothetical.

Kept: `jobs` table, SKIP LOCKED claiming, `processed_events` unique constraint,
Render Cron Job. Retry becomes the simplest correct form — attempt count,
`run_after`, terminal failed state with the error recorded — not a tuned backoff
policy. [[tech-stack]] §4's decisive argument, that the dedupe marker and the
financial write share one `COMMIT`, is untouched. **~2 hours, not half a day.**

**R8. Event-driven ingestion is cut; [[mission]] §4 is amended.** No webhook
endpoints, no signature verification, no backfill of missed deliveries.

The constitutional content of §4 is **one engine over one canonical model, with
no second code path for live events** — and that is satisfied by Phase 10 being
pure functions, whatever calls them. A webhook is a trigger. None of the three
§6 demo scenarios is event-driven: all three are state comparisons over a seeded
dataset. And the property event-driven uniquely proves — a duplicate delivery
producing exactly one financial effect — is a *database* property that lives in
R7's `processed_events` constraint and is proven by enqueueing the same event id
twice.

mission.md's own status line permits this: "this document wins or this document
must be amended first." A one-line dated amendment is more honest than an
endpoint built so a sentence stays technically true.

*Limitation to record:* no webhook ingestion; reconciliation is triggered on
demand and on a schedule.

**R9. Backfill-of-missed-webhooks is cut; scheduled date-windowed
reconciliation is kept.** Two different things share the word. With no webhooks
there are no missed deliveries to recover. But the date-variance problem is
real and documented: [[tech-stack]] §8 records the reporting APIs as eventually
consistent, roughly a day behind, with the v2 resource endpoints authoritative
for recent activity, so a record read today can be genuinely incomplete. A
scheduled run over a date window handles that, and R7 keeps the Cron Job
anyway.

*Correction worth carrying, because it bounds what to build toward:* this does
**not** unlock settlement processing. [[mission]] §7 defers the consolidated
settlement check on two grounds — it is settlement-account reconciliation, which
is out of scope, and the partner fee cost detail arrives only as a **monthly,
SFTP-delivered report for approved partners**, in integer cents. That is blocked
on data we do not ingest, not on scheduling. The in-scope check is unchanged:
the platform fee PayPal recorded on a transaction against the commission the
marketplace's rules specify.

**R10. Parked-event reprocessing is kept, narrowed.** Not for webhooks — a batch
ingest controls its own ordering. For the **cross-source** case, which survives
without them: the marketplace has a refund whose PayPal record has not appeared
yet because reporting lags. Classifying that as "missing from PayPal" is exactly
the false positive Phase 11's hardest criterion forbids — the record is late,
not absent.

So: an `awaiting_evidence` state plus re-evaluation on the next scheduled run.
R9's run *is* the retry. This is [[mission]] P4 — "unmatched is not the same as
wrong" — in the form that matters here.

**R11. The Haiku field-*name*-guessing pass is cut. The investigation agent is
not touched.** The two were conflated once while replanning, so the distinction
is stated plainly here: this cut is [[tech-stack]] §9.2 pass 3 only — a Haiku
call during *connector setup* that guesses which source field name means which
canonical one, e.g. that `vendor_id` is probably `seller_id`. It never reads a
financial record and never participates in an investigation. Passes 1 and 2
stay (exact match, curated synonym table), which §9.2 itself says cover most of
what the readme's examples show, as does the human-review step. The **§9.4
validation gate stays whole**, including the hard failure on a money field
resolving an amount without a currency — §9.3 names that exact mistake as the
single most likely place for a mapping to be quietly wrong.

**The LLM that produces evidence-backed recommendations is fully kept, and is
not in scope for any cut in this document.** Phase 12 stands as [[roadmap]] and
[[tech-stack]] §5 specify it: Opus 5.5, ranked candidate causes, per-cause cited
record ids, confidence, the `insufficient_evidence` branch, persisted summarized
reasoning, and model-id comparison with downgrade flagging. It is the
differentiator, the cutline protects it, and [[mission]] §4 lists it as the
third of three capabilities. Confirmed explicitly with the operator,
2026-11-08.

*Limitation to record:* field mapping is deterministic only; unmatched fields
are mapped by hand.

**Three different things are called "confidence" in these specs, and only the
first is cut.** This caused the conflation twice while replanning, so:

| Where | What it scores | Status |
|---|---|---|
| [[tech-stack]] §9.2 pass 3 | A guess that source column `vendor_id` means canonical `seller_id` | **Cut** |
| [[tech-stack]] §5.2 output contract | A **candidate cause** in an investigation report, with its cited record ids | **Kept** |
| `readme.md` §9.3, via R5 | Whether an exception is a genuine discrepancy, used to rank the queue | **Kept** |

And two different things are called "unmatched". An **unmapped field** is a
schema question — which canonical field a column corresponds to — answered once
per marketplace by a human before any reconciliation runs, with no financial
record involved. An **unmatched record** is a refund with no capture to tie to,
a ledger entry with no PayPal counterpart, a seller paid something other than
their entitlement. Those are Phase 11's output and **Phase 12's agent is what
explains them**: reconstruct the lifecycle, rank probable causes against
retrieved evidence, cite the ids each cause rests on, attach a confidence, and
return `insufficient_evidence` rather than guess. R11 does not touch any of it.

**R12. The eval harness stays, against the roadmap's cutline.** The cutline says
drop it and score by hand. That reasoning inverts here: hand-scoring costs hours
this plan does not have, so the harness *replaces* a manual pass rather than
adding one. It is also cheap once Block A's manifest exists — read the manifest,
run the engine, print a table. [[mission]] §6 is a table of measures and the
readme claims detection and root-cause figures; one command printing measured
output is what makes those claims true rather than asserted.

**R13. Dashboard: drop the seller financial position view; keep a lean run
summary.** The run summary is kept because R9 adds scheduled runs and the run
history is how anyone sees that a scheduled run happened and what changed —
and because [[tech-stack]] §10 requires runs to be append-only and timestamped,
so the data exists either way. Lean means one AG Grid table over runs, with
click-through to that run's exceptions, rather than a charted view.

The seller position view is the least load-bearing of the four views for the
three demo scenarios — the investigation detail tells the underpaid-seller story
better, with evidence attached — and is the easiest to add back if Day 2 runs
ahead.

**R15. The demo opens with one live sandbox call, which nothing depends on.**
The capture script is made demo-safe and run on screen at the start: one order
creation plus capture against the sandbox, around fifteen seconds. The
reconciliation then proceeds from fixtures whether or not that call succeeded.
If it works, live PayPal has been shown; if it returns one of §8's
non-transient `5xx`s, the demo continues unaffected. This is the whole benefit
of R6 and costs about half an hour.

**R14. Already out, staying out.** Bryntum, below the cutline by definition.
Settlement-account reconciliation, seller-facing views and the rest of
[[mission]] §7. AG Grid **stays** — `readme.md` §12 makes it a sponsor
technology doing real grouping and drill-down, and it is the dashboard's primary
surface.

**R16. The PayPal capture runs *before* Block B, not inside Block D.**
2026-11-08, after Block A merged.

The capture script, the fixtures and the replay ingester — old Block D's
"7-lite" — move to the front of the remaining work. The rest of Block D (the
`jobs` table, SKIP LOCKED, `processed_events`, the Cron Job and the
date-windowed run) stays where it was.

*Why.* Block C compares the marketplace's records against **PayPal's reported**
figures, and Block F's dashboard renders them. Block A had to invent a
synthetic PayPal side (its D12) so Block C was not blocked on Day 2 work — and
building Block C and Block F on invented provider rows means re-validating both
when the real ones land. Capturing first means the seed is re-pointed at real
provider data **once**, before anything is built on it.

*It also retires D12 entirely*, which is the stronger reason. Those rows
fabricate provider figures. They are labelled `_synthetic` and a debt was
recorded to remove them, but a fabricated provider figure that must be
remembered-about is worse than one that never exists. [[mission]] P3 is the
principle; this removes the exposure rather than managing it.

*Block B is unaffected* and does not move. It is pure functions over
hand-computed fixtures, so it needs no provider record at all — which is why
the capture can go ahead of it for free.

**New order:**

```
capture (script, fixtures, replay ingester)   <- retires D12
B   entitlement engine (Phase 10)
C   matching and classification (Phase 11)     <- now on real provider records
D   queue, Cron Job, date-windowed run          (the rest of old Block D)
E   investigation agent (Phase 12)
F   queue, approval, dashboard (13 + 14)
G   eval harness, demo, rehearsal, limitations
```

**R6 is unchanged.** The reconciliation run still replays from captured
responses rather than calling PayPal during the demo. Only the capture's
*position* in the sequence moves.

**Hand-checkability survives, and that is not an accident.** The clean
baseline's expected figures stay hand-derivable because **we choose what to
push to the sandbox**: a real order of USD 160 across two sellers at a 10%
platform fee produces a genuine PayPal response carrying 160 and 16, while
90 / 54 / 16 remain derivable on paper. Real provider data and a hand-tabulated
baseline are compatible as long as the inputs are chosen rather than arbitrary.
Phase 11's zero-false-positive gate is unaffected.

*Two things this creates, named so neither is discovered:*

**It needs operator time, which no other block does.** The capture needs live
sandbox credentials and decisions about what to push — which orders, which
sellers, which currencies. Every other block is work that can proceed
unattended.

**A shape the sandbox cannot produce keeps a hand-authored provider record, and
stays marked.** Phase 0b confirmed platform fees, delayed disbursement and FX
under a seller payee are all reachable, but the FX case needs a seller
onboarded in another currency and the `DELAYED` case needs
`DELAY_FUNDS_DISBURSEMENT` granted at onboarding. If a sandbox seller lacks
either, that one record stays authored and labelled the way D12's rows are, and
the readme's limitations section states it. Honest and labelled beats silently
synthetic.
*Limitation to record, only if it occurs:* named provider records are
hand-authored because the sandbox could not produce that shape.

**What this costs in rework.** If the sandbox amounts differ from the literals
in `reckonai/sim/data.py`, that module is re-pointed at the captured figures
and `validation.md` §2's table re-derived with it. `seed.verify()` is what makes
that safe: it recomputes every stated figure, so a re-pointing that misses one
fails rather than passing quietly.

## Not cut under any pressure

- **P1** — every number from the deterministic engine; the LLM computes nothing.
- **P3** — no fabricated or estimated financial value. `fx_conversion.fee` stays
  `NULL`, never zero; `disbursement.released_at` keeps no default.
- **P6** — nothing moves money; the referenced-payouts release is never called.
- **P4 and Phase 11's zero-false-positive gate**, with both known traps — FX
  cent-rounding and healthy `DELAYED` states — in the clean baseline on purpose.
- **Phase 12 entire** — the LLM that produces evidence-backed recommendations,
  including the `insufficient_evidence` branch. See R11.
- **R5's approve/reject**, and the decision record behind it.
- Every financial record's `NOT NULL` `raw_payload` link; `tenant_id` on every
  marketplace table; `Decimal` and `NUMERIC` throughout.

## Blocks

**Day 1**

- **A — simulated marketplace, faults, connector** (4 + 5 + 6) · ~5 h
  Endpoints with deliberately non-canonical field names. Internally consistent
  multi-seller, multi-currency seed with at least one `DELAYED` order. Six
  seeded faults plus the manifest that becomes Block G's ground truth. Mapping
  config and §9.4 gate. Idempotent ingestion.
  Inherited from Phase 2: **delete `GET /money/example`** from `app.py`, the
  service index and `tests/test_money_json.py`; and **the seed builder needs its
  own consistency check**, because D13 means no constraint ties an order total to
  the sum of its items, so the clean baseline has no database backstop.
  Two things that must stay *out* of the faults because they are not faults:
  funds held under `DELAYED` with no release requested, and PayPal's
  cent-rounding of a converted settlement. Both belong in the clean baseline
  precisely so Block C proves they are not flagged.
- **B — entitlement engine** (10) · ~3 h
  Pure functions, no I/O. The [[mission]] worked example passes exactly: $160
  across two sellers at 10% → A $90, B $54, commission $16; after a $40 refund
  on A's item → A $54, B $54, commission $12. Expected commission computed as a
  figure **comparable to PayPal's platform fee**, since that is what Block C
  checks it against. A capture fee is never defaulted to zero.
- **C — matching and classification** (11) · ~4 h
  Match on stable identifiers, compare currency-aware, classify including the
  non-error states and R10's `awaiting_evidence`. Compare against PayPal's
  **reported** settlement, never a recomputation of gross times rate. Then
  freeze the dashboard's JSON contract.

**Day 2**

- **D — fixture replay, queue, scheduled run** (7-lite + 8 + R9) · ~4.5 h
  Capture script — demo-safe per R15 — fixtures, replay ingester. Refunds read **through the order**,
  which carries `platform_fees` the refund endpoint omits. Raw payloads retained
  and diffed against the model — Phase 0 saw three fields PayPal does not
  declare, two of which stopped being returned a day later. Then `jobs`, SKIP
  LOCKED, `processed_events`, Cron Job, and the date-windowed reconciliation run.
- **E — investigation agent** (12) · ~3 h
  Read-only retrieval tools plus the engine as a recompute tool, via the Tool
  Runner. Scenario 1 end to end, naming the missing ledger adjustment and citing
  real ids. `insufficient_evidence` when evidence is removed. `stop_reason`
  checked before content is read; model id stored and compared, fallback flagged
  as downgraded.
- **F — queue, approval, dashboard** (13 + 14) · ~5 h
  Prioritisation scoring, decision records, then exception queue on AG Grid,
  investigation detail with evidence trail, lean run summary, approve/reject.
  Amounts formatted from strings with `Intl.NumberFormat` on the record's own
  currency; no client-side arithmetic. Deploy — once.
- **G — eval harness, demo script, rehearsal, limitations** · ~3 h
  One command, one metric table, scored against Block A's manifest. Three
  scenarios from a clean database with no manual intervention. One rehearsal
  against the deployed environment, since R15 makes a live demo the deliverable
  and the deployed app load-bearing. The readme's limitations section carrying
  every item above marked *limitation to record*.

**~27.5 hours against ~20.** Stated rather than hidden. Everything still listed
is constitutional, protects the false-positive gate, or is a link in the demo
chain below, so the remaining lever is hours.

### The chain the live demo walks

Nothing cut in this document breaks a link:

```
marketplace API  →  connector + mapping  →  canonical model
                                                  ↑
                  PayPal fixtures (R6, R15) ───────┘
                                                  ↓
entitlement engine  →  classified exceptions  →  agent investigation
                                                     with cited evidence
                                                  ↓
            prioritised queue  →  approve / reject  →  eval table
```

What R8 changes is only the **trigger**: reconciliation runs on demand, or on
R9's schedule, rather than on a webhook.

## If Day 2 runs short, drop in this order

1. Lean run summary (R13) — the exception queue carries the demo.
2. R5's prioritisation **scoring**, keeping approve/reject and the decision
   record. P6 survives; only the ranking goes.
3. Scenario 3 (FX) as a *dashboard* story, keeping it as a unit-tested engine
   case. The FX rounding guard stays in Block C regardless — it is a
   false-positive gate, not a feature.

**Never dropped:** Block B, Block C's false-positive gate, Block E's
`insufficient_evidence` branch, approve/reject.

## Documents this replan edited — **all applied 2026-11-08, commit `aba4a8f`**

Do not re-apply these. Each document carries its own dated amendment note, and
the notes are the authority on what changed; this list is only the index.

Not the five existing `specs/2026-*` directories — those are the historical
record of completed phases and record what was true when written.

1. **`specs/mission.md`** — §4, a dated amendment scoping event-driven
   ingestion out of the MVP while keeping the single-engine requirement (R8).
2. **`specs/tech-stack.md`** — §1 PayPal row; §4 Cron Jobs backfill clause;
   §5 Haiku secondary model and its refusal rule; §6 event-driven sentence;
   §8 "plus webhooks" and the webhook-handling paragraph; §9.2 pass 3.
3. **`specs/roadmap.md`** — status line; Phase 5 title and body; Phase 6 marked
   sequenced into Block A; Phase 7 narrowed; Phase 8 narrowed; Phase 9 removed;
   Phase 13 confirmed as its own block; Cutline revised.
4. **`readme.md`** — a new "MVP scope and limitations" section near the top, plus
   edits only where the text would otherwise state something false: §5.2's
   heading and closing line, §10.2 step 8, the §14 summary. The rest of the
   readme is aspirational product definition that the scope section covers
   globally; editing all twelve webhook references is not worth the tokens.
5. **`CLAUDE.md`** — phase status, and a pointer making this directory the
   session entry point.

## State

**Updated 2026-11-10 after Block F.** This section is the first thing a new
session reads, so it is kept current rather than left as a record of when the
replan was written.

**Blocks D, E and F are built. Block G (eval harness, demo script, rehearsal,
limitations) is next.** Block F is on branch `block-f-queue-approval-dashboard`
and **not yet merged**; a merge deploys, and its prerequisites are in
`specs/2026-11-10-queue-approval-dashboard/validation.md` §3.

**Block F delivered Phases 13 and 14**, and built the read API and the frontend
that did not exist: the append-only approve/reject record, the pure priority
score over the four factors, a queued investigation job, and a React + AG Grid
Enterprise dashboard. Beyond the roadmap's list it added, at the operator's
request: a natural-language grid assistant (AG Grid's AI Toolkit, display-only),
a "Run reconciliation" action, cited references that open as records with the
provider's retained response, a Coverage view (clean, parked, with exceptions)
and CSV export of the queue and the decision log. Its `validation.md` tabulates
a `/code-review medium` (10 findings, 9 fixed) and what is unproven.

**Later in Block F, at the operator's request:** "Investigate all" and checkbox
"Investigate selected" on the queue (inline confirm, because each is a paid
model call); the **scheduled run now queues investigations for what it finds**,
capped by `AUTO_INVESTIGATE_MAX` (default 25) and skipped with a warning when no
model key is set, while a person's "Run reconciliation" never investigates by
itself; and a plain-language wording pass for a finance audience, all in
`frontend/src/copy.ts` (requirements D17 and D18). That pass fixed a real
mislabel: the drawer showed the engine's "Expected" and "Reported" the wrong way
round for a reader.

**Four things from Block F worth carrying.** A decision is keyed by (order,
code), not by exception id, because every run writes a fresh row for the same
fault. Approver identity is free text with no authentication (D5), the weakest
part of an accountability product and a recorded limitation. "Clean" means "no
newest finding": a run records how many orders it covered, not which. And a run
is date-grained, so "Run reconciliation" a second time in one day is answered,
not queued.

**Block G inherits** the seeded orders being dated after today (so every age
factor reads 0%), the unproven Anthropic path and successful-investigation
rendering, and the deploy values only a person can set.

Block E delivered Phase 12 entire: read-only retrieval tools plus the engine as
a recompute tool, the structured report with ranked causes and per-cause cited
ids, the `insufficient_evidence` branch, the `investigation` and
`investigation_cause` tables, and a `python -m` entry point. Five of its seven
criteria are met offline; the two that need a real model call are evidenced by
two hand-run investigations rather than by CI, because a paid call per test run
is not acceptable and a key-gated test skipped in CI would be a green run that
covered nothing.

**Four things from Block E worth carrying at this level.**

**OpenAI is a second selectable provider, by an operator decision taken during
the block.** `settings.agent_provider` chooses; both return the same structured
report, which is the seam. `tech-stack.md` §1 and §5 are amended, and **§5.1's
request table is now marked Anthropic-specific** - every row of it was derived
from real Claude API behaviour and several are 400s there rather than
preferences. The no-fallback decision is **not** reversed: a refusal still ends
the investigation.

**Nothing downgrades, and the columns are what prove it.** With no refusal
fallback on either provider, `investigation.downgraded` can never be set in the
MVP. That is the honest version of §5.2's rule rather than an exemption, and a
`CHECK` refuses a downgraded row that carries no returned model to compare.

**"Cite the capture" was incomplete.** A platform fee PayPal *reverses* has no
`capture_id` - its parent is the refund - and Scenario 1's own order carries
exactly that shape. The stable reference is the fee's parent, whichever it is,
and the fee tool returns `fee_kind` so a capture fee and a reversal are never
added together.

**Resolution and stability are different questions about a citation.** A
`platform_fee.external_id` resolves to a real record and will not find it again
after a reseed. Treating it as fabricated would be false and would fail on the
classifier's own findings, so the two are measured separately.

**What Block D delivered**, kept here because its results are still load-bearing.
`jobs` with `SKIP LOCKED`, `processed_events`, retry, a worker that claims
rather than ticks, the `reconciliation_run` and `reconciliation_exception`
tables, the date-windowed run, and the Cron Job, which was **declared in
`render.yaml` and then created by the Blueprint sync PR #22's merge triggered
(2026-11-10 14:27 UTC)**. It has not yet fired - Render does not run a cron job
at creation, and the first tick is 01:00 UTC on 2026-11-11 - so Phase 8's
fourth criterion is recorded as unobserved rather than claimed. (This used to
read "declared but not applied - the deploy budget is spent"; both halves were
wrong.) Four of its five criteria
are met, measured end to end: 18 orders selected, 6 findings, 5 exceptions,
with the manifest's own figures.

Four things from Block D worth carrying at this level.

**The window needed a second clause, and the dispute case is what proved it.**
Windowing on `order.placed_at` alone cannot see a refund that settles a dispute
30 days after the order - every scheduled window has moved past it, so the
order is never re-selected and R10 silently never fires. Selection is now that
clause **unioned with** any order of any age holding evidence retrieved since
the last completed run. It cannot be a SQL join, because a late PayPal refund
reaches its order only through the matcher's claim.

**`dispute` is now a position-moving ledger category, and that took two
changes rather than one.** Block C excluded it for good evidence reasons, but
the exclusion meant a dispute loss the expected entitlement had already
absorbed was missing from the ledger sum - a false positive on correct data.
Adding it to `POSITION_CATEGORIES` was only half: `_partition_by_ledger` also
read only `refund` entries when deciding whether a provider refund had been
recorded, so one correct booking produced two findings. A hand-authored fixture
found the second half; the false-positive gate was re-run and is unchanged.

**The claim must be committed before the handler runs.** Sharing one
transaction means a failing handler's rollback undoes the attempt increment
too, and the job retries forever looking untried. `handlers.dispatch` refuses
an uncommitted claim, because the mistake is invisible until something raises.

**A guard caught a column name.** `order_ref` is one of the sim's deliberately
non-canonical outward field names, so the exception table's column is
`order_external_ref` while the frozen JSON contract's key stays `order_ref`.
Claiming the name would have weakened the guard that keeps the connector's
mapping genuinely exercised.

Phases 0, 0b, 1, 2, 3 and **Block A (4 + 5 + 6) are complete and merged** — PR
#14, merge commit `2d002b1`, CI green.

**The PayPal capture (R16), Block B (Phase 10), Block C (Phase 11) and Block D
(Phase 8 + R9 + the exception table) are complete.** Block D's spec and
evidence are in `specs/2026-11-09-queue-scheduled-run/`; its `validation.md` §9
lists three debts handed forward and §8 three corrections it made to earlier
documents.

Block C delivered `reckonai/recon/{vocabulary,matcher,tolerance,classifier}.py`
— pure, under the same purity and mypy gates as the engine — and
`reckonai/reconcile.py`, the one module holding both a `Session` and an
`OrderCase`, which is the entry point Block D's run wraps. All four Phase 11
exit criteria pass against the seeded tenant: six faults detected with the
manifest's own impact figures, and **zero exceptions on the fifteen clean
purchase units**.

**A `/code-review medium` on Block C returned nine findings, all nine real**,
two of them reproducible false positives on correct data - the worst reported a
clean equal-share two-seller order as duplicated, which no seeded order could
show because none splits equally. Block C's `validation.md` §9 tabulates all
nine with their fixes, and each has a regression test. The lesson worth
carrying past this block: **the gate proves the dataset, not the logic** - a
rule can be wrong in a shape the eighteen seeded orders do not contain.

Three further things worth carrying at this level. **Two of the manifest's six
impact figures were stale** — F3 and F6 described no record in the dataset, left
behind when R16's capture re-pointed the seed at real provider figures — so the
gate would have failed against a correct implementation; they are corrected
against the fixtures and now pinned by `tests/test_manifest_agrees_with_seed.py`.
**The FX guard turned out not to be a tolerance**: comparing only reported
figures removes the shape that produces the one-cent artefact, so ORD-0008 is
clean because the forbidden arithmetic is absent. And **the held-funds guard is
not exercised by the database gate** — ORD-0005's ledger is complete, so the
residual never reaches it; it is proven in unit tests, and that gap is recorded
rather than papered over.

Block B delivered `reckonai/engine/{entitlement,refunds,positions}.py` — pure
functions over `typing.Protocol` inputs, with all six [[roadmap]] Phase 10 exit
criteria met and `seed.verify()` now recomputing through the real engine rather
than a second copy. **Read
`specs/2026-11-09-entitlement-engine/requirements.md` §4 before Block C**: it
is the handoff, and it names four things not to spend time on as well as six to
get right.

Two things worth carrying at this level. A `/code-review medium` on Block B
returned **six findings, all six real**, the worst of which had the engine
fabricating an entitlement on repeated partial refunds — per-refund commission
reversals accumulate rounding, and nothing already written caught it because
every stated invariant still held. And a **type checker is now wired in**:
strict mypy over `src/reckonai/engine/` only, gated in CI, with the reasoning
and the five out-of-scope errors in that directory's D9 and `validation.md` §8.

The capture produced 18 real sandbox orders in `fixtures/paypal/`, replayed by
`reckonai.paypal.replay`, with the marketplace seed re-pointed at the captured
figures and Block A's synthetic provider side deleted. Read
`specs/2026-11-09-paypal-capture/` before touching the dataset: its
`validation.md` §2 is the current hand-check, Block A's §2 is superseded, and
§7 lists five things found only by running it.

`specs/2026-11-08-block-a-marketplace-connector/requirements.md` §3 remains
useful but its seller list is stale, and its claim that every seeded capture
states a fee is now false. That directory's `validation.md` §8 listed five
debts: **debt 2 was retired by the capture, debt 4 was settled by Block B**
(the test-order guard), and **debt 1 — reconciling `manifest.py`'s
classification strings into a shared exception vocabulary — is Block C's stated
first task**. Debt 3 (the open-dispute trap) is Block C's to take or decline
deliberately, and debt 5 (seeding the deployed database) is Block F's.

**The deploy budget below is spent.** R2 allowed one deploy, in Block F. Block A
was merged to `main`, and both Render services track `main` with no
`autoDeploy` setting, so the merge deployed. This was accepted deliberately,
not overlooked. The deployed app is verified healthy and serving
`/sim/marketplace`; `validation.md` §10 has the checks. A later block that needs
another deploy is therefore spending an unbudgeted one, which is a decision to
make consciously rather than a rule being broken.

**Corrected 2026-11-10: it was never a decision.** The paragraph above names
the mechanism correctly - both services track `main` with no `autoDeploy`
setting - and then draws the wrong conclusion from it. Merging *is* deploying.
Render's history shows the API and the worker redeployed on PRs #19, #20, #21
and #22 (each a `new_commit` deploy), and #22's merge also ran a Blueprint sync.
There is no budget to spend or to save: Block E's merge will deploy, running its
migration through the web service's `preDeployCommand`, and so will Block F's.
What *is* choosable is when to merge, and what the deployed environment needs
(an agent credential, a seeded database) by the time that happens.

## Related

- [[mission]] — P1, P3, P4, P5, P6, P7 constrain every cut above; §4 is amended
- [[tech-stack]] — §4 queue, §5 agent, §8 PayPal rules, §9 mapping, §10 audit
- [[roadmap]] — the exit criteria that still bind, and the cutline this revises
- `specs/multiparty-direction.md` §8.5 — what is still unverified
- `specs/paypal-fields.md` — what P3 is enforced against
