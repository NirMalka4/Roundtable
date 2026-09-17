"""Tests for tool-call persistence redaction.

The redacted ``{name, args?, ok?, toolError?}`` calls these produce are what the run
loop stamps onto each ``AttemptDetail.tool_calls`` and the ``usage-summary.json``
artifact surfaces per attempt.
"""

from __future__ import annotations

from roundtable.backend import RetentionPolicy
from roundtable.persistence.tool_redaction import (
    redact_args,
    redact_tool_calls,
    retain_tool_calls,
)


def test_redact_args_keeps_allowlisted_drops_rest():
    args = {"path": "src/a.py", "pattern": "TODO", "token": "secret", "body": "x" * 10}
    out = redact_args("view", args)
    assert out == {"path": "src/a.py", "pattern": "TODO"}


def test_redact_args_drops_free_form_query():
    # ``query`` is a free-form search string (can carry a pasted token/snippet) and
    # is not tied to a file the agent read — dropped like ``command``.
    out = redact_args("grep", {"pattern": "TODO", "query": "internal secret phrase"})
    assert out == {"pattern": "TODO"}


def test_redact_args_mcp_tool_keeps_allowlisted_refs():
    # MCP tools now go through the SAME allowlist as builtin tools: identifying refs
    # (which PR/repo) are kept, free-form payloads (body) are dropped.
    out = redact_args(
        "ado-get_pull_request",
        {"pullRequestId": 42, "repository": "repo", "body": "secret payload"},
    )
    assert out == {"pullRequestId": 42, "repository": "repo"}


def test_redact_args_clips_long_scalar():
    out = redact_args("view", {"path": "p" * 1000})
    assert out["path"].endswith("…")
    assert len(out["path"]) <= 501


def test_redact_tool_calls_shapes_and_omits_empty_args():
    raw = [
        {"name": "view", "args": {"path": "a.py", "secret": "s"}},
        {"name": "bash", "args": {"command": "rm -rf /"}},  # command not allowlisted
        {"name": "ado-get_pull_request", "args": {"pullRequestId": 1, "body": "x"}},
        {"not_a_name": True},  # skipped
    ]
    out = redact_tool_calls(raw)
    assert out == [
        {
            "name": "view",
            "args": {"path": "a.py"},
            "argumentRetention": {"dropped": {"secret": "unretained"}},
        },
        {
            "name": "bash",
            "argumentRetention": {"dropped": {"command": "sensitive"}},
        },
        {
            "name": "ado-get_pull_request",
            "args": {"pullRequestId": 1},
            "argumentRetention": {"dropped": {"body": "sensitive"}},
        },
    ]


def test_redact_tool_calls_passes_through_safe_error_provenance():
    raw = [
        {"name": "view", "args": {"path": "a.py"}, "ok": True},
        {
            "name": "ado-get_pull_request",
            "args": {"pullRequestId": 1},
            "ok": False,
            "toolError": {
                "source": "ado-mcp",
                "type": "HttpError",
                "code": 404,
                "message": "not found",
            },
        },
        {"name": "grep", "args": {"pattern": "x"}},  # no outcome known
    ]
    out = redact_tool_calls(raw)
    assert out == [
        {"name": "view", "args": {"path": "a.py"}, "ok": True},
        {
            "name": "ado-get_pull_request",
            "args": {"pullRequestId": 1},
            "ok": False,
            "toolError": {
                "source": "ado-mcp",
                "type": "HttpError",
                "code": 404,
                "message": "not found",
            },
        },
        {"name": "grep", "args": {"pattern": "x"}},
    ]


def test_redact_tool_calls_drops_tool_error_on_success():
    out = redact_tool_calls([{"name": "view", "ok": True, "toolError": {"message": "leftover"}}])
    assert out == [{"name": "view", "ok": True}]


def test_redact_tool_calls_empty_input():
    assert redact_tool_calls(None) == []
    assert redact_tool_calls([]) == []


def test_powershell_keeps_error_provenance_but_drops_sensitive_message():
    raw = [
        {
            "name": "powershell",
            "args": {"command": "pytest --token super-secret"},
            "cwd": "C:\\repo",
            "startedAt": "2026-08-11T10:00:00Z",
            "endedAt": "2026-08-11T10:01:00Z",
            "durationMs": 60_000.0,
            "state": "background",
            "background": True,
            "shellId": "sh-1",
            "activeAtTimeout": True,
            "ok": False,
            "toolError": {
                "source": "sdk-host",
                "type": "ShellError",
                "code": "EXIT_1",
                "message": "failed --token super-secret",
            },
        }
    ]
    assert redact_tool_calls(raw) == [
        {
            "name": "powershell",
            "cwd": "C:\\repo",
            "startedAt": "2026-08-11T10:00:00Z",
            "endedAt": "2026-08-11T10:01:00Z",
            "durationMs": 60_000.0,
            "state": "background",
            "background": True,
            "shellId": "sh-1",
            "activeAtTimeout": True,
            "argumentRetention": {"dropped": {"command": "sensitive"}},
            "ok": False,
            "toolError": {
                "source": "sdk-host",
                "type": "ShellError",
                "code": "EXIT_1",
            },
            "toolErrorRetention": {"dropped": {"message": "sensitive"}},
        }
    ]


def test_powershell_keeps_known_safe_timeout_message():
    out = redact_tool_calls(
        [
            {
                "name": "powershell",
                "ok": False,
                "toolError": {"message": "timed out"},
            }
        ]
    )
    assert out == [
        {
            "name": "powershell",
            "ok": False,
            "toolError": {"message": "timed out"},
        }
    ]


def test_retention_marks_call_and_scalar_truncation() -> None:
    retained = retain_tool_calls(
        [
            {"name": "view", "args": {"path": "abcdef"}},
            {"name": "grep", "args": {"query": "secret"}},
        ],
        RetentionPolicy(max_scalar_length=3, max_tool_calls=1),
    )

    assert retained.truncated is True
    assert retained.omitted_count == 1
    assert retained.calls == [
        {
            "name": "view",
            "args": {"path": "abc…"},
            "argumentRetention": {"truncated": {"path": {"omittedCount": 3}}},
        }
    ]
