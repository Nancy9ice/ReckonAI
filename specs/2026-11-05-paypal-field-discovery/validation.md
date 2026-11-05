# Phase 0 — PayPal field discovery · Validation

**Branch:** `phase-0/paypal-field-discovery`
Scope: `./requirements.md` · Task groups: `./plan.md`

Merge-readiness is **demonstrated by commands that pass or fail**, not asserted.
Anything that cannot be automated is listed in §3 as an explicit manual check
with a stated reason.

---

## 1. Exit criteria mapped to checks

Every roadmap exit criterion, and how it is proven.

| # | Roadmap exit criterion | Check | Type |
|---|---|---|---|
| E1 | APIMatic plugin installed and returning context | §3 M1 | Manual |
| E2 | `specs/paypal-fields.md` committed with declared and observed as separate columns | `check_fields_doc.py --columns` | Auto |
| E3 | Every canonical-intent field marked present / absent / unreliable | `check_fields_doc.py --coverage` | Auto |
| E4 | Verdicts judged on observed, not declared | `check_fields_doc.py --verdict-source` | Auto |
| E5 | Declared-but-not-observed set listed explicitly | `check_fields_doc.py --gap-section` | Auto |
| E6 | Every absent field carries a decision | `check_fields_doc.py --decisions` | Auto |
| E7 | Observed data is reproducible, not transcribed | `probe_paypal.py` re-run + diff | Auto |

## 2. Automated checks

Run in order. Every one must exit 0.

### C1 — Probe runs standalone

```bash
uv run scripts/probe_paypal.py --help
```

Exits 0 with no `pyproject.toml` in the repo. Proves the PEP 723 inline
metadata works and that Phase 1's scaffold has not been pulled forward
(`requirements.md` D2, §4).

### C2 — Observed data is reproducible

```bash
uv run scripts/probe_paypal.py --out /tmp/observed-recheck.json
uv run scripts/check_fields_doc.py --reproduce /tmp/observed-recheck.json
```

Re-probing the fixture transactions yields the same field-presence verdicts as
the committed `artifacts/observed.json`. Sample values and timestamps may
differ; **presence must not**. This is what makes the observed column evidence
rather than a claim (`requirements.md` D3).

### C3 — Table structure

```bash
uv run scripts/check_fields_doc.py --columns
```

`specs/paypal-fields.md` parses as markdown tables with distinct `declared` and
`observed` columns per record type.

### C4 — Coverage

```bash
uv run scripts/check_fields_doc.py --coverage
```

Every PayPal-sourced canonical field in `requirements.md` §7 has a row —
payment, capture, refund, dispute, payout batch, payout item, and all eight
`fx_conversion` fields. **A missing row fails.** A canonical field nobody
checked is the exact failure this phase exists to prevent.

### C5 — Verdicts are well-formed

```bash
uv run scripts/check_fields_doc.py --verdicts
```

Every verdict is exactly one of `present`, `absent`, `present-but-unreliable`.
No blanks, no hedging, no "probably" (`requirements.md` D6). Every
`present-but-unreliable` row states its condition (D5).

### C6 — Verdicts derive from observed, not declared

```bash
uv run scripts/check_fields_doc.py --verdict-source
```

Cross-checks each verdict against `artifacts/observed.json`. A field marked
`present` that was never populated in observed data **fails**. This is the
mechanical enforcement of [[mission]] P3 and the single most important check
here — it is the one that stops a documented field being mistaken for a
delivered one.

### C7 — The gap section exists and is honest

```bash
uv run scripts/check_fields_doc.py --gap-section
```

A declared-but-not-observed section exists. Every field in
`declared.json` but absent from `observed.json` appears in it. If the set is
genuinely empty, the section says so explicitly rather than being omitted.

### C8 — Absent fields carry decisions

```bash
uv run scripts/check_fields_doc.py --decisions
```

Every `absent` and `present-but-unreliable` field has either a derivation (and
is marked as derived) or a declared limitation. No field is left undecided.

### C9 — Repo conventions

```bash
uv run scripts/check_fields_doc.py --conventions
```

- No emojis in anything added by this branch ([[tech-stack]] §13).
- No credential-shaped strings in any tracked file: no client secret, no access
  token, no `Bearer` value.
- No raw buyer PII in committed artifacts. Amounts, currencies, rates, fees and
  timestamps are expected and must **not** be stripped.
- Monetary values in committed artifacts are strings or integers, never JSON
  floats ([[tech-stack]] §2).

## 3. Manual checks

Automatable in principle, but not worth the harness for a half-day phase.

**M1 — APIMatic plugin returns real context.** Ask it for a PayPal response
shape and confirm a substantive answer comes back, not an error or a generic
reply. Record the question asked and a one-line summary in `notes.md`.
*Why manual:* asserting a plugin's answer quality requires judgement.

**M2 — `notes.md` stands alone.** Someone who reads only `notes.md` knows what
Phase 3 must change. *Why manual:* comprehensibility is not checkable by script.

**M3 — Fixture coverage is honest.** Each record type in `fixtures.md` either
has real sandbox ids or a stated reason it could not be created. *Why manual:*
distinguishing "could not" from "did not try" needs a human.

## 4. Merge gate

All of these, together:

- [ ] C1 through C9 exit 0
- [ ] M1, M2, M3 confirmed and recorded in `notes.md`
- [ ] `specs/paypal-fields.md` committed
- [ ] `artifacts/observed.json` and `artifacts/declared.json` committed
- [ ] `fixtures.md` and `notes.md` committed
- [ ] No credentials in any tracked file, verified by C9
- [ ] Working tree clean; every `plan.md` group committed separately
- [ ] PR open against `main` referencing this directory

## 5. What does *not* block merge

Stated so the phase can finish rather than drift.

- **PayPal lacking settlement or FX detail is not a failure.** A clearly
  recorded absence is a successful outcome — it is the finding the phase was
  run to obtain.
- **An unanswerable FX row is acceptable** if the sandbox cannot create a
  cross-currency payment (open question Q3), *provided* `notes.md` states that
  and the row reads `unanswered`, not `absent`. Those are different facts and
  must not be conflated.
- **The canonical model needing amendment is not a failure.** If this phase
  shows [[tech-stack]] §3 must change, that finding is the phase working as
  designed. Record it in `notes.md`; do not amend the model on this branch —
  that is Phase 3's work.

## 6. What makes this phase fail

The inverse, kept short deliberately:

1. A verdict that rests on declared rather than observed data (C6).
2. A canonical field with no row (C4).
3. An absent field with no decision (C8).
4. A hand-transcribed observed column that cannot be reproduced (C2).
5. A credential in a tracked file (C9).
