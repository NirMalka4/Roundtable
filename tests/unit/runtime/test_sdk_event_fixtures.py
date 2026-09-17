"""Mapper tests against **real** SDK event types (no live calls, no premium).

The other mapper tests build events from ``SimpleNamespace``, so they can only
prove the mappers are internally consistent — not that they read the *actual*
SDK attribute layout. These fixtures construct genuine
``copilot.SessionEvent`` envelopes wrapping the real ``AssistantMessageData`` /
``AssistantUsageData`` payloads (and the real ``SessionEventType`` enum), so a
drift between an assumed field and the shipped SDK shape fails here.

This is the regression guard for the ``duration`` bug found in the live smoke
(m03): ``AssistantUsageData.duration`` is a ``timedelta``, which the old
``float(duration)`` path silently zeroed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

copilot = pytest.importorskip("copilot")
se = pytest.importorskip("copilot.generated.session_events")

from roundtable.backend.sdk.events import event_type  # noqa: E402
from roundtable.backend.sdk.result_map import map_result_fields  # noqa: E402
from roundtable.backend.sdk.usage_map import extract_turn_usage  # noqa: E402


def _event(event_type_, data):
    """A real ``SessionEvent`` envelope (real enum type + real data payload)."""
    return copilot.SessionEvent(
        data=data,
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type=event_type_,
    )


def _assistant_message(content, tool_requests=None):
    return _event(
        copilot.SessionEventType.ASSISTANT_MESSAGE,
        se.AssistantMessageData(content=content, message_id="m1", tool_requests=tool_requests),
    )


def _tool_request(name, arguments):
    return se.AssistantMessageToolRequest(
        name=name, tool_call_id=f"call-{name}", arguments=arguments
    )


def _assistant_usage(*, output_tokens=0, cost=0.0, duration=None):
    return _event(
        copilot.SessionEventType.ASSISTANT_USAGE,
        se.AssistantUsageData(
            model="gpt", output_tokens=output_tokens, cost=cost, duration=duration
        ),
    )


def test_event_type_reads_real_enum_value() -> None:
    # SessionEventType is a plain Enum, so the wire string is only on ``.value``.
    assert event_type(_assistant_message("x")) == "assistant.message"
    assert event_type(_assistant_usage()) == "assistant.usage"


def test_result_map_reads_real_message_shape() -> None:
    events = [
        _assistant_message(
            "thinking",
            [_tool_request("view", {"path": "README.md"}), _tool_request("grep", {"q": "x"})],
        ),
        _assistant_message("final answer"),
    ]
    mapped = map_result_fields(events)
    assert mapped.final_content == "final answer"
    assert mapped.tools_used == ["view", "grep"]
    assert mapped.tool_calls[0] == {"name": "view", "args": {"path": "README.md"}}
    assert mapped.rounds == 2


def test_usage_map_reads_real_usage_shape_including_timedelta_duration() -> None:
    # Regression guard (m03): duration is a timedelta, not a number.
    events = [
        _assistant_usage(output_tokens=100, cost=1.0, duration=timedelta(milliseconds=4200)),
        _assistant_usage(output_tokens=27, cost=1.0, duration=timedelta(milliseconds=800)),
    ]
    turn = extract_turn_usage(events)
    assert turn.output_tokens == 127
    assert turn.rounds == 2
    assert turn.api_duration_ms == 5000.0
    assert not hasattr(turn, "premium")


def test_usage_map_tolerates_missing_duration() -> None:
    # duration is Optional in the real type; a None must not raise.
    turn = extract_turn_usage([_assistant_usage(output_tokens=5, cost=1.0, duration=None)])
    assert turn.api_duration_ms == 0.0


def test_non_assistant_real_event_ignored_by_mappers() -> None:
    idle = _event(copilot.SessionEventType.SESSION_IDLE, se.SessionIdleData())
    assert map_result_fields([idle]).rounds == 0
    assert extract_turn_usage([idle]) == extract_turn_usage([])
