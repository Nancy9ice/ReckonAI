"""The simulated marketplace's dataset, as literals.

`specs/2026-11-08-block-a-marketplace-connector/requirements.md` D5 and D5a,
re-pointed at captured PayPal data under the replan's R16.

**This module is the marketplace's own books, and nothing else.** Orders,
sellers, commission rules, the seller ledger. It states no capture, no
platform fee PayPal took, no exchange rate and no settlement - those are
PayPal's, they live in `fixtures/paypal/`, and `reckonai.paypal.replay` loads
them. An earlier version invented all of it (Block A's D12); R16 retired that
rather than scheduling its removal, because a fabricated provider figure that
has to be remembered about is worse than one that never exists
(specs/mission.md P3).

**Every expected figure here is still hand-written, not computed.** That is
D5a and it is why `seed.verify()` is a real check: the builder writes rows
from these literals and `verify()` recomputes them from the items and the
commission rules. Two independent derivations that have to agree. So **if you
change a unit price, change its expected figures too** - `verify()` will tell
you which one you missed, and that is the feature.

**Two sellers, not four.** The dataset's sellers map one-to-one onto real
onboarded PayPal accounts, because the connector resolves `seller_id` from
`payee.merchant_id` - a dataset seller sharing an account with another would
have every one of its orders attributed to the wrong seller. Only two accounts
can process cards (the German account's `PPCP_CUSTOM` vetting is
`NEED_MORE_DATA`), so `vendor-citrine` and `vendor-dunlin` are gone and their
orders moved. Dunlin's case - a seller never granted `PARTNER_FEE`, whose
missing platform fee must not be flagged - survives as an engine unit test
rather than as seeded data.

**Both sellers settle USD, so EUR orders convert.** That is what carries
specs/mission.md section 6 scenario 3. The conversion direction is inverted
from the original plan for the vetting reason above; a US marketplace taking
EUR orders is at least as realistic as the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

TENANT_SLUG = "reckonmarket"
TENANT_NAME = "ReckonMarket"

#: The tenant A3's connector ingests into, kept apart from the one the seed
#: writes so a row-count comparison is meaningful rather than circular.
INGEST_TENANT_SLUG = "reckonmarket-ingested"
INGEST_TENANT_NAME = "ReckonMarket (ingested)"


def _at(day: int, hour: int = 12) -> datetime:
    """A fixed timestamp, so the dataset is reproducible.

    Timezone-aware always: `Base.type_annotation_map` maps `datetime` to
    `TIMESTAMPTZ`, and a naive value in a system reconciling across PayPal's
    clock and a marketplace's own is a defect waiting for a daylight-saving
    boundary.
    """
    return datetime(2026, 11, day, hour, 0, tzinfo=UTC)


RULES_VALID_FROM = _at(1, 0)


@dataclass(frozen=True)
class SellerSpec:
    """One merchant. PayPal's `payee`.

    `merchant_id` is a **real onboarded sandbox account**, and the join
    between this dataset and `fixtures/paypal/`. Changing one means
    re-capturing.
    """

    ref: str
    name: str
    merchant_id: str
    default_currency: str
    partner_fee_enabled: bool
    delay_funds_enabled: bool


SELLERS: tuple[SellerSpec, ...] = (
    SellerSpec(
        ref="vendor-aurora",
        name="Aurora Audio",
        merchant_id="C24NAWELTTHXJ",
        default_currency="USD",
        partner_fee_enabled=True,
        delay_funds_enabled=True,
    ),
    SellerSpec(
        ref="vendor-bellweather",
        name="Bellweather Books",
        merchant_id="E4FUU2CX86ALC",
        default_currency="USD",
        partner_fee_enabled=True,
        delay_funds_enabled=True,
    ),
)

#: Merchant id -> dataset seller, which is the direction the connector
#: resolves in: a capture names a payee, and the payee must become a seller.
BY_MERCHANT: dict[str, str] = {s.merchant_id: s.ref for s in SELLERS}


@dataclass(frozen=True)
class RuleSpec:
    ref: str
    rate: str
    seller_ref: str | None = None
    category: str | None = None


#: **One rule, and it has to be one.** specs/roadmap.md Phase 10's exit
#: criterion requires both sellers on `ORD-0001` to resolve to 10%, so a
#: seller-scoped rate cannot sit on either of them. Rule-resolution
#: specificity stays covered by `tests/test_commission_rules.py` as a unit,
#: where it belongs - exercising it here would make the hand-check in
#: validation.md section 2 turn on a tie-break rather than on arithmetic.
COMMISSION_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(ref="RULE-DEFAULT", rate="0.100000"),
)


@dataclass(frozen=True)
class ItemSpec:
    ref: str
    seller_ref: str
    quantity: int
    unit_price: str
    line_total: str
    category: str | None = None


@dataclass(frozen=True)
class RefundSpec:
    """A refund the marketplace recorded.

    `reflected_in_ledger` is F1's lever. When true the builder writes the
    refund debit **and** the pro-rata commission reversal credit, which is the
    healthy shape - PayPal reverses the platform fee automatically and pro
    rata, confirmed against real captures (2.00 on 20/70, 3.00 on 30/110), so
    a marketplace keeping its commission on a refunded sale disagrees with the
    provider.

    `paypal_record_present` is F5's lever, and its default has to be `True`:
    if no refund ever had a provider counterpart, "the counterpart is missing"
    would describe every refund rather than the one that earns
    `awaiting_evidence`. The capture script honours it by simply not calling
    refund for that order.
    """

    ref: str
    seller_ref: str
    amount: str
    expected_commission_reversal: str
    reflected_in_ledger: bool = True
    paypal_record_present: bool = True


@dataclass(frozen=True)
class DisputeSpec:
    """A dispute, with only the fields a marketplace would certainly hold.

    Phase 0 observed not one dispute value; their shape is known from a pinned
    contract only. **Writes no ledger entry**: an open dispute moves no money,
    and a speculative debit would make the seller's position wrong in the
    clean baseline, which is the one thing this dataset cannot afford
    (specs/mission.md P4).
    """

    ref: str
    seller_ref: str
    amount: str
    reason: str
    status: str


@dataclass(frozen=True)
class OrderSpec:
    """One order and every figure the marketplace expects of it.

    `expected_commission` and `expected_entitlements` are hand-written (D5a)
    and are what the marketplace *believes*. For a faulty order that belief is
    wrong on purpose, which is why faulty orders are excluded from `verify()`.
    """

    ref: str
    currency: str
    total: str
    placed_at: datetime
    items: tuple[ItemSpec, ...]
    expected_commission: str
    expected_entitlements: dict[str, str]
    disbursement_mode: str = "INSTANT"
    refunds: tuple[RefundSpec, ...] = ()
    disputes: tuple[DisputeSpec, ...] = ()

    # --- fault levers, all marketplace-side ---------------------------------
    #
    # Every fault is an error in the marketplace's own books, measured against
    # PayPal's correct record. The capture pushed eighteen *correct* orders;
    # nothing here changes what PayPal did.

    #: F4: credit the sale twice, under distinct references.
    duplicate_ledger_entry: bool = False

    #: F3: the currency the marketplace records the sale in, where that is not
    #: the order's own - a marketplace tracking what it owes in its home
    #: currency, converted at a stale rate. `(currency, amount)` per seller.
    recorded_in_currency: dict[str, tuple[str, str]] = field(default_factory=dict)

    #: F6: what the ledger credits, where that understates the entitlement.
    understated_credit: dict[str, str] = field(default_factory=dict)


A = "vendor-aurora"
B = "vendor-bellweather"


def _item(
    ref: str,
    seller: str,
    price: str,
    category: str,
    qty: int = 1,
    line_total: str | None = None,
) -> ItemSpec:
    """One order line.

    `line_total` defaults to the unit price, which is right only at quantity
    one - and passing `qty` without it was a real bug caught by `verify()`:
    ORD-0009 declared 3 x 59.97 where it meant 3 x 19.99. Stating the total
    explicitly for any multi-quantity line is the point, since Phase 2 D13
    stores what the marketplace said rather than computing it.
    """
    return ItemSpec(
        ref=ref,
        seller_ref=seller,
        quantity=qty,
        unit_price=price,
        line_total=line_total if line_total is not None else price,
        category=category,
    )


# ---------------------------------------------------------------------------
# The twelve clean orders
#
# Every gross below matches a real capture in `fixtures/paypal/`. The
# arithmetic is shown so a reader can check it without running anything, which
# is the point of a hand-checkable baseline. Commission is the tenant's 10%,
# quantized HALF_UP.
# ---------------------------------------------------------------------------

CLEAN_ORDERS: tuple[OrderSpec, ...] = (
    # specs/mission.md's worked example, captured exactly: $160 across two
    # sellers at 10% -> A $90, B $54, commission $16.
    OrderSpec(
        ref="ORD-0001",
        currency="USD",
        total="160.00",
        placed_at=_at(2),
        items=(
            _item("ITEM-0001-A", A, "100.00", "audio"),
            _item("ITEM-0001-B", B, "60.00", "books"),
        ),
        expected_commission="16.00",
        expected_entitlements={A: "90.00", B: "54.00"},
    ),
    OrderSpec(
        ref="ORD-0002",
        currency="USD",
        total="50.00",
        placed_at=_at(3),
        items=(_item("ITEM-0002-A", A, "50.00", "audio"),),
        expected_commission="5.00",
        expected_entitlements={A: "45.00"},
    ),
    OrderSpec(
        ref="ORD-0003",
        currency="USD",
        total="120.00",
        placed_at=_at(3, 15),
        items=(_item("ITEM-0003-A", B, "120.00", "books"),),
        expected_commission="12.00",
        expected_entitlements={B: "108.00"},
    ),
    OrderSpec(
        ref="ORD-0004",
        currency="USD",
        total="80.00",
        placed_at=_at(4),
        items=(_item("ITEM-0004-A", A, "80.00", "audio"),),
        expected_commission="8.00",
        expected_entitlements={A: "72.00"},
    ),
    # **Non-fault 1.** DELAYED, funds held, never released. A healthy
    # intermediate state and a legal DISBURSEMENT_STATES member, in the clean
    # baseline precisely so Block C proves it is not flagged. The capture
    # confirmed PayPal reads such a capture as COMPLETED while the seller is
    # unpaid, which is the trap.
    OrderSpec(
        ref="ORD-0005",
        currency="EUR",
        total="200.00",
        placed_at=_at(5),
        items=(_item("ITEM-0005-A", B, "200.00", "books"),),
        expected_commission="20.00",
        expected_entitlements={B: "180.00"},
        disbursement_mode="DELAYED",
    ),
    OrderSpec(
        ref="ORD-0006",
        currency="USD",
        total="75.00",
        placed_at=_at(6),
        items=(
            _item("ITEM-0006-A", A, "30.00", "audio"),
            _item("ITEM-0006-B", B, "45.00", "books"),
        ),
        expected_commission="7.50",
        expected_entitlements={A: "27.00", B: "40.50"},
    ),
    # 33.33 * 0.10 = 3.3330, which quantizes HALF_UP to 3.33, leaving 30.00.
    OrderSpec(
        ref="ORD-0007",
        currency="USD",
        total="33.33",
        placed_at=_at(7),
        items=(_item("ITEM-0007-A", A, "33.33", "audio"),),
        expected_commission="3.33",
        expected_entitlements={A: "30.00"},
    ),
    # **Non-fault 2, and the subtler of the two.** Two sellers on one EUR
    # order, both settling USD, so PayPal converts **each capture separately**:
    #
    #   36.02 EUR -> 39.24 USD  and  73.37 EUR -> 79.93 USD  = 119.17 USD
    #   converting the 109.39 EUR total once        ->         119.18 USD
    #
    # One cent apart, and **neither figure is wrong** - both come from
    # PayPal's own rate. A reconciler that converts the order total once
    # reports a discrepancy that does not exist. This is the artefact
    # requirements.md D4 keeps in the clean baseline on purpose, and
    # specs/roadmap.md Phase 11's hardest criterion is measured against it.
    #
    # The amounts are chosen, not arbitrary: at PayPal's sandbox rate most
    # splits round identically, and a single-seller order cannot produce the
    # artefact at all because there is nothing to convert twice.
    OrderSpec(
        ref="ORD-0008",
        currency="EUR",
        total="121.54",
        placed_at=_at(8),
        items=(
            _item("ITEM-0008-A", B, "40.02", "books"),
            _item("ITEM-0008-B", A, "81.52", "audio"),
        ),
        # 40.02 * 0.10 = 4.002 -> 4.00 ; 81.52 * 0.10 = 8.152 -> 8.15
        expected_commission="12.15",
        expected_entitlements={B: "36.02", A: "73.37"},
    ),
    # 59.97 * 0.10 = 5.9970 -> 6.00, so the seller keeps 53.97.
    OrderSpec(
        ref="ORD-0009",
        currency="USD",
        total="59.97",
        placed_at=_at(9),
        items=(
            _item("ITEM-0009-A", B, "19.99", "books", qty=3, line_total="59.97"),
        ),
        expected_commission="6.00",
        expected_entitlements={B: "53.97"},
        disputes=(
            DisputeSpec(
                ref="DISP-0009-A",
                seller_ref=B,
                amount="19.99",
                reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                status="WAITING_FOR_SELLER_RESPONSE",
            ),
        ),
    ),
    OrderSpec(
        ref="ORD-0010",
        currency="USD",
        total="45.00",
        placed_at=_at(10),
        items=(_item("ITEM-0010-A", A, "45.00", "audio"),),
        expected_commission="4.50",
        expected_entitlements={A: "40.50"},
    ),
    # **The clean counterpart to F1**, and what makes F1 detectable as an
    # absence rather than a shape nobody has seen. PayPal's real reversal on
    # this order was 2.00, matching the figure below.
    OrderSpec(
        ref="ORD-0011",
        currency="USD",
        total="70.00",
        placed_at=_at(11),
        items=(_item("ITEM-0011-A", A, "70.00", "audio"),),
        expected_commission="7.00",
        expected_entitlements={A: "63.00"},
        refunds=(
            RefundSpec(
                ref="RFND-0011-A",
                seller_ref=A,
                amount="20.00",
                expected_commission_reversal="2.00",
            ),
        ),
    ),
    OrderSpec(
        ref="ORD-0012",
        currency="EUR",
        total="120.00",
        placed_at=_at(12),
        items=(_item("ITEM-0012-A", B, "120.00", "books"),),
        expected_commission="12.00",
        expected_entitlements={B: "108.00"},
    ),
)


# ---------------------------------------------------------------------------
# The six faults - all marketplace-side (requirements.md D4)
#
# Their own orders, ORD-0013 to ORD-0018, so the clean baseline stays exactly
# as hand-tabulated and `verify()` excludes faulty rows by reference rather
# than by a heuristic. PayPal's record for each is correct; the error is in
# the marketplace's books.
# ---------------------------------------------------------------------------

FAULTY_ORDERS: tuple[OrderSpec, ...] = (
    # F1 - scenario 1. A real 30.00 refund, with a real 3.00 fee reversal on
    # PayPal's side, that the seller ledger never records.
    OrderSpec(
        ref="ORD-0013",
        currency="USD",
        total="110.00",
        placed_at=_at(14),
        items=(_item("ITEM-0013-A", B, "110.00", "books"),),
        expected_commission="11.00",
        expected_entitlements={B: "99.00"},
        refunds=(
            RefundSpec(
                ref="RFND-0013-A",
                seller_ref=B,
                amount="30.00",
                expected_commission_reversal="3.00",
                reflected_in_ledger=False,
            ),
        ),
    ),
    # F2 - scenario 2. The marketplace retained 30.00 where its own rule gives
    # 20.00, and PayPal's platform fee records the rule-correct 20.00. The
    # figures below are the **wrong** ones the marketplace believes, which is
    # why this order is excluded from `verify()`.
    OrderSpec(
        ref="ORD-0014",
        currency="USD",
        total="200.00",
        placed_at=_at(15),
        items=(_item("ITEM-0014-A", A, "200.00", "audio"),),
        expected_commission="30.00",
        expected_entitlements={A: "170.00"},
    ),
    # F3 - scenario 3. The marketplace records what it owes in its home
    # currency, converted at a stale rate: 262.00 USD against a real
    # settlement of 294.16 USD at PayPal's reported rate. A 32.16 USD gap,
    # two orders of magnitude clear of ORD-0008's one-cent artefact, so no
    # threshold can confuse the two.
    OrderSpec(
        ref="ORD-0015",
        currency="EUR",
        total="300.00",
        placed_at=_at(16),
        items=(_item("ITEM-0015-A", B, "300.00", "books"),),
        expected_commission="30.00",
        expected_entitlements={B: "270.00"},
        recorded_in_currency={B: ("USD", "262.00")},
    ),
    # F4 - one sale credited twice under distinct references. Distinct because
    # UNIQUE (tenant_id, external_ref) refuses a literal repeat, and a real
    # double-post carries its own id.
    OrderSpec(
        ref="ORD-0016",
        currency="USD",
        total="90.00",
        placed_at=_at(17),
        items=(_item("ITEM-0016-A", A, "90.00", "audio"),),
        expected_commission="9.00",
        expected_entitlements={A: "81.00"},
        duplicate_ledger_entry=True,
    ),
    # F5 - **the fault whose correct answer is not an exception.** R10's
    # cross-source case: the marketplace recorded a refund and reflected it
    # correctly; PayPal's record has not appeared, because the capture
    # deliberately did not issue one. Classifying this as "missing from
    # PayPal" is the false positive Phase 11 forbids - the record is late, not
    # absent - so the answer is `awaiting_evidence`.
    OrderSpec(
        ref="ORD-0017",
        currency="USD",
        total="60.00",
        placed_at=_at(18),
        items=(_item("ITEM-0017-A", A, "60.00", "audio"),),
        expected_commission="6.00",
        expected_entitlements={A: "54.00"},
        refunds=(
            RefundSpec(
                ref="RFND-0017-A",
                seller_ref=A,
                amount="15.00",
                expected_commission_reversal="1.50",
                reflected_in_ledger=True,
                paypal_record_present=False,
            ),
        ),
    ),
    # F6 - the ledger credits 120.00 EUR where the entitlement is 135.00 and
    # PayPal released the full amount (147.08 USD converted). A 15.00 EUR
    # shortfall on a completed release, so nothing in flight explains it.
    OrderSpec(
        ref="ORD-0018",
        currency="EUR",
        total="150.00",
        placed_at=_at(19),
        items=(_item("ITEM-0018-A", B, "150.00", "books"),),
        expected_commission="15.00",
        expected_entitlements={B: "135.00"},
        disbursement_mode="DELAYED",
        understated_credit={B: "120.00"},
    ),
)

FAULTY_ORDER_REFS: frozenset[str] = frozenset(o.ref for o in FAULTY_ORDERS)


# ---------------------------------------------------------------------------
# Hand-computed totals, carried into validation.md section 2
# ---------------------------------------------------------------------------

#: Net clean position per seller and currency, refunds and reversals included.
EXPECTED_POSITIONS: dict[tuple[str, str], str] = {
    # 90.00 + 45.00 + 72.00 + 27.00 + 30.00 + 40.50 + (63.00 - 20.00 + 2.00)
    (A, "USD"): "349.50",
    # ORD-0008 only
    (A, "EUR"): "73.37",
    # 54.00 + 108.00 + 40.50 + 53.97
    (B, "USD"): "256.47",
    # 180.00 + 36.02 + 108.00
    (B, "EUR"): "324.02",
}

#: Total expected commission per currency across the clean set.
EXPECTED_COMMISSION_TOTALS: dict[str, str] = {
    # 16 + 5 + 12 + 8 + 7.50 + 3.33 + 6 + 4.50 + (7 - 2)
    "USD": "67.33",
    # 20.00 + 12.15 + 12.00
    "EUR": "44.15",
}


def clean_order(ref: str) -> OrderSpec:
    for order in CLEAN_ORDERS:
        if order.ref == ref:
            return order
    raise KeyError(f"no clean order {ref!r}")


def faulty_order(ref: str) -> OrderSpec:
    for order in FAULTY_ORDERS:
        if order.ref == ref:
            return order
    raise KeyError(f"no faulty order {ref!r}")


def order(ref: str) -> OrderSpec:
    try:
        return clean_order(ref)
    except KeyError:
        return faulty_order(ref)


def seller(ref: str) -> SellerSpec:
    for spec in SELLERS:
        if spec.ref == ref:
            return spec
    raise KeyError(f"no seller {ref!r}")


def rate_for(seller_ref: str) -> Decimal:
    """The rate a seller resolves to here. A convenience for tests' own
    arithmetic - **not** the resolution the engine performs, which `verify()`
    uses instead."""
    for rule in COMMISSION_RULES:
        if rule.seller_ref == seller_ref:
            return Decimal(rule.rate)
    return Decimal(COMMISSION_RULES[0].rate)


@dataclass(frozen=True)
class Dataset:
    sellers: tuple[SellerSpec, ...] = SELLERS
    rules: tuple[RuleSpec, ...] = COMMISSION_RULES
    orders: tuple[OrderSpec, ...] = CLEAN_ORDERS

    #: Orders `verify()` must skip, **by reference, never by a heuristic**.
    #: The builder knows which it broke; a heuristic that tried to detect them
    #: would be the reconciliation engine reimplemented inside the fixture.
    faulty_order_refs: frozenset[str] = field(default_factory=frozenset)


#: The clean baseline alone: Block B's worked example, and the dataset Phase
#: 11's false-positive gate is measured against.
CLEAN = Dataset()

#: Clean and faulty coexisting, which is Phase 5's second exit criterion, and
#: what the demo and the eval harness both run on.
FULL = Dataset(
    orders=CLEAN_ORDERS + FAULTY_ORDERS,
    faulty_order_refs=FAULTY_ORDER_REFS,
)
