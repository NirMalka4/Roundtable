"""persistence.tool_redaction: scrub agent tool-call arguments for persistence.

Under the SDK backend agents run with an approve-all permission handler (there is no
``--allow-all-tools`` CLI flag); their raw tool-call arguments (captured in
``CopilotResult.tool_calls``) can carry secrets, tokens, or large payloads. Before
any of it lands in a persisted artifact it passes through here, which keeps only a
small allowlist of *observability-relevant* argument keys (which file/pattern/PR the
agent acted on) and drops everything else. The allowlist is applied uniformly to
builtin AND MCP/server tools — an MCP call's identifying refs (e.g. which pull request
or thread it touched) are useful review signal, while its free-form payload keys
(bodies, comments) fall outside the allowlist and are dropped like any other.

Redaction is deliberately fail-closed: an unrecognized argument key is dropped by
default, so a newly added tool (builtin or MCP) can never leak a sensitive key until
that key is explicitly allowlisted here. A denylist would be fail-open and is rejected
for that reason.

This is the single owner of "what of an agent's tool call is safe to persist";
callers hand it the raw ``{name, args, ok?, toolError?}`` list and get back a bounded,
redacted one (name + allowlisted args + structured failure provenance).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.backend import DEFAULT_RETENTION_POLICY, RetentionPolicy

# Argument keys worth recording for review observability — all identify *what the
# agent acted on* (which file/pattern it read, or which PR/thread/repo an MCP tool
# targeted), never credentials or free-form payloads. Anything not listed is dropped.
# ``command``/``query`` are excluded (shell command lines and free-form search strings
# are the most likely place for an inline secret or pasted source); MCP payload keys
# such as ``body``/``content``/``comment``/``text`` are likewise absent by design.
_ALLOWED_ARG_KEYS = frozenset(
    {
        # Builtin file/search tools — what the agent read.
        "path",
        "paths",
        "file",
        "files",
        "pattern",
        "glob",
        "view_range",
        "line",
        "offset",
        "limit",
        "type",
        "output_mode",
        # ADO/MCP identifying refs — which PR/thread/repo the tool acted on. These are
        # opaque IDs and paths, never payloads, so they carry signal without secrets.
        "pullRequestId",
        "repository",
        "repositoryId",
        "project",
        "organization",
        "id",
        "ids",
        "threadId",
        "commentId",
        "iterationId",
        "filePath",
        "status",
        "top",
        "skip",
    }
)

# Cap a single recorded scalar so a pathological argument can't bloat the artifact.
_SENSITIVE_ARG_KEYS = frozenset(
    {"body", "command", "comment", "content", "password", "query", "text", "token"}
)
_SAFE_TOOL_FACT_KEYS = frozenset(
    {
        "cwd",
        "startedAt",
        "endedAt",
        "durationMs",
        "state",
        "exitCode",
        "background",
        "shellId",
        "activeAtTimeout",
    }
)
_SAFE_TOOL_ERROR_KEYS = ("source", "type", "code", "timeoutOwner", "message")
_SHELL_TOOLS = frozenset({"powershell", "read_powershell", "stop_powershell", "list_powershell"})
_SAFE_SHELL_ERROR_MESSAGES = frozenset(
    {"timeout", "timed out", "deadline exceeded", "cancelled", "canceled"}
)


@dataclass(frozen=True)
class RetainedToolCalls:
    calls: list[dict[str, Any]]
    truncated: bool
    omitted_count: int


def _clip(value: Any, policy: RetentionPolicy) -> tuple[Any, int]:
    """Bound a scalar (or shallow list of scalars); drop nested structures."""
    if isinstance(value, str):
        omitted = max(0, len(value) - policy.max_scalar_length)
        retained_text = value if not omitted else value[: policy.max_scalar_length] + "…"
        return retained_text, omitted
    if isinstance(value, (int, float, bool)) or value is None:
        return value, 0
    if isinstance(value, Sequence):
        retained: list[Any] = []
        omitted = 0
        for item in value:
            if isinstance(item, (str, int, float, bool)):
                clipped, item_omitted = _clip(item, policy)
                retained.append(clipped)
                omitted += item_omitted
        return retained, omitted
    return None, 0


def _redact_args(
    args: Mapping[str, Any] | None,
    policy: RetentionPolicy,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(args, Mapping):
        return {}, {}
    retained: dict[str, Any] = {}
    truncated: dict[str, dict[str, int]] = {}
    dropped: dict[str, str] = {}
    for key, value in args.items():
        if key not in _ALLOWED_ARG_KEYS:
            dropped[key] = "sensitive" if key in _SENSITIVE_ARG_KEYS else "unretained"
            continue
        clipped, omitted = _clip(value, policy)
        if clipped is not None and clipped != []:
            retained[key] = clipped
        if omitted:
            truncated[key] = {"omittedCount": omitted}
    retention: dict[str, Any] = {}
    if truncated:
        retention["truncated"] = truncated
    if dropped:
        retention["dropped"] = dropped
    return retained, retention


def redact_args(name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the allowlisted, length-bounded subset of one tool call's arguments.

    Applied uniformly to builtin and MCP tools. Empty dict when nothing survives.
    """
    return _redact_args(args, DEFAULT_RETENTION_POLICY)[0]


