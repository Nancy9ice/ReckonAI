"""Matching, on hand-built records.

The matcher's whole job is to pair records that share no foreign key, so the
cases that matter are the ones where the pairing is wrong rather than absent: a
reference stripped into a different order, two payments claiming one order, two
refunds of equal value. Each has its own test, because each produces a
confident wrong answer rather than a failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from reckonai.money import Money
from reckonai.recon.matcher import (
    capture_reference_from_href,
    ledger_by_category,
    match_orders,
    match_refunds,
    order_ref_for_reference,
    order_references,
)
from reckonai.recon.vocabulary import EvidenceBasis


@dataclass
class FakeOrder:
    external_ref: str
    total: Money = field(default_factory=lambda: Money("100.00", "USD"))
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakePayment:
    external_id: str
    amount: Money = field(default_factory=lambda: Money("100.00", "USD"))
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakeRefund:
    external_id: str
    amount: Money
    capture_id: uuid.UUID | None = None
    capture_linkage_is_derived: bool = False
    seller_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakeEntry:
    entry_category: str
    amount: Money
    order_id: uuid.UUID | None
    external_ref: str = "LED-1"
    transaction_type: str = "credit"
    seller_id: uuid.UUID = field(default_factory=uuid.uuid4)
    id: uuid.UUID = field(default_factory=uuid.uuid4)


# ---------------------------------------------------------------------------
# Reading the link out of the payload
# ---------------------------------------------------------------------------


def test_references_are_read_from_the_retained_payload() -> None:
    """The real shape, as `reckonai.paypal.capture` writes it."""
    body = {
        "id": "0Y157906SJ4811901",
        "purchase_units": [
            {"reference_id": "ORD-0001-1"},
            {"reference_id": "ORD-0001-2"},
        ],
    }

    assert order_references(body) == ("ORD-0001-1", "ORD-0001-2")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"purchase_units": []},
        {"purchase_units": "not a list"},
        {"purchase_units": [{}]},
        {"purchase_units": [{"reference_id": ""}]},
        {"purchase_units": [{"reference_id": 7}]},
        {"purchase_units": ["not a dict"]},
    ],
)
def test_a_payload_without_references_yields_none(body: dict[str, object]) -> None:
    """A retained payload is evidence, not a validated schema.

    Phase 0 saw three fields PayPal does not declare, two of which stopped
    being returned a day later. A parser that raised on a shape it did not
    expect would turn that into a failed reconciliation run.
    """
    assert order_references(body) == ()


def test_a_suffixed_reference_resolves_to_its_order() -> None:
    assert order_ref_for_reference("ORD-0001-1", frozenset({"ORD-0001"})) == "ORD-0001"


def test_an_unsuffixed_reference_resolves_to_itself() -> None:
    assert order_ref_for_reference("ORD-0001", frozenset({"ORD-0001"})) == "ORD-0001"


def test_an_order_whose_own_ref_looks_suffixed_wins() -> None:
    """The case that makes membership-first the right rule.

    A marketplace ref is free-form text, so an order genuinely called
    `ORD-0009-1` can exist. Stripping blindly would file its records against
    `ORD-0009` - a different order, with a different seller and a different
    expected position - and every amount would look wrong on both.
    """
    known = frozenset({"ORD-0009", "ORD-0009-1"})

    assert order_ref_for_reference("ORD-0009-1", known) == "ORD-0009-1"


def test_an_unresolvable_reference_returns_none_rather_than_a_guess() -> None:
    assert order_ref_for_reference("ORD-9999-1", frozenset({"ORD-0001"})) is None
    assert order_ref_for_reference("ORD-0001-x", frozenset({"ORD-0001"})) is None
    assert order_ref_for_reference("-1", frozenset({"ORD-0001"})) is None


# ---------------------------------------------------------------------------
# Orders to payments
# ---------------------------------------------------------------------------


def test_a_two_unit_order_matches_one_payment_once() -> None:
    """Both purchase units name the same order; the match is not duplicated."""
    order = FakeOrder("ORD-0001")
    payment = FakePayment("0Y157906SJ4811901")

    result = match_orders(
        (order,), (payment,), {payment.id: ("ORD-0001-1", "ORD-0001-2")}
    )

    assert len(result.matched) == 1
    match = result.matched[0]
    assert match.order_ref == "ORD-0001"
    assert match.order_id == order.id
    assert match.payment_id == payment.id
    assert match.basis is EvidenceBasis.PROVIDER_REPORTED
    assert result.marketplace_only == ()
    assert result.provider_only == ()
    assert result.contested == ()


def test_a_marketplace_order_with_no_payment_is_reported_not_judged() -> None:
    """specs/mission.md P4: unmatched is not the same as wrong."""
    result = match_orders((FakeOrder("ORD-0017"),), (), {})

    assert result.matched == ()
    assert result.marketplace_only == ("ORD-0017",)


def test_a_payment_with_no_resolvable_reference_is_provider_only() -> None:
    payment = FakePayment("PAY-UNKNOWN")

    result = match_orders((FakeOrder("ORD-0001"),), (payment,), {payment.id: ()})

    assert result.provider_only == ("PAY-UNKNOWN",)
    assert result.marketplace_only == ("ORD-0001",)


def test_a_payment_whose_references_were_not_supplied_is_provider_only() -> None:
    """Absent evidence for the link is not a link.

    The caller reads the payload; if it supplied none, saying so beats pairing
    on the one order that happens to be left over.
    """
    payment = FakePayment("PAY-NO-PAYLOAD")

    result = match_orders((FakeOrder("ORD-0001"),), (payment,), {})

    assert result.provider_only == ("PAY-NO-PAYLOAD",)


def test_two_payments_claiming_one_order_are_contested_not_resolved() -> None:
    """Picking one would discard evidence of a real problem.

    A duplicate capture of one order is exactly the kind of fault this product
    exists to find, so collapsing it into a single arbitrary match would hide
    it behind a clean-looking reconciliation.
    """
    order = FakeOrder("ORD-0001")
    first, second = FakePayment("PAY-A"), FakePayment("PAY-B")

    result = match_orders(
        (order,),
        (first, second),
        {first.id: ("ORD-0001-1",), second.id: ("ORD-0001-1",)},
    )

    assert result.contested == ("ORD-0001",)
    assert result.matched == ()
    # Contested, not also unmatched: the order was claimed, ambiguously.
    assert result.marketplace_only == ()


def test_one_payment_may_carry_two_orders() -> None:
    """Not in our dataset, and not refused on that basis.

    PayPal permits purchase units from several marketplace orders in one
    order, and treating that as contested would report a healthy structure as
    a problem.
    """
    first, second = FakeOrder("ORD-0001"), FakeOrder("ORD-0002")
    payment = FakePayment("PAY-A")

    result = match_orders(
        (first, second), (payment,), {payment.id: ("ORD-0001-1", "ORD-0002-1")}
    )

    assert {m.order_ref for m in result.matched} == {"ORD-0001", "ORD-0002"}
    assert result.contested == ()


def test_matches_are_ordered_deterministically() -> None:
    """specs/mission.md P7: a historical run must be re-derivable.

    Dict iteration order follows insertion, which follows the caller's query
    order. Sorting here means the same records produce the same output
    regardless of how they were fetched.
    """
    orders = tuple(FakeOrder(ref) for ref in ("ORD-0003", "ORD-0001", "ORD-0002"))
    payments = tuple(FakePayment(f"PAY-{i}") for i in range(3))
    references = {
        payment.id: (f"{order.external_ref}-1",)
        for order, payment in zip(orders, payments, strict=True)
    }

    result = match_orders(orders, payments, references)

    assert [m.order_ref for m in result.matched] == [
        "ORD-0001",
        "ORD-0002",
        "ORD-0003",
    ]


# ---------------------------------------------------------------------------
# Refunds across sources
# ---------------------------------------------------------------------------


def test_refunds_pair_on_amount_and_say_that_is_what_they_rest_on() -> None:
    marketplace = FakeRefund("RFND-0013-A", Money("30.00", "USD"))
    provider = FakeRefund("5XY99", Money("30.00", "USD"))

    matches, mp_only, pv_only = match_refunds((marketplace,), (provider,))

    assert len(matches) == 1
    assert matches[0].basis is EvidenceBasis.AMOUNT_WITHIN_ORDER
    assert matches[0].ambiguous is False
    assert matches[0].marketplace_external_id == "RFND-0013-A"
    assert matches[0].provider_external_id == "5XY99"
    assert (mp_only, pv_only) == ((), ())


def test_a_marketplace_refund_with_no_provider_record_is_returned_unpaired() -> None:
    """F5's shape. The record is late, not absent - R10, and the classifier's
    call to make, not this module's."""
    marketplace = FakeRefund("RFND-0017-A", Money("15.00", "USD"))

    matches, mp_only, pv_only = match_refunds((marketplace,), ())

    assert matches == ()
    assert [r.external_id for r in mp_only] == ["RFND-0017-A"]
    assert pv_only == ()


