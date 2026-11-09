"""The Render Blueprint declares what the phase decided.

Render has no local validator, so the first real test of `render.yaml` is the
apply itself (plan.md Group 8). This covers the part that does not need Render:
that the file parses, and that the decisions recorded in requirements.md are
actually the ones in it.

**Why this is worth a test rather than a review.** Four of the database's fields
cannot be changed after the instance is created - `name`, `region`,
`postgresMajorVersion`, `databaseName` and `user`. A later edit to any of them
would not fail an apply; it would either be silently ignored on the existing
instance or, on a fresh one, succeed and be permanent. A test is the only thing
that notices.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_REGION = "frankfurt"
EXPECTED_POSTGRES_MAJOR = "16"
EXPECTED_DATABASE_PLAN = "0.1c-256mb"


@pytest.fixture(scope="module")
def blueprint() -> dict[str, Any]:
    with (PROJECT_ROOT / "render.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def services(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    return blueprint["services"]


@pytest.fixture(scope="module")
def web(services: list[dict[str, Any]]) -> dict[str, Any]:
    return next(service for service in services if service["type"] == "web")


@pytest.fixture(scope="module")
def worker(services: list[dict[str, Any]]) -> dict[str, Any]:
    return next(service for service in services if service["type"] == "worker")


@pytest.fixture(scope="module")
def database(blueprint: dict[str, Any]) -> dict[str, Any]:
    databases = blueprint["databases"]
    assert len(databases) == 1
    return databases[0]


def test_exactly_three_components(blueprint: dict[str, Any]) -> None:
    """One web service, one worker, one database - and no fourth.

    The job queue lives in tables inside the same database. A fourth component
    appearing here would mean that co-location had been given up, and with it
    the single transaction covering a dedupe marker and its financial write
    (specs/tech-stack.md section 4).
    """
    assert len(blueprint["services"]) == 2
    assert len(blueprint["databases"]) == 1


def test_no_static_site_yet(blueprint: dict[str, Any]) -> None:
    """The frontend is Phase 14 (requirements.md D1)."""
    assert not any(
        service["type"] in {"static", "web_static"} for service in blueprint["services"]
    )


def test_every_component_shares_one_region(
    services: list[dict[str, Any]], database: dict[str, Any]
) -> None:
    """Irreversible on the database, and cross-region traffic would be absurd."""
    for service in services:
        assert service["region"] == EXPECTED_REGION
    assert database["region"] == EXPECTED_REGION


def test_the_postgres_major_version_is_pinned_as_a_string(
    database: dict[str, Any],
) -> None:
    """Unquoted `16` would parse as an int, and this field cannot be changed.

    Omitting it entirely is worse: Render then provisions the latest major
    version, breaking the parity with compose.yaml and CI that
    requirements.md D3 exists to protect.
    """
    assert database["postgresMajorVersion"] == EXPECTED_POSTGRES_MAJOR
    assert isinstance(database["postgresMajorVersion"], str)


def test_the_database_plan_is_a_plan_id(database: dict[str, Any]) -> None:
    """`Basic-256mb` is a display name and is rejected; `0.1c-256mb` is the id."""
    assert database["plan"] == EXPECTED_DATABASE_PLAN


def test_external_database_access_is_closed(database: dict[str, Any]) -> None:
    """An absent ipAllowList means allow every IP on a new database.

    This repository becomes public at submission, so the secure posture has to
    be written down rather than inherited (requirements.md D13).
    """
    assert database["ipAllowList"] == []


def test_liveness_is_the_health_check(web: dict[str, Any]) -> None:
    """Not /health/ready: a readiness-gated check turns connection pressure
    into a restart loop that cannot converge."""
    assert web["healthCheckPath"] == "/health"


def test_only_the_web_service_migrates(
    web: dict[str, Any], worker: dict[str, Any]
) -> None:
    """requirements.md D7, both halves of it."""
    assert "alembic upgrade head" in web["preDeployCommand"]
    assert "preDeployCommand" not in worker

    # And never from a start command, where every instance would race on every
    # restart.
    for service in (web, worker):
        assert "alembic" not in service["startCommand"]


def test_both_services_install_from_the_lockfile(
    web: dict[str, Any], worker: dict[str, Any]
) -> None:
    """specs/tech-stack.md section 3: the lockfile is authoritative everywhere."""
    for service in (web, worker):
        assert "uv sync --frozen" in service["buildCommand"]


def test_runtime_commands_do_not_resync_the_environment(
    web: dict[str, Any], worker: dict[str, Any]
) -> None:
    """Every `uv run` outside the build carries --no-sync.

    `uv run` syncs before executing by default. In a deployed service that
    undoes the build's --no-dev by reinstalling the dev group, and it does
    network installs at container start, before the port is bound - so a
    registry hiccup becomes a service that will not boot. Verified locally on
    2026-11-07: a bare `uv run uvicorn` after a --no-dev build installed 9
    packages.
    """
    commands = [
        service[key]
        for service in (web, worker)
        for key in ("startCommand", "preDeployCommand")
        if key in service
    ]
    assert commands, "expected at least one runtime command to check"

    for command in commands:
        if "uv run" in command:
            assert "--no-sync" in command, f"`uv run` without --no-sync: {command}"


def test_the_database_credential_is_referenced_never_written(
    services: list[dict[str, Any]],
) -> None:
    """Render generates the password and injects it; nothing here carries it."""
    for service in services:
        database_url = next(
            var for var in service["envVars"] if var["key"] == "DATABASE_URL"
        )
        assert "value" not in database_url
        assert database_url["fromDatabase"]["property"] == "connectionString"


def test_every_paypal_variable_is_declared_without_a_value(
    services: list[dict[str, Any]],
) -> None:
    """Phase 7 added these, brought forward by the replan's R16.

    This test previously asserted that **no** PayPal variable existed yet,
    on the reasoning that "an empty secret declared now invites being filled
    in the dashboard and then forgotten". That reasoning was about declaring
    them *early*; now the demo's live order needs them, and what matters is
    the half that still holds: a credential must never be in this file.

    So each is `sync: false` - Render prompts for the value - and none
    carries a `value` or a `generateValue`. The same rule the database URL
    follows.
    """
    declared = set()
    for service in services:
        for var in service["envVars"]:
            key = var["key"]
            if not key.startswith("PAYPAL_"):
                continue
            declared.add(key)
            assert var.get("sync") is False, f"{key} must be sync: false"
            assert "value" not in var, f"{key} carries a value in the blueprint"
            assert "generateValue" not in var, key

    assert declared == {
        "PAYPAL_CLIENT_ID",
        "PAYPAL_CLIENT_SECRET",
        "PAYPAL_BN_CODE",
    }, declared


def test_the_predeploy_command_seeds_but_never_calls_paypal(
    services: list[dict[str, Any]],
) -> None:
    """`--ensure` is deterministic; capturing from PayPal is not.

    A sandbox `5xx` in a pre-deploy command fails the deploy and takes the
    service down - the failure mode R6 removed by replaying from fixtures.
    So the blueprint may seed, and may not capture.
    """
    web = next(s for s in services if s["name"] == "reckonai-api")
    command = " ".join(web["preDeployCommand"].split())

    assert "alembic upgrade head" in command
    assert "reckonai.demo_data --ensure" in command
    assert "--live" not in command, "the live call must not gate a deploy"
    assert "capture_paypal" not in command, (
        "capturing writes fixtures and creates sandbox orders; it must never "
        "run on a deploy"
    )
    # Without --no-sync, uv run syncs before executing and undoes the build's
    # --no-dev, installing packages at container start.
    assert command.count("--no-sync") == 2
