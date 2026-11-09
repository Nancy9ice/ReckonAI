"""The replay ingester: captured PayPal responses into the canonical tables.

The replan's R6 and R16. **The four rules this module exists to hold are each
tested in the failing direction too**, because every one of them was found by
running the capture and would otherwise be discovered as a wrong number in a
reconciliation rather than as a test failure.
"""

from __future__ import annotations

import copy
import json
import pathlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.models import (
    Capture,
    Disbursement,
    FxConversion,
    Payment,
    PlatformFee,
    RawPayload,
    Refund,
    Seller,
)
from reckonai.money import Money
from reckonai.paypal import replay
from reckonai.sim import data, seed

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "paypal"


def _doc(ref: str) -> dict:
    return json.loads((FIXTURES / f"{ref}.json").read_text(encoding="utf-8"))


@pytest.fixture
def replayed(session: Session) -> replay.ReplayResult:
    """The marketplace seeded, then PayPal's side replayed over it."""
    built = seed.build(
        session, data.FULL, tenant_slug=f"rp-{uuid.uuid4().hex[:8]}",
        tenant_name="Replay",
    )
    session.flush()
    out = replay.ingest_directory(session, FIXTURES, tenant_id=built.tenant_id)
    session.flush()
    return out


def _paypal(statement, model):
    """Restrict to rows whose payload came from PayPal.

    **One canonical table per record type holds both sources**, told apart
    only by their payload's `source` (Phase 3's D3). The seed writes the
    marketplace's own refunds into the same `refund` table, so a query that
    does not filter counts five where PayPal supplied two - which is exactly
    what these tests caught on first run.
    """
    return statement.join(
        RawPayload,
        (RawPayload.id == model.raw_payload_id)
        & (RawPayload.tenant_id == model.tenant_id),
    ).where(RawPayload.source == "paypal")


def _count(session: Session, model: object, tenant_id: uuid.UUID) -> int:
    """PayPal-source rows of one model.

    `raw_payload` is the exception: it carries `source` itself rather than
    pointing at a row that does, so it filters directly.
    """
    statement = select(func.count()).select_from(model)
    if model is RawPayload:
        statement = statement.where(RawPayload.source == "paypal")
    else:
        statement = _paypal(statement, model)
    return session.scalar(statement.where(model.tenant_id == tenant_id))


# ---------------------------------------------------------------------------
# What lands
# ---------------------------------------------------------------------------


def test_every_captured_order_is_replayed(
    session: Session, replayed: replay.ReplayResult
) -> None:
    assert len(replayed.orders) == len(data.FULL.orders) == 18
    assert _count(session, Payment, replayed.tenant_id) >= 18


