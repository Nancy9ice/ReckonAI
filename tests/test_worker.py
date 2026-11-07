"""The worker's shutdown behaviour.

The loop itself is verified by running the process (plan.md Group 5), and on
Render by the log stream (validation.md M4). What is worth unit-testing is the
part that is easy to get wrong and invisible when it is: that the wait between
ticks is interrupted by a signal rather than slept through.
"""

from __future__ import annotations

import signal
import time

from reckonai.worker import ShutdownSignal


def test_wait_returns_immediately_once_shutdown_is_requested() -> None:
    shutdown = ShutdownSignal()
    shutdown._handle(signal.SIGTERM, None)

    assert shutdown.requested

    started = time.monotonic()
    shutdown.wait(30.0)
    elapsed = time.monotonic() - started

    # The point of using an Event rather than time.sleep: Render kills the
    # process after maxShutdownDelaySeconds, so sleeping through a SIGTERM risks
    # being killed mid-shutdown instead of exiting cleanly.
    assert elapsed < 1.0, "a requested shutdown must not wait out the tick interval"


def test_wait_blocks_while_no_shutdown_is_requested() -> None:
    shutdown = ShutdownSignal()

    assert not shutdown.requested

    started = time.monotonic()
    shutdown.wait(0.2)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.15, "the tick interval must actually be waited out"


def test_a_signal_handler_sets_the_flag() -> None:
    for signum in (signal.SIGTERM, signal.SIGINT):
        shutdown = ShutdownSignal()
        assert not shutdown.requested

        shutdown._handle(signum, None)

        assert shutdown.requested
