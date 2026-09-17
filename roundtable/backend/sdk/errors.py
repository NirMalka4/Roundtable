"""Text/exception → :class:`BackendOutcome` classification for the SDK runner.

The SDK surfaces failures as **exceptions** (transport/timeout/cancel) and
``session.error`` **events** — so we do not manufacture fake exit codes. Instead
:func:`classify_error` maps either source onto a :class:`BackendOutcome` (defined
in the backend-neutral :mod:`roundtable.backend.outcome`), plus a normalized
message. This is the single error-pattern table in the system.
"""

from __future__ import annotations

import asyncio
import re

from ..outcome import BackendOutcome

# Outcomes the adapter must not retry because the same protocol mismatch will fail
# identically on every attempt.
FATAL_OUTCOMES = frozenset({BackendOutcome.PROTOCOL_MISMATCH})

# Ordered (outcome, patterns). First match wins.
_PATTERNS: tuple[tuple[BackendOutcome, tuple[re.Pattern[str], ...]], ...] = (
    (
        BackendOutcome.PROTOCOL_MISMATCH,
        (
            re.compile(r"protocol\s*version", re.I),
            re.compile(r"protocol\s*mismatch", re.I),
            re.compile(r"incompatible\s*(protocol|version)", re.I),
            re.compile(r"unsupported\s*protocol", re.I),
        ),
    ),
    (
        BackendOutcome.AUTH,
        (
            re.compile(r"\b401\b|\b403\b", re.I),
            re.compile(r"unauthor", re.I),
            re.compile(r"forbidden", re.I),
            re.compile(r"authenticat", re.I),
            re.compile(r"not\s+logged\s+in", re.I),
            re.compile(r"\btoken\b.*\b(invalid|expired|missing)\b", re.I),
            re.compile(r"access\s*denied", re.I),
            re.compile(r"billing", re.I),
        ),
    ),
    (
        BackendOutcome.RATE_LIMITED,
        (
            re.compile(r"\b429\b", re.I),
            re.compile(r"rate\s*limit", re.I),
            re.compile(r"quota", re.I),
            re.compile(r"throttl", re.I),
        ),
    ),
    (
        BackendOutcome.MODEL_UNAVAILABLE,
        (
            re.compile(r"model\s*not\s*found", re.I),
            re.compile(r"\bnot\s+found\b", re.I),
            re.compile(r"\bnot\s+available\b", re.I),
            re.compile(r"\bunavailable\b", re.I),
            re.compile(r"response stream has been closed", re.I),
        ),
    ),
    (
        BackendOutcome.TRANSPORT,
        (
            re.compile(r"connection\s*(refused|reset|closed|error)", re.I),
            re.compile(r"broken\s*pipe", re.I),
            re.compile(r"econnrefused|econnreset", re.I),
            re.compile(r"\btransport\b", re.I),
            re.compile(r"disconnect", re.I),
        ),
    ),
)


def _text_of(exc: BaseException | None, message: str | None) -> str:
    if message:
        return message
    if exc is not None:
        return str(exc) or exc.__class__.__name__
    return ""


def classify_error(
    exc: BaseException | None = None,
    *,
    message: str | None = None,
    from_session_error: bool = False,
) -> tuple[BackendOutcome, str]:
    """Classify a failure into ``(outcome, message)``.

    ``exc`` is a raised exception (transport/timeout/cancel); ``message`` is text
    from a ``session.error`` event (pass ``from_session_error=True`` so an
    otherwise-unmatched event resolves to :attr:`BackendOutcome.SESSION_ERROR`
    rather than :attr:`BackendOutcome.UNKNOWN`). At least one should be given.
    """
    # Control-flow exception types are unambiguous and take priority over text.
    if isinstance(exc, asyncio.CancelledError):
        return BackendOutcome.CANCELLED, _text_of(exc, message) or "cancelled"
    if isinstance(exc, TimeoutError):
        return BackendOutcome.TIMEOUT, _text_of(exc, message) or "timed out"

    text = _text_of(exc, message)
    for outcome, patterns in _PATTERNS:
        if any(p.search(text) for p in patterns):
            return outcome, text

    if from_session_error:
        return BackendOutcome.SESSION_ERROR, text or "session error"
    return BackendOutcome.UNKNOWN, text or "unknown backend error"
