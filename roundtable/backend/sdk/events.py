"""Small helpers for reading SDK ``SessionEvent`` objects robustly.

``SessionEventType`` is a plain ``Enum`` (NOT a ``StrEnum``): a member is not
equal to its string and ``str(member)`` yields ``'SessionEventType.X'``, so the
event type must be read via ``.value``. Centralizing that here keeps every mapper
(and the bridge) from re-learning the trap. Plain strings pass through unchanged,
so synthetic test events built from ``SimpleNamespace(type="assistant.message")``
work without importing the SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..result import DEFAULT_RETENTION_POLICY, RetentionPolicy

_SENSITIVE_FIELDS = frozenset(
    {
        "arguments",
        "body",
        "command",
        "content",
        "prompt",
        "query",
        "text",
        "token",
    }
)


@dataclass(frozen=True)
class EventProjection:
    events: list[dict[str, Any]]
    truncated: bool
    omitted_count: int


def event_type(event: Any) -> str:
    """Return an event's type as its wire string (e.g. ``"assistant.message"``)."""
    t = getattr(event, "type", None)
    return str(getattr(t, "value", t) or "")


def event_data(event: Any) -> Any:
    """Return an event's ``.data`` payload (``None`` if absent)."""
    return getattr(event, "data", None)


def is_type(event: Any, wire: str) -> bool:
    return event_type(event) == wire


def _field_names(data: Any) -> frozenset[str]:
    if isinstance(data, Mapping):
        return frozenset(str(key) for key, value in data.items() if value is not None)
    values = vars(data) if hasattr(data, "__dict__") else {}
    return frozenset(str(key) for key, value in values.items() if value is not None)


def project_events(
    events: list[Any],
    policy: RetentionPolicy = DEFAULT_RETENTION_POLICY,
) -> EventProjection:
    retained: list[dict[str, Any]] = []
    for event in events[: policy.max_events]:
        fields = _field_names(event_data(event))
        item: dict[str, Any] = {"type": event_type(event)}
        sensitive = sorted(fields & _SENSITIVE_FIELDS)
        if sensitive:
            item["sensitiveValues"] = [
                {"field": field, "classification": "high", "retained": False} for field in sensitive
            ]
        retained.append(item)
    omitted = max(0, len(events) - policy.max_events)
    return EventProjection(retained, omitted > 0, omitted)
