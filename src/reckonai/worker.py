"""The background worker process.

Phase 1 scope is deliberately narrow: it starts, logs, and stays up. It claims
no work, because the `jobs` table does not exist until Phase 8 and designing
that table here would be the mistake Phase 0 exists to prevent, one layer down
(specs/2026-11-07-repo-deployment-skeleton requirements.md section 4).

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

from reckonai import __version__
from reckonai.config import get_settings
from reckonai.db import check_connection, create_app_engine
from reckonai.logging import configure_logging, event

# This component's share of the connection budget; the cap it is drawn against
# is `reckonai.db.USABLE_CONNECTIONS`. See requirements.md D6.
WORKER_POOL_SIZE = 2
WORKER_MAX_OVERFLOW = 2

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
    # connections should not crash-loop the process. Phase 8 replaces this with
    # real retry and backoff around an actual job claim.
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
            logger.info(event("reckonai.worker.tick", tick=ticks))
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
