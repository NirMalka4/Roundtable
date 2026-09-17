"""usage-map: SDK ``assistant.usage`` events → :class:`AgentUsage`.

Contract subtlety (see ``run_loop._merge_attempt_usage``):
the run loop sums ``output_tokens``/``input_tokens``/``cache_*``/``reasoning_tokens``
and ``rounds`` across attempts **per-turn**, but treats SDK billing,
``duration_ms``, and ``session_duration_ms`` as **session-cumulative** and subtracts
the running total on a resumed turn. Billing is already cumulative in the bridge's
``session.rpc.usage.get_metrics()`` snapshot. This module re-creates only the
cumulative duration view from per-turn events.

All usage is advisory/observability, never a parity or gate target. The event-level
``cost`` is deliberately ignored. ``duration`` is a ``timedelta`` (verified against
the real SDK type), summed into ``duration_ms``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..usage import NOT_APPLICABLE_BILLING, AgentUsage, BillingValue
from .events import event_data, is_type

_ASSISTANT_USAGE = "assistant.usage"


@dataclass(frozen=True)
class TurnUsage:
    """Per-turn usage extracted from one turn's ``assistant.usage`` events."""

    output_tokens: int = 0
    rounds: int = 0  # one assistant.usage event == one model API call
    api_duration_ms: float = 0.0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    finish_reasons: tuple[str, ...] = ()
    models: tuple[str, ...] = ()


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _duration_ms(value: object) -> float:
    """Milliseconds from an ``assistant.usage`` ``duration`` (a ``timedelta``).

    The real SDK type is ``timedelta`` — ``float(timedelta)`` raises, which would
    silently zero the field (m03). Numeric values are tolerated for synthetic tests.
    """
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        return as_float(total_seconds()) * 1000.0
    return as_float(value)


def extract_turn_usage(events: list[object]) -> TurnUsage:
    """Sum a single turn's ``assistant.usage`` events into a :class:`TurnUsage`."""
    output = rounds = 0
    inp = cache_read = cache_write = reasoning = 0
    duration = 0.0
    finishes: list[str] = []
    models: list[str] = []
    for event in events:
        if not is_type(event, _ASSISTANT_USAGE):
            continue
        data = event_data(event)
        rounds += 1
        output += _as_int(getattr(data, "output_tokens", 0))
        inp += _as_int(getattr(data, "input_tokens", 0))
        cache_read += _as_int(getattr(data, "cache_read_tokens", 0))
        cache_write += _as_int(getattr(data, "cache_write_tokens", 0))
        reasoning += _as_int(getattr(data, "reasoning_tokens", 0))
        duration += _duration_ms(getattr(data, "duration", None))
        reason = getattr(data, "finish_reason", None)
        if isinstance(reason, str) and reason and reason not in finishes:
            finishes.append(reason)
        served = getattr(data, "model", None)
        if isinstance(served, str) and served and served not in models:
            models.append(served)
    return TurnUsage(
        output_tokens=output,
        rounds=rounds,
        api_duration_ms=duration,
        input_tokens=inp,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        finish_reasons=tuple(finishes),
        models=tuple(models),
    )


class SessionUsageAccumulator:
    """Turn a stream of per-turn usages into the loop's session-cumulative view.

    One instance per review (held in the runner closure). ``record`` returns the
    :class:`AgentUsage` for this turn: per-turn ``output_tokens``/``rounds`` plus
    session-cumulative billing, ``duration_ms``, and ``session_duration_ms``.
    """

    def __init__(self) -> None:
        self._api_ms: dict[str, float] = {}
        self._wall_ms: dict[str, float] = {}

    def record(
        self,
        session_id: str,
        turn: TurnUsage,
        wall_clock_s: float,
        billing: BillingValue = NOT_APPLICABLE_BILLING,
    ) -> AgentUsage:
        cum_api = self._api_ms[session_id] = (
            self._api_ms.get(session_id, 0.0) + turn.api_duration_ms
        )
        cum_wall = self._wall_ms[session_id] = (
            self._wall_ms.get(session_id, 0.0) + wall_clock_s * 1000.0
        )
        return AgentUsage(
            output_tokens=turn.output_tokens,
            rounds=turn.rounds,
            duration_ms=cum_api,
            session_duration_ms=cum_wall,
            input_tokens=turn.input_tokens,
            cache_read_tokens=turn.cache_read_tokens,
            cache_write_tokens=turn.cache_write_tokens,
            reasoning_tokens=turn.reasoning_tokens,
            finish_reasons=turn.finish_reasons,
            observed_models=turn.models,
            billing=billing,
        )
