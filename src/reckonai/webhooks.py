"""The public webhook route.

`specs/2026-11-11-live-ingestion/requirements.md` F1 to F3.

Acknowledge fast: verify, store, enqueue, answer. The work is the queue's. Any
status other than `2xx` makes PayPal redeliver, so the codes are chosen for
that: a bad signature is `401` (redelivery will not help, and nothing is stored),
and an inability to ask PayPal is `503` (redelivery will).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from reckonai.api.deps import get_session
from reckonai.config import get_settings
from reckonai.logging import event as log_event
from reckonai.paypal import live, webhook

router = APIRouter()
logger = logging.getLogger("reckonai.webhooks")


def get_verifier() -> Iterator[webhook.Verifier]:
    """Overridden in tests; in service, built from settings on each request.

    Unconfigured is a refusal with a reason, not a `500`: the route is public,
    and a delivery cannot be verified without credentials.
    """
    settings = get_settings()
    try:
        credentials = live.credentials()
    except live.LiveFetchError:
        logger.error(log_event("reckonai.webhook.not_configured"))
        raise HTTPException(
            status_code=503, detail="webhook verification is not configured"
        ) from None
    verifier = webhook.Verifier(credentials, settings.paypal_webhook_id)
    try:
        yield verifier
    finally:
        verifier.close()


SessionDep = Annotated[Session, Depends(get_session)]
VerifierDep = Annotated[webhook.Verifier, Depends(get_verifier)]


@router.post("/webhooks/paypal")
async def receive_paypal(
    request: Request, session: SessionDep, verifier: VerifierDep
) -> dict[str, str]:
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="body is not JSON") from None
    if (
        not isinstance(event, dict)
        or not event.get("id")
        or not event.get("event_type")
    ):
        raise HTTPException(status_code=400, detail="not a PayPal event")

    try:
        genuine = await run_in_threadpool(
            verifier.verify, dict(request.headers), event, raw
        )
    except webhook.VerifierUnavailable:
        raise HTTPException(status_code=503, detail="cannot verify right now") from None
    if not genuine:
        raise HTTPException(status_code=401, detail="signature not verified")

    outcome = await run_in_threadpool(_ingest, session, event)
    return {"status": str(outcome)}


def _ingest(session: Session, event: dict) -> webhook.Outcome:
    outcome = webhook.ingest_event(session, event)
    session.commit()
    return outcome
