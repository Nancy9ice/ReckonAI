"""`python -m reckonai.eval [--agent] [--json PATH]`.

Exit 0: the deterministic gate passed. Exit 1: it failed. Exit 2: the command could
not run (a non-local database, or `--agent` with no model key). The agent half
never changes the exit code, and whatever happens in it the free half's figures are
printed and written.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data, reconcile
from reckonai.agent import citations
from reckonai.agent.providers import Provider, provider_for
from reckonai.config import ConfigurationError, Settings, get_settings
from reckonai.db import create_app_engine
from reckonai.eval import agent as agent_eval
from reckonai.eval import deterministic
from reckonai.models import Tenant
from reckonai.sim import data, manifest

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reckonai.eval", description=__doc__)
    parser.add_argument(
        "--agent",
        action="store_true",
        help="also score the AI agent (paid model calls; the count is printed first)",
    )
    parser.add_argument("--json", type=Path, help="write the results to this file")
    args = parser.parse_args(argv)

    settings = get_settings()
    host = urlparse(settings.sqlalchemy_url.replace("+psycopg", "")).hostname
    # No host means a local socket. This cannot see a remote database tunnelled to
    # localhost; it only stops the common mistake of pointing at one by name. The
    # eval deletes and re-creates the demo tenant before rolling back.
    if host is not None and host not in LOCAL_HOSTS:
        print(
            f"refusing to run against database host {host!r}: local only",
            file=sys.stderr,
        )
        return 2

    provider: Provider | None = None
    if args.agent:
        try:
            provider = provider_for(settings)
        except ConfigurationError as error:
            print(error, file=sys.stderr)
            return 2

    result: dict[str, object] = {"date": datetime.now(UTC).isoformat()}
    engine = create_app_engine(settings.sqlalchemy_url, pool_size=1, max_overflow=1)
    gate_ok = False
    try:
        with Session(engine) as session:
            try:
                gate_ok = _evaluate(session, settings, provider, result)
            finally:
                # The database is left as it was found, whoever is using it.
                session.rollback()
    finally:
        engine.dispose()
        if args.json is not None:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(result, indent=2, default=str))
    return 0 if gate_ok else 1


def _evaluate(
    session: Session,
    settings: Settings,
    provider: Provider | None,
    result: dict[str, object],
) -> bool:
    """Reseed inside the open transaction, score, and return the gate."""
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()
    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert tenant_id is not None, "the demo tenant was not seeded"

    findings = reconcile.run(session, tenant_id)
    resolver = citations.CitationResolver(session, tenant_id)
    det = deterministic.score(
        findings,
        faults=manifest.FAULTS,
        clean_orders=[order.ref for order in data.CLEAN_ORDERS],
        resolve=resolver.resolve,
    )
    print(deterministic.render(det))
    result["deterministic"] = asdict(det) | {"ok": det.ok}

    if provider is not None:
        _evaluate_agent(session, tenant_id, settings, provider, result)
    return det.ok


def _evaluate_agent(
    session: Session,
    tenant_id: uuid.UUID,
    settings: Settings,
    provider: Provider,
    result: dict[str, object],
) -> None:
    """The agent half reports and never gates: whatever it hits, the free half's
    figures and the exit code stand."""
    print()
    print(
        f"Agent half: {len(agent_eval.planned_cases())} model calls on "
        f"{settings.agent_provider} / {settings.agent_model}.",
        flush=True,
    )
    try:
        scores = agent_eval.run(
            session, tenant_id, provider=provider, model=settings.agent_model
        )
    except Exception as error:  # noqa: BLE001
        print(f"\nAgent half stopped: {type(error).__name__}: {error}")
        result["agent_error"] = f"{type(error).__name__}: {error}"
        return
    print()
    print(
        agent_eval.render(
            scores, provider=settings.agent_provider, model=settings.agent_model
        )
    )
    result["agent"] = {
        "provider": settings.agent_provider,
        "model": settings.agent_model,
        "cases": [asdict(s) for s in scores],
    }


if __name__ == "__main__":
    sys.exit(main())
