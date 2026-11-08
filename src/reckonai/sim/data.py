"""The simulated marketplace's dataset, as literals.

specs/2026-11-08-block-a-marketplace-connector/requirements.md D5 and D5a. Four
sellers, twelve clean orders, two currencies, one FX order and one `DELAYED`
order - small enough that `validation.md` section 2 can tabulate every expected
figure by hand, which is what "the seed dataset reconciles perfectly by hand"
(specs/roadmap.md Phase 4) actually requires.

**Every expected figure here is hand-written, not computed.** That is D5a and it
is the whole reason `seed.verify()` is a real check: the builder writes rows
from these literals and `verify()` recomputes them from the items and the
commission rules. Two independent derivations that have to agree. Had the
builder computed the figures itself, `verify()` would be asserting that one
function agrees with itself - which is the shape of the Phase 3 gate that
passed with its own subject commented out.

So **if you change a unit price here, change its expected figures too.**
`verify()` will tell you which one you missed, and that is the feature.

**This module imports nothing from `reckonai.models`.** A layering preference
rather than the mapping guard - requirements.md D1 is explicit that the guard
that matters is on the outward field names in `fields.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

# ---------------------------------------------------------------------------
# Identifiers
#
# The tenant slug is fixed so the sim is addressable; the connector's own
# tenant (requirements.md D2) is created separately and is not this one.
# ---------------------------------------------------------------------------

TENANT_SLUG = "reckonmarket"
TENANT_NAME = "ReckonMarket"

#: The tenant A3 ingests into, kept apart so a row-count comparison between the
#: seeded side and the ingested side is meaningful rather than circular.
INGEST_TENANT_SLUG = "reckonmarket-ingested"
INGEST_TENANT_NAME = "ReckonMarket (ingested)"


def _at(day: int, hour: int = 12) -> datetime:
    """A fixed timestamp in November 2026, so the dataset is reproducible.

    Timezone-aware, always: `Base.type_annotation_map` maps `datetime` to
    `TIMESTAMPTZ`, and a naive value in a system reconciling across PayPal's
    clock and a marketplace's own is a defect waiting for a daylight-saving
    boundary.
    """
    return datetime(2026, 11, day, hour, 0, tzinfo=UTC)


#: Every commission rule's window opens before the earliest order.
RULES_VALID_FROM = _at(1, 0)


@dataclass(frozen=True)
class SellerSpec:
    """One merchant. PayPal's `payee` on the purchase unit."""

    ref: str
    name: str
    merchant_id: str
    default_currency: str

    #: Whether `PARTNER_FEE` was granted at onboarding. specs/tech-stack.md
    #: section 8: it is granted **per seller**, not as an account-level switch,
    #: so whether a platform fee is even possible varies by seller.
    partner_fee_enabled: bool

    #: Whether `DELAY_FUNDS_DISBURSEMENT` was granted. Only a seller with this
    #: can have a `DELAYED` order, which is why `ORD-0005` belongs to S3.
    delay_funds_enabled: bool


SELLERS: tuple[SellerSpec, ...] = (
    SellerSpec(
        ref="vendor-aurora",
        name="Aurora Audio",
        merchant_id="MERCH-AURORA-001",
        default_currency="USD",
        partner_fee_enabled=True,
        delay_funds_enabled=False,
    ),
    SellerSpec(
        ref="vendor-bellweather",
        name="Bellweather Books",
        merchant_id="MERCH-BELLW-002",
        default_currency="USD",
        partner_fee_enabled=True,
        delay_funds_enabled=False,
    ),
    SellerSpec(
        ref="vendor-citrine",
        name="Citrine Ceramics",
        merchant_id="MERCH-CITRI-003",
        default_currency="EUR",
        partner_fee_enabled=True,
        delay_funds_enabled=True,
    ),
    # **The seller onboarded without PARTNER_FEE**, and the reason this dataset
    # has a fourth seller at all. The `Seller` model's docstring names the
    # failure it guards: Phase 11 must not classify a missing platform fee as a
    # discrepancy for a seller who was never onboarded with the capability.
    # With no such seller in the baseline, that branch would never be measured.
    SellerSpec(
        ref="vendor-dunlin",
        name="Dunlin Devices",
        merchant_id="MERCH-DUNLI-004",
        default_currency="USD",
        partner_fee_enabled=False,
        delay_funds_enabled=False,
    ),
)