def test_a_provider_refund_with_no_marketplace_record_is_returned_unpaired() -> None:
    provider = FakeRefund("5XY99", Money("30.00", "USD"))

    matches, mp_only, pv_only = match_refunds((), (provider,))

    assert matches == ()
    assert mp_only == ()
    assert [r.external_id for r in pv_only] == ["5XY99"]


def test_refunds_of_different_amounts_do_not_pair() -> None:
    matches, mp_only, pv_only = match_refunds(
        (FakeRefund("RFND-A", Money("30.00", "USD")),),
        (FakeRefund("5XY99", Money("20.00", "USD")),),
    )

    assert matches == ()
    assert len(mp_only) == 1
    assert len(pv_only) == 1


def test_the_same_number_in_another_currency_does_not_pair() -> None:
    """specs/mission.md P2, in the matcher.

    30.00 EUR and 30.00 USD are not the same refund, and pairing them would
    make a currency error look like a clean match.
    """
    matches, mp_only, pv_only = match_refunds(
        (FakeRefund("RFND-A", Money("30.00", "EUR")),),
        (FakeRefund("5XY99", Money("30.00", "USD")),),
    )

    assert matches == ()
    assert len(mp_only) == 1 and len(pv_only) == 1


def test_equal_amounts_quantized_differently_still_pair() -> None:
    """`20.0` and `20.00` are one amount, and the key must agree."""
    matches, _, _ = match_refunds(
        (FakeRefund("RFND-A", Money("20.0", "USD")),),
        (FakeRefund("5XY99", Money("20.00", "USD")),),
    )

    assert len(matches) == 1


