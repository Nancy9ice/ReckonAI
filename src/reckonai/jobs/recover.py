"""Missed-delivery recovery.

`specs/2026-11-11-live-ingestion/requirements.md` F9.

PayPal retries a failed delivery for days, so this is the safety net behind the
webhook, not the main path. It lists the events PayPal recorded recently and
feeds each through `webhook.ingest_event`, the same function a delivery goes
through. `processed_event` makes an event already handled a no-op, so running
this over a window that overlaps what was delivered normally is safe by
construction.

**Events fetched from PayPal with our own credential over TLS need no
signature check**: the signature exists to authenticate a caller we cannot
otherwise identify, and here we are the caller.

**It does not fall back** to re-fetching known orders if the event list is
refused. `LiveDenied` propagates and is logged by the caller; a quiet fallback
would hide that recovery did not run.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from reckonai.logging import event as log_event
from reckonai.paypal import live, webhook

logger = logging.getLogger("reckonai.jobs.recover")


def recover(
    session: Session, client: live.LiveClient, *, since: datetime, until: datetime
) -> Counter[str]:
    """Queue handlers for events in the window not already handled.

    Commits per event, so one malformed event cannot discard the others.
    """
    outcomes: Counter[str] = Counter()
    for item in client.events(since, until):
        if not item.get("id") or not item.get("event_type"):
            outcomes["malformed"] += 1
            continue
        outcome = webhook.ingest_event(session, item)
        session.commit()
        outcomes[str(outcome)] += 1

    logger.info(log_event("reckonai.jobs.recover.done", **dict(outcomes)))
    return outcomes