def test_the_counts_follow_from_the_fixtures(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """Derived from the fixtures rather than hard-coded.

    A hard-coded 21 would still pass if the ingester dropped a capture and a
    fixture were deleted together.
    """
    captures = fees = conversions = refunds = releases = 0
    for path in FIXTURES.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        releases += len(doc.get("releases") or [])
        for unit in doc["order"]["purchase_units"]:
            payments = unit.get("payments", {})
            for cap in payments.get("captures", []):
                captures += 1
                brk = cap.get("seller_receivable_breakdown") or {}
                fees += len(brk.get("platform_fees") or [])
                if (brk.get("exchange_rate") or {}).get("value"):
                    conversions += 1
            for ref in payments.get("refunds", []):
                refunds += 1
                brk = ref.get("seller_payable_breakdown") or {}
                fees += len(brk.get("platform_fees") or [])

    assert _count(session, Capture, replayed.tenant_id) == captures
    assert _count(session, Refund, replayed.tenant_id) == refunds
    assert _count(session, PlatformFee, replayed.tenant_id) == fees
    assert _count(session, FxConversion, replayed.tenant_id) == conversions
    assert _count(session, Disbursement, replayed.tenant_id) == releases


def test_replaying_twice_inserts_nothing(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """Idempotence, asserted on rows rather than on the result counters."""
    before = {
        m.__tablename__: _count(session, m, replayed.tenant_id)
        for m in (Payment, Capture, Refund, PlatformFee, FxConversion,
                  Disbursement, RawPayload)
    }
    again = replay.ingest_directory(
        session, FIXTURES, tenant_id=replayed.tenant_id
    )
    session.flush()
    after = {
        m.__tablename__: _count(session, m, replayed.tenant_id)
        for m in (Payment, Capture, Refund, PlatformFee, FxConversion,
                  Disbursement, RawPayload)
    }

    assert before == after
    assert again.total_inserted == 0, dict(again.inserted)


# ---------------------------------------------------------------------------
# Rule 1 - the platform fee comes from the capture-time snapshot
# ---------------------------------------------------------------------------


def test_the_platform_fee_is_taken_from_the_capture_time_snapshot(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """The trap this rule exists for, on the order that exhibits it.

    ORD-0013's capture reports `3.00` after its refund - its *reversal*
    amount - where at capture it was `11.00`. Reading the later value would
    understate what PayPal took by 8.00 and invent a discrepancy.
    """
    doc = _doc("ORD-0013")

    at_capture = doc["order_at_capture"]["purchase_units"][0]["payments"][
        "captures"
    ][0]["seller_receivable_breakdown"]["platform_fees"][0]["amount"]["value"]
    after = doc["order"]["purchase_units"][0]["payments"]["captures"][0][
        "seller_receivable_breakdown"
    ]["platform_fees"][0]["amount"]["value"]
    assert at_capture != after, (
        "this fixture no longer exhibits the trap, so this test proves "
        "nothing - find one that does or delete it"
    )

    fee = session.scalar(
        select(PlatformFee).where(
            PlatformFee.tenant_id == replayed.tenant_id,
            PlatformFee.external_id.like("pf-%-0"),
            PlatformFee.amount_amount == Decimal("11.00"),
        )
    )
    assert fee is not None
    assert fee.amount == Money(at_capture, "USD")


def test_reading_the_later_value_would_be_wrong(session: Session) -> None:
    """The failing direction: feed the ingester only the post-refund read.

    Without the snapshot it records 3.00 where PayPal took 11.00.
    """
    built = seed.build(
        session, data.CLEAN, tenant_slug=f"rp-{uuid.uuid4().hex[:8]}",
        tenant_name="Replay",
    )
    session.flush()

    doc = copy.deepcopy(_doc("ORD-0013"))
    doc.pop("order_at_capture")  # the ingester falls back to the readback
    replay.ingest_document(session, doc, tenant_id=built.tenant_id)
    session.flush()

    fee = session.scalar(
        select(PlatformFee).where(PlatformFee.tenant_id == built.tenant_id)
    )
    assert fee is not None
    assert fee.amount == Money("3.00", "USD"), (
        "without the snapshot the ingester records the reversal, which is "
        "the whole reason rule 1 exists"
    )


# ---------------------------------------------------------------------------
# Rule 2 - the payee comes from the readback
# ---------------------------------------------------------------------------


def test_every_capture_resolves_to_a_seller(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """Seller attribution is the join the whole reconciliation rests on.

    An unresolved payee leaves `seller_id` NULL, and every figure derived per
    seller silently loses that capture.
    """
    captures = list(
        session.scalars(
            select(Capture).where(Capture.tenant_id == replayed.tenant_id)
        )
    )
    assert captures
    unresolved = [c.external_id for c in captures if c.seller_id is None]
    assert not unresolved, f"captures with no seller: {unresolved}"


def test_the_payee_is_absent_from_the_capture_time_snapshot() -> None:
    """Why rule 2 is a rule and not an accident.

    The order creation response omits `purchase_units[].payee` entirely, so
    an ingester reading only the snapshot would resolve no seller at all.
    """
    doc = _doc("ORD-0008")
    assert "payee" not in doc["order_at_capture"]["purchase_units"][0]
    assert doc["order"]["purchase_units"][0]["payee"]["merchant_id"]


# ---------------------------------------------------------------------------
# Rule 3 - refunds through the order
# ---------------------------------------------------------------------------


def test_a_refund_carries_its_breakdown_and_its_fee_reversal(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """The refund endpoint returns a null breakdown; the order carries it.

    PayPal reverses the platform fee automatically and pro rata - 2.00 on a
    20.00 refund of a 70.00 sale with a 7.00 fee.
    """
    refund = session.scalar(
        _paypal(select(Refund), Refund).where(
            Refund.tenant_id == replayed.tenant_id,
            Refund.amount_amount == Decimal("20.00"),
        )
    )
    assert refund is not None
    assert refund.gross_amount == Money("20.00", "USD")
    assert refund.net_amount == Money("18.00", "USD")

    reversal = session.scalar(
        select(PlatformFee).where(
            PlatformFee.tenant_id == replayed.tenant_id,
            PlatformFee.refund_id == refund.id,
        )
    )
    assert reversal is not None
    assert reversal.amount == Money("2.00", "USD")


def test_a_refund_reports_a_measured_zero_fee_where_a_capture_reports_none(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """**Absent and zero are different claims, and both appear here.**

    PayPal states `paypal_fee: 0.00` on a refund and omits the field entirely
    on a capture. Storing NULL for the first or 0.00 for the second would
    conflate a measurement with a silence - the distinction
    `fx_conversion`'s `CHECK (fee <> 0)` exists to protect.
    """
    refund = session.scalar(
        _paypal(select(Refund), Refund).where(
            Refund.tenant_id == replayed.tenant_id
        )
    )
    assert refund is not None
    assert refund.paypal_fee == Money("0.00", "USD")

    for capture in session.scalars(
        _paypal(select(Capture), Capture).where(
            Capture.tenant_id == replayed.tenant_id
        )
    ):
        assert capture.paypal_fee is None, capture.external_id


def test_f5_has_no_replayed_refund(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """R10's case: the marketplace refunded, PayPal's record has not arrived.

    Asserted as a count, because the clean refunds must be present for the
    absence to be a distinguishable state rather than the universal one.
    """
    assert _count(session, Refund, replayed.tenant_id) == 2


# ---------------------------------------------------------------------------
# Rule 4 - a conversion only where a rate was reported
# ---------------------------------------------------------------------------


def test_a_conversion_exists_only_where_a_rate_was_reported(
    session: Session, replayed: replay.ReplayResult
) -> None:
    conversions = list(
        session.scalars(
            select(FxConversion).where(
                FxConversion.tenant_id == replayed.tenant_id
            )
        )
    )
    assert conversions

    for conversion in conversions:
        # P3: PayPal publishes no conversion fee, and the CHECK refuses a
        # zero. An absent fee and a measured fee of nothing differ.
        assert conversion.fee is None
        assert conversion.is_derived is False
        assert conversion.source.currency != conversion.target.currency
        assert conversion.rate > 0


def test_the_conversion_source_is_the_net_not_the_gross(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """Converting the gross is "wrong by the whole fee".

    Checked arithmetically against PayPal's own rate rather than by reading
    which field was used, so a future refactor that quietly switched to the
    gross would fail.
    """
    conversion = session.scalar(
        select(FxConversion).where(
            FxConversion.tenant_id == replayed.tenant_id,
            FxConversion.source_amount == Decimal("135.00"),
        )
    )
    assert conversion is not None, "ORD-0008/0018's 135.00 EUR net"

    implied = (conversion.source.amount * conversion.rate).quantize(
        Decimal("0.01")
    )
    assert implied == conversion.target.amount


def test_a_same_currency_capture_gets_no_conversion(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """A USD order to a USD seller converts nothing, and must carry no row.

    An empty conversion row could be mistaken for one whose fields went
    missing, which is why it is never created.
    """
    usd_only = [o.ref for o in data.FULL.orders if o.currency == "USD"]
    assert usd_only

    for conversion in session.scalars(
        select(FxConversion).where(
            FxConversion.tenant_id == replayed.tenant_id
        )
    ):
        assert conversion.source.currency == "EUR"


# ---------------------------------------------------------------------------
# Disbursement
# ---------------------------------------------------------------------------


def test_the_release_record_carries_no_timestamp(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """P3: the record has a `time_updated`, but what it means is unestablished.

    Writing it into `released_at` would present our reading of an ambiguous
    field as a provider timestamp.
    """
    release = session.scalar(
        select(Disbursement).where(
            Disbursement.tenant_id == replayed.tenant_id
        )
    )
    assert release is not None
    assert release.released_at is None
    assert release.state == "disbursement_completed"
    assert release.amount == Money("147.08", "USD")


def test_held_and_released_captures_carry_different_states(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """`status` reads COMPLETED while the seller is unpaid, so the state
    column is what answers it.

    ORD-0005 is held and never released; ORD-0018 was released.
    """
    states = {
        c.external_id: (c.status, c.disbursement_state)
        for c in session.scalars(
            select(Capture).where(
                Capture.tenant_id == replayed.tenant_id,
                Capture.disbursement_mode == "DELAYED",
            )
        )
    }
    assert len(states) == 2, states

    found = sorted(state for _, state in states.values())
    assert found == ["disbursement_completed", "funds_held"]
    assert all(status == "COMPLETED" for status, _ in states.values())


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_every_replayed_row_links_to_a_paypal_payload(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """P7, and that the payload records the read path it came from."""
    payloads = {
        row.id: row
        for row in session.scalars(
            select(RawPayload).where(
                RawPayload.tenant_id == replayed.tenant_id,
                RawPayload.source == "paypal",
            )
        )
    }
    assert payloads

    for row in payloads.values():
        assert row.endpoint == replay.PAYPAL_ENDPOINT
        # No synthetic marker survives: R16 retired the invented side.
        assert "_synthetic" not in row.body

    for model in (Capture, Refund, PlatformFee, FxConversion, Disbursement):
        rows = list(
            session.scalars(
                _paypal(select(model), model).where(
                    model.tenant_id == replayed.tenant_id
                )
            )
        )
        assert rows, model.__tablename__
        for row in rows:
            assert row.raw_payload_id in payloads, model.__tablename__


def test_replaying_an_empty_directory_refuses(
    session: Session, tmp_path: pathlib.Path
) -> None:
    """Replaying nothing would leave every marketplace record unmatched,
    which reads as eighteen findings rather than as a missing step."""
    with pytest.raises(replay.ReplayError, match="no fixtures"):
        replay.ingest_directory(session, tmp_path, tenant_id=uuid.uuid4())


def test_a_marketplace_seller_is_reachable_from_a_payee(
    session: Session, replayed: replay.ReplayResult
) -> None:
    """The dataset's sellers must map onto the captured payees, or every
    order is attributed to nobody."""
    merchants = {
        row.paypal_merchant_id
        for row in session.scalars(
            select(Seller).where(Seller.tenant_id == replayed.tenant_id)
        )
    }
    assert merchants == {s.merchant_id for s in data.SELLERS}


def test_paypals_stated_conversion_currencies_match_the_amounts_it_converted(
) -> None:
    """The one assumption the ingester makes without checking.

    `fx_conversion.source` and `.target` are built from `net_amount` and
    `receivable_amount`, so their currencies are **derived** from the amounts
    rather than read from `exchange_rate.source_currency` and
    `.target_currency`, which PayPal also states and which the ingester never
    reads.

    They agree on every captured conversion. If they ever did not, the row
    would carry a real money figure under the wrong currency - and nothing
    downstream could tell, because both sides would look internally
    consistent. specs/mission.md P2: an amount is meaningless without its
    currency, and a *wrong* currency is worse than a missing one.

    This is the useful half of Phase 7's "raw payloads are diffed against the
    model" criterion, reduced to the one case where a mismatch would change a
    financial conclusion. The full diff watches for live schema drift, which
    frozen fixtures cannot have.
    """
    checked = 0
    for path in sorted(FIXTURES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for unit in doc["order"]["purchase_units"]:
            for cap in unit.get("payments", {}).get("captures", []):
                breakdown = cap.get("seller_receivable_breakdown") or {}
                rate = breakdown.get("exchange_rate")
                if not rate:
                    continue
                checked += 1
                assert rate["source_currency"] == (
                    breakdown["net_amount"]["currency_code"]
                ), f"{path.stem}: PayPal converted from a currency it did not state"
                assert rate["target_currency"] == (
                    breakdown["receivable_amount"]["currency_code"]
                ), f"{path.stem}: PayPal converted to a currency it did not state"

    assert checked == 6, (
        f"{checked} conversions checked; the dataset has 6, so this is no "
        "longer covering what it claims to"
    )


def test_no_money_bearing_field_in_the_fixtures_is_silently_ignored() -> None:
    """A field PayPal sends that nothing reads is a figure nobody sees.

    Deliberately a **named allow-list** rather than a count: a new
    money-shaped field appearing in a re-captured fixture should fail here
    and be looked at, not absorbed by a threshold.
    """
    import re

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "reckonai" / "paypal" / "replay.py"
    ).read_text()
    consumed = set(re.findall(r"""(?:get|\[)\(?["']([a-z_]+)["']""", source))

    def leaves(node, out):
        if isinstance(node, dict):
            for key, value in node.items():
                out.add(key)
                leaves(value, out)
        elif isinstance(node, list):
            for item in node:
                leaves(item, out)

    found: set[str] = set()
    for path in FIXTURES.glob("*.json"):
        leaves(json.loads(path.read_text(encoding="utf-8")), found)

    moneyish = re.compile(
        r"amount|value|fee|total|gross|net|rate|currency|receivable|payable"
    )
    ignored = {k for k in found if moneyish.search(k)} - consumed

    # Each of these is accounted for, by name:
    #   network, network_transaction_reference - card routing metadata,
    #     carrying no financial value
    #   source_currency, target_currency - stated by PayPal inside
    #     `exchange_rate`, and derived by the ingester from the amounts
    #     instead. The test above asserts the two agree.
    assert ignored == {
        "network",
        "network_transaction_reference",
        "source_currency",
        "target_currency",
    }, f"unaccounted money-bearing fields: {sorted(ignored)}"
