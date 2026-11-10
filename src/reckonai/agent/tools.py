"""The agent's tool surface: read-only retrieval, plus the engine as a tool.

specs/tech-stack.md section 5.2 lists what the agent gets, and this module is
that list. Every method is a tool the model may call; nothing else is reachable.

**Three properties hold structurally rather than by instruction**, which is the
whole design of this module:

1. **Nothing mutates.** No method opens a write, issues an `INSERT`/`UPDATE`/
   `DELETE`, or flushes. specs/mission.md P6: the agent recommends, a person
   approves. `tests/test_agent_tools.py` asserts it by enumerating the surface
   rather than by reading the code.

2. **No parameter is a monetary amount.** Every argument is a record reference
   or a tenant-scoped filter. specs/mission.md P1 forbids the LLM from doing
   arithmetic that reaches a financial conclusion, and the enforcement is that
   there is nowhere to put a number it invented. The figures come from
   `recompute`, which is `reckonai.reconcile.run` - the same code Block D's
   scheduled run calls, so the agent cannot produce a figure the engine would
   not.

3. **Retrieval is narrow.** Each method returns the fields a decision needs,
   and a full raw payload only when asked for one by record. section 5.2 puts
   the cost exposure in evidence volume rather than model choice, and this is
   the mechanism - loading JSONB payloads into context repeatedly is the thing
   that actually costs money.

Returns are plain JSON-ready dicts, with **every amount a string carrying its
currency**, matching the frozen `Finding` contract. The model never receives a
bare number it might be tempted to add up, and specs/tech-stack.md section 2's
rule that amounts cross a boundary as strings holds here as much as over HTTP.

**The withhold set** (`requirements.md` D4) empties named record kinds so an
exception can be investigated with its explanation deliberately out of reach.
That is how Phase 12's third exit criterion is produced without touching the
seeded dataset or the manifest that is Block G's ground truth. `recompute` is
never withheld: it returns verdicts, not evidence, and suppressing it would
test whether the agent can work without the engine, which is not the question.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from functools import cached_property
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from reckonai.models import (
    Capture,
    CommissionRule,
    Disbursement,
    Dispute,
    Order,
    OrderItem,
    Payment,
    PlatformFee,
    RawPayload,
    Refund,
    Seller,
    SellerLedgerEntry,
)
from reckonai.money import Money
from reckonai.recon import OrderCase
from reckonai.reconcile import exceptions, load_order_cases, run

#: Any tenant-scoped record carrying an external reference.
_RecordT = TypeVar("_RecordT", Capture, Refund, Disbursement, PlatformFee)

#: The record kinds `withhold` understands.
#:
#: Named for what the agent would be reaching for rather than for the table,
#: because that is what a withhold argument reads as at the call site:
#: `withhold={"ledger"}` is the ledger entries being out of reach.
WITHHOLDABLE: frozenset[str] = frozenset(
    {
        "order",
        "items",
        "sellers",
        "payments",
        "captures",
        "refunds",
        "disputes",
        "ledger",
        "disbursements",
        "rules",
        "fees",
        "payloads",
    }
)


class UnknownWithholdKind(ValueError):
    """A withhold set naming something that is not a record kind.

    Raised rather than ignored: a misspelled `--withhold ledgers` that quietly
    withheld nothing would produce an investigation that looks like it proved
    the `insufficient_evidence` branch while the agent could see everything.
    That is a false pass on the exit criterion most at risk, so it fails loudly.
    """


def _money(value: Money | None) -> dict[str, str] | None:
    """One amount, as a string with its currency, or None.

    Never a float and never a bare number. The model is explaining these
    figures, not computing with them, and a JSON number here would invite
    exactly the arithmetic specs/mission.md P1 forbids.
    """
    if value is None:
        return None
    return {"amount": str(value.amount), "currency": value.currency}


class InvestigationTools:
    """Every tool the investigation agent can call, for one tenant.

    Construct one per investigation. It holds a `Session` it only ever reads
    through, which is why `reckonai.agent` sits outside the purity gate.
    """

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        withhold: Iterable[str] = (),
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._withhold = frozenset(withhold)

        unknown = self._withhold - WITHHOLDABLE
        if unknown:
            raise UnknownWithholdKind(
                f"Not record kinds: {sorted(unknown)}. "
                f"Known kinds are {sorted(WITHHOLDABLE)}."
            )

    @property
    def withheld(self) -> tuple[str, ...]:
        """What is out of reach, for the audit row."""
        return tuple(sorted(self._withhold))

    def _is_withheld(self, kind: str) -> bool:
        return kind in self._withhold

    # ---- Orders, items, sellers -----------------------------------------

    def fetch_order(self, order_ref: str) -> dict[str, Any] | None:
        """The order with this marketplace reference, or None.

        `None` rather than an empty dict or an exception, so "no such order"
        is a fact the model can act on instead of an error it has to recover
        from.
        """
        if self._is_withheld("order"):
            return None

        order = self._session.scalar(
            select(Order).where(
                Order.tenant_id == self._tenant_id, Order.external_ref == order_ref
            )
        )
        if order is None:
            return None

        return {
            "order_ref": order.external_ref,
            "placed_at": order.placed_at.isoformat(),
            "total": _money(order.total),
            "disbursement_mode": order.disbursement_mode,
        }

    def fetch_order_items(self, order_ref: str) -> list[dict[str, Any]]:
        """The lines on an order, each with its seller and its own amount."""
        if self._is_withheld("items"):
            return []

        order = self._session.scalar(
            select(Order).where(
                Order.tenant_id == self._tenant_id, Order.external_ref == order_ref
            )
        )
        if order is None:
            return []

        items = self._session.scalars(
            select(OrderItem)
            .where(
                OrderItem.tenant_id == self._tenant_id,
                OrderItem.order_id == order.id,
            )
            .order_by(OrderItem.external_ref)
        )
        return [
            {
                "item_ref": item.external_ref,
                "seller_id": str(item.seller_id),
                "category": item.category,
                "line_total": _money(item.line_total),
            }
            for item in items
        ]

    def fetch_sellers(self) -> list[dict[str, Any]]:
        """Every seller for this tenant, so an id in a finding has a name."""
        if self._is_withheld("sellers"):
            return []

        sellers = self._session.scalars(
            select(Seller)
            .where(Seller.tenant_id == self._tenant_id)
            .order_by(Seller.external_ref)
        )
        return [
            {
                "seller_id": str(seller.id),
                "seller_ref": seller.external_ref,
                "name": seller.name,
                "default_currency": seller.default_currency,
            }
            for seller in sellers
        ]

    # ---- Provider records -------------------------------------------------

    def fetch_payments(self, order_ref: str) -> list[dict[str, Any]]:
        """Payments matched to this order.

        The order-to-payment tie is a **match rather than a stated fact** -
        `Payment.order_id` is NULL on every provider-origin row by design -
        so this reads the match `reckonai.reconcile` makes rather than a
        column, and says so in the result.
        """
        if self._is_withheld("payments"):
            return []

        payment_ids = self._payment_ids_for(order_ref)
        if not payment_ids:
            return []

        payments = self._session.scalars(
            select(Payment).where(
                Payment.tenant_id == self._tenant_id, Payment.id.in_(payment_ids)
            )
        )
        return [
            {
                "payment_ref": payment.external_id,
                "amount": _money(payment.amount),
                "status": payment.status,
                "disbursement_mode": payment.disbursement_mode,
                "order_link_basis": (
                    "matched on purchase_units[].reference_id, not a stored column"
                ),
            }
            for payment in payments
        ]

    def fetch_captures(self, order_ref: str) -> list[dict[str, Any]]:
        """Captures belonging to this order's purchase units.

        Scoped per unit, not per payment: one payment may legitimately carry
        units from two orders, and handing an order all of a payment's
        captures would attribute the other order's money to it.
        """
        if self._is_withheld("captures"):
            return []

        return [
            {
                "capture_ref": capture.external_id,
                "seller_id": str(capture.seller_id) if capture.seller_id else None,
                "gross": _money(capture.gross_amount),
                "net": _money(capture.net_amount),
                # Absent, not zero. No capture in the dataset states a
                # `paypal_fee` at all, and specs/mission.md P3 keeps an absent
                # fee and a measured zero different claims.
                "paypal_fee": _money(capture.paypal_fee),
                "disbursement_mode": capture.disbursement_mode,
                "disbursement_state": capture.disbursement_state,
                "status": capture.status,
            }
            for capture in self._capture_rows(order_ref)
        ]

    def fetch_refunds(self, order_ref: str) -> list[dict[str, Any]]:
        """Refunds attributed to this order, from either source.

        `source` and `linkage_is_derived` are both returned because they
        change how strong the evidence is. A PayPal refund reaches its order
        through three parsed hops, and a finding resting on that carries
        `derived_linkage` - which a report must not present as a
        provider-reported fact.
        """
        if self._is_withheld("refunds"):
            return []

        case = self._case_for(order_ref)
        if case is None:
            return []

        # The source split is the case's claim, not a column: both sources
        # write rows to `refund` and are told apart by their payload
        # (`reckonai.models.events`). The fields come from the row.
        source_of = {
            refund.external_id: source
            for source, refunds in (
                ("marketplace", case.marketplace_refunds),
                ("paypal", case.provider_refunds),
            )
            for refund in refunds
        }

        return [
            {
                "refund_ref": refund.external_id,
                "seller_id": str(refund.seller_id) if refund.seller_id else None,
                "amount": _money(refund.amount),
                "source": source_of.get(refund.external_id),
                "linkage_is_derived": refund.capture_linkage_is_derived,
                "capture_linkage_href": refund.capture_linkage_href,
            }
            for refund in self._rows_by_external(
                Refund, Refund.external_id, source_of
            )
        ]

    def fetch_disputes(self, order_ref: str) -> list[dict[str, Any]]:
        """Disputes against this order's captures.

        Worth a tool of its own because the **dispute-versus-refund shape is
        undecidable from the records**: a dispute-backed refund is
        `awaiting_evidence` rather than a guess in either direction. An agent
        explaining that state needs to see the dispute.
        """
        if self._is_withheld("disputes"):
            return []

        case = self._case_for(order_ref)
        capture_ids = {c.id for c in case.captures} if case else set()
        if not capture_ids:
            return []

        disputes = self._session.scalars(
            select(Dispute).where(
                Dispute.tenant_id == self._tenant_id,
                Dispute.capture_id.in_(capture_ids),
            )
        )
        return [
            {
                "dispute_ref": dispute.external_id,
                "amount": _money(dispute.amount),
                "lifecycle_stage": dispute.lifecycle_stage,
                "dispute_state": dispute.dispute_state,
                "status": dispute.status,
                "reason": dispute.reason,
            }
            for dispute in disputes
        ]

    def fetch_platform_fees(self, order_ref: str) -> list[dict[str, Any]]:
        """PayPal's record of the commission it actually took.

        Independent evidence against the marketplace's own rules, which is why
        `platform_fee` is a first-class table rather than incidental.

        **`cite_this_ref` is the reference to cite, and it is not always a
        capture.** `platform_fee.external_id` is rebuilt from a payload UUID
        on every seed: it names this evidence but cannot be used to find it
        again. The stable reference is the fee's parent - the capture for a
        fee PayPal took, the **refund** for one it reversed. `fee_kind` says
        which, because the two must not be added together.
        """
        if self._is_withheld("fees"):
            return []

        case = self._case_for(order_ref)
        if case is None:
            return []

        captures_by_id = {c.id: c.external_id for c in case.captures}
        refunds_by_id = {
            refund.id: refund.external_id
            for refund in (*case.marketplace_refunds, *case.provider_refunds)
        }

        fees: list[dict[str, Any]] = []
        for fee in case.platform_fees:
            # **A capture fee and a refund reversal are different records**,
            # and `reckonai.models.fees` warns why they must stay
            # distinguishable: adding a reversal to a capture fee would double
            # the commission. The parent that is set is what says which it is,
            # and it is also the stable reference to cite - a reversal has no
            # capture to point at.
            if fee.refund_id is not None:
                kind, cite = "refund_reversal", refunds_by_id.get(fee.refund_id)
            elif fee.capture_id is not None:
                kind, cite = "capture_fee", captures_by_id.get(fee.capture_id)
            else:
                kind, cite = "unattached", None

            fees.append(
                {
                    "amount": _money(fee.amount),
                    "seller_id": str(fee.seller_id) if fee.seller_id else None,
                    "fee_kind": kind,
                    "cite_this_ref": cite,
                    "unstable_fee_ref": fee.external_id,
                }
            )

        return fees

    def fetch_seller_ledger_entries(self, order_ref: str) -> list[dict[str, Any]]:
        """The marketplace's own ledger entries for this order.

        **The central evidence for Scenario 1**, and the thing whose *absence*
        is the fault: a refund PayPal performed that the seller ledger does
        not record. An agent has to be able to see that nothing is here.

        `transaction_type` and `entry_category` are both returned because they
        answer different questions, and Block D found that the hard way:
        `_partition_by_ledger` decides what counts as having recorded a
        refund, and reading only `refund` entries there made one correct
        booking produce two findings. `dispute` is a position-moving category
        too.

        The amount is **unsigned** - direction is carried by
        `transaction_type`, not by a minus sign. An agent reading the number
        alone would get the sign of every adjustment wrong.
        """
        if self._is_withheld("ledger"):
            return []

        order = self._session.scalar(
            select(Order).where(
                Order.tenant_id == self._tenant_id, Order.external_ref == order_ref
            )
        )
        if order is None:
            return []

        entries = self._session.scalars(
            select(SellerLedgerEntry)
            .where(
                SellerLedgerEntry.tenant_id == self._tenant_id,
                SellerLedgerEntry.order_id == order.id,
            )
            .order_by(SellerLedgerEntry.occurred_at, SellerLedgerEntry.external_ref)
        )
        return [
            {
                "entry_ref": entry.external_ref,
                "seller_id": str(entry.seller_id) if entry.seller_id else None,
                "transaction_type": entry.transaction_type,
                "entry_category": entry.entry_category,
                "amount_unsigned": _money(entry.amount),
                "occurred_at": entry.occurred_at.isoformat(),
            }
            for entry in entries
        ]

    def fetch_disbursements(self, order_ref: str) -> list[dict[str, Any]]:
        """Releases discharging this order's captures.

        **Both sides of the held-funds question are here.** A capture's
        `disbursement_state` never carries `release_requested` or
        `disbursement_pending` - those exist only on the release row - so an
        agent reading only the capture would report a release in flight as a
        mismatch.

        `released_at` carries no default and may be absent: PayPal's release
        record has no usable timestamp, and writing our own clock there would
        present an ingestion time as a provider timestamp (P3).
        """
        if self._is_withheld("disbursements"):
            return []

        case = self._case_for(order_ref)
        if case is None:
            return []

        return [
            {
                "disbursement_ref": release.external_id,
                "seller_id": str(release.seller_id) if release.seller_id else None,
                "amount": _money(release.amount),
                "state": release.state,
                "provider_status": release.provider_status,
                "released_at": (
                    release.released_at.isoformat() if release.released_at else None
                ),
            }
            for release in self._rows_by_external(
                Disbursement,
                Disbursement.external_id,
                (r.external_id for r in case.disbursements),
            )
        ]

    # ---- Rules ------------------------------------------------------------

    def fetch_commission_rules(self) -> list[dict[str, Any]]:
        """The commission rules in force, so expected commission is explicable.

        `rate` is a fraction, not a percentage - 10% is `0.100000`. Returned as
        a string for the same reason amounts are: it is an exact decimal and a
        JSON float would not be.
        """
        if self._is_withheld("rules"):
            return []

        rules = self._session.scalars(
            select(CommissionRule)
            .where(CommissionRule.tenant_id == self._tenant_id)
            .order_by(CommissionRule.valid_from)
        )
        return [
            {
                "seller_id": str(rule.seller_id) if rule.seller_id else None,
                "category": rule.category,
                "rate": str(rule.rate),
                "fixed": _money(rule.fixed),
                "valid_from": rule.valid_from.isoformat(),
                "valid_to": rule.valid_to.isoformat() if rule.valid_to else None,
            }
            for rule in rules
        ]

    # ---- The retained evidence -------------------------------------------

    def fetch_raw_payload(self, record_ref: str) -> dict[str, Any] | None:
        """The retained source payload behind one record, by its reference.

        **Fetched only when explicitly asked for.** This is the expensive
        tool - a raw payload is the largest thing the agent can pull into
        context - and section 5.2 names repeated payload loading as the real
        cost risk rather than the model.
        """
        if self._is_withheld("payloads"):
            return None

        payload_id = self._payload_id_for(record_ref)
        if payload_id is None:
            return None

        payload = self._session.scalar(
            select(RawPayload).where(
                RawPayload.tenant_id == self._tenant_id, RawPayload.id == payload_id
            )
        )
        if payload is None:
            return None

        return {
            "record_ref": record_ref,
            "source": payload.source,
            "source_record_type": payload.source_record_type,
            "body": payload.body,
        }

    # ---- The engine, as a tool -------------------------------------------

    def recompute(self) -> list[dict[str, Any]]:
        """Every finding for this tenant, from the deterministic engine.

        This is `reckonai.reconcile.run` - the same code Block D's scheduled
        run calls - so the agent cannot produce a number the engine would not
        produce (specs/mission.md P1). It returns **every** finding regardless
        of any window, so the agent is not limited to what a scheduled run
        covered.

        **Never withheld.** It returns verdicts rather than evidence, and
        suppressing it would test whether the agent can work without the
        engine, which is not what the evidence gap is for.
        """
        return [finding.as_dict() for finding in run(self._session, self._tenant_id)]

    def recompute_exceptions(self) -> list[dict[str, Any]]:
        """Only the findings that are genuine discrepancies.

        Counted through `exceptions_only()`, never `len(findings)`:
        `awaiting_evidence` is a finding and not an exception, so the raw count
        would present a lifecycle state as something wrong.
        """
        return [
            finding.as_dict()
            for finding in exceptions(self._session, self._tenant_id)
        ]

    # ---- Internals --------------------------------------------------------

    @cached_property
    def _cases(self) -> dict[str, OrderCase]:
        """Every assembled `OrderCase` for this tenant, by order reference.

        Reuses `reckonai.reconcile.load_order_cases` rather than re-deriving
        the cross-source linkages. Three of those linkages are claims made in
        that module and not columns, and a fourth was added after Block D's
        review; a second implementation here could disagree with the
        classifier about which refund belongs to which order - two answers to
        a question that must have one.

        **Cached per instance, and that is not an optimisation to skip.**
        `load_order_cases` reads every order, payload, capture, refund, fee
        and ledger entry for the tenant. An agent makes many tool calls per
        investigation, and rebuilding all of that per call would make
        retrieval cost grow with the model's curiosity. One investigation sees
        one consistent snapshot, which is also the more defensible answer for
        an audit: two tools cannot disagree because the records moved between
        them.
        """
        cases, _, _ = load_order_cases(self._session, self._tenant_id)
        return {case.order_ref: case for case in cases}

    def _case_for(self, order_ref: str) -> OrderCase | None:
        return self._cases.get(order_ref)

    def _rows_by_external(
        self,
        model: type[_RecordT],
        column: InstrumentedAttribute[str],
        refs: Iterable[str],
    ) -> tuple[_RecordT, ...]:
        """The ORM rows whose external reference is in `refs`.

        **Why this exists rather than reading the `OrderCase` directly.** The
        case holds `typing.Protocol` views, which expose exactly what the
        classifier needs and no more - a capture's protocol has no
        `disbursement_state`, a release's has no `released_at`. Those fields
        are genuinely useful to an investigation, so the linkage comes from
        the case (which order a record belongs to, a claim `reconcile.py`
        owns) and the fields come from the row.

        The alternative was widening the protocols so the agent could read
        them, which would add attributes to the pure package for a consumer
        that is not the classifier.
        """
        references = set(refs)
        if not references:
            return ()

        return tuple(
            self._session.scalars(
                select(model).where(
                    model.tenant_id == self._tenant_id, column.in_(references)
                )
            )
        )

    def _capture_rows(self, order_ref: str) -> tuple[Capture, ...]:
        case = self._case_for(order_ref)
        if case is None:
            return ()
        return self._rows_by_external(
            Capture, Capture.external_id, (c.external_id for c in case.captures)
        )

    def _payment_ids_for(self, order_ref: str) -> set[uuid.UUID]:
        return {
            capture.payment_id
            for capture in self._capture_rows(order_ref)
            if capture.payment_id is not None
        }

    def _payload_id_for(self, record_ref: str) -> uuid.UUID | None:
        """Which retained payload belongs to the record with this reference.

        Searched across the record types that carry one. A reference naming no
        record returns None rather than raising, so a fabricated citation
        produces an honest "nothing here" instead of an error the model would
        try to work around.
        """
        sources: tuple[
            tuple[InstrumentedAttribute[uuid.UUID], InstrumentedAttribute[str]], ...
        ] = (
            (Capture.raw_payload_id, Capture.external_id),
            (Refund.raw_payload_id, Refund.external_id),
            (Payment.raw_payload_id, Payment.external_id),
            (Disbursement.raw_payload_id, Disbursement.external_id),
            (Dispute.raw_payload_id, Dispute.external_id),
            (SellerLedgerEntry.raw_payload_id, SellerLedgerEntry.external_ref),
        )

        for payload_column, reference_column in sources:
            found = self._session.scalar(
                select(payload_column).where(
                    reference_column.class_.tenant_id == self._tenant_id,
                    reference_column == record_ref,
                )
            )
            if found is not None:
                return found

        return None