@dataclass(frozen=True)
class RuleSpec:
    """A commission rule. `seller_ref` or `category` of `None` widens scope."""

    ref: str
    rate: str
    seller_ref: str | None = None
    category: str | None = None


COMMISSION_RULES: tuple[RuleSpec, ...] = (
    # The tenant-wide default: 10%, which is the rate specs/mission.md's worked
    # example uses and the one `ORD-0001` has to resolve to.
    RuleSpec(ref="RULE-DEFAULT", rate="0.100000"),
    # Citrine negotiated 8%. Exercises seller-scoped resolution against the
    # default above, which `reckonai.engine.rules.resolve_commission_rule`
    # settles by specificity.
    RuleSpec(ref="RULE-CITRINE", rate="0.080000", seller_ref="vendor-citrine"),
    # **An explicitly stated rate of zero, which is not a defaulted zero.**
    # Dunlin was not onboarded for partner fees, so PayPal cannot take a
    # platform fee on its orders at all, and a marketplace commission it has no
    # mechanism to collect would be fiction. Stating 0% is the honest model.
    #
    # The rule exists rather than being omitted because
    # `resolve_commission_rule` raises `NoApplicableCommissionRule` when nothing
    # matches - and a seller whose commission is genuinely nil is a different
    # claim from a seller whose rate nobody recorded. specs/tech-stack.md
    # section 9.6 forbids *defaulting* money to zero; it does not forbid a
    # measured zero that a contract states.
    RuleSpec(ref="RULE-DUNLIN", rate="0.000000", seller_ref="vendor-dunlin"),
)

# Category-scoped resolution is deliberately not exercised here. Phase 2's
# `tests/test_commission_rules.py` already covers it as a unit, and adding a
# category rule that overlaps the two above would make the hand-check in
# validation.md section 2 turn on a specificity tie-break rather than on
# arithmetic.


@dataclass(frozen=True)
class ItemSpec:
    """One order line, attributed to the seller who fulfils it."""

    ref: str
    seller_ref: str
    quantity: int
    unit_price: str
    line_total: str
    category: str | None = None


@dataclass(frozen=True)
class RefundSpec:
    """A refund the marketplace recorded.

    `reflected_in_ledger` is what fault F1 turns off. When true the builder
    writes the refund debit **and** the pro-rata commission reversal credit,
    which is the healthy shape; PayPal reverses the platform fee automatically
    and pro rata, so a marketplace that keeps its commission on a refunded sale
    disagrees with the provider.
    """

    ref: str
    seller_ref: str
    amount: str
    expected_commission_reversal: str
    reflected_in_ledger: bool = True


@dataclass(frozen=True)
class DisputeSpec:
    """A dispute the marketplace recorded against a sale.

    **Every field but the amount is nullable in the canonical table, and that
    is not caution about sample size.** Phase 0 observed not one dispute value:
    their shape is known from a pinned contract only. So this spec states the
    few fields a marketplace would certainly hold and leaves the rest absent,
    which is the honest shape rather than a guess dressed as evidence.

    **What this deliberately does not do** is revert its capture to `PENDING`.
    An open dispute does exactly that in the real API, and the capture loses
    its whole `seller_receivable_breakdown` with it - which is a genuine
    false-positive trap and a good candidate for Block C. It is left out here
    because requirements.md D4 fixes the clean baseline's non-faults at two,
    and quietly adding a third would change what Phase 11's gate is measured
    against without the manifest recording it. Recorded in validation.md
    section 8 as work for Block C to take or decline deliberately.
    """

    ref: str
    seller_ref: str
    amount: str
    reason: str
    status: str


