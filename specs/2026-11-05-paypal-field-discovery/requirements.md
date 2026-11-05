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
| **Declared** | The APIMatic-generated PayPal Server SDK contract, at a pinned tag — reached via the APIMatic Context Plugin. Disputes and payouts fall back to PayPal's REST reference; see §3.1. | the field exists in the API |
| **Observed** | Live sandbox responses | the field was actually populated for our transactions |

**Amended 2026-11-05.** The declared column was originally specified as coming
from the APIMatic Context Plugin directly. It cannot: the plugin carries no
PayPal field names at all. It is a guide to the generated SDK, and the SDK's
generated model documentation is the contract. See `notes.md`, "Prerequisite 1"
and "The declared column is obtainable". The column's *meaning* is unchanged;
only its source is corrected.

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
  columns, covering the five record types below.

**The declared column has two grades of source, and each row must say which it
used** (amended 2026-11-05). The SDK covers three of the five record types; its
controllers at tag 2.4.0 are `orders`, `payments`, `subscriptions`,
`transaction_search`, `vault` and `o_auth_authorization`. There is no disputes
controller and no payouts controller, and zero models matching `payout`.

| Record type | Fields of interest | Declared source |
|---|---|---|
| **Transactions / captures** | ids, amounts, currencies, fees | SDK 2.4.0 generated contract |
| **Refunds** | linkage back to the original capture, partial-refund shape | SDK 2.4.0 generated contract |
| **Settlement and FX** | rate, conversion fee, settlement currency, conversion timestamp | SDK 2.4.0 generated contract |
| **Disputes** | status, amount, lifecycle states | PayPal REST reference (Disputes v1) — prose documentation, not a generated contract |
| **Payouts** | batch and item structure | PayPal REST reference (Payouts v1) — prose documentation, not a generated contract |

A generated contract and a prose reference are not equivalent evidence. The
first is machine-extracted and version-pinned; the second is read by a human and
can drift from the deployed API without any signal. Rows sourced from prose must
be marked as such, so a reader can tell how much the declared cell is worth.
This does not change the observed column: Group 2 still creates dispute and
payout fixtures and Group 3 still probes them.
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
- **No PayPal Server SDK as a project dependency** (added 2026-11-05). The SDK
  is *read* for the declared column, never installed into the project or
  imported by anything here. Clone it outside the working tree and treat it as a
  read-only reference. Adding it would pull Phase 1's scaffold forward and hand
  a generated deserializer a say in how money is parsed, which §6 D2 and
  [[tech-stack]] §8.1 both refuse. Whether Phase 7's real client uses the SDK is
  Phase 7's decision, recorded in `notes.md`, not settled here.
- **No marketplace-side work.** This phase is PayPal only.

If the phase reveals something important about a later phase, it is written
into this directory as a note and carried forward, not implemented here.

## 5. Prerequisites

Three of these block the phase. Prerequisite 2 was reclassified on 2026-11-05;
see below.

| # | Prerequisite | Blocking | Status | Owner |
|---|---|---|---|---|
| 1 | APIMatic Context Plugin for PayPal installed and returning context | Yes | **Met 2026-11-05** — installed as `paypal@context-plugins-local`, 48 skills loaded. Returns SDK-usage context, not field shapes; see the §1 amendment and `notes.md` | User |
| 2 | `paypal-sandbox` MCP server reachable | **No** (was Yes) | **Open** — token is not in Claude Code's process environment. Does not gate the phase | User |
| 3 | PayPal sandbox credentials available to a local script as env vars | Yes | **Met 2026-11-05** — loadable by a local script; base URL `https://api-m.sandbox.paypal.com` | User |
| 4 | A PayPal developer sandbox account with API access | Yes | **Met 2026-11-05** — token acquired, 30 scopes, **all eight** needed endpoint paths permitted; see `notes.md` | User |

Prerequisite 3 is separate from 2 on purpose: the MCP server carries its own
credentials, but a standalone probe script needs its own client id and secret in
the environment. Having the MCP server working does not imply the script can
authenticate. **Confirmed in practice on 2026-11-05 in the opposite direction** —
the script authenticates and reaches every endpoint while the MCP server is
still down.

**Prerequisite 2 downgraded to non-blocking, 2026-11-05.** It was listed as
blocking, but nothing in `validation.md` depends on it: E1 is the APIMatic
plugin, and E2 through E7, C1 through C9 and M1 through M3 are all script- or
document-based. D4 already settles the matter — "the MCP server is for
exploration; the script is for the record", and "the committed table derives
from the script's output". Holding the phase for a convenience that produces no
committed evidence, and whose bearer token expires roughly every nine hours,
costs schedule for nothing. The script path has been verified end to end.

This is **not** the degraded path §5 originally rejected. That rejection was
about attempting the phase with no API access and no declared-contract source,
which would have made the observed column empty and marked every canonical field
`absent` — a false finding indistinguishable from the real one. We now have
working credentials, a pinned declared contract, and six of eight endpoints
permitted. Nothing about the evidence is weakened by the MCP server being
absent.

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
- **Two `fx_conversion` fields are already decided on the declared side**
  (established 2026-11-05, before any sandbox call). The SDK declares
  `ExchangeRate` with exactly three Optional string fields —
  `source_currency`, `target_currency`, `value` — nested under
  `seller_receivable_breakdown` as Optional and Read-only. It carries **no
  temporal field and no conversion-fee field**. So `fx_conversion.timestamp`
  and a distinct FX conversion fee have no declared source, and no sandbox run
  can produce one. These two rows are therefore settled now: each must be
  derived-and-labelled (`is_derived`, [[tech-stack]] §2) or declared a
  limitation, per §3.1. A timestamp taken from the capture's own `create_time`
  is a derivation, not provider evidence. `paypal_fee` and
  `paypal_fee_in_receivable_currency` are declared, but neither is declared *as*
  an FX conversion fee, and treating one as such would be the substitution
  [[mission]] P3 forbids. Group 6 consumes this; see `notes.md`.
- **The declared contract requires no change to the money rules.** `float`
  appears zero times across all 373 generated model pages; every monetary field
  is `Money{currency_code: str, value: str}`. PayPal declares money as strings
  carrying their own currency, exactly as [[tech-stack]] §2 requires, so amounts
  parse straight into `Decimal` with no float ever existing.
- Repo conventions apply to everything written here, including no emojis
  ([[tech-stack]] §13) and money as `Decimal` or string, never `float`
  ([[tech-stack]] §2) — relevant because the probe parses amounts.

## 8. Open questions

| # | Question | Resolve by |
|---|---|---|
| Q1 | Does the sandbox expose settlement/FX detail at all, or only on specific transaction types? | The phase itself — this is the central question |
| Q2 | Does the declared contract in PayPal Server SDK `2.4.0` (commit `b1460117a5618fb1a5b36b3ea0b1a49e124220c3`) match the sandbox API version we hit? Reworded 2026-11-05: there is no plugin-held contract to compare, so the question is SDK tag against sandbox. | Task group 5 |
| Q3 | Can a cross-currency payment be created in sandbox without a second funding source? | Task group 2 — if not, the FX row may stay unanswered and that becomes a stated limitation |

---

## Related

- [[mission]] — P3 governs this phase
- [[tech-stack]] — §8, §8.1 PayPal and APIMatic; §2 money rules
- [[roadmap]] — Phase 0, and Phase 3 which consumes this output
- `./plan.md` — the task groups
- `./validation.md` — the merge gate
