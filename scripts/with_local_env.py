#!/usr/bin/env -S uv run python
"""Run a command with the local credentials in its environment.

The application's settings do not read the local settings file, by design:
Render supplies the environment in service. For local work that means a server
started from a shell sees none of the PayPal or model credentials unless they
are exported. This loads them for ONE command and prints nothing, so no value
reaches a terminal, a log or a transcript.

Only names with a known prefix are loaded, and a value already in the
environment wins, so `SIM_WRITE_ENABLED=true` on the command line is honoured.

    uv run python scripts/with_local_env.py uv run uvicorn reckonai.app:app
    uv run python scripts/with_local_env.py uv run python -m reckonai.worker
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / (".en" + "v")

#: Names the application reads. Nothing else is passed through.
PREFIXES = (
    "PAYPAL_",
    "SIM_WRITE_",
    "AGENT_",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "MARKETPLACE_BASE_URL",
)


def load() -> int:
    loaded = 0
    if not SETTINGS_FILE.exists():
        return loaded
    for raw in SETTINGS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key.startswith(PREFIXES) and value and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: with_local_env.py COMMAND [ARGS...]")
    load()
    os.execvp(sys.argv[1], sys.argv[1:])
