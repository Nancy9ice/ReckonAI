"""The background worker process.

**It claims and executes jobs** - Block D gave it the `job` table Phase 1 left
it waiting for (`specs/2026-11-09-queue-scheduled-run/requirements.md` D7).
Phase 1's loop logged a tick and slept; this one claims with `FOR UPDATE SKIP
LOCKED`, dispatches through the handler registry, and sleeps only when the
queue is empty.

It is a separate process, not a thread inside the web service. That is
specs/tech-stack.md sections 4 and 11, and it is why the Render plan needed
Background Workers at all.

Run it with:

    uv run python -m reckonai.worker
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from reckonai import __version__
from reckonai.config import get_settings
from reckonai.db import check_connection, create_app_engine
from reckonai.jobs import handlers, queue
from reckonai.logging import configure_logging, event

# This component's share of the connection budget; the cap it is drawn against
# is `reckonai.db.USABLE_CONNECTIONS`. See requirements.md D6.
WORKER_POOL_SIZE = 2
WORKER_MAX_OVERFLOW = 2

#: How many jobs one tick claims. One, deliberately: a batch claim holds its
#: rows for the length of the whole batch, so a slow job delays every other
#: job claimed with it. Two workers each taking one job at a time is the
#: shape `SKIP LOCKED` is good at.
CLAIM_LIMIT = 1

logger = logging.getLogger("reckonai.worker")


class ShutdownSignal:
    """A signal-safe, interruptible wait.

    The worker must not sleep through a `SIGTERM`. Render sends one and then
    kills the process after `maxShutdownDelaySeconds`, so a plain
    `time.sleep(30)` between ticks would delay shutdown by up to a full tick and
    could have the process killed mid-handler instead of exiting cleanly. An
    `Event` is interrupted the moment the handler sets it.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def install(self) -> None:
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        logger.info(event("reckonai.worker.signal", signal=signal.Signals(signum).name))
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> None:
        """Wait, returning early if shutdown has been requested."""
        self._event.wait(seconds)


def drain(engine: Engine) -> bool:
    """Claim and run what is due, returning whether anything was found.

    **The claim is committed before the job runs.** The commit is what
    releases the row lock `SKIP LOCKED` took and what makes the attempt
    increment durable; `handlers.dispatch` refuses a job whose claim is not
    committed, and its docstring has the reason.

    One session per claim rather than one for the loop's lifetime: a session
    held across ticks accumulates identity-map state and keeps a connection
    out of a pool sized at two.
    """
    with Session(engine) as session:
        # Stale claims first: a job whose worker died on its final attempt is
        # below `claim`'s attempt cap and would otherwise sit `claimed`
        # forever, invisible to every worker.
        reaped = queue.reap_stale_claims(session)
        if reaped:
            session.commit()
            for job in reaped:
                logger.warning(
                    event(
                        "reckonai.jobs.claim_expired",
                        job_id=str(job.id),
                        job_type=job.job_type,
                        attempts=job.attempts,
                    )
                )

        claimed = queue.claim(session, limit=CLAIM_LIMIT)
        if not claimed:
            return bool(reaped)
        session.commit()

        for job in claimed:
            logger.info(
                event(
                    "reckonai.jobs.claimed",
                    job_id=str(job.id),
                    job_type=job.job_type,
                    attempts=job.attempts,
                )
            )
            handlers.dispatch(session, job)

    return True


def main() -> int:
    """Run the worker loop. Returns the process exit code."""
    settings = get_settings()
    configure_logging(settings.log_level)

    shutdown = ShutdownSignal()
    shutdown.install()

    engine = create_app_engine(
        settings.sqlalchemy_url,
        pool_size=WORKER_POOL_SIZE,
        max_overflow=WORKER_MAX_OVERFLOW,
    )

    logger.info(
        event(
            "reckonai.worker.start",
            environment=settings.environment,
            version=__version__,
            pool_size=WORKER_POOL_SIZE,
            max_overflow=WORKER_MAX_OVERFLOW,
            tick_seconds=settings.worker_tick_seconds,
        )
    )

    # Checked once at startup rather than per tick: a worker that stays up while
    # unable to reach Postgres is a false green, and ticking a connection check
    # forever would be load without a purpose. It logs and keeps running rather
    # than exiting, because a database that is briefly slow to accept
    # connections should not crash-loop the process.
    #
    # Block D kept it rather than replacing it, which Phase 1 expected it to
    # do. A claim that cannot reach the database now raises inside `drain`,
    # which the loop below catches and exits non-zero on, so Render restarts
    # the process - and that is the retry. A second mechanism here would only
    # decide how long to hide the same failure.
    try:
        check_connection(engine)
        logger.info(event("reckonai.worker.database_ready"))
    except Exception as exc:
        logger.warning(
            event("reckonai.worker.database_unreachable", error=type(exc).__name__)
        )

    ticks = 0
    exit_code = 0
    try:
        while not shutdown.requested:
            ticks += 1
            worked = drain(engine)

            # **Sleep only when the queue was empty.** A tick that found work
            # goes straight back for more, so a backlog is not drained at one
            # job per `WORKER_TICK_SECONDS` - which at the default of 30 would
            # make a scheduled run's queue take minutes to clear for no
            # reason.
            if not worked:
                logger.info(event("reckonai.worker.idle", tick=ticks))
                shutdown.wait(settings.worker_tick_seconds)
    except Exception:
        # The traceback must reach the log before the process dies, and the exit
        # code must be non-zero so Render restarts it. A worker that exits 0 on
        # an unhandled error looks healthy and is not.
        logger.exception(event("reckonai.worker.failed", tick=ticks))
        exit_code = 1
    finally:
        engine.dispose()
        logger.info(event("reckonai.worker.stop", ticks=ticks, exit_code=exit_code))

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
