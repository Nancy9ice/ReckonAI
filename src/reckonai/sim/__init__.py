"""The simulated marketplace: endpoints, seed data and the consistency check.

specs/roadmap.md Phases 4 and 5, run as Block A per
`specs/2026-11-08-replan/requirements.md`.

This package is **the thing being integrated against**, not part of the
product. It stands in for a marketplace's own backend: it holds orders,
sellers, commission rules and a seller ledger, and it serves them over HTTP
under deliberately non-canonical field names so the connector's mapping is
exercised for real rather than matched trivially
(`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D3).

Three modules, in dependency order:

  - `data` - the dataset as hand-written literals, importing nothing from
    `reckonai.models`
  - `seed` - writes it to the canonical tables, and `verify()`, which
    recomputes every stated figure and is what makes the clean baseline
    trustworthy
  - `fields` - the outward field names, in one place, so R3's guard is a single
    assertion
  - `router` - the endpoints, reading the tables back and renaming outward
"""

from __future__ import annotations

from reckonai.sim.router import router

__all__ = ["router"]
