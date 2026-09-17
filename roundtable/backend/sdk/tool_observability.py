"""Safe, event-sourced observability for SDK tool executions and timeouts."""

from __future__ import annotations

import re
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from .events import event_data, event_type

_START = "tool.execution_start"
_COMPLETE = "tool.execution_complete"
_SHELL_TOOLS = frozenset({"powershell", "read_powershell", "stop_powershell", "list_powershell"})
_BACKGROUND_WAIT_TOOLS = frozenset({"read_powershell", "list_powershell"})
_SHELL_ID_RE = re.compile(r"<shellId:\s*([A-Za-z0-9_.-]+)\s+([^>]+)>")


def _field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict) and value.get(name) is not None:
            return value[name]
        found = getattr(value, name, None)
        if found is not None:
            return found
    return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value:
        return value
    return None


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (b - a).total_seconds())


def _completion_metadata(data: Any, *, include_output: bool = False) -> dict[str, Any]:
    result = _field(data, "result")
    contents = _field(result, "contents") or []
    metadata: dict[str, Any] = {}
    text_parts: list[str] = []
    for content in contents:
        kind = _field(content, "type")
        if kind == "shell_exit":
            metadata["exitCode"] = _field(content, "exit_code", "exitCode")
            metadata["shellId"] = _field(content, "shell_id", "shellId")
            metadata["cwd"] = _field(content, "cwd")
        elif kind == "terminal":
            exit_code = _field(content, "exit_code", "exitCode")
            if exit_code is not None:
                metadata["exitCode"] = exit_code
            metadata["cwd"] = _field(content, "cwd") or metadata.get("cwd")
            text_parts.append(str(_field(content, "text") or ""))
        elif kind == "text":
            text_parts.append(str(_field(content, "text") or ""))
    text_parts.append(str(_field(result, "content") or ""))
    if include_output:
        output = "\n".join(part for part in text_parts if part)
        if output:
            metadata["output"] = output
    marker = _SHELL_ID_RE.search("\n".join(text_parts))
    if marker:
        metadata.setdefault("shellId", marker.group(1))
        marker_state = marker.group(2).lower()
        if "running" in marker_state:
            metadata["background"] = True
    return metadata


def _tool_error(data: Any) -> dict[str, Any]:
    """Project structured failure provenance supplied by the SDK."""
    error = _field(data, "error")
    if isinstance(error, str):
        return {"message": error}
    if error is None:
        return {}
    projected: dict[str, Any] = {}
    for target, names in (
        ("source", ("source", "error_source", "errorSource", "origin")),
        ("type", ("type", "error_type", "errorType", "kind")),
        ("code", ("code", "error_code", "errorCode")),
        ("timeoutOwner", ("timeout_owner", "timeoutOwner")),
        ("message", ("message",)),
    ):
        value = _field(error, *names)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            projected[target] = value
    return projected


