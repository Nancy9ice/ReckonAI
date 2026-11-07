# ReckonAI — working rules

AI-powered financial reconciliation and investigation agent for marketplace
operators using PayPal.

The specs in `specs/` are the constitution. Read them before implementing:

- `specs/mission.md` — what we are building, and principles P1–P7
- `specs/tech-stack.md` — locked technology decisions
- `specs/roadmap.md` — phase order and exit criteria

Also read, and in this order:

- `specs/multiparty-direction.md` — the integration model everything assumes.
  The product targets PayPal Complete Payments Platform for Marketplaces, with
  both `INSTANT` and `DELAYED` disbursement. The amendments it called for were
  applied on 2026-11-07, so the three documents above are current; §8 lists what
  is **still unverified** and may not be designed around.
- `specs/paypal-fields.md` — what PayPal actually returns, per field, with the
  evidence. The root specs state rules and point here; they do not repeat it.

**Phase 0 is complete.** Its observed evidence was captured against a
**first-party** integration, so it does not cover platform fees, disbursement
modes or any seller-level view. Phase 0b closes that gap and runs before
Phase 3.

## Hard rules

**No emojis in the codebase.** Source, comments, docstrings, log messages, test
names, commit messages, CLI output, UI strings. Typographic punctuation,
box-drawing characters in docs, and non-ASCII characters in real data are fine —
this bans pictographs, not all non-ASCII. See `specs/tech-stack.md` §13.

**Never use `float` for a monetary value.** `decimal.Decimal` in Python,
`NUMERIC` in Postgres, strings over JSON. Every amount carries its currency.
See `specs/tech-stack.md` §2.

**The LLM never does arithmetic that reaches a financial conclusion.** All
monetary calculation lives in the deterministic engine. The agent explains and
investigates; it does not compute. (`mission.md` P1)

**Never fabricate a financial value.** If PayPal does not supply a settlement,
rate or fee, record the gap as a limitation. Do not estimate, infer, or
substitute a public FX rate for a provider conversion record. (`mission.md` P3)

**Nothing moves money.** No tool and no code path alters a seller balance,
issues a refund or initiates a payout without human approval. (`mission.md` P6)

**The agent loop is append-only.** No compaction, no context editing, no
trimming of earlier tool results. See `specs/tech-stack.md` §5.2.

## Conventions

- Package manager is `uv`. Install with `uv sync --frozen`, run with `uv run`.
  No hand-maintained `requirements.txt`.
- The reconciliation engine is pure functions with no I/O — no database handle,
  HTTP client or clock read inside a calculation.
- Retain raw source payloads; never discard the evidence for a decision.
- Every table carrying marketplace data has a `tenant_id`, and every query
  filters by it.
