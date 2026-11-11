"""The execution substrate: a job queue and a dedupe marker.

specs/tech-stack.md section 4, narrowed by the replan's R7:
`specs/2026-11-09-queue-scheduled-run/requirements.md` D5 and D6.

**Why the queue is Postgres tables and not a broker.** Idempotency has to be
transactional with the financial write it protects. With the dedupe marker in
Postgres and the exception rows in Postgres, one `COMMIT` covers both. Split
across Redis and Postgres, a crash between the two leaves a marker claiming an
event was processed when its financial effect was rolled back - a silent lost
refund. That argument is the reason these two tables are here rather than in a
service of their own, and nothing in this module may weaken it.

**Neither table links to a `raw_payload`.** The rule is that every *financial
record* carries the evidence behind it, and a job is not one: it is an
instruction to go and look. The rows a job produces carry their own evidence -
see `reckonai/models/reconciliation.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from reckonai.models.base import Base, TenantMixin, TimestampMixin
from reckonai.models.lifecycle import state_check

#: What a job asks the worker to do.
#:
#: A checked vocabulary rather than free text, for the reason
#: `LEDGER_ENTRY_CATEGORIES` is one: a typo in an enqueuer becomes a job no
#: handler claims, which sits in the queue looking like a backlog rather than
#: like a bug. The `CHECK` refuses it at the insert instead.
#:
#: Two members: Block F registered `investigation` here (requirements.md D7),
#: a one-line change by design plus the migration that widens the `CHECK`.
JOB_TYPES: tuple[str, ...] = ("reconciliation_run", "investigation", "paypal_event")

#: Where a job is in its life.
#:
#: `claimed` is a distinct state from `queued` because `FOR UPDATE SKIP
#: LOCKED` only holds a row for the life of a transaction. The claim commits
#: the status change, so a worker that dies after claiming leaves a `claimed`
#: row rather than a locked one - recoverable by `run_after`, which is D6's
#: stated reason for not building a heartbeat.
#:
#: `failed` is terminal: attempts are exhausted and `last_error` says why.
JOB_STATUSES: tuple[str, ...] = ("queued", "claimed", "done", "failed")

#: The default ceiling on attempts. Three, not a tuned policy - R7 narrowed
#: retry to an attempt count, a `run_after` and a terminal failed state.
DEFAULT_MAX_ATTEMPTS = 3

#: How long a failed job waits before it is eligible again. A fixed delay, for
#: the same reason: an exponential backoff is a policy to tune, and nothing in
#: this MVP has the traffic to tune it against.
RETRY_DELAY_SECONDS = 60


class Job(TenantMixin, TimestampMixin, Base):
    """One unit of work, claimed by exactly one worker.

    Claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, which is what makes
    concurrent claiming safe without a broker. That is not a hypothetical
    condition to construct in a test: Render's rolling deploy runs two workers
    concurrently for about 90 seconds on every deploy (observed 2026-11-08),
    so this has to be right before the first real job type exists.
    """

    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    job_type: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The handler's arguments. JSONB rather than columns per job type, because
    #: a second job type would otherwise be a migration.
    #:
    #: **No monetary value belongs in here.** A `Decimal` does not survive a
    #: JSON round trip as itself, and specs/tech-stack.md section 2 requires
    #: money to cross JSON as a string with its currency. A job carries
    #: identifiers and a date window; the amounts are read from the canonical
    #: tables by the handler.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="queued", index=True
    )

    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")

    max_attempts: Mapped[int] = mapped_column(
        nullable=False, server_default=str(DEFAULT_MAX_ATTEMPTS)
    )

    #: Not eligible for claiming before this. Server-defaulted to now, so an
    #: enqueue that states no time means "as soon as possible" rather than
    #: leaving a NULL that every claim query then has to special-case.
    run_after: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True
    )

    claimed_at: Mapped[datetime | None] = mapped_column()

    #: The exception type and message of the last failure. **Never a
    #: traceback**: a traceback can carry row contents, and this column is
    #: read by a dashboard (requirements.md D6).
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        state_check("job_type", JOB_TYPES, name="job_type"),
        state_check("status", JOB_STATUSES, name="status"),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        # A job cannot have tried more times than it is allowed to. The
        # constraint is what makes `failed` reachable only through the path
        # that records an error, rather than by an off-by-one in a handler.
        CheckConstraint("attempts <= max_attempts", name="attempts_within_cap"),
        UniqueConstraint("tenant_id", "id"),
    )


class ProcessedEvent(TenantMixin, TimestampMixin, Base):
    """The marker that makes processing the same work twice impossible.

    **The database is what enforces this, not application logic.** A handler
    that checked for an existing row and then inserted one has a window
    between the two; a `UNIQUE` constraint has none.

    specs/tech-stack.md section 4 names the key as "the provider's event id",
    and the replan's R8 removed webhooks, which were what would supply one. So
    the key here is the enqueuer's idempotency key, of which a provider event
    id is one kind - see requirements.md D5. A scheduled run's key is its
    tenant and window, so the same window cannot be reconciled twice.

    The marker is inserted **in the same transaction** as the rows the job
    writes. That co-location is the whole reason the queue is in Postgres.
    """

    __tablename__ = "processed_event"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)

    #: The idempotency key. Opaque to this table: a provider event id, or a
    #: composed key like `recon:<tenant>:<start>:<end>`.
    event_id: Mapped[str] = mapped_column(String(256), nullable=False)

    #: Which job type claimed the key, for reading a row usefully without
    #: parsing `event_id`. Deliberately **not** part of the unique key: two
    #: job types sharing a key would be a collision to find, not to permit.
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)

    processed_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        state_check("job_type", JOB_TYPES, name="job_type"),
        # The constraint this table exists for. Scoped by tenant: two
        # marketplaces reconciling the same date window are two pieces of
        # work, and a global key would silently drop the second.
        UniqueConstraint("tenant_id", "event_id", name="event_id_per_tenant"),
        UniqueConstraint("tenant_id", "id"),
    )
