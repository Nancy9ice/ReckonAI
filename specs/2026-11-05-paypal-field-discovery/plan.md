# Phase 0 — PayPal field discovery · Plan

**Branch:** `phase-0/paypal-field-discovery`
Scope and decisions: `./requirements.md` · Merge gate: `./validation.md`

Each group leaves the repo in a working state and ends with one commit, so any
group can be reverted on its own. Do not start a group until the previous one's
exit condition holds.

---

## Group 0 — Prerequisite gate · no commit

Not implementation. A hard stop that prevents the rest of the phase being done
on guesswork.

1. Confirm the APIMatic Context Plugin is installed and loading its skills.
   **Done 2026-11-05.** Do not ask it for an endpoint response shape — it holds
   no PayPal field names and cannot answer that. Confirm instead that it names
   the SDK distribution and version, and that its skills load. See `notes.md`,
   "M1 — the plugin test, and what it returned".
2. Confirm the `paypal-sandbox` MCP server connects (it returned `ECONNRESET`
   earlier).
3. Confirm sandbox credentials are present as environment variables and are
   **not** in any tracked file.
4. Record the sandbox API version and base URL in `notes.md` in this directory.

**Exit:** all four prerequisites in `requirements.md` §5 hold.
**If blocked:** stop and report which prerequisite failed. Do not proceed
degraded — that path was explicitly rejected.

---

## Group 1 — Scaffold the probe harness · commit

1. Create `scripts/probe_paypal.py` as a single file with a PEP 723
   `# /// script` block declaring its dependencies (`httpx`).
2. Implement OAuth token acquisition against the sandbox, reading credentials
   from the environment. No credential ever reaches a file or a log line.
3. Implement `--out <path>` writing JSON, and a `--record-type` selector.
4. Parse every monetary value with `Decimal`, never `float`
   ([[tech-stack]] §2) — relevant even in a throwaway, because a `float` here
   would misreport what PayPal returned. PayPal sends amounts as JSON strings
   (confirmed against the declared contract, `requirements.md` §7), so pass the
   string straight to `Decimal`; never let it touch `float` on the way.
5. No client class, no retry layer, no pagination abstraction. A flat script.
6. **Do not import the PayPal Server SDK** (added 2026-11-05). The probe reads
   raw JSON off the wire and records what was actually there. Any deserializer
   between the response and `observed.json` is an interpreter standing between
   us and the evidence, and the observed column is the one artifact this phase
   exists to produce. `requirements.md` §4 forbids the SDK as a dependency here
   for the same reason.

**Verify:** `uv run scripts/probe_paypal.py --help` exits 0 with no
`pyproject.toml` present.
**Commit:** `chore(phase-0): add disposable PayPal sandbox probe harness`

---

## Group 2 — Seed sandbox fixture transactions · commit

An empty sandbox observes nothing.

1. Create a same-currency payment, captured.
2. Create a **cross-currency** payment — customer currency differing from the
   account's settlement currency. This is the only path to an answer on the FX
   row.
3. Issue a partial refund against one capture.
4. Open a dispute if the sandbox permits it; if not, record that as a
   limitation rather than leaving the row blank.
5. Create a payout batch with at least two items.
6. Record every created id in `fixtures.md` in this directory, so the probe is
   re-runnable against known transactions.

**Verify:** each fixture id is retrievable through the API.
**Commit:** `docs(phase-0): record sandbox fixture transactions`
**If Q3 blocks cross-currency:** record it in `notes.md`, continue, and let the
FX row carry an explicit `unanswered — see notes` rather than a guess.

---

## Group 3 — Capture the observed column · commit

1. Run the probe across all five record types: transactions/captures, refunds,
   disputes, payouts, and whatever carries settlement/FX.
2. Write raw responses to `artifacts/raw/` in this directory.
3. Redact identifiers and any buyer PII from the committed copies. Amounts,
   currencies, rates, fees and timestamps are **kept** — they are the evidence.
4. Derive `artifacts/observed.json`: per record type, each field path with
   whether it was populated, on how many of N sampled records, and a sample
   value.

**Verify:** `observed.json` has a non-empty entry for every record type that
Group 2 could create.
**Commit:** `docs(phase-0): capture observed PayPal sandbox field data`

---

## Group 4 — Capture the declared column · commit

**Rewritten 2026-11-05.** The original steps said to get the declared shape from
the plugin. The plugin holds no field names; see `requirements.md` §1.

1. Clone the SDK outside the working tree and pin it to the tag — not `main`,
   which keeps moving after a release with nothing in the tree to signal it:

   ```bash
   git clone --filter=blob:none --branch main \
     https://github.com/paypal/PayPal-Python-Server-SDK
   git checkout tags/2.4.0   # commit b1460117a5618fb1a5b36b3ea0b1a49e124220c3
   ```

   `pip install paypal-server-sdk` is **not** a substitute: `doc/` never lands in
   `site-packages`, so the installed package carries no field tables.
