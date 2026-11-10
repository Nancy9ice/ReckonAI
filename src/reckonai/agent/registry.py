"""One description of the tool surface, adapted to each provider.

The tools live in `reckonai.agent.tools` as ordinary methods. This module says
what the *model* sees: a name, a description, and the parameters - which are
every one a string reference.

**Why a registry rather than decorated functions.** The Anthropic path wants
`@beta_tool` callables and the OpenAI path wants JSON function specs plus a
dispatch table. Written twice, the two would drift: a tool described one way
to one provider and another way to the other makes Block G's comparison
measure the prompt rather than the models. Written once, the schema both
providers receive is provably the same object.

**Every parameter is a string, and that is the invariant rather than a
convenience.** specs/mission.md P1 forbids the LLM from doing arithmetic that
reaches a financial conclusion, and the structural enforcement is that no tool
accepts a number. Because every parameter is a record reference, the JSON
schema is generated from a single shape here and
`tests/test_agent_registry.py` asserts no property is ever anything but a
string.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from reckonai.agent.tools import InvestigationTools, Withheld


@dataclass(frozen=True)
class ToolSpec:
    """One tool, as the model sees it and as we dispatch it."""

    name: str
    description: str

    #: Parameter name to its description. Every parameter is a required
    #: string; there is deliberately no way to express an optional one or a
    #: numeric one.
    parameters: dict[str, str]

    #: What to run. Called with the parameters as keyword arguments.
    call: Callable[..., Any]

    def input_schema(self) -> dict[str, Any]:
        """The JSON Schema both providers receive.

        `additionalProperties: false` with every property required is what
        strict tool use needs on either side, and it is what makes a
        hallucinated argument a schema error rather than a silently ignored
        key.
        """
        return {
            "type": "object",
            "properties": {
                name: {"type": "string", "description": description}
                for name, description in self.parameters.items()
            },
            "required": sorted(self.parameters),
            "additionalProperties": False,
        }

    def run_as_json(self, **arguments: Any) -> str:
        """Run the tool and serialize the result for the model.

        JSON text rather than a Python object, because that is what crosses to
        both providers identically. `default=str` is a backstop for a value
        that is not natively JSON - it should never fire, since the tools
        already stringify amounts and identifiers, and a `Decimal` reaching
        here would be a tool returning a bare number.

        **A withheld record kind becomes an explicit marker**, never an empty
        result. Returning `[]` made "you may not see this" identical to "this
        does not exist", and the live run proving exit criterion 3 showed the
        agent reading it the second way - asserting at full confidence that no
        ledger entries existed for a refund whose entries it had been denied.
        The marker says plainly that the records are unavailable, so the model
        can reach `insufficient_evidence` rather than inventing the stronger
        claim.
        """
        try:
            result = self.call(**arguments)
        except Withheld as withheld:
            return json.dumps(
                {
                    "unavailable": True,
                    "record_kind": withheld.kind,
                    "note": (
                        f"The {withheld.kind} records are NOT AVAILABLE for "
                        "this investigation. This does NOT mean none exist - "
                        "it means you cannot see them. You must not conclude "
                        "anything about whether such records exist. If your "
                        "explanation depends on them, return the verdict "
                        "insufficient_evidence."
                    ),
                }
            )
        return json.dumps(result, default=str)


def tool_specs(tools: InvestigationTools) -> tuple[ToolSpec, ...]:
    """The whole surface, bound to one `InvestigationTools` instance.

    Order is fixed and alphabetical-by-purpose rather than arbitrary, because
    the tool list is part of the cached prompt prefix: specs/tech-stack.md
    section 5.1 caches the tool definitions, and caching is a prefix match, so
    an unstable ordering would silently cost the cache on every call.
    """
    order_ref = "The marketplace order reference, for example ORD-0013."

    return (
        ToolSpec(
            name="recompute",
            description=(
                "Re-run the deterministic reconciliation engine and return "
                "every finding for this tenant, including the non-exceptions. "
                "This is the only source of financial figures: never calculate "
                "an amount yourself. Call this first."
            ),
            parameters={},
            call=tools.recompute,
        ),
        ToolSpec(
            name="recompute_exceptions",
            description=(
                "The subset of findings that are genuine discrepancies. "
                "'awaiting_evidence' is a finding but not an exception - it "
                "means a counterpart is late, not that anything is wrong."
            ),
            parameters={},
            call=tools.recompute_exceptions,
        ),
        ToolSpec(
            name="fetch_order",
            description="The order: its reference, date, total and disbursement mode.",
            parameters={"order_ref": order_ref},
            call=tools.fetch_order,
        ),
        ToolSpec(
            name="fetch_order_items",
            description=(
                "The lines on an order, each with its seller and its own line "
                "total."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_order_items,
        ),
        ToolSpec(
            name="fetch_sellers",
            description=(
                "Every seller for this tenant, so a seller id in a finding can "
                "be named."
            ),
            parameters={},
            call=tools.fetch_sellers,
        ),
        ToolSpec(
            name="fetch_payments",
            description=(
                "PayPal payments matched to this order. The order-to-payment "
                "link is a match on the purchase unit's reference_id, not a "
                "field PayPal stated."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_payments,
        ),
        ToolSpec(
            name="fetch_captures",
            description=(
                "Captures for this order's purchase units, with gross, net and "
                "disbursement state. A null paypal_fee means PayPal stated no "
                "fee - it does not mean zero."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_captures,
        ),
        ToolSpec(
            name="fetch_refunds",
            description=(
                "Refunds attributed to this order, from the marketplace or from "
                "PayPal. Check linkage_is_derived: a derived link was parsed "
                "from a HATEOAS link rather than stated, and a cause resting on "
                "it must say so."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_refunds,
        ),
        ToolSpec(
            name="fetch_disputes",
            description=(
                "Disputes against this order's captures. Whether a refund was "
                "dispute-backed cannot be decided from the records, so a "
                "dispute-backed refund is 'awaiting_evidence' rather than a "
                "guess in either direction."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_disputes,
        ),
        ToolSpec(
            name="fetch_platform_fees",
            description=(
                "PayPal's own record of the commission it took, which is "
                "independent evidence against the marketplace's rules. Cite "
                "cite_this_ref, never unstable_fee_ref. fee_kind distinguishes "
                "a fee PayPal took from one it reversed; never add the two."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_platform_fees,
        ),
        ToolSpec(
            name="fetch_seller_ledger_entries",
            description=(
                "The marketplace's own ledger entries for this order. An "
                "absent entry is what a missing adjustment looks like."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_seller_ledger_entries,
        ),
        ToolSpec(
            name="fetch_disbursements",
            description=(
                "Releases discharging this order's captures. A release in "
                "flight is a healthy intermediate state, not an unpaid "
                "obligation: its state lives here and never on the capture."
            ),
            parameters={"order_ref": order_ref},
            call=tools.fetch_disbursements,
        ),
        ToolSpec(
            name="fetch_commission_rules",
            description=(
                "The commission rules in force. The rate is a fraction, so 10 "
                "percent is 0.100000."
            ),
            parameters={},
            call=tools.fetch_commission_rules,
        ),
        ToolSpec(
            name="fetch_raw_payload",
            description=(
                "The retained source payload behind one record, by its "
                "reference. Expensive - use it only when a field you need is "
                "not on another tool's result."
            ),
            parameters={
                "record_ref": (
                    "A record reference returned by another tool, such as a "
                    "capture, refund, payment or ledger entry reference."
                )
            },
            call=tools.fetch_raw_payload,
        ),
    )
