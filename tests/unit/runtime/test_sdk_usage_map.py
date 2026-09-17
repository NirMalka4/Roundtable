"""Unit tests for the SDK usage mapper + session-cumulative accumulator."""

from __future__ import annotations

from types import SimpleNamespace

from roundtable.backend import BillingValue
from roundtable.backend.sdk.usage_map import (
    SessionUsageAccumulator,
    TurnUsage,
    extract_turn_usage,
)


def _usage(
    output_tokens=0,
    cost=0.0,
    duration=0.0,
    input_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    reasoning_tokens=0,
    finish_reason=None,
):
    return SimpleNamespace(
        type="assistant.usage",
        data=SimpleNamespace(
            output_tokens=output_tokens,
            cost=cost,
            duration=duration,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            finish_reason=finish_reason,
        ),
    )


def test_extract_turn_usage_sums_events_but_ignores_event_cost() -> None:
    events = [
        _usage(output_tokens=10, cost=1.0, duration=100.0),
        _usage(output_tokens=5, cost=1.0, duration=50.0),
        SimpleNamespace(type="assistant.message", data=SimpleNamespace(content="x")),
    ]
    turn = extract_turn_usage(events)
    assert turn == TurnUsage(output_tokens=15, rounds=2, api_duration_ms=150.0)


def test_extract_turn_usage_sums_input_and_cache_tokens() -> None:
    events = [
        _usage(output_tokens=10, input_tokens=100, cache_read_tokens=40, reasoning_tokens=3),
        _usage(output_tokens=5, input_tokens=20, cache_read_tokens=10, cache_write_tokens=8),
    ]
    turn = extract_turn_usage(events)
    assert turn.input_tokens == 120
    assert turn.cache_read_tokens == 50
    assert turn.cache_write_tokens == 8
    assert turn.reasoning_tokens == 3


def test_accumulator_forwards_input_and_cache_tokens_per_turn() -> None:
    acc = SessionUsageAccumulator()
    usage = acc.record(
        "s1",
        TurnUsage(output_tokens=10, input_tokens=200, cache_read_tokens=80, reasoning_tokens=4),
        wall_clock_s=1.0,
    )
    assert usage.input_tokens == 200
    assert usage.cache_read_tokens == 80
    assert usage.reasoning_tokens == 4
    assert usage.total_tokens == 210  # input + output


def test_extract_turn_usage_empty() -> None:
    assert extract_turn_usage([]) == TurnUsage()


def test_extract_turn_usage_collects_distinct_finish_reasons() -> None:
    events = [
        _usage(output_tokens=5, finish_reason="tool_calls"),
        _usage(output_tokens=5, finish_reason="tool_calls"),
        _usage(output_tokens=5, finish_reason="length"),
    ]
    turn = extract_turn_usage(events)
    assert turn.finish_reasons == ("tool_calls", "length")


def test_accumulator_forwards_finish_reasons() -> None:
    acc = SessionUsageAccumulator()
    usage = acc.record(
        "s1", TurnUsage(output_tokens=3, finish_reasons=("length",)), wall_clock_s=1.0
    )
    assert usage.finish_reasons == ("length",)


def test_accumulator_first_turn_is_turn_value() -> None:
    acc = SessionUsageAccumulator()
    billing = BillingValue.complete(1_000_000_000.0, 1.0)
    usage = acc.record(
        "s1",
        TurnUsage(output_tokens=10, rounds=2),
        wall_clock_s=2.0,
        billing=billing,
    )
    assert usage.output_tokens == 10
    assert usage.rounds == 2
    assert usage.billing == billing
    assert usage.session_duration_ms == 2000.0


def test_accumulator_forwards_cumulative_billing_and_accumulates_duration() -> None:
    acc = SessionUsageAccumulator()
    acc.record("s1", TurnUsage(api_duration_ms=100.0), wall_clock_s=1.0)
    billing = BillingValue.complete(2_000_000_000.0, 2.0)
    second = acc.record(
        "s1",
        TurnUsage(output_tokens=3, rounds=1, api_duration_ms=50.0),
        wall_clock_s=1.0,
        billing=billing,
    )
    assert second.billing == billing
    assert second.duration_ms == 150.0
    assert second.session_duration_ms == 2000.0
    assert second.output_tokens == 3
    assert second.rounds == 1


def test_accumulator_keys_by_session() -> None:
    acc = SessionUsageAccumulator()
    acc.record("s1", TurnUsage(api_duration_ms=100.0), wall_clock_s=1.0)
    other = acc.record("s2", TurnUsage(api_duration_ms=50.0), wall_clock_s=1.0)
    assert other.duration_ms == 50.0
    assert other.session_duration_ms == 1000.0
