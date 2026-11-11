"""`capture_live_order` against a stub PayPal: retries and idempotency.

The sandbox is slow at times; a read timing out after an order is created must
not lose the order or let a retry create a second one for the same reference.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx2 as httpx
import pytest

from reckonai.config import get_settings
from reckonai.paypal import capture

ORDER = {
    "id": "PPORD1",
    "status": "COMPLETED",
    "purchase_units": [
        {
            "payee": {"merchant_id": "M1"},
            "payments": {"captures": [{"id": "CAP1"}]},
        }
    ],
}


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[httpx.Request]]:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr(capture.time, "sleep", lambda _s: None)

    def install(read_failures: int = 0) -> list[httpx.Request]:
        seen: list[httpx.Request] = []
        failures = {"left": read_failures}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/oauth2/token"):
                return httpx.Response(200, json={"access_token": "t"})
            if request.method == "POST":
                return httpx.Response(201, json=ORDER)
            if failures["left"] > 0:
                failures["left"] -= 1
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json=ORDER)

        real = httpx.Client

        def factory(**kwargs: Any) -> httpx.Client:
            return real(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(capture.httpx, "Client", factory)
        return seen

    yield install
    get_settings.cache_clear()


def _request_ids(seen: list[httpx.Request]) -> list[str]:
    return [
        r.headers["PayPal-Request-Id"]
        for r in seen
        if r.method == "POST" and "checkout/orders" in r.url.path
    ]


def test_a_slow_readback_is_retried_and_the_order_is_kept(stub: Any) -> None:
    seen = stub(read_failures=2)
    order = capture.capture_live_order(payees=[("M1", "10.00", "1.00")], order_ref="R1")
    assert order.order_id == "PPORD1" and order.captures == ("CAP1",)
    assert len([r for r in seen if r.method == "GET"]) == 3


def test_a_readback_that_never_answers_names_the_order(stub: Any) -> None:
    stub(read_failures=3)
    with pytest.raises(capture.CaptureUnavailable, match="PPORD1"):
        capture.capture_live_order(payees=[("M1", "10.00", "1.00")], order_ref="R1")


def test_a_named_order_reuses_its_request_id_so_a_retry_cannot_duplicate(
    stub: Any,
) -> None:
    seen = stub()
    for _ in range(2):
        capture.capture_live_order(payees=[("M1", "10.00", "1.00")], order_ref="R1")
    first, second = _request_ids(seen)
    assert first == second == "reckonai-R1"


def test_an_unnamed_order_still_gets_a_fresh_request_id(stub: Any) -> None:
    seen = stub()
    for _ in range(2):
        capture.capture_live_order(payees=[("M1", "10.00", "1.00")])
    first, second = _request_ids(seen)
    assert first != second
