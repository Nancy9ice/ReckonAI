"""The mapping guard: no canonical field draws from a source field of its name.

specs/roadmap.md Phase 6's R3 guard and
`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D1 and D3.

**Why this file exists at all.** We author both sides of the connector boundary.
That makes a trivially-matching mapping easy to write, and a mapping that
matches trivially means mapping was never exercised - so the whole of A3 would
pass while proving nothing. The replan names that risk explicitly so it is
guarded rather than discovered, and these are the guards.

An earlier draft of D1 proposed a different one: that `reckonai.sim` import
nothing from `reckonai.models`. That is unimplementable - the seed builder's
whole job is writing those tables - and it was never the real risk. The risk is
in the **names**, so the guard is on the names.
"""

from __future__ import annotations

import pytest

from reckonai.models import Base
from reckonai.sim import fields


def _canonical_column_names() -> set[str]:
    """Every column name in the canonical schema."""
    return {
        column.name
        for table in Base.metadata.tables.values()
        for column in table.columns
    }


def _canonical_composite_names() -> set[str]:
    """Every composite attribute name, such as `total` or `gross_amount`.

    **Not columns**, which is the point: `money_columns("gross_amount")` emits
    `gross_amount_amount` and `gross_amount_currency`, so a column-name check
    passes the bare `gross_amount` silently - and `gross_amount` is exactly the
    name a mapping author would reach for. This is the half of the guard that
    caught a real name during Block A.
    """
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        names.update(prop.key for prop in mapper.composites)
    return names


def test_no_canonical_field_maps_from_an_identically_named_source_field() -> None:
    """R3's guard, stated exactly as the replan states it.

    If this fails, the offending entries are named rather than counted - a bare
    count would say the guard broke without saying where.
    """
    offenders = fields.identical_pairs()

    assert not offenders, (
        "a canonical field draws from a source field of the same name, which "
        "means the mapping for it is not exercised: "
        + ", ".join(f"{r}.{c} <- {o}" for r, c, o in offenders)
    )


def test_no_outward_name_collides_with_a_canonical_column() -> None:
    """The stronger, global half.

    A name that is canonical *somewhere else* is the near-miss a mapping gets
    wrong: `state` is innocuous on a dispute and wrong on a disbursement.
    """
    collisions = fields.outward_names() & _canonical_column_names()

    assert not collisions, (
        "these outward field names are also canonical column names, which is "
        f"the near-miss the guard exists for: {sorted(collisions)}"
    )


def test_no_outward_name_collides_with_a_canonical_composite() -> None:
    """The half a column-name check cannot see.

    `gross_amount` is a composite attribute on `capture` and `refund`, not a
    column, so the test above would wave it through. This is the check that
    moved the order total's outward name to `order_gross`.
    """
    composites = _canonical_composite_names()

    # The guard is worthless if the set it compares against is empty, which is
    # how a reflection-based check fails silently. `total`, `unit_price` and
    # `amount` are all composites, so a working implementation finds them.
    assert {"total", "unit_price", "amount"} <= composites, (
        "no composite attributes were discovered, so this check is comparing "
        f"against an empty set and cannot fail. Found: {sorted(composites)}"
    )

    collisions = fields.outward_names() & composites

    assert not collisions, (
        "these outward field names are also canonical composite attributes: "
        f"{sorted(collisions)}"
    )


def test_every_resource_the_router_serves_has_a_field_map() -> None:
    """So a new endpoint cannot be added outside the guard.

    The guard iterates `ALL_FIELD_MAPS`; an endpoint that built its keys inline
    would be invisible to it. This pins the two together.
    """
    # `from reckonai.sim import router` gives the APIRouter, not the module -
    # `__init__` re-exports it under that name. Import the module explicitly.
    from reckonai.sim.router import router as api_router

    served = {
        route.path.rsplit("/", 1)[-1]
        for route in api_router.routes
        if getattr(route, "path", "").startswith("/marketplace/")
        and "{" not in getattr(route, "path", "")
    }
    mapped = {
        name.replace("_", "-")
        for name in (*fields.ALL_FIELD_MAPS, *fields.NON_CANONICAL_FIELD_MAPS)
    }

    assert served <= mapped, (
        "these endpoints have no entry in fields.py, so the mapping guard "
        f"does not see them: {sorted(served - mapped)}"
    )


@pytest.mark.parametrize("resource", sorted(fields.ALL_FIELD_MAPS))
def test_a_field_map_is_read_only(resource: str) -> None:
    """`MappingProxyType`, so an endpoint cannot add a key past the guard."""
    with pytest.raises(TypeError):
        fields.ALL_FIELD_MAPS[resource]["smuggled"] = "seller_id"  # type: ignore[index]
