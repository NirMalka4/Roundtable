from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from roundtable.backend.sdk.result_map import map_result_fields
from roundtable.backend.sdk.tool_observability import (
    active_tool_snapshot,
    overdue_tool_snapshot,
    timeout_phase,
)

T0 = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)


def _event(kind, data, offset=0):
    return SimpleNamespace(type=kind, data=data, timestamp=T0 + timedelta(seconds=offset))


def _start(call_id, name, args, offset=0):
    return _event(
        "tool.execution_start",
        SimpleNamespace(tool_call_id=call_id, tool_name=name, arguments=args),
        offset,
    )


def _complete(call_id, *, text="", exit_code=None, shell_id=None, offset=2):
    contents = []
    if shell_id and exit_code is not None:
        contents.append(
            SimpleNamespace(
                type="shell_exit", shell_id=shell_id, exit_code=exit_code, cwd="C:\\repo"
            )
        )
    elif text:
        contents.append(SimpleNamespace(type="text", text=text))
    return _event(
        "tool.execution_complete",
        SimpleNamespace(
            tool_call_id=call_id,
            success=True,
            error=None,
            result=SimpleNamespace(contents=contents, content=text),
        ),
        offset,
    )


def _message(call_id, command):
    request = SimpleNamespace(
        name="powershell", arguments={"command": command}, tool_call_id=call_id
    )
    return _event(
        "assistant.message",
        SimpleNamespace(content="done", tool_requests=[request]),
    )


def test_powershell_execution_maps_safe_observability_without_command_copy() -> None:
    events = [
        _message("c1", "python -m pytest tests/unit/test_x.py -q --token secret"),
        _start("c1", "powershell", {"command": "python -m pytest tests/unit/test_x.py -q"}),
        _complete("c1", exit_code=0, shell_id="sh-1"),
    ]
    call = map_result_fields(events, default_cwd="C:\\repo").tool_calls[0]
    assert call == {
        "name": "powershell",
        "args": {"command": "python -m pytest tests/unit/test_x.py -q --token secret"},
        "ok": True,
        "cwd": "C:\\repo",
        "startedAt": "2026-08-11T10:00:00Z",
        "endedAt": "2026-08-11T10:00:02Z",
        "durationMs": 2000.0,
        "state": "exited",
        "exitCode": 0,
        "shellId": "sh-1",
        "activeAtTimeout": False,
    }


def test_tool_output_is_exposed_only_when_submission_validation_requests_it() -> None:
    events = [
        _message("c1", "src/a.py"),
        _start("c1", "view", {"path": "src/a.py"}),
        _complete("c1", text="exact source excerpt"),
    ]
    persisted = map_result_fields(events).tool_calls[0]
    ephemeral = map_result_fields(events, include_tool_output={"view"}).tool_calls[0]
    assert "output" not in persisted
    assert "exact source excerpt" in ephemeral["output"]


def test_tool_output_allowlist_excludes_other_tools() -> None:
    events = [
        _message("c1", "src/a.py"),
        _start("c1", "grep", {"pattern": "secret"}),
        _complete("c1", text="sensitive search output"),
    ]
    call = map_result_fields(events, include_tool_output={"view"}).tool_calls[0]
    assert "output" not in call


def test_failed_tool_preserves_sdk_error_provenance() -> None:
    events = [
        _message("c1", "rg"),
        _start("c1", "rg", {"pattern": "x"}),
        _event(
            "tool.execution_complete",
            SimpleNamespace(
                tool_call_id="c1",
                success=False,
                error=SimpleNamespace(
                    message="deadline exceeded",
                    source="sdk-host",
                    error_type="ToolTimeout",
                    error_code="TOOL_TIMEOUT",
                    timeout_owner="builtin-tool",
                ),
                result=SimpleNamespace(contents=[], content=""),
            ),
            20,
        ),
    ]

    call = map_result_fields(events).tool_calls[0]
    assert call["toolError"] == {
        "source": "sdk-host",
        "type": "ToolTimeout",
        "code": "TOOL_TIMEOUT",
        "timeoutOwner": "builtin-tool",
        "message": "deadline exceeded",
    }


def test_timeout_phase_sweeps_model_foreground_and_background_wait() -> None:
    background = [
        _start("c1", "powershell", {"command": "pytest"}, 0),
        _complete("c1", text="<shellId: sh-2 running>", offset=1),
        _start("c2", "read_powershell", {"shellId": "sh-2", "delay": 30}, 2),
    ]
    assert timeout_phase([]) == "model_wait"
    assert timeout_phase([_start("c1", "powershell", {"command": "pytest"})]) == "tool_running"
    assert timeout_phase(background) == "background_tool_wait"
    assert active_tool_snapshot(background) == [
        {
            "name": "read_powershell",
            "startedAt": "2026-08-11T10:00:02Z",
            "state": "running",
            "activeAtTimeout": True,
        },
        {
            "name": "powershell",
            "startedAt": "2026-08-11T10:00:00Z",
            "shellId": "sh-2",
            "state": "background",
            "activeAtTimeout": True,
        },
    ]


@pytest.mark.parametrize("control_tool", ["stop_powershell", "read_powershell"])
def test_failed_shell_control_keeps_background_process_active(control_tool) -> None:
    events = [
        _start("c1", "powershell", {"command": "pytest"}, 0),
        _complete("c1", text="<shellId: sh-2 running>", offset=1),
        _start("c2", control_tool, {"shellId": "sh-2"}, 2),
        _event(
            "tool.execution_complete",
            SimpleNamespace(
                tool_call_id="c2",
                success=False,
                error=SimpleNamespace(message="stop failed"),
                result=SimpleNamespace(contents=[], content=""),
            ),
            3,
        ),
    ]
    assert timeout_phase(events) == "background_tool_wait"
    assert any(item.get("shellId") == "sh-2" for item in active_tool_snapshot(events))


def test_overdue_tool_snapshot_returns_only_expired_active_shells() -> None:
    events = [_start("c1", "powershell", {"command": "pytest"}, 0)]
    assert overdue_tool_snapshot(events, 120, now=T0 + timedelta(seconds=119)) == []
    assert overdue_tool_snapshot(events, 120, now=T0 + timedelta(seconds=120)) == [
        {
            "name": "powershell",
            "startedAt": "2026-08-11T10:00:00Z",
            "state": "running",
            "activeAtTimeout": True,
        }
    ]


def test_overdue_tool_snapshot_ignores_non_shell_tools() -> None:
    events = [_start("c1", "view", {"path": "large.txt"}, 0)]
    assert overdue_tool_snapshot(events, 120, now=T0 + timedelta(seconds=300)) == []