@dataclass(frozen=True)
class FxSpec:
    """A currency conversion PayPal reported on a settlement.

    **`fee` is absent and stays absent.** specs/mission.md P3, and the
    `CHECK (fee_amount IS NULL OR fee_amount <> 0)` on `fx_conversion` refuses
    a zero one: an absent fee and a measured fee of nothing are different
    claims. PayPal publishes no conversion fee, so there is none to record.

    `settlement_amount` is **PayPal's own reported figure**, not a
    recomputation. See `ORD-0008` for why the distinction has teeth.
    """

    rate: str
    target_currency: str
    settlement_amount: str

    #: What a naive per-item recomputation produces, recorded so the test for
    #: the rounding trap can assert the two differ rather than assuming it.
    #: Not written to any table.
    naive_recomputation: str


@dataclass(frozen=True)
class OrderSpec:
    """One customer order and every figure expected of it.

    `expected_commission` and `expected_entitlements` are hand-written (D5a).
    `verify()` recomputes both and compares.
    """

    ref: str
    currency: str
    total: str
    placed_at: datetime
    items: tuple[ItemSpec, ...]

    #: The marketplace's own expected commission on this order, in `currency`.
    expected_commission: str

    #: Per seller ref, what that seller is owed from this order before any
    #: refund, in `currency`.
    expected_entitlements: dict[str, str]

    #: `INSTANT` or `DELAYED`. Never `None` in the clean set: a silent mode
    #: would make a held-funds transaction look like a completed one, and the
    #: column is nullable to *record* that silence, not to invite it.
    disbursement_mode: str = "INSTANT"

    #: PayPal's own processing fee on the capture. Synthetic, per D12.
    #: **Never defaulted to zero** - `None` means unknown, which is a different
    #: claim, and specs/roadmap.md Phase 10 forbids the default.
    paypal_fee: str | None = None

    refunds: tuple[RefundSpec, ...] = ()
    disputes: tuple[DisputeSpec, ...] = ()
    fx: FxSpec | None = None

    #: Which seller the capture settles to. Set where an order has items from
    #: one seller only; `None` where it has several, since PayPal issues a
    #: capture per purchase unit and the sim models the simple case.
    settles_to: str | None = None


# ---------------------------------------------------------------------------
# The twelve clean orders
#
# Every figure below is hand-derived. The arithmetic is shown so a reader can
# check it without running anything, which is the point of a hand-checkable
# baseline.
# ---------------------------------------------------------------------------

