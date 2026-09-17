"""Unit tests for the SDK event-type helpers (the plain-Enum trap)."""

from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

from roundtable.backend.sdk.events import event_data, event_type, is_type


class _FakeType(Enum):
    ASSISTANT_MESSAGE = "assistant.message"


def test_plain_string_type_passes_through() -> None:
    ev = SimpleNamespace(type="assistant.message", data=None)
    assert event_type(ev) == "assistant.message"
    assert is_type(ev, "assistant.message")


def test_enum_member_read_via_value() -> None:
    ev = SimpleNamespace(type=_FakeType.ASSISTANT_MESSAGE, data=None)
    # str(member) would give ' _FakeType.ASSISTANT_MESSAGE' — the trap this guards.
    assert event_type(ev) == "assistant.message"
    assert is_type(ev, "assistant.message")


def test_missing_type_is_empty_string() -> None:
    assert event_type(SimpleNamespace(data=None)) == ""


def test_event_data_returns_payload_or_none() -> None:
    payload = SimpleNamespace(content="x")
    assert event_data(SimpleNamespace(type="t", data=payload)) is payload
    assert event_data(SimpleNamespace(type="t")) is None