def retain_tool_calls(
    raw_calls: Sequence[Mapping[str, Any]] | None,
    policy: RetentionPolicy = DEFAULT_RETENTION_POLICY,
) -> RetainedToolCalls:
    if not raw_calls:
        return RetainedToolCalls([], False, 0)
    out: list[dict[str, Any]] = []
    for call in raw_calls[: policy.max_tool_calls]:
        if not isinstance(call, Mapping):
            continue
        name = call.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        entry: dict[str, Any] = {"name": name.strip()}
        args, retention = _redact_args(call.get("args"), policy)
        if args:
            entry["args"] = args
        if retention:
            entry["argumentRetention"] = retention
        for key in _SAFE_TOOL_FACT_KEYS:
            if key in call:
                value, _omitted = _clip(call[key], policy)
                if value is not None:
                    entry[key] = value
        if isinstance(call.get("ok"), bool):
            entry["ok"] = call["ok"]
            error = call.get("toolError")
            if entry["ok"] is False and isinstance(error, Mapping):
                retained_error: dict[str, Any] = {}
                omitted_by_key: dict[str, int] = {}
                dropped_by_key: dict[str, str] = {}
                for key in _SAFE_TOOL_ERROR_KEYS:
                    if key == "message" and name.strip().lower() in _SHELL_TOOLS:
                        message = error.get(key)
                        if (
                            not isinstance(message, str)
                            or message.strip().lower() not in _SAFE_SHELL_ERROR_MESSAGES
                        ):
                            if message is not None:
                                dropped_by_key[key] = "sensitive"
                            continue
                    value, omitted = _clip(error.get(key), policy)
                    if value is not None and value != "":
                        retained_error[key] = value
                    if omitted:
                        omitted_by_key[key] = omitted
                if retained_error:
                    entry["toolError"] = retained_error
                retention: dict[str, Any] = {}
                if omitted_by_key:
                    retention["truncated"] = omitted_by_key
                if dropped_by_key:
                    retention["dropped"] = dropped_by_key
                if retention:
                    entry["toolErrorRetention"] = retention
        out.append(entry)
    omitted = max(0, len(raw_calls) - policy.max_tool_calls)
    return RetainedToolCalls(out, omitted > 0, omitted)


def redact_tool_calls(raw_calls: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Redact a raw ``[{name, args, ok?, toolError?}]`` list into a bounded one.

    Each entry keeps the tool name, its allowlisted args, and — when the call's
    outcome is known — ``ok`` and safe structured failure provenance. The list is capped at
    :data:`_MAX_CALLS_PER_AGENT`. ``args`` is omitted when it redacts to empty, so a
    name-only call stays compact.
    """
    return retain_tool_calls(raw_calls).calls


def redact_timeout_snapshot(
    snapshot: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Fail-closed projection of active-at-timeout tool/background facts."""
    return redact_tool_calls(snapshot)
