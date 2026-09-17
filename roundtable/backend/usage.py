"""Usage facts from SDK events plus SDK session billing metrics.

Each model API call emits an ``assistant.usage`` event carrying ``output_tokens``,
``input_tokens``, ``cache_read_tokens`` / ``cache_write_tokens`` (the cached vs newly
computed portions of the input), ``reasoning_tokens``, and a ``duration``.
:func:`extract_turn_usage` reduces those into token and timing observations.
Billing comes only from the separate SDK session usage RPC.

Token relationships: ``cache_read_tokens`` is the cached subset of ``input_tokens``
(uncached input = ``input_tokens - cache_read_tokens``); ``reasoning_tokens`` is the
reasoning subset of ``output_tokens``; ``total_tokens`` = input + output.

Each ``assistant.usage`` event also carries a ``finish_reason`` (why the model stopped
that API call). :func:`anomalous_finish_reasons` keeps only the *non-benign* ones —
``length`` (the response was truncated) or ``content_filter`` — which directly explain
an empty/malformed agent output that trips OVG. Benign reasons (``stop``/``tool_calls``)
are dropped so a healthy run surfaces nothing.

Token counts are advisory/observability — deterministically derived from the run
but NEVER a parity or gate target (the upstream model/cache behaviour is
non-deterministic).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

#: Finish reasons that mean the turn completed normally — never surfaced as an
#: anomaly. Everything else (``length``, ``content_filter``, or any unknown value)
#: is kept, since surfacing an unexpected stop reason is safer than hiding it.
_BENIGN_FINISH_REASONS = frozenset({"stop", "tool_calls", "tool_call"})

SDK_BILLING_SOURCE = "copilot-sdk/session.usage.getMetrics"
SDK_BILLING_WARNING = "sdk_usage_metrics_unavailable"
NANO_AIU_PER_AI_CREDIT = Decimal("1000000000")


class BillingStatus(StrEnum):
    """Completeness of an SDK billing observation."""

    NOT_APPLICABLE = "not_applicable"
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BillingValue:
    """Raw SDK session billing values with explicit provenance and completeness."""

    source: str = SDK_BILLING_SOURCE
    status: BillingStatus = BillingStatus.NOT_APPLICABLE
    total_nano_aiu: float | None = None
    total_premium_request_cost: float | None = None
    warning_code: str | None = None

    @classmethod
    def complete(
        cls,
        total_nano_aiu: float,
        total_premium_request_cost: float | None,
    ) -> BillingValue:
        return cls(
            status=BillingStatus.COMPLETE,
            total_nano_aiu=total_nano_aiu,
            total_premium_request_cost=total_premium_request_cost,
        )

    @classmethod
    def unavailable(
        cls,
        total_premium_request_cost: float | None = None,
    ) -> BillingValue:
        return cls(
            status=BillingStatus.UNAVAILABLE,
            total_premium_request_cost=total_premium_request_cost,
            warning_code=SDK_BILLING_WARNING,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> BillingValue:
        """Load the explicit billing wire shape; legacy cost fields are ignored."""
        if not isinstance(value, Mapping):
            return NOT_APPLICABLE_BILLING
        premium = _optional_number(value.get("totalPremiumRequestCost"))
        if value.get("source") != SDK_BILLING_SOURCE:
            return BillingValue.unavailable(premium)
        status = value.get("status")
        nano = _optional_number(value.get("totalNanoAiu"))
        if status == BillingStatus.COMPLETE.value and nano is not None:
            return BillingValue.complete(nano, premium)
        if status == BillingStatus.UNAVAILABLE.value:
            return BillingValue.unavailable(premium)
        return NOT_APPLICABLE_BILLING

    @property
    def is_complete(self) -> bool:
        return self.status is BillingStatus.COMPLETE and self.total_nano_aiu is not None

    @property
    def is_unavailable(self) -> bool:
        return self.status is BillingStatus.UNAVAILABLE

    @property
    def is_not_applicable(self) -> bool:
        return self.status is BillingStatus.NOT_APPLICABLE

    def merge(self, other: BillingValue) -> BillingValue:
        """Aggregate raw values without converting or rounding."""
        if self.is_not_applicable:
            return other
        if other.is_not_applicable:
            return self
        premium = _sum_optional(
            self.total_premium_request_cost,
            other.total_premium_request_cost,
        )
        if self.is_unavailable or other.is_unavailable:
            return BillingValue.unavailable(premium)
        assert self.total_nano_aiu is not None and other.total_nano_aiu is not None
        return BillingValue.complete(self.total_nano_aiu + other.total_nano_aiu, premium)

    def delta(self, previous: BillingValue) -> BillingValue:
        """Return a resumed session's newly incurred raw billing contribution."""
        premium = _difference_optional(
            self.total_premium_request_cost,
            previous.total_premium_request_cost,
        )
        if not self.is_complete or not previous.is_complete:
            return BillingValue.unavailable(premium)
        assert self.total_nano_aiu is not None and previous.total_nano_aiu is not None
        nano = self.total_nano_aiu - previous.total_nano_aiu
        if nano < 0 or _is_negative(premium):
            return BillingValue.unavailable()
        return BillingValue.complete(nano, premium)

    def to_dict(self) -> dict[str, Any]:
        """Exact SDK fields and provenance for machine-readable artifacts."""
        out: dict[str, Any] = {"source": self.source, "status": self.status.value}
        if self.is_complete:
            out["totalNanoAiu"] = self.total_nano_aiu
        if self.total_premium_request_cost is not None:
            out["totalPremiumRequestCost"] = self.total_premium_request_cost
        if self.warning_code:
            out["warningCode"] = self.warning_code
        return out