def test_two_refunds_of_equal_value_pair_but_are_flagged_ambiguous() -> None:
    """The count is right; no individual pairing is trustworthy.

    Two 10.00 refunds on one order are genuinely indistinguishable without a
    shared identifier. Reporting a confident pairing would let the agent cite
    the wrong refund id as evidence, which Block E's exit criterion forbids.
    """
    matches, mp_only, pv_only = match_refunds(
        (
            FakeRefund("RFND-A", Money("10.00", "USD")),
            FakeRefund("RFND-B", Money("10.00", "USD")),
        ),
        (
            FakeRefund("5XY01", Money("10.00", "USD")),
            FakeRefund("5XY02", Money("10.00", "USD")),
        ),
    )

    assert len(matches) == 2
    assert all(match.ambiguous for match in matches)
    assert (mp_only, pv_only) == ((), ())


def test_a_provider_record_is_never_paired_twice() -> None:
    """Two marketplace refunds, one provider record.

    The surplus marketplace refund must come back unpaired rather than reuse
    the one provider record - double-counting it would make a missing record
    look present.
    """
    matches, mp_only, pv_only = match_refunds(
        (
            FakeRefund("RFND-A", Money("10.00", "USD")),
            FakeRefund("RFND-B", Money("10.00", "USD")),
        ),
        (FakeRefund("5XY01", Money("10.00", "USD")),),
    )

    assert len(matches) == 1
    assert matches[0].ambiguous is True
    assert [r.external_id for r in mp_only] == ["RFND-B"]
    assert pv_only == ()


def test_refund_pairing_is_deterministic() -> None:
    """Same inputs, same pairing, whatever order they arrive in (P7)."""
    first = FakeRefund("RFND-A", Money("10.00", "USD"))
    second = FakeRefund("RFND-B", Money("20.00", "USD"))
    left = FakeRefund("5XY01", Money("10.00", "USD"))
    right = FakeRefund("5XY02", Money("20.00", "USD"))

    forwards, _, _ = match_refunds((first, second), (left, right))
    backwards, _, _ = match_refunds((second, first), (right, left))

    assert [m.marketplace_external_id for m in forwards] == [
        m.marketplace_external_id for m in backwards
    ]
    assert [m.provider_external_id for m in forwards] == [
        m.provider_external_id for m in backwards
    ]


