"""Serving the built dashboard from the API's own origin.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D1. Vite builds
`frontend/dist`; this serves it, so the dashboard and `/api` share one URL, one
deploy and no CORS.

**Absence is a supported state.** CI and local API work run without a frontend
build, and the service index at `/` must keep answering then. The directory is
checked per request for `/` and once at start for the assets mount, so a
missing build is a JSON index and never a crash.
"""

from __future__ import annotations

import os
from pathlib import Path

#: `src/reckonai/spa.py` -> the repository root.
_DEFAULT = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def dist_dir() -> Path | None:
    """The build directory, or `None` if there is no build to serve."""
    path = Path(os.environ.get("FRONTEND_DIST", _DEFAULT))
    return path if (path / "index.html").is_file() else None
