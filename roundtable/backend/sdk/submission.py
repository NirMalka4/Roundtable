"""Copilot SDK adapter for the engine-owned output-submission contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..result import SUBMISSION_TOOL_NAME, OutputSubmission
from .compat import require_terminal_tool_support


def build_submission_tool(
    submission: OutputSubmission,
    tool_calls: Callable[[], list[dict[str, Any]]],
) -> Any:
    """Create the one non-deferred, permission-free terminal SDK tool."""

    copilot = require_terminal_tool_support()

    async def handler(invocation: Any) -> Any:
        arguments = getattr(invocation, "arguments", None)
        if not isinstance(arguments, dict) or "output" not in arguments:
            decision = submission.reject_arguments(
                "missing required tool argument 'output'; submit {\"output\": <value>}"
            )
        else:
            decision = submission.submit(arguments["output"], tool_calls=tool_calls())
        if decision.status == "accepted":
            return copilot.ToolResult(
                text_result_for_llm="Output accepted.",
                result_type="success",
                tool_telemetry={},
            )
        return copilot.ToolResult(
            text_result_for_llm=decision.feedback,
            result_type="failure",
            error="output validation failed",
            tool_telemetry={},
        )

    return copilot.Tool(
        name=SUBMISSION_TOOL_NAME,
        description=(
            "Submit the complete structured output for validation. Correct and resubmit "
            "in this turn when validation rejects it."
        ),
        parameters=submission.parameters,
        handler=handler,
        skip_permission=True,
        defer="never",
        is_terminal=True,
    )
