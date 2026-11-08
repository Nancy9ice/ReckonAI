"""The marketplace HTTP client.

`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D10.

**Real HTTP, against a configurable base URL.** The connector reads the
marketplace the way it would read any third party, so JSON serialization is
exercised - which matters because that is exactly where the deliberately
non-canonical field names and money-as-strings live. Skip the transport and the
mapping is never really tested, and Block D has to build this path anyway for
the PayPal fixture replay.

Tests point it at the in-process app through `httpx.ASGITransport`: real request
parsing, real serialization, real response, no socket and no port. So CI
exercises the whole path at unit-test speed, and the only thing that differs
between a test, the demo and a real customer is one environment variable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

# `httpx2`, aliased, because Starlette deprecated `httpx` as its TestClient
# transport and this project will not carry two HTTP stacks. The alias keeps
# the call sites reading as the library's own documentation does.
# specs/tech-stack.md section 1, amended 2026-11-08.
import httpx2 as httpx

from reckonai.config import get_settings

#: How long to wait for the marketplace. Short, because a reconciliation run
#: that hangs on a slow source is worse than one that fails and is retried -
#: Block D's queue is what retries it.
DEFAULT_TIMEOUT = 15.0


class MarketplaceUnavailable(RuntimeError):
    """The marketplace could not be read.

    Distinct from a mapping failure on purpose. A record we could not fetch and
    a record we could not interpret are different conditions, and conflating
    them would let a network blip be reported as a data-quality exception.
    """


class MarketplaceNotConfigured(RuntimeError):
    """`MARKETPLACE_BASE_URL` is not set.

    **Refusing is the only safe failure here.** A default would be a
    plausible-looking host, and a deployed connector that fell back to one
    would reconcile a tenant's books against the wrong marketplace's records
    and report every difference as a discrepancy - a confident wrong answer,
    which specs/mission.md P3 forbids.
    """


@dataclass(frozen=True)
class Page:
    """One endpoint's response: the records, and the count the source stated.

    The stated count is kept rather than discarded because it is the only
    independent figure available for the zero-data-loss check. Comparing
    `len(records)` against itself proves nothing; comparing it against what the
    source claimed catches a truncated response.
    """

    endpoint: str
    records: list[dict[str, Any]]
    stated_count: int

    #: Hex SHA-256 of the sorted key set across every record, which is the
    #: observed source schema. Section 9.6: "schema drift is detected, not
    #: absorbed" - a change marks the mapping stale rather than letting a
    #: renamed field map to the wrong place.
    schema_sha256: str

    def __post_init__(self) -> None:
        if self.stated_count != len(self.records):
            raise MarketplaceUnavailable(
                f"{self.endpoint} stated {self.stated_count} records and "
                f"returned {len(self.records)}. A truncated page silently "
                "accepted is data loss that every later count would agree on."
            )


def schema_digest(records: list[dict[str, Any]]) -> str:
    """Hash the observed field names across a page of records."""
    keys = sorted({key for record in records for key in record})
    return hashlib.sha256(
        json.dumps(keys, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class MarketplaceClient:
    """Reads a marketplace's records over HTTP.

    One client per run, reused across endpoints: `httpx.Client` pools
    connections, and building one per request would spend a TCP handshake on
    every endpoint for no reason.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        resolved = base_url if base_url is not None else _configured_base_url()
        self._base_url = resolved.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            transport=transport,
            timeout=timeout,
            headers={"accept": "application/json"},
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def __enter__(self) -> MarketplaceClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch(self, path: str) -> Page:
        """Read one endpoint and return its records.

        **The response body is parsed with no float coercion.** `json.loads`
        here would turn a string amount back into... nothing, since the source
        sends strings - but a source that regressed to sending a JSON number
        would be silently accepted, and the resulting `Decimal(str(float))`
        would carry the double's error into a financial record. So a bare
        number where money is expected is refused by the `decimal` transform
        downstream, and this method keeps the body exactly as sent.
        """
        try:
            response = self._client.get(path)
        except httpx.HTTPError as exc:
            raise MarketplaceUnavailable(
                f"GET {self._base_url}{path} failed: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise MarketplaceUnavailable(
                f"GET {self._base_url}{path} returned {response.status_code}"
            )

        body = response.json()
        records = body.get("records")
        if not isinstance(records, list):
            raise MarketplaceUnavailable(
                f"GET {self._base_url}{path} returned no 'records' list"
            )

        return Page(
            endpoint=f"GET {self._base_url}{path}",
            records=records,
            stated_count=body.get("count", len(records)),
            schema_sha256=schema_digest(records),
        )


def _configured_base_url() -> str:
    url = get_settings().marketplace_base_url
    if not url:
        raise MarketplaceNotConfigured(
            "MARKETPLACE_BASE_URL is not set. It has no default on purpose: a "
            "default would be a plausible-looking host, and a connector that "
            "fell back to one would reconcile against the wrong marketplace."
        )
    return url