def _event_records(
    events: list[Any],
    default_cwd: str | None,
    *,
    include_output_for: Collection[str] = (),
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for event in events:
        kind = event_type(event)
        data = event_data(event)
        call_id = _field(data, "tool_call_id", "toolCallId")
        if not isinstance(call_id, str):
            continue
        if kind == _START:
            name = str(_field(data, "tool_name", "toolName") or "")
            args = _field(data, "arguments") or {}
            records[call_id] = {
                "toolCallId": call_id,
                "name": name,
                "args": args,
                "cwd": default_cwd,
                "startedAt": _iso(_field(event, "timestamp")),
                "state": "running",
            }
        elif kind == _COMPLETE:
            record = records.setdefault(call_id, {"toolCallId": call_id})
            has_start = "startedAt" in record
            ended_at = _iso(_field(event, "timestamp"))
            if has_start and ended_at:
                record["endedAt"] = ended_at
            record.update(
                _completion_metadata(
                    data,
                    include_output=str(record.get("name") or "") in include_output_for,
                )
            )
            ok = bool(_field(data, "success"))
            record["ok"] = ok
            if record.get("background"):
                record["state"] = "background"
            elif has_start:
                record["state"] = "exited" if ok else "failed"
            tool_error = _tool_error(data)
            if not ok and tool_error:
                record["toolError"] = tool_error
    for record in records.values():
        duration = _seconds_between(record.get("startedAt"), record.get("endedAt"))
        if duration is not None:
            record["durationMs"] = round(duration * 1000.0, 1)
        if not record.get("cwd"):
            record.pop("cwd", None)
    return records


def active_tool_snapshot(events: list[Any], default_cwd: str | None = None) -> list[dict[str, Any]]:
    """Return active shell/tool facts available immediately before timeout cleanup."""
    records = _event_records(events, default_cwd)
    active_shells: dict[str, dict[str, Any]] = {}
    active_calls: list[dict[str, Any]] = []
    for record in records.values():
        name = str(record.get("name") or "")
        shell_id = record.get("shellId")
        args = record.get("args")
        referenced_id = _field(args, "shellId", "shell_id")
        if name == "powershell" and record.get("state") == "background" and shell_id:
            active_shells[str(shell_id)] = record
        elif name == "stop_powershell" and referenced_id:
            if record.get("ok") is True:
                active_shells.pop(str(referenced_id), None)
        elif (
            name == "read_powershell"
            and referenced_id
            and record.get("ok") is True
            and record.get("state") == "exited"
        ):
            active_shells.pop(str(referenced_id), None)
        if record.get("state") == "running":
            active_calls.append(record)

    snapshot: list[dict[str, Any]] = []
    for record in [*active_calls, *active_shells.values()]:
        item = {
            key: record[key]
            for key in ("name", "cwd", "startedAt", "shellId", "state")
            if record.get(key) is not None
        }
        item["activeAtTimeout"] = True
        if item not in snapshot:
            snapshot.append(item)
    return snapshot


def timeout_phase(events: list[Any], default_cwd: str | None = None) -> str:
    snapshot = active_tool_snapshot(events, default_cwd)
    if any(
        item.get("name") in _BACKGROUND_WAIT_TOOLS or item.get("state") == "background"
        for item in snapshot
    ):
        return "background_tool_wait"
    if snapshot:
        return "tool_running"
    return "model_wait"


def overdue_tool_snapshot(
    events: list[Any],
    deadline_s: float,
    *,
    default_cwd: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return active shell facts whose execution age reached ``deadline_s``."""
    current = now or datetime.now(UTC)
    overdue: list[dict[str, Any]] = []
    for item in active_tool_snapshot(events, default_cwd):
        if item.get("name") not in _SHELL_TOOLS:
            continue
        started = item.get("startedAt")
        if not isinstance(started, str):
            continue
        try:
            started_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (current - started_at).total_seconds() >= deadline_s:
            overdue.append(item)
    return overdue


def tool_call_observability(
    events: list[Any],
    *,
    default_cwd: str | None = None,
    timeout_snapshot: list[dict[str, Any]] | None = None,
    include_output: Collection[str] = (),
) -> dict[str, dict[str, Any]]:
    """Return tool metadata keyed by call id, optionally with ephemeral output."""
    records = _event_records(events, default_cwd, include_output_for=include_output)
    active_shell_ids = {
        item.get("shellId")
        for item in (timeout_snapshot or [])
        if item.get("activeAtTimeout") and item.get("shellId")
    }
    active_calls = {
        (item.get("name"), item.get("startedAt"))
        for item in (timeout_snapshot or [])
        if item.get("activeAtTimeout") and not item.get("shellId")
    }
    for record in records.values():
        if "startedAt" in record:
            record["activeAtTimeout"] = (
                record.get("shellId") in active_shell_ids
                or (
                    record.get("name"),
                    record.get("startedAt"),
                )
                in active_calls
            )
    return records