NOT_APPLICABLE_BILLING = BillingValue()


def format_ai_credits(total_nano_aiu: float) -> str:
    """Convert one fully aggregated nano-AIU value for human presentation."""
    credits = Decimal(str(total_nano_aiu)) / NANO_AIU_PER_AI_CREDIT
    rendered = format(credits, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _sum_optional(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left + right


def _difference_optional(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


def _is_negative(value: float | None) -> bool:
    return value is not None and value < 0


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def anomalous_finish_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    """Distinct non-benign finish reasons, in first-seen order (empty when healthy)."""
    out: list[str] = []
    for reason in reasons:
        if reason and reason.lower() not in _BENIGN_FINISH_REASONS and reason not in out:
            out.append(reason)
    return tuple(out)


@dataclass(frozen=True)
class AgentUsage:
    """Token, timing, and billing facts for one or more LLM round-trips.

    All numeric fields are additive across rounds and across backend attempts, so
    :func:`merge` sums them. Billing has its own completeness-aware merge rules.
    ``rounds`` counts LLM calls, which can exceed ``attempts`` when an agent makes
    several tool-using turns within a single subprocess.
    """

    output_tokens: int = 0
    rounds: int = 0
    duration_ms: float = 0.0
    session_duration_ms: float = 0.0
    """End-to-end session wall time this turn, measured by the bridge (``time.monotonic``
    around ``send_and_wait``) — NOT reported by the SDK. Distinct from ``duration_ms``,
    the model round-trip time summed from ``assistant.usage.duration``. Both are treated
    as session-cumulative on resumed turns, so the run loop contributes only the per-turn
    delta before merging (see ``_merge_attempt_usage``)."""
    input_tokens: int = 0
    """Prompt/input tokens (SDK ``assistant.usage.input_tokens``). Per-turn, summed
    across rounds — like ``output_tokens``, not session-cumulative."""
    cache_read_tokens: int = 0
    """Cached subset of ``input_tokens`` served from the prompt cache."""
    cache_write_tokens: int = 0
    """Input tokens written to the prompt cache this turn."""
    reasoning_tokens: int = 0
    """Reasoning subset of ``output_tokens`` (reasoning models only)."""
    finish_reasons: tuple[str, ...] = ()
    """Distinct ``assistant.usage.finish_reason`` values seen this turn, in first-seen
    order. Diagnostic only; :func:`anomalous_finish_reasons` filters the benign ones."""
    observed_models: tuple[str, ...] = ()
    """Distinct models the runtime actually billed this turn, in first-seen order
    (``assistant.usage.model``). The declared model is what we *asked* for; this is
    what we *got*. Keeping both is what lets a silent reroute be detected — the
    runtime substitutes a model rather than refusing one it cannot honour."""
    billing: BillingValue = NOT_APPLICABLE_BILLING
    """Session billing reported by ``session.rpc.usage.get_metrics()``."""

    @property
    def total_tokens(self) -> int:
        """Input + output tokens (the headline per-agent consumption figure)."""
        return self.input_tokens + self.output_tokens

    @property
    def is_empty(self) -> bool:
        """True when neither model usage nor SDK billing was measured."""
        return self.rounds == 0 and self.output_tokens == 0 and self.billing.is_not_applicable

    def merge(self, other: AgentUsage) -> AgentUsage:
        """Return the additive combination of ``self`` and ``other``.

        Numeric fields sum; ``finish_reasons`` and ``observed_models`` union in
        first-seen order.
        """
        return AgentUsage(
            output_tokens=self.output_tokens + other.output_tokens,
            rounds=self.rounds + other.rounds,
            duration_ms=self.duration_ms + other.duration_ms,
            session_duration_ms=self.session_duration_ms + other.session_duration_ms,
            input_tokens=self.input_tokens + other.input_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            finish_reasons=_union(self.finish_reasons, other.finish_reasons),
            observed_models=_union(self.observed_models, other.observed_models),
            billing=self.billing.merge(other.billing),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form for ``trace.json`` (camelCase keys)."""
        d: dict[str, Any] = {
            "outputTokens": self.output_tokens,
            "rounds": self.rounds,
            "durationMs": round(self.duration_ms, 1),
            "billing": self.billing.to_dict(),
        }
        # Omitted when unmeasured so runs that never surfaced a session duration
        # keep trace.json byte-stable.
        if self.session_duration_ms:
            d["sessionDurationMs"] = round(self.session_duration_ms, 1)
        # Token breakdown is likewise omitted when zero, so backends/runs that do
        # not surface input/cache detail (e.g. the mock runner) stay byte-stable.
        d.update(self._token_breakdown())
        # Surface only stop reasons that explain a failure (truncation / content
        # filter); benign runs emit nothing, keeping trace.json byte-stable.
        anomalies = anomalous_finish_reasons(self.finish_reasons)
        if anomalies:
            d["anomalousFinishReasons"] = list(anomalies)
        return d

    def _token_breakdown(self) -> dict[str, int]:
        """Optional per-token-category keys, each omitted when zero-valued."""
        out: dict[str, int] = {}
        if self.input_tokens:
            out["inputTokens"] = self.input_tokens
            out["totalTokens"] = self.total_tokens
        for key, val in (
            ("cacheReadTokens", self.cache_read_tokens),
            ("cacheWriteTokens", self.cache_write_tokens),
            ("reasoningTokens", self.reasoning_tokens),
        ):
            if val:
                out[key] = val
        return out


EMPTY_USAGE = AgentUsage()


def _union(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    """Order-preserving union of two finish-reason tuples."""
    out = list(a)
    for reason in b:
        if reason not in out:
            out.append(reason)
    return tuple(out)


def merge_usage(usages: Iterable[AgentUsage]) -> AgentUsage:
    """Sum a sequence of :class:`AgentUsage` across attempts or agents."""
    acc = EMPTY_USAGE
    for u in usages:
        acc = acc.merge(u)
    return acc


def as_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
