"""What a provider must return, and the audit facts it must carry back.

`requirements.md` D12: two providers, one report. This module is the contract
between them. The report shape is in `reckonai.agent.report`; what is here is
everything *about* the call that specs/tech-stack.md section 5.2 requires to be
persisted and compared.

**Every field below exists because an audit constraint names it**, not because
it was available:

- `model_returned` is compared against the model requested, so a swap is
  disclosed rather than silent.
- `stop_reason` is read **before** any response content, so a refusal is never
  processed as a report.
- `summarized_reasoning` is the recorded reasoning, stored as supporting
  context and never as the authoritative cause - the cited records are the
  authority.

`report` is `None` wherever no conclusion was reached. That is the only way a
refusal is representable without inventing a report to hold the refusal, which
would be exactly the silent-downgrade failure section 5.2 forbids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from reckonai.agent.registry import ToolSpec
from reckonai.agent.report import InvestigationReport


class ProviderError(RuntimeError):
    """The provider could not be called at all.

    Distinct from a refusal, which is a 200 with a stop reason and is a
    *result*. This is a transport or configuration failure, and the
    investigation is recorded as failed with no stop reason - we observed no
    response, so claiming one would be asserting something that did not happen.
    """


@dataclass(frozen=True)
class ProviderResult:
    """One completed call to one provider."""

    #: `None` where no conclusion was reached - a refusal, or a response that
    #: carried no parseable report.
    report: InvestigationReport | None

    #: Which provider served it, as `reckonai.models.investigation`
    #: spells it.
    provider: str

    #: What we asked for.
    model_requested: str

    #: What the response says served it. `None` where no response arrived.
    model_returned: str | None

    #: The provider's own stop reason, whatever it calls it. `None` where no
    #: response arrived.
    stop_reason: str | None

    #: The summarized reasoning, where the provider returns any.
    summarized_reasoning: str | None

    #: How many turns the loop took. Not an audit requirement - a cost signal,
    #: since section 5.2 puts the exposure in evidence volume and this is the
    #: cheapest proxy for it.
    turns: int = 0

    @property
    def downgraded(self) -> bool:
        """Whether a **different** model served this than was requested.

        Nothing in the MVP should make this true: neither provider has a
        refusal fallback and there is none between them. It is computed rather
        than assumed so that the claim is *proved* by the stored comparison
        instead of being asserted by this comment.

        **An alias resolving to its own dated snapshot is not a downgrade**,
        and a plain inequality said it was. Found on the first live run: a
        request for `gpt-4.1` is served by `gpt-4.1-2025-04-14`, and the
        report was stored flagged as downgraded. That is the same model named
        more precisely, and §5.2's flag exists to tell a finance team their
        reasoning came from a weaker model - a flag that fires on every
        OpenAI response would make the audit trail noise rather than
        evidence, which is the same failure as it never firing.

        **A bare prefix test is not enough**, and code review caught the
        false negative it introduced: `gpt-4.1` served by `gpt-4.1-mini`
        satisfies `startswith("gpt-4.1")` and so would not have been flagged.
        That is a genuine downgrade - the weaker variant extends the stronger
        id - and it is the silent kind §5.2 calls worse than a failed
        investigation, in the one column that exists to prove it cannot
        happen.

        So the extension has to look like a **dated snapshot** rather than
        like anything at all: a hyphen followed by an ISO date, which is how
        both providers name a pinned version (`gpt-4.1-2025-04-14`,
        `claude-haiku-4-5-20251001`). Any other difference is a swap.
        """
        if self.model_returned is None:
            return False
        if self.model_returned == self.model_requested:
            return False

        suffix = self.model_returned.removeprefix(self.model_requested)
        if suffix == self.model_returned:
            # Not an extension of the requested id at all.
            return True

        return _DATE_SUFFIX.fullmatch(suffix) is None

    @property
    def refused(self) -> bool:
        """Whether the provider declined to answer.

        A refusal arrives as a successful HTTP response, so this is read
        before any content. It becomes `verdict: failed` - no report, no cause
        list, nothing presented as a conclusion.
        """
        return self.stop_reason in _REFUSAL_STOP_REASONS


#: A dated-snapshot suffix, as both providers spell one: a hyphen then an ISO
#: date, with or without separators (`-2025-04-14`, `-20251001`).
#:
#: Narrow on purpose. This is the only difference between a requested model id
#: and a returned one that is **not** a downgrade, so anything this does not
#: match is treated as a model swap and flagged.
_DATE_SUFFIX = re.compile(r"-\d{4}-?\d{2}-?\d{2}")

#: The stop reason recorded when a tool loop hits its iteration ceiling.
#:
#: **One spelling for both providers.** The OpenAI loop detects this itself;
#: the Anthropic runner simply stops and returns a message whose own
#: `stop_reason` is "tool_use" with nothing parsed, so that path normalises to
#: this too. Block G compares this column across providers, and one outcome
#: spelled two ways would read as a difference between the models rather than
#: between the adapters. Found by code review.
ITERATION_CEILING = "max_iterations"

#: Stop reasons that mean the provider declined, across both providers.
#:
#: Anthropic returns `refusal`. OpenAI's equivalents are a `content_filter`
#: finish reason and an incomplete response whose reason is `content_filter`.
#: Collected in one place so `refused` is a single rule rather than a
#: per-provider branch that could disagree with itself.
_REFUSAL_STOP_REASONS: frozenset[str] = frozenset(
    {"refusal", "content_filter"}
)


class Provider(Protocol):
    """One way of running an investigation.

    Deliberately one method. Everything that differs between providers -
    building tool schemas, driving the loop, requesting a structured report,
    reading a stop reason - happens behind it, and nothing above it needs to
    know which provider ran.
    """

    name: str

    def investigate(
        self,
        *,
        system: str,
        user: str,
        tools: tuple[ToolSpec, ...],
        model: str,
    ) -> ProviderResult:
        """Run one investigation to completion.

        Must read the stop reason before any response content, and must never
        raise on a refusal - a refusal is a `ProviderResult` with no report.
        `ProviderError` is for a call that did not happen.
        """
        ...