CLEAN_ORDERS: tuple[OrderSpec, ...] = (
    # specs/mission.md's worked example, seeded exactly, and the order Block B
    # asserts against: $160 across two sellers at 10% -> A $90, B $54,
    # commission $16.
    OrderSpec(
        ref="ORD-0001",
        currency="USD",
        total="160.00",
        placed_at=_at(2),
        items=(
            ItemSpec(
                ref="ITEM-0001-A",
                seller_ref="vendor-aurora",
                quantity=1,
                unit_price="100.00",
                line_total="100.00",
                category="audio",
            ),
            ItemSpec(
                ref="ITEM-0001-B",
                seller_ref="vendor-bellweather",
                quantity=1,
                unit_price="60.00",
                line_total="60.00",
                category="books",
            ),
        ),
        # 100.00 * 0.10 = 10.00 ; 60.00 * 0.10 = 6.00
        expected_commission="16.00",
        expected_entitlements={
            "vendor-aurora": "90.00",
            "vendor-bellweather": "54.00",
        },
        paypal_fee="5.34",
    ),
    OrderSpec(
        ref="ORD-0002",
        currency="USD",
        total="50.00",
        placed_at=_at(3),
        items=(
            ItemSpec(
                ref="ITEM-0002-A",
                seller_ref="vendor-aurora",
                quantity=2,
                unit_price="25.00",
                line_total="50.00",
                category="audio",
            ),
        ),
        expected_commission="5.00",
        expected_entitlements={"vendor-aurora": "45.00"},
        paypal_fee="1.75",
        settles_to="vendor-aurora",
    ),
    OrderSpec(
        ref="ORD-0003",
        currency="USD",
        total="120.00",
        placed_at=_at(3, 15),
        items=(
            ItemSpec(
                ref="ITEM-0003-A",
                seller_ref="vendor-bellweather",
                quantity=1,
                unit_price="120.00",
                line_total="120.00",
                category="books",
            ),
        ),
        expected_commission="12.00",
        expected_entitlements={"vendor-bellweather": "108.00"},
        paypal_fee="4.08",
        settles_to="vendor-bellweather",
    ),
    # Dunlin: 0% by the rule that states it. No platform fee row is written,
    # because PayPal cannot take one for a seller without `PARTNER_FEE`.
    OrderSpec(
        ref="ORD-0004",
        currency="USD",
        total="80.00",
        placed_at=_at(4),
        items=(
            ItemSpec(
                ref="ITEM-0004-A",
                seller_ref="vendor-dunlin",
                quantity=1,
                unit_price="80.00",
                line_total="80.00",
                category="devices",
            ),
        ),
        expected_commission="0.00",
        expected_entitlements={"vendor-dunlin": "80.00"},
        paypal_fee="2.82",
        settles_to="vendor-dunlin",
    ),
    # **Non-fault 1.** `DELAYED`, funds held, no release requested. A healthy
    # intermediate state and a legal `DISBURSEMENT_STATES` member, in the clean
    # baseline precisely so Block C proves it is not flagged
    # (requirements.md D4). `released_at` stays NULL - P3 forbids writing our
    # own clock where PayPal supplied no timestamp.
    OrderSpec(
        ref="ORD-0005",
        currency="EUR",
        total="200.00",
        placed_at=_at(5),
        items=(
            ItemSpec(
                ref="ITEM-0005-A",
                seller_ref="vendor-citrine",
                quantity=1,
                unit_price="200.00",
                line_total="200.00",
                category="ceramics",
            ),
        ),
        # Citrine's negotiated 8%: 200.00 * 0.08 = 16.00
        expected_commission="16.00",
        expected_entitlements={"vendor-citrine": "184.00"},
        disbursement_mode="DELAYED",
        paypal_fee="6.80",
        settles_to="vendor-citrine",
    ),
    OrderSpec(
        ref="ORD-0006",
        currency="USD",
        total="75.00",
        placed_at=_at(6),
        items=(
            ItemSpec(
                ref="ITEM-0006-A",
                seller_ref="vendor-aurora",
                quantity=2,
                unit_price="15.00",
                line_total="30.00",
                category="audio",
            ),
            ItemSpec(
                ref="ITEM-0006-B",
                seller_ref="vendor-bellweather",
                quantity=1,
                unit_price="45.00",
                line_total="45.00",
                category="books",
            ),
        ),
        # 30.00 * 0.10 = 3.00 ; 45.00 * 0.10 = 4.50
        expected_commission="7.50",
        expected_entitlements={
            "vendor-aurora": "27.00",
            "vendor-bellweather": "40.50",
        },
        paypal_fee="2.48",
    ),
    # A rounding case that is not a trap, only arithmetic: 33.33 * 0.10 =
    # 3.3330, which quantizes HALF_UP to 3.33, leaving exactly 30.00.
    OrderSpec(
        ref="ORD-0007",
        currency="USD",
        total="33.33",
        placed_at=_at(7),
        items=(
            ItemSpec(
                ref="ITEM-0007-A",
                seller_ref="vendor-aurora",
                quantity=1,
                unit_price="33.33",
                line_total="33.33",
                category="audio",
            ),
        ),
        expected_commission="3.33",
        expected_entitlements={"vendor-aurora": "30.00"},
        paypal_fee="1.46",
        settles_to="vendor-aurora",
    ),
    # **Non-fault 2, and the subtler of the two.** The buyer pays USD; Citrine
    # settles in EUR; PayPal converts and reports the result.
    #
    # Citrine's share is 50.00 - 4.00 = 46.00 and 100.00 - 8.00 = 92.00, so
    # 138.00 USD. At PayPal's reported rate of 0.9183:
    #
    #   converting the total once:  138.00 * 0.9183 = 126.7254 -> 126.73
    #   converting each line:        46.00 * 0.9183 =  42.2418 ->  42.24
    #                                92.00 * 0.9183 =  84.4836 ->  84.48
    #                                                            = 126.72
    #
    # **One cent apart, and neither figure is wrong.** PayPal reported 126.73,
    # so that is the settlement; a reconciler that recomputes per line gets
    # 126.72 and reports a discrepancy that does not exist. This is the
    # cent-rounding artefact requirements.md D4 keeps in the clean baseline on
    # purpose, and specs/roadmap.md Phase 11's hardest criterion is measured
    # against it.
    OrderSpec(
        ref="ORD-0008",
        currency="USD",
        total="150.00",
        placed_at=_at(8),
        items=(
            ItemSpec(
                ref="ITEM-0008-A",
                seller_ref="vendor-citrine",
                quantity=1,
                unit_price="50.00",
                line_total="50.00",
                category="ceramics",
            ),
            ItemSpec(
                ref="ITEM-0008-B",
                seller_ref="vendor-citrine",
                quantity=1,
                unit_price="100.00",
                line_total="100.00",
                category="ceramics",
            ),
        ),
        expected_commission="12.00",
        expected_entitlements={"vendor-citrine": "138.00"},
        paypal_fee="5.05",
        settles_to="vendor-citrine",
        fx=FxSpec(
            rate="0.9183",
            target_currency="EUR",
            settlement_amount="126.73",
            naive_recomputation="126.72",
        ),
    ),
    # 59.97 * 0.10 = 5.9970, which quantizes HALF_UP to 6.00 - so the seller
    # keeps 53.97 and the marketplace's cut is three thousandths of a cent more
    # than the contract's literal product. The rounding has to land somewhere.
    OrderSpec(
        ref="ORD-0009",
        currency="USD",
        total="59.97",
        placed_at=_at(9),
        items=(
            ItemSpec(
                ref="ITEM-0009-A",
                seller_ref="vendor-bellweather",
                quantity=3,
                unit_price="19.99",
                line_total="59.97",
                category="books",
            ),
        ),
        expected_commission="6.00",
        expected_entitlements={"vendor-bellweather": "53.97"},
        paypal_fee="2.17",
        settles_to="vendor-bellweather",
        # One dispute, so the endpoint Phase 4 requires serves something and
        # the tests over it mean something. It is **open and unresolved**, so
        # it moves no money and changes none of this order's figures - a
        # dispute with an outcome would, and that is a Block C concern.
        disputes=(
            DisputeSpec(
                ref="DISP-0009-A",
                seller_ref="vendor-bellweather",
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
        items=(
            ItemSpec(
                ref="ITEM-0010-A",
                seller_ref="vendor-dunlin",
                quantity=1,
                unit_price="45.00",
                line_total="45.00",
                category="devices",
            ),
        ),
        expected_commission="0.00",
        expected_entitlements={"vendor-dunlin": "45.00"},
        paypal_fee="1.61",
        settles_to="vendor-dunlin",
    ),
    # **The clean counterpart to fault F1**, and the reason F1 is detectable as
    # an absence rather than as a shape nobody has ever seen. A 20.00 refund on
    # a 70.00 sale, fully reflected: the seller is debited 20.00 and credited
    # back the 2.00 commission PayPal reversed pro rata. Net entitlement
    # 63.00 - 20.00 + 2.00 = 45.00.
    OrderSpec(
        ref="ORD-0011",
        currency="USD",
        total="70.00",
        placed_at=_at(11),
        items=(
            ItemSpec(
                ref="ITEM-0011-A",
                seller_ref="vendor-aurora",
                quantity=1,
                unit_price="70.00",
                line_total="70.00",
                category="audio",
            ),
        ),
        expected_commission="7.00",
        expected_entitlements={"vendor-aurora": "63.00"},
        paypal_fee="2.33",
        settles_to="vendor-aurora",
        refunds=(
            RefundSpec(
                ref="RFND-0011-A",
                seller_ref="vendor-aurora",
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
        items=(
            ItemSpec(
                ref="ITEM-0012-A",
                seller_ref="vendor-citrine",
                quantity=2,
                unit_price="60.00",
                line_total="120.00",
                category="ceramics",
            ),
        ),
        # 120.00 * 0.08 = 9.60
        expected_commission="9.60",
        expected_entitlements={"vendor-citrine": "110.40"},
        paypal_fee="4.08",
        settles_to="vendor-citrine",
    ),
)


# ---------------------------------------------------------------------------
# Hand-computed totals, carried into validation.md section 2
#
# These are the sums a reader can check the dataset against without running it.
# `tests/test_sim_seed.py` asserts the recomputation matches them, which makes
# them a third independent derivation rather than a comment.
# ---------------------------------------------------------------------------

#: Per seller ref and currency, the clean net entitlement across all twelve
#: orders - refunds and their commission reversals included.
EXPECTED_POSITIONS: dict[tuple[str, str], str] = {
    # 90.00 + 45.00 + 27.00 + 30.00 + (63.00 - 20.00 + 2.00) = 237.00
    ("vendor-aurora", "USD"): "237.00",
    # 54.00 + 108.00 + 40.50 + 53.97 = 256.47
    ("vendor-bellweather", "USD"): "256.47",
    # 138.00, settled as EUR 126.73 (ORD-0008)
    ("vendor-citrine", "USD"): "138.00",
    # 184.00 + 110.40 = 294.40
    ("vendor-citrine", "EUR"): "294.40",
    # 80.00 + 45.00 = 125.00
    ("vendor-dunlin", "USD"): "125.00",
}

#: Total commission the marketplace expects, per currency, across the clean set.
EXPECTED_COMMISSION_TOTALS: dict[str, str] = {
    # 16.00 + 5.00 + 12.00 + 0.00 + 7.50 + 3.33 + 12.00 + 6.00 + 0.00
    #   + (7.00 - 2.00) = 66.83
    "USD": "66.83",
    # 16.00 + 9.60 = 25.60
    "EUR": "25.60",
}


def clean_order(ref: str) -> OrderSpec:
    """One clean order by reference, for a test that names a specific case."""
    for order in CLEAN_ORDERS:
        if order.ref == ref:
            return order
    raise KeyError(f"no clean order {ref!r}")


def seller(ref: str) -> SellerSpec:
    for spec in SELLERS:
        if spec.ref == ref:
            return spec
    raise KeyError(f"no seller {ref!r}")


def rate_for(seller_ref: str) -> Decimal:
    """The commission rate a seller resolves to in this dataset.

    A convenience for the tests' own arithmetic. **Not** the resolution the
    engine performs: `reckonai.engine.rules.resolve_commission_rule` is what
    `verify()` uses, so the seed is checked against the real resolver rather
    than against this shortcut.
    """
    for rule in COMMISSION_RULES:
        if rule.seller_ref == seller_ref:
            return Decimal(rule.rate)
    return Decimal(COMMISSION_RULES[0].rate)


@dataclass(frozen=True)
class Dataset:
    """Everything the builder writes, so a caller can pass a modified copy."""

    sellers: tuple[SellerSpec, ...] = SELLERS
    rules: tuple[RuleSpec, ...] = COMMISSION_RULES
    orders: tuple[OrderSpec, ...] = CLEAN_ORDERS

    #: Fault ids whose rows `verify()` must skip. Empty for the clean dataset;
    #: group 3's faults populate it. Excluded **by id**, never by a heuristic -
    #: the builder knows which rows it broke (requirements.md D6).
    faulty_order_refs: frozenset[str] = field(default_factory=frozenset)


CLEAN = Dataset()
