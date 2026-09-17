"""JSON format pre-check + the shared OVG result type.

This is the format gate that runs before any structural/semantic validation: it
accepts a single JSON object, tolerating (and stripping) a short prose preamble or
a ``json`` code fence, and rejects anything that is not a JSON object at the root.

It lives on its own — separate from the declarative gate pipeline (:mod:`pipeline`) —
because both share it: the pipeline calls it first. ``OvgResult`` is the small,
gate-neutral verdict those consumers speak.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ._jscompat import js_typeof

Gate = str  # 'format' | 'all' | a concrete gate name


@dataclass
class OvgResult:
    """A single validation verdict: pass/fail + the gate that decided it."""

    passed: bool
    gate: Gate
    parsed: Any | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_PROSE_RE = re.compile(r"(Here|Let me|I'll|I will|Based on|After|The|This)", re.IGNORECASE)
_PROSE_JSON_RE = re.compile(r"(\{[\s\S]*\})\s*$")
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```")
_EXCERPT_LIMIT = 120


def _line_column(text: str, position: int) -> tuple[int, int]:
    line = text.count("\n", 0, position) + 1
    last_break = text.rfind("\n", 0, position)
    return line, position - last_break


def _excerpt(text: str, position: int) -> str:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    column = position - line_start
    start = max(0, column - _EXCERPT_LIMIT // 2)
    end = min(len(line), start + _EXCERPT_LIMIT)
    start = max(0, end - _EXCERPT_LIMIT)
    shown = line[start:end]
    left = "…" if start else ""
    right = "…" if end < len(line) else ""
    caret = " " * (len(left) + column - start) + "^"
    return f"{left}{shown}{right}\n{caret}"


def _open_containers(text: str, stop: int | None = None) -> tuple[list[tuple[str, int]], bool]:
    stack: list[tuple[str, int]] = []
    in_string = escaped = False
    mismatched = False
    for position, char in enumerate(text[:stop]):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append((char, position))
        elif char in "]}":
            expected = "[" if char == "]" else "{"
            if not stack or stack[-1][0] != expected:
                mismatched = True
                break
            stack.pop()
    return stack, mismatched


def json_parse_error_message(text: str, exc: json.JSONDecodeError) -> str:
    """Precise, bounded diagnostic for the exact failed parser location."""

    eof = exc.pos >= len(text)
    kind = "EOF" if eof else exc.msg
    message = (
        f"{kind} at line {exc.lineno}, column {exc.colno}, character {exc.pos}. "
        f"{_excerpt(text, exc.pos)}"
    )
    stack, _mismatched = _open_containers(text, exc.pos)
    if stack:
        opener, position = stack[0]
        line, column = _line_column(text, position)
        container = (
            "root object"
            if opener == "{" and position == 0
            else ("object" if opener == "{" else "array")
        )
        message += (
            f"\nUnmatched {container} opened at line {line}, column {column}, character {position}."
        )
    return message


def hypothetical_closure(raw: str) -> Any | None:
    """Parse an EOF-truncated value after diagnostic-only mechanical closure."""

    text = raw.strip()
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        stack, mismatched = _open_containers(text)
        if mismatched or not stack or exc.pos < len(text):
            return None
        closing = "".join("}" if opener == "{" else "]" for opener, _ in reversed(stack))
        try:
            return json.loads(text + closing)
        except json.JSONDecodeError:
            return None
    return None


def parse_json_value(raw: str) -> OvgResult:
    """Parse one JSON value while preserving legacy preamble/fence extraction."""

    trimmed = raw.strip()
    if _PROSE_RE.match(trimmed):
        match = _PROSE_JSON_RE.search(trimmed)
        if not match:
            return OvgResult(
                False,
                "format",
                None,
                ["Output contains prose preamble with no extractable JSON"],
                [],
            )
        trimmed = match.group(1)
    elif trimmed.startswith("```"):
        match = _FENCE_RE.search(trimmed)
        if not match:
            return OvgResult(
                False,
                "format",
                None,
                ["Output starts with code fence but no closing fence found"],
                [],
            )
        trimmed = match.group(1)
    try:
        return OvgResult(True, "format", json.loads(trimmed), [], [])
    except json.JSONDecodeError as exc:
        return OvgResult(
            False,
            "format",
            None,
            [f"Invalid JSON: {json_parse_error_message(trimmed, exc)}"],
            [],
        )


def validate_json_format(raw: str) -> OvgResult:
    """Parse ``raw`` into a JSON object, stripping a prose preamble / code fence.

    Returns a passing ``OvgResult`` (gate ``"format"``) carrying the parsed object,
    or a failing one whose ``errors`` describe why the payload was not a JSON object.
    """
    parsed_result = parse_json_value(raw)
    if not parsed_result.passed:
        return parsed_result
    parsed = parsed_result.parsed

    if not isinstance(parsed, dict):
        got = "array" if isinstance(parsed, list) else js_typeof(parsed)
        return OvgResult(False, "format", None, [f"Root must be object, got {got}"], [])

    return OvgResult(True, "format", parsed, [], [])
