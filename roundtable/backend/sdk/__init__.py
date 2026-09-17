"""SDK backend for the LLM seam — a typed `github-copilot-sdk` runner that drives
every LLM agent run.

Everything here lazy-imports the ``copilot`` package via
:func:`compat.require_sdk`, so offline commands such as ``--help`` and ``view``
never pay the import cost.

The result contract (what a backend must produce)
--------------------------------------------------
The runner returns a :class:`runtime.backend.result.CopilotResult`.
The SDK runner populates only the **derived** fields the pipeline actually
consumes downstream (verified: ``trace.py`` reads ``token_usage`` / ``tools_used``
/ ``mcp_servers`` / attempt details, and ``reporting/*`` never reads
``CopilotResult.events``):

    final_content, tool_calls[{name, args, ok?, toolError?}], tools_used[str],
    tool_call_count, rounds, session_id, exit_code, timed_out, wall_clock_s,
    token_usage, mcp_servers[{name, status, transport}]

``events`` is therefore **not** load-bearing and is left empty (mirroring
``mock_runner``); no CLI-JSONL-shaped event fidelity is synthesized. ``raw_stdout``
/ ``raw_stderr`` carry the final content / diagnostic text only.

Error handling is backend-neutral: SDK failures map (via :mod:`errors`) to a
:class:`runtime.backend.outcome.BackendOutcome` carried on
``CopilotResult.backend_outcome``; the message is kept in ``raw_stderr`` for
diagnostics only.
"""

from __future__ import annotations

from .auth import AuthConfig, AuthMode, AuthUnavailableError, resolve_auth
from .bridge import SdkBridge, SdkStartError, TurnResult
from .compat import (
    PINNED_SDK_VERSION,
    require_sdk,
    require_terminal_tool_support,
    sdk_versions,
)
from .errors import BackendOutcome, classify_error
from .submission import build_submission_tool

__all__ = [
    "PINNED_SDK_VERSION",
    "AuthConfig",
    "AuthMode",
    "AuthUnavailableError",
    "BackendOutcome",
    "SdkBridge",
    "SdkStartError",
    "TurnResult",
    "build_submission_tool",
    "classify_error",
    "require_sdk",
    "require_terminal_tool_support",
    "resolve_auth",
    "sdk_versions",
]
