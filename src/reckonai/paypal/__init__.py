"""PayPal's side of the canonical model: captured fixtures, replayed.

The replan's R6 and R16. The reconciliation run never calls PayPal: records
are replayed from responses captured against the sandbox by
`scripts/capture_paypal.py`, through the same normalization a live retrieval
would use.

`replay.ingest_document` takes one captured order and writes it. It is the
unit deliberately - the demo's opening live call produces exactly that shape,
so a freshly created order can be ingested on the spot, and the seeded
fixtures are the fallback when it fails.
"""

from __future__ import annotations

from reckonai.paypal.replay import (
    PAYPAL_ENDPOINT,
    ReplayResult,
    ingest_directory,
    ingest_document,
)

__all__ = [
    "PAYPAL_ENDPOINT",
    "ReplayResult",
    "ingest_directory",
    "ingest_document",
]
