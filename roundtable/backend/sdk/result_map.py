"""result-map: derive CopilotResult's event-sourced fields from SDK events.

Derivation rules: the final answer is the LAST ``assistant.message`` with non-empty
content; tool calls are the **requests** (not completions) carried on those messages,
in request order; ``rounds`` is the number of ``assistant.message`` events (a post-hoc
round proxy). Exit/timeout are NOT derived here — they come from the backend outcome
at the runner level.

Each tool call is joined by ``tool_call_id`` to its SDK execution observability so the
recorded entry also carries ``ok`` and structured ``toolError`` provenance when the
SDK supplied it. A request with no matching completion keeps neither field.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from ..result import SUBMISSION_TOOL_NAME
from .events import event_data, is_type
from .tool_observability import tool_call_observability

_ASSISTANT_MESSAGE = "assistant.message"
_CUSTOM_AGENTS_UPDATED = "session.custom_agents_updated"


@dataclass
class MappedResult:
    """The subset of CopilotResult fields derivable from a turn's events."""

    final_content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    rounds: int = 0


def _content_text(content: Any) -> str:
    """Coerce an assistant message's content to text (str, or joined text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        ]
        return "".join(parts)
    return "" if content is None else str(content)


def map_result_fields(
    events: list[Any],
    agent_name: str | None = None,
    *,
    default_cwd: str | None = None,
    timeout_snapshot: list[dict[str, Any]] | None = None,
    include_tool_output: Collection[str] = (),
) -> MappedResult:
    """Reduce a turn's collected events to the event-sourced CopilotResult fields."""
    result = MappedResult()
    observability = tool_call_observability(
        events,
        default_cwd=default_cwd,
        timeout_snapshot=timeout_snapshot,
        include_output=include_tool_output,
    )
    for event in events:
        if not is_type(event, _ASSISTANT_MESSAGE):
            continue
        data = event_data(event)
        result.rounds += 1
        text = _content_text(getattr(data, "content", None))
        if text.strip():
            result.final_content = text
        for request in getattr(data, "tool_requests", None) or []:
            name = getattr(request, "name", None)
            if not isinstance(name, str):
                continue
            if name == SUBMISSION_TOOL_NAME:
                continue
            result.tools_used.append(name)
            call: dict[str, Any] = {"name": name, "args": getattr(request, "arguments", None)}
            tool_call_id = getattr(request, "tool_call_id", None)
            if isinstance(tool_call_id, str):
                for key, value in observability.get(tool_call_id, {}).items():
                    if key not in {"toolCallId", "name", "args"}:
                        call[key] = value
            result.tool_calls.append(call)
    result.tool_call_count = len(result.tool_calls)
    return result