# ---------------------------------------------------------------------------
# The ledger index
# ---------------------------------------------------------------------------


def test_ledger_entries_group_by_order_and_category() -> None:
    order_id = uuid.uuid4()
    sale = FakeEntry("sale", Money("90.00", "USD"), order_id, external_ref="LED-1")
    commission = FakeEntry(
        "commission", Money("9.00", "USD"), order_id, external_ref="LED-2"
    )

    grouped = ledger_by_category((sale, commission))

    assert grouped[(order_id, "sale")] == (sale,)
    assert grouped[(order_id, "commission")] == (commission,)
    assert (order_id, "refund") not in grouped


def test_a_duplicated_category_keeps_both_entries() -> None:
    """F4's shape: one sale credited twice under distinct references.

    Collapsing them would erase the fault being looked for.
    """
    order_id = uuid.uuid4()
    first = FakeEntry("sale", Money("90.00", "USD"), order_id, external_ref="LED-a")
    duplicate = FakeEntry(
        "sale", Money("90.00", "USD"), order_id, external_ref="LED-a-dup"
    )

    grouped = ledger_by_category((first, duplicate))

    assert len(grouped[(order_id, "sale")]) == 2


def test_an_entry_with_no_order_belongs_to_no_order() -> None:
    """A manual adjustment or a periodic fee has no order behind it."""
    grouped = ledger_by_category((FakeEntry("adjustment", Money("5.00", "USD"), None),))

    assert grouped == {}


def test_the_index_is_ordered_deterministically() -> None:
    order_id = uuid.uuid4()
    entries = tuple(
        FakeEntry("sale", Money("1.00", "USD"), order_id, external_ref=ref)
        for ref in ("LED-c", "LED-a", "LED-b")
    )

    grouped = ledger_by_category(entries)

    assert [e.external_ref for e in grouped[(order_id, "sale")]] == [
        "LED-a",
        "LED-b",
        "LED-c",
    ]


# ---------------------------------------------------------------------------
# The derived refund-to-capture linkage
# ---------------------------------------------------------------------------


def test_the_capture_id_is_parsed_out_of_the_up_link() -> None:
    """The real href, as `reckonai.paypal.replay` retains it."""
    href = "https://api.sandbox.paypal.com/v2/payments/captures/8F044077K7411563J"

    assert capture_reference_from_href(href) == "8F044077K7411563J"


def test_a_refund_self_link_is_not_mistaken_for_a_capture() -> None:
    """The two hrefs differ by one path segment.

    A parser that took the last segment unconditionally would resolve a
    refund's `self` link to a capture id that happens to be the refund's own,
    match nothing, and silently drop the refund from its order.
    """
    href = "https://api.sandbox.paypal.com/v2/payments/refunds/7DE24497M24248828"

    assert capture_reference_from_href(href) is None


@pytest.mark.parametrize(
    "href",
    [
        None,
        "",
        "https://api.sandbox.paypal.com/v2/payments/captures/",
        "https://api.sandbox.paypal.com/v2/checkout/orders/0Y157906SJ4811901",
        "not a url",
        "/v2/payments/authorizations/3RT12345",
    ],
)
def test_an_unusable_href_resolves_to_nothing(href: str | None) -> None:
    """`None` rather than a guess.

    A refund that resolves to no capture belongs to no order's
    reconciliation, which is the honest answer: inventing one would attach a
    real refund to the wrong seller's position.
    """
    assert capture_reference_from_href(href) is None


def test_query_strings_and_trailing_slashes_are_tolerated() -> None:
    """Phase 0 saw PayPal's responses change shape between reads, so the
    parser is forgiving about the parts that carry no meaning."""
    base = "https://api.sandbox.paypal.com/v2/payments/captures/8F044077K7411563J"

    assert capture_reference_from_href(base + "/") == "8F044077K7411563J"
    assert capture_reference_from_href(base + "?fields=all") == "8F044077K7411563J"
    assert capture_reference_from_href(base + "#frag") == "8F044077K7411563J"