2. For the three SDK-covered record types, extract the declared shape from
   `doc/models/*.md` — 373 pages, each a `Name | Type | Tags | Description`
   table whose Tags column carries `Required`, `Optional` and `Read-only`.
   Cross-check against the model classes in `paypalserversdk/models/`, where
   `_names`, `_optionals`, `_nullables` and `from_dictionary` live.
3. For **disputes and payouts**, the SDK has no coverage at all. **Amended
   2026-11-06:** take the declared shape from PayPal's published OpenAPI 3.0.3
   documents in `paypal/paypal-rest-api-specifications`, pinned to a commit, and
   mark every such row with the `openapi_specification` source grade. The
   original instruction was to read PayPal's prose REST reference; a pinned
   machine-readable document from PayPal is strictly better evidence on the
   exact axis `requirements.md` §3.1 cares about. See the §3.1 amendment and
   `notes.md`, Group 4.
4. Write `artifacts/declared.json` keyed on the **API property name** — the
   value side of each model's `_names` map, not the Python attribute name. The
   generator renames fields (`mtype` for a property called `type`,
   `person_type` for `personType`), and `observed.json` comes from raw JSON, so
   keying on the Python name makes the two artifacts undiffable and defeats the
   point of this step.
5. Record provenance in `declared.json` itself: plugin, SDK repository, tag and
   commit SHA, and per record type which of the two source grades was used.
   Without it the declared column is an unverifiable claim, the same objection
   D3 makes about the observed column.
6. Note any place the SDK's contract and the sandbox's actual API version
   disagree (open question Q2).

**Verify:** `declared.json` covers every record type in `observed.json`, carries
a provenance block, and is keyed on API property names.
**Commit:** `docs(phase-0): capture declared PayPal API contract`

---

## Group 5 — Build the field availability table · commit

1. Generate `specs/paypal-fields.md` from the two artifacts — one row per
   field, with declared and observed as separate columns.
2. Assign each field a verdict: `present`, `absent`, or
   `present-but-unreliable`. Judged on **observed** only
   (`requirements.md` D1, D6).
3. For `present-but-unreliable`, state the condition under which the field
   appears.
4. Add the **declared-but-not-observed** section — the highest-value output of
   this phase. Every entry gets a line on what its absence means for the
   canonical model.
5. Cover every PayPal-sourced canonical field listed in `requirements.md` §7.
   A canonical field with no row is an incomplete table.

**Verify:** no blank verdict cells; every field from §7 appears.
**Commit:** `docs(phase-0): add PayPal field availability table`

---

## Group 6 — Record decisions for absent fields · commit

For each `absent` and each `present-but-unreliable` field, one of:

- **Derive it** — state the derivation, and that it must be written to an
  `is_derived` field and never presented as provider evidence
  ([[tech-stack]] §2, [[mission]] P3).
- **Declare the limitation** — state what the product cannot do as a result.

Then assess the blast radius:

1. If settlement or FX evidence is absent, write the consequence for readme §13
   Scenario 3 and for [[roadmap]] Phase 11 into `notes.md`.
2. List any `fx_conversion` field from `requirements.md` §7 that cannot be
   populated from PayPal.

**Verify:** no absent field is left without a decision.
**Commit:** `docs(phase-0): record decisions for absent PayPal fields`

---

## Group 7 — Validation checks · commit

1. Add `scripts/check_fields_doc.py` implementing the automated checks in
   `validation.md` — every expected canonical field present, every verdict from
   the allowed set, every absent field carrying a decision.
2. Run the full check sequence from `validation.md` and fix what fails.
3. Confirm the repo conventions pass, including the emoji scan.

**Verify:** every check in `validation.md` passes.
**Commit:** `test(phase-0): add field-availability doc checks`

---

## Group 8 — Handoff · commit

1. Write `notes.md` conclusions for the phases that consume this: Phase 3
   (canonical event tables), Phase 7 (PayPal ingestion), Phase 11 (currency
   comparison).
2. State plainly whether the canonical model as sketched in [[tech-stack]] §3
   survives contact with the real API, or needs amendment. **If it needs
   amendment, say so here** — that finding is this phase succeeding, not
   failing.
3. Open a PR against `main` referencing this directory.

**Verify:** a reader of `notes.md` alone knows what to change before Phase 3.
**Commit:** `docs(phase-0): handoff notes for phases 3, 7 and 11`

---

## Sequencing notes

- Groups 1 and 2 are independent and can run in either order.
- Group 3 needs 1 and 2. Group 5 needs 3 and 4. Everything else is linear.
- Groups 3 and 4 both write to `artifacts/`; keep their files separate so
  either can be regenerated alone.
- **The only group that can fail in an interesting way is Group 2**, if the
  sandbox will not create a cross-currency payment. Everything downstream is
  mechanical once the data exists.
