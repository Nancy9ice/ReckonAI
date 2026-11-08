# Phase 2 — Canonical core · Notes

Findings, recorded gate results, and anything the implementation met that the
specs did not predict. Scope: `./requirements.md` · Plan: `./plan.md` · Gate:
`./validation.md`

---

## Group 0 — Prerequisite gate · CLOSED 2026-11-07

All four facts hold.

| # | Fact | Result |
|---|---|---|
| 1 | Branch cut from `origin/main`, not from `docs/phase-1-complete` | **Pass.** `origin/main` is an ancestor of `HEAD`. Phase 1's PR #8 is merged at `6e63dbc` |
| 2 | Local database up and `DATABASE_URL` exported | **Pass**, after starting Docker — see below |
| 3 | `alembic current` reports the baseline revision | **Pass.** `92be96b3c343 (head)` |
| 4 | Deployed service healthy **before** the phase changes anything | **Pass.** `/health/ready` `200`, `/health` `200` |

**On fact 1.** The branch was first cut from `docs/phase-1-complete` while that
branch was still unmerged, then reset onto `origin/main` once PR #8 landed. The
reset is why fact 1 is checked rather than assumed: a Phase 2 branch based on an
unmerged documentation branch would have carried its commit into this phase's
history and made the eventual PR diff misleading.

**On fact 2.** The Docker daemon was not running, so
`docker compose up -d --wait` failed with
`failed to connect to the docker API at unix:///Users/.../docker.sock`. Starting
Docker Desktop and waiting for the daemon resolved it in about eight seconds.
Worth noting only because the error names a socket path rather than saying the
daemon is down, which reads like a configuration fault and is not one.

**On fact 4.** This reading is the reason Group 9's identical check means
anything. `/health/ready` runs `SELECT 1`, so a `200` after the migration shows
the service came back with the new schema; without the before-reading it would
show only that the service worked at some point.

---

## Group 1 — Money and the currency registry · 2026-11-08

**75 tests pass**, `ruff` clean, and the module imports and runs with
`DATABASE_URL` unset - which is the evidence that the money path has no database
dependency rather than merely not using one today.

One implementation consequence of the float gate worth recording, because it
looks like a workaround and is not. **`value.py` cannot write
`isinstance(value, float)`**, because the gate in `tests/test_money_purity.py`
parses the file's AST and fails on any reference to the `float` builtin,
including inside a type check. So an inexact amount is rejected by *exclusion* -
it reaches the final `else` because it is not a `Decimal`, `int` or `str` - and
the error message names the offending type at runtime through
`type(value).__name__`.

That is stricter than an explicit check rather than weaker: any inexact numeric
type is refused by the same branch, including ones nobody has thought about,
such as a numpy scalar arriving through a future connector.

## Group 2 — Largest-remainder allocation · 2026-11-08

**541 tests pass.** The sum-exactly property is parameterised across 8 amounts,
all 7 registry currencies and 9 weight vectors, which is where most of that
count comes from.

**Decimal division was wrong here, and subtly.** The first implementation
computed each ideal share as `Decimal(units) * weight / weight_total`. Decimal
division is evaluated at the context precision - 28 significant digits - so both
the share and its remainder are *rounded* results, and comparing rounded
remainders can seat the spare minor unit on the wrong party. That is precisely
the residue the function exists to eliminate, reintroduced by the arithmetic
meant to remove it.

Replaced with exact integer arithmetic: the weights are scaled to a common
integer basis, and `divmod(units * weight, weight_total)` gives the floor and
the remainder's numerator. All remainders share the denominator, so their
numerators order them exactly and no division is needed to compare them.
`test_remainders_are_compared_exactly_not_at_context_precision` uses weights
that differ only at the 28th significant digit, and is what would notice a
regression to division.

### The fail-observation, run rather than asserted

`plan.md` Group 2 requires watching the property fail before trusting it. The
remainder distribution loop was disabled (`order[:0]` in place of
`order[:leftover]`) and the suite re-run:

```
311 failed, 230 passed in 0.57s
```

The failures include `test_parts_sum_exactly_to_the_total` across every
currency and every weight vector that does not divide evenly,
`test_negative_totals_also_sum_exactly` for all 7 currencies, and every named
residue case. Restored, and the full money suite is **616 passed**.

This is the check `validation.md` §5 names first among the ways this phase could
be green and wrong: a sum property written with a tolerance, or comparing
rounded sums, would have passed with the distribution removed.

## Group 3 — The JSON boundary · 2026-11-08

**641 tests pass** across the three money suites. Two findings, one of which
changed a class hierarchy.

### `MoneyError` now derives from `ValueError`

`tests/test_money_json.py::test_a_money_field_refuses_an_inexact_amount` failed
on the first run: `InvalidAmount` escaped the Pydantic validator instead of
becoming a `ValidationError`. Pydantic wraps `ValueError` and `AssertionError`
raised inside a validator, and nothing else.

Rather than translating money errors into `ValueError` at each boundary, the
family's base was changed to derive from `ValueError`. That is the accurate base
anyway - every one of these is a bad value - and `except MoneyError` is
unaffected. The practical consequence is that a JSON number arriving in a
request body is refused as an ordinary field validation failure rather than
escaping as a 500.

### Pydantic renders a `Decimal` as a JSON string, so the realistic regression is a float

Worth recording because it changed what the fail-observation had to be.

The first attempt at breaking the gate made `_serialize` return
`quantized.amount` - a bare `Decimal` - rather than `str(...)`. **The suite
stayed green: 25 passed.** Pydantic's JSON serializer renders a `Decimal` as a
string, so a `Decimal` on the wire is already safe and the gate correctly did
not flag it.

Breaking it with an actual float did fire the gate. The body became:

```
{"amount":{"amount":104.05,"currency":"USD"}, ...}
```

and **9 tests failed**, including every currency case and the JPY one. So the
gate distinguishes the harmless case from the harmful one, which is the right
behaviour and not something the first observation would have shown.

**The JPY case is why `_decode_strictly` is paired with an `isinstance` check.**
`parse_float` never fires for a JSON integer, so a broken serializer emitting
JPY's `1699` as a bare number would slip past the decoder hook alone. The
docstring on `_reject_json_number` records this, since it is the reason the
obvious `isinstance(body["amount"], str)` assertion is insufficient *and* the
reason it is still present alongside the hook.
