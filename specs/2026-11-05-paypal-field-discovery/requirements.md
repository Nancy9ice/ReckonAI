# Phase 0 — PayPal field discovery · Requirements

**Branch:** `phase-0/paypal-field-discovery`
**Roadmap phase:** [[roadmap]] Phase 0 · 0.5 day
**Primary output:** `specs/paypal-fields.md`

---

## 1. Goal

Establish what PayPal **actually returns** for our transaction types, before
the canonical financial model is designed around it.

The deliverable is not code. It is a committed, evidence-backed field
availability table with two independent columns:

| Column | Source | Meaning |
|---|---|---|
| **Declared** | APIMatic Context Plugin / PayPal API contract | the field exists in the API |
| **Observed** | Live sandbox responses | the field was actually populated for our transactions |

**The gap between those columns is the real product of this phase.** A field
that is declared but never observed is a field the canonical model would
otherwise have been built on, and which would arrive empty in Phase 7 — after
Phases 2 and 3 had already committed to it.

## 2. Why this runs first

[[mission]] P3: *actual conversion evidence beats estimates.* We may not
substitute an estimate for a provider record, so we must know in advance which
provider records exist. [[tech-stack]] §8 carries the same rule.

The cost of being wrong is asymmetric. Discovering in Phase 7 that PayPal never
returns a settlement rate means reworking Phases 2, 3 and 7 with no schedule
left. Discovering it now costs a column in a table.

## 3. In scope

### 3.1 From the roadmap exit criteria

- APIMatic Context Plugin installed and returning PayPal context.
- `specs/paypal-fields.md` committed, with declared and observed as separate
  columns, covering:
  - **Transactions / captures** — ids, amounts, currencies, fees
  - **Refunds** — linkage back to the original capture, partial-refund shape
  - **Disputes** — status, amount, lifecycle states
  - **Payouts** — batch and item structure
  - **Settlement and FX** — rate, conversion fee, settlement currency,
    conversion timestamp
- Every field the canonical model intends to carry marked `present`, `absent`,
  or `present-but-unreliable` — **judged on observed, never on declared.**
- Every declared-but-not-observed field listed explicitly, as its own section.
- Every absent field carrying a decision: derive it (and be labelled derived
  per [[tech-stack]] §2) or declare the limitation.

### 3.2 Included prep, and why

Scope is exit criteria **plus prep that would otherwise cause immediate
rework**. Two inclusions, each justified:

**A single-file probe script** (`scripts/probe_paypal.py`).
Justification: without it, the observed column is hand-transcribed from
exploratory tool calls and is not reproducible. Anyone re-running the phase, or
checking it at review time, could not verify the result. The script makes the
observed column **machine-produced and re-runnable**, which is also what lets
validation be automated rather than asserted.

It is deliberately a throwaway harness, **not** the PayPal client of Phase 7 —
see §4.

**Sandbox fixture transactions.**
Justification: an empty sandbox observes nothing. We need at least one of each
record type to exist before presence can be assessed, including a
cross-currency payment — otherwise the FX row, the most important row in the
table, is unanswerable.

## 4. Out of scope

Stated explicitly because this phase sits next to work it must not start:

- **No PayPal client abstraction.** No service class, no interface, no
  retry/pagination layer. Phase 7 builds the real client, informed by this
  output. The probe script is a disposable instrument.
- **No canonical tables, no migrations, no SQLAlchemy models.** Those are
  Phases 2 and 3.
- **No normalization or mapping.** Phase 6 and 7.
- **No reconciliation logic of any kind.**
- **No uv project scaffold.** That is Phase 1's exit criterion. The probe runs
  as a standalone script — see the decision in §6.
- **No marketplace-side work.** This phase is PayPal only.

If the phase reveals something important about a later phase, it is written
into this directory as a note and carried forward, not implemented here.

## 5. Prerequisites — blocking

This phase cannot start until all four hold. The first two are the reason the
phase was deferred rather than attempted degraded.

| # | Prerequisite | Status | Owner |
|---|---|---|---|
| 1 | APIMatic Context Plugin for PayPal installed and returning context | **Not installed** — no entry in project or user settings | User |
| 2 | `paypal-sandbox` MCP server reachable | **Disconnected** — `ECONNRESET` | User |
| 3 | PayPal sandbox credentials available to a local script as env vars | Unverified | User |
| 4 | A PayPal developer sandbox account with API access | Unverified | User |

Prerequisite 3 is separate from 2 on purpose: the MCP server carries its own
credentials, but a standalone probe script needs its own client id and secret in
the environment. Having the MCP server working does not imply the script can
authenticate.

## 6. Decisions

**D1 — Judge on observed, never declared.** A field is `present` only if a
sandbox response populated it. A documented field with no observed value is
`absent`, and the declaration is recorded as context, not as evidence
([[mission]] P3).

**D2 — The probe is a single file with inline dependency metadata.** It uses a
PEP 723 `# /// script` block declaring its dependencies and runs via
`uv run scripts/probe_paypal.py`, requiring no `pyproject.toml`. This keeps the
uv project scaffold inside Phase 1 where it belongs, and keeps the probe
visibly disposable.

**D3 — Probe output is committed as an artifact.** Raw captured responses and a
derived `observed.json` are committed under this directory, with identifiers and
any PII redacted. Without the artifact, the table is an unverifiable claim.

**D4 — The MCP server is for exploration; the script is for the record.** Use
the MCP tools to find out what to ask for; use the script to capture the answer
reproducibly. The committed table derives from the script's output.

**D5 — `present-but-unreliable` is a real verdict**, used where a field appears
on some records and not others. It must state the condition under which it
appears — a field present only on cross-currency transactions is useful
information, not a failure.

**D6 — Three verdicts only.** `present`, `absent`, `present-but-unreliable`.
No "probably", no blank cells. An unknown is an unfinished phase.

## 7. Context for whoever picks this up

- The canonical model's PayPal-sourced fields come from [[tech-stack]] §3 and
  [[roadmap]] Phase 3: payment, capture, refund, dispute, payout batch, payout
  item, and `fx_conversion` with source amount/currency, target
  amount/currency, rate, timestamp, fee, provider, `is_derived`.
- `fx_conversion` is where this phase earns its keep. Readme §13 Scenario 3 and
  [[roadmap]] Phase 11 both depend on real conversion evidence. If PayPal does
  not supply it, Scenario 3 narrows to what PayPal does supply and the gap is
  stated openly — it is **not** filled with a public FX rate.
- Repo conventions apply to everything written here, including no emojis
  ([[tech-stack]] §13) and money as `Decimal` or string, never `float`
  ([[tech-stack]] §2) — relevant because the probe parses amounts.

## 8. Open questions

| # | Question | Resolve by |
|---|---|---|
| Q1 | Does the sandbox expose settlement/FX detail at all, or only on specific transaction types? | The phase itself — this is the central question |
| Q2 | Does the APIMatic plugin's declared contract match the sandbox API version we hit? | Task group 5 |
| Q3 | Can a cross-currency payment be created in sandbox without a second funding source? | Task group 2 — if not, the FX row may stay unanswered and that becomes a stated limitation |

---

## Related

- [[mission]] — P3 governs this phase
- [[tech-stack]] — §8, §8.1 PayPal and APIMatic; §2 money rules
- [[roadmap]] — Phase 0, and Phase 3 which consumes this output
- `./plan.md` — the task groups
- `./validation.md` — the merge gate
