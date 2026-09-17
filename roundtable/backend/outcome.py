"""Backend-neutral outcome of one SDK invocation.

Dependency-free by design (no project imports) so both the neutral result contract
(:mod:`roundtable.backend.result`) and any backend adapter can share it without
an import cycle.
"""

from __future__ import annotations

from enum import StrEnum


class BackendOutcome(StrEnum):
    """Normalized outcome of one backend invocation (str-valued for logging)."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    AUTH = "auth"
    MODEL_UNAVAILABLE = "model_unavailable"
    RATE_LIMITED = "rate_limited"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    TRANSPORT = "transport"
    SESSION_ERROR = "session_error"
    UNKNOWN = "unknown"
