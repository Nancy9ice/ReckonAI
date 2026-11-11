"""The webhook handler job: re-fetch the order a verified event names.

`specs/2026-11-11-live-ingestion/requirements.md` F4.

The dedupe marker is inserted **before** the work and in the same transaction,
as `reconcile_job.execute` does it, so a crash cannot leave a marker for work
that was rolled back, and a failed fetch rolls the marker back with it so the
retry is not mistaken for a duplicate.

`make_client` is module-level so a test replaces it; in service it reads the
sandbox credentials from settings and refuses any other host.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.jobs import queue
from reckonai.paypal import live, replay
from reckonai.paypal.webhook import JOB_TYPE as _JOB_TYPE
from reckonai.paypal.webhook import event_key

JOB_TYPE = _JOB_TYPE


class AlreadyProcessed(Exception):
    """This event was already handled. The job is done, not failed."""


def make_client() -> live.LiveClient:
    return live.LiveClient(live.credentials())


def execute(
    session: Session, *, tenant_id: uuid.UUID, payload: dict[str, object]
) -> None:
    event_id = str(payload["event_id"])
    key = event_key(event_id)
    try:
        queue.mark_processed(
            session, tenant_id=tenant_id, event_id=key, job_type=JOB_TYPE
        )
    except IntegrityError as exc:
        raise AlreadyProcessed(key) from exc

    client = make_client()
    order, _merchant = client.fetch_order(
        str(payload["order_id"]), [str(payload["merchant_id"])]
    )
    document = live.build_document(session, tenant_id, order)
    replay.ingest_document(session, document, tenant_id=tenant_id)
