"""Unit tests for the SDK backend error taxonomy."""

from __future__ import annotations

import asyncio

import pytest

from roundtable.backend.sdk.errors import (
    FATAL_OUTCOMES,
    BackendOutcome,
    classify_error,
)


def test_cancelled_exception_wins_over_text() -> None:
    outcome, _ = classify_error(asyncio.CancelledError("rate limit"))
    assert outcome is BackendOutcome.CANCELLED


@pytest.mark.parametrize("exc", [TimeoutError(), TimeoutError("deadline")])
def test_timeout_exceptions(exc: BaseException) -> None:
    outcome, _ = classify_error(exc)
    assert outcome is BackendOutcome.TIMEOUT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("JSON-RPC protocol version mismatch", BackendOutcome.PROTOCOL_MISMATCH),
        ("unsupported protocol", BackendOutcome.PROTOCOL_MISMATCH),
        ("HTTP 401 Unauthorized", BackendOutcome.AUTH),
        ("authentication failed", BackendOutcome.AUTH),
        ("not logged in", BackendOutcome.AUTH),
        ("HTTP 429 Too Many Requests", BackendOutcome.RATE_LIMITED),
        ("quota exhausted", BackendOutcome.RATE_LIMITED),
        ('Model "x" is not available.', BackendOutcome.MODEL_UNAVAILABLE),
        ("model not found", BackendOutcome.MODEL_UNAVAILABLE),
        # Patterns folded in from the retired runtime.model_errors table:
        ("access denied", BackendOutcome.AUTH),
        ("billing issue on the account", BackendOutcome.AUTH),
        ("the resource was not found", BackendOutcome.MODEL_UNAVAILABLE),
        ("response stream has been closed", BackendOutcome.MODEL_UNAVAILABLE),
        ("connection refused", BackendOutcome.TRANSPORT),
        ("broken pipe", BackendOutcome.TRANSPORT),
        ("server disconnected", BackendOutcome.TRANSPORT),
    ],
)
def test_text_classification(text: str, expected: BackendOutcome) -> None:
    outcome, message = classify_error(message=text)
    assert outcome is expected
    assert message == text


def test_protocol_precedes_auth_when_both_present() -> None:
    # Ordered table: protocol is checked before auth.
    outcome, _ = classify_error(message="401 unauthorized protocol version")
    assert outcome is BackendOutcome.PROTOCOL_MISMATCH


def test_unmatched_session_event_is_session_error() -> None:
    outcome, msg = classify_error(message="agent produced no output", from_session_error=True)
    assert outcome is BackendOutcome.SESSION_ERROR
    assert msg == "agent produced no output"


def test_unmatched_exception_is_unknown() -> None:
    outcome, _ = classify_error(ValueError("weird internal state"))
    assert outcome is BackendOutcome.UNKNOWN


def test_empty_input_defaults() -> None:
    assert classify_error()[0] is BackendOutcome.UNKNOWN


def test_only_protocol_is_fatal() -> None:
    assert frozenset({BackendOutcome.PROTOCOL_MISMATCH}) == FATAL_OUTCOMES
