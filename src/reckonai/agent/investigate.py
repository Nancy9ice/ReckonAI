"""Investigate one exception: the service function, and the CLI around it.

`requirements.md` D8. A `python -m` entry point, the same invocation shape as
`reckonai.jobs.schedule`:

    uv run python -m reckonai.agent.investigate --fault F1
    uv run python -m reckonai.agent.investigate --order ORD-0013 --withhold ledger

**No HTTP route and no job type**, deliberately. Block F owns the next deploy,
the dashboard and the queued form, and a synchronous route over a multi-minute
model call is the wrong shape. This block's job is to prove the agent works at
all.

`investigate()` is separated from `main()` so the orchestration is testable
without a process boundary - and so Block F can call it from a job handler
without reaching through a CLI.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.agent.prompt import SYSTEM_PROMPT, user_turn
from reckonai.agent.providers import Provider, ProviderError, provider_for
from reckonai.agent.registry import tool_specs
from reckonai.agent.report import InvestigationReport
from reckonai.agent.store import (
    InconsistentReport,
    failed_investigation,
    store_investigation,
)
from reckonai.agent.tools import WITHHOLDABLE, InvestigationTools
from reckonai.config import ConfigurationError, Settings, get_settings
from reckonai.db import create_app_engine
from reckonai.logging import configure_logging, event
from reckonai.models import Investigation, Tenant
from reckonai.recon import Finding, exceptions_only
from reckonai.reconcile import run
from reckonai.sim import data, manifest

logger = logging.getLogger("reckonai.agent.investigate")


class UnknownFault(LookupError):
    """`--fault` named something the sim manifest does not carry."""


class NoSuchException(LookupError):
    """There is no exception to investigate for the given order.

    Raised rather than investigated as a blank subject: an agent handed an
    order with no finding would be asked to explain nothing, and whatever it
    returned would be a cause for a discrepancy that does not exist.
    """


@dataclass(frozen=True)
class Outcome:
    """What one investigation produced, for the CLI to print."""

    investigation: Investigation
    report: InvestigationReport | None

    #: References the agent cited that resolve to no record. Empty is the
    #: passing case for Phase 12's second exit criterion.
    unresolved: tuple[str, ...]

    #: References that resolve but will not survive a reseed - a
    #: `platform_fee.external_id`. Empty is the passing case for Block C's
    #: cite-the-capture rule.
    unstable: tuple[str, ...]


def investigate(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    order_ref: str,
    provider: Provider,
    model: str,
    withhold: tuple[str, ...] = (),
    code: str | None = None,
) -> Outcome:
    """Investigate one exception on one order, and persist the result.

    **The exception is selected through `exceptions_only()`**, never
    `len(findings)`: `awaiting_evidence` is a finding and not an exception, so
    selecting from raw findings could hand the agent a lifecycle state to
    explain as though it were a discrepancy.

    Flushes but does not commit. The caller owns the transaction, so a CLI
    commits and a test's rolled-back session does not.
    """
    finding = _exception_for(session, tenant_id, order_ref, code)

    tools = InvestigationTools(session, tenant_id, withhold=withhold)
    specs = tool_specs(tools)

    try:
        result = provider.investigate(
            system=SYSTEM_PROMPT,
            user=user_turn(order_ref, str(finding.code), finding.detail),
            tools=specs,
            model=model,
        )
    except ProviderError as error:
        # The call did not happen. Recorded as a failed investigation with no
        # stop reason, because no response was observed.
        return Outcome(
            investigation=failed_investigation(
                session,
                tenant_id,
                order_ref=order_ref,
                code=str(finding.code),
                provider=provider.name,
                model_requested=model,
                reason=str(error),
                withheld=tools.withheld,
            ),
            report=None,
            unresolved=(),
            unstable=(),
        )

    try:
        stored = store_investigation(
            session,
            tenant_id,
            order_ref=order_ref,
            code=str(finding.code),
            result=result,
            withheld=tools.withheld,
        )
    except InconsistentReport as error:
        # **The attempt still gets a row.** `store_investigation` raises this
        # before it adds anything, so an uncaught `InconsistentReport` left a
        # paid model call with no `investigation` row at all - contradicting
        # `models/investigation.py`'s "one row per attempt, whatever the
        # outcome" and emptying the audit trail in exactly the case where
        # something went wrong. Both providers can produce it: nothing in
        # either schema puts `minItems` on `causes`, so a `reported` verdict
        # with an empty list is schema-valid. Found by code review.
        return Outcome(
            investigation=failed_investigation(
                session,
                tenant_id,
                order_ref=order_ref,
                code=str(finding.code),
                provider=result.provider,
                model_requested=result.model_requested,
                reason=f"the report was inconsistent and was not stored: {error}",
                withheld=tools.withheld,
                # A response *was* observed here, unlike the ProviderError
                # branch above. Recording what served it is the difference
                # between "we called and got something unusable" and "we never
                # called".
                model_returned=result.model_returned,
                stop_reason=result.stop_reason,
            ),
            report=None,
            unresolved=(),
            unstable=(),
        )

    # Read back from the persisted rows rather than from the report, so what
    # is reported is what was actually written.
    from reckonai.agent.citations import CitationResolver
    from reckonai.models import InvestigationCause

    resolver = CitationResolver(session, tenant_id)
    cited: list[str] = []
    unresolved: list[str] = []
    # Ordered by rank: everything else in this block is deterministic for
    # Block G's comparison, and without this the reported citations came back
    # in whatever order Postgres chose. Found by code review.
    for cause in session.scalars(
        select(InvestigationCause)
        .where(
            InvestigationCause.tenant_id == tenant_id,
            InvestigationCause.investigation_id == stored.id,
        )
        .order_by(InvestigationCause.rank)
    ):
        cited.extend(cause.cited_refs)
        unresolved.extend(cause.unresolved_refs)

    return Outcome(
        investigation=stored,
        report=result.report,
        unresolved=tuple(unresolved),
        unstable=resolver.unstable(tuple(cited)),
    )


def _exception_for(
    session: Session, tenant_id: uuid.UUID, order_ref: str, code: str | None
) -> Finding:
    """The exception to investigate, or a named error.

    `reconcile.run` returns every finding regardless of any window, so the
    agent is not limited to what a scheduled run covered - which is what lets
    the CLI be pointed at any order.
    """
    candidates = [
        finding
        for finding in exceptions_only(run(session, tenant_id))
        if finding.order_ref == order_ref
    ]

    if code is not None:
        candidates = [
            finding for finding in candidates if str(finding.code) == code
        ]

    if not candidates:
        raise NoSuchException(
            f"no exception on {order_ref}"
            + (f" with code {code}" if code else "")
            + " - nothing to investigate"
        )

    if len(candidates) == 1:
        return candidates[0]

    # **Worst first, but only among comparable amounts.** Keying on the bare
    # `Decimal` compared across currencies, so a 500 JPY impact outranked a
    # 100 USD one - which specs/mission.md P2 forbids: two amounts are not
    # comparable merely because their numbers look alike. `mixed_currency_order`
    # exists precisely because one order can carry both.
    #
    # With one currency, rank by impact. With more than one, there is no
    # defensible ordering, so take the engine's own emission order and say so
    # rather than inventing a comparison. Found by code review.
    currencies = {finding.impact.currency for finding in candidates}
    if len(currencies) > 1:
        logger.warning(
            event(
                "reckonai.agent.investigate.incomparable_impacts",
                order_ref=order_ref,
                currencies=sorted(currencies),
                chose=str(candidates[0].code),
            )
        )
        return candidates[0]

    return max(candidates, key=lambda finding: finding.impact.amount)


def _order_for_fault(fault_id: str) -> str:
    """The order a seeded fault sits on.

    `--fault F1` resolves through the manifest, which is Block G's ground
    truth, so the demo and the eval name the same thing.
    """
    try:
        return manifest.fault(fault_id).order_ref
    except KeyError:
        # A named error, like every other bad input in `main()`. The module
        # docstring states that as the design, and `manifest.fault` raises a
        # bare `KeyError`. Found by code review.
        known = ", ".join(entry.id for entry in manifest.FAULTS)
        raise UnknownFault(
            f"no fault {fault_id!r} in the sim manifest. Known faults: {known}"
        ) from None


def main(argv: list[str] | None = None) -> int:
    """The CLI. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m reckonai.agent.investigate",
        description="Investigate one reconciliation exception with the AI agent.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--order", help="the marketplace order reference")
    target.add_argument(
        "--fault", help="a seeded fault id from the sim manifest, such as F1"
    )
    parser.add_argument(
        "--code",
        help="which exception to investigate, if the order carries more than one",
    )
    parser.add_argument(
        "--withhold",
        action="append",
        default=[],
        choices=sorted(WITHHOLDABLE),
        help=(
            "withhold a record kind from the agent's tools, leaving the "
            "database untouched. Repeatable."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=("anthropic", "openai"),
        help="override settings.agent_provider for this run",
    )
    parser.add_argument(
        "--model", help="override settings.agent_model for this run"
    )
    parser.add_argument("--tenant", help="tenant slug (default: the demo tenant)")
    args = parser.parse_args(argv)

    configure_logging()

    try:
        settings = _settings_for(args)
    except ConfigurationError as error:
        # A named error rather than a traceback: the likeliest failure here is
        # a missing credential, and the message says which variable.
        logger.error("%s", error)
        return 2

    try:
        order_ref = args.order or _order_for_fault(args.fault)
    except UnknownFault as error:
        logger.error("%s", error)
        return 2

    engine = create_app_engine(
        settings.sqlalchemy_url, pool_size=1, max_overflow=1
    )
    with Session(engine) as session:
        tenant_slug = args.tenant or data.TENANT_SLUG
        tenant_id = session.scalar(
            select(Tenant.id).where(Tenant.slug == tenant_slug)
        )
        if tenant_id is None:
            logger.error(
                "no tenant %r in this database - seed it first with "
                "`python -m reckonai.demo_data --ensure`",
                tenant_slug,
            )
            return 2

        try:
            # `provider_for` is what asserts the credential, so it belongs
            # inside this handler. Found by running the CLI with no key set:
            # it raised a `ConfigurationError` through `main` as a traceback,
            # when the whole point of the named error is that a missing
            # variable reads as one line.
            provider = provider_for(settings)
        except ConfigurationError as error:
            logger.error("%s", error)
            return 2

        try:
            outcome = investigate(
                session,
                tenant_id,
                order_ref=order_ref,
                provider=provider,
                model=settings.agent_model,
                withhold=tuple(args.withhold),
                code=args.code,
            )
        except NoSuchException as error:
            logger.error("%s", error)
            return 1

        session.commit()
        _print_outcome(outcome, settings)

    return 0


def _settings_for(args: argparse.Namespace) -> Settings:
    """Settings with the CLI's overrides applied.

    Rebuilt through `model_copy` rather than mutated, so the provider/model
    agreement validator still runs - `--provider openai` without `--model`
    must fail rather than sending a Claude id to OpenAI.
    """
    settings = get_settings()

    overrides: dict[str, str] = {}
    if args.provider:
        overrides["agent_provider"] = args.provider
    if args.model:
        overrides["agent_model"] = args.model

    if not overrides:
        return settings

    merged = settings.model_dump()
    merged.update(overrides)
    try:
        return Settings(**merged)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def _print_outcome(outcome: Outcome, settings: Settings) -> None:
    """Report what happened, through the structured logger."""
    row = outcome.investigation

    logger.info(
        event(
            "reckonai.agent.investigate.complete",
            investigation_id=str(row.id),
            order_ref=row.order_external_ref,
            code=row.code,
            verdict=row.verdict,
            provider=row.provider,
            model_requested=row.model_requested,
            model_returned=row.model_returned,
            downgraded=row.downgraded,
            stop_reason=row.stop_reason,
            withheld=row.withheld,
            causes=len(outcome.report.causes) if outcome.report else 0,
            unresolved_citations=list(outcome.unresolved),
            unstable_citations=list(outcome.unstable),
        )
    )

    if outcome.report is None:
        logger.error(
            "the investigation produced no report (verdict: %s)", row.verdict
        )
        return

    logger.info("verdict: %s", row.verdict)
    logger.info("summary: %s", outcome.report.summary)
    for rank, cause in enumerate(outcome.report.ranked(), start=1):
        logger.info(
            "cause %d (confidence %.2f): %s", rank, cause.confidence, cause.description
        )
        logger.info("    cites: %s", ", ".join(cause.cited_refs) or "nothing")

    if outcome.unresolved:
        # Phase 12's second exit criterion, failing. Loud, because a
        # fabricated citation is the most serious error the agent can make.
        logger.error(
            "FABRICATED CITATIONS - these name no record: %s",
            ", ".join(outcome.unresolved),
        )
    if outcome.unstable:
        logger.warning(
            "unstable citations (cite the capture or refund instead): %s",
            ", ".join(outcome.unstable),
        )


if __name__ == "__main__":
    sys.exit(main())
