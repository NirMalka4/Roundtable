"""Derive InspectorX's verdict from its Judge output shape.

InspectorX's Judge emits a verdict string and review-specific overlay buckets.
Parsing that shape belongs to this configuration bundle; shared decision code owns
only the neutral verdict labels, result type, icons, and process mappings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from roundtable.decision import (
    APPROVE,
    APPROVE_WITH_SUGGESTIONS,
    REJECT,
    UNKNOWN,
    VerdictResult,
    extract_session_id,
    get_verdict_icon,
)
from roundtable.graph import Configuration
from roundtable.result_access import response_of, unreadable_reason
from roundtable.types import severity_levels
from roundtable.utils import extract_json_detailed

_CANONICAL = {APPROVE, APPROVE_WITH_SUGGESTIONS, REJECT, UNKNOWN}


def severity_keys(configuration: Configuration) -> tuple[str, ...]:
    """Severity bucket keys, most- to least-severe."""
    return tuple(reversed(severity_levels(configuration)))


def to_canonical_verdict(value: Any) -> str:
    """Normalize InspectorX Judge prose to a canonical verdict label."""
    if not isinstance(value, str):
        return UNKNOWN
    normalized = value.upper().strip()
    if normalized in _CANONICAL:
        return normalized
    if "APPROVE_WITH_SUGGESTIONS" in normalized:
        return APPROVE_WITH_SUGGESTIONS
    if "APPROVE" in normalized and "REJECT" not in normalized:
        return APPROVE
    return UNKNOWN


def to_severity_bucket(value: str) -> str:
    """Map an InspectorX overlay severity to a configured bucket."""
    normalized = value.lower()
    if "critical" in normalized:
        return "critical"
    if "high" in normalized or "blocking" in normalized:
        return "high"
    if "medium" in normalized or "warning" in normalized:
        return "medium"
    if "low" in normalized:
        return "low"
    return "info"


def compute_overlay_severity_counts(
    overlay: list[dict[str, Any]], configuration: Configuration
) -> dict[str, int]:
    """Best-effort counts from InspectorX ``verdict_severity`` values."""
    counts = dict.fromkeys(severity_keys(configuration), 0)
    for entry in overlay:
        raw = entry.get("verdict_severity")
        if not isinstance(raw, str):
            continue
        bucket = to_severity_bucket(raw)
        if bucket in counts:
            counts[bucket] += 1
    return counts


@dataclass
class JudgeSummary:
    """Structured InspectorX Judge summary."""

    verdict: str
    verdict_overridden: bool
    verdict_icon: str
    blocking_count: int
    non_blocking_count: int
    safe_count: int
    severity_counts: dict[str, int]
    published_primary_count: int
    merged_subordinate_count: int
    judge_observations_count: int
    needs_human_judgment_count: int
    parsed: bool
    parse_error: str | None = None
    raw_json: Any = None


def _empty_overlay_summary(
    parse_error: str | None, parsed: bool, configuration: Configuration
) -> JudgeSummary:
    return JudgeSummary(
        verdict=UNKNOWN,
        verdict_overridden=False,
        verdict_icon=get_verdict_icon(UNKNOWN),
        blocking_count=0,
        non_blocking_count=0,
        safe_count=0,
        severity_counts=dict.fromkeys(severity_keys(configuration), 0),
        published_primary_count=0,
        merged_subordinate_count=0,
        judge_observations_count=0,
        needs_human_judgment_count=0,
        parsed=parsed,
        parse_error=parse_error,
    )


def _truthy_or(*values: Any) -> Any:
    result: Any = None
    for result in values:
        if result:
            return result
    return result


def parse_judge_summary(raw: str, configuration: Configuration) -> JudgeSummary:
    """Extract a summary from InspectorX Judge JSON."""
    if not raw or not raw.strip():
        return _empty_overlay_summary("Input is empty", False, configuration)

    extracted = extract_json_detailed(raw)
    parsed_object: Any = None
    parse_error: str | None = None
    try:
        parsed_object = json.loads(extracted.json)
    except Exception as err:
        base = str(err)
        parse_error = (
            f"JSON parse failed: {base} ({extracted.reason})"
            if extracted.reason
            else f"JSON parse failed: {base}"
        )

    if not isinstance(parsed_object, dict):
        return _empty_overlay_summary(
            parse_error or extracted.reason or "Failed to parse JSON into an object",
            False,
            configuration,
        )

    verdict_string = _truthy_or(
        parsed_object.get("Verdict"),
        parsed_object.get("verdict"),
        parsed_object.get("review_verdict"),
    )
    canonical = to_canonical_verdict(verdict_string)

    overlay_raw = parsed_object.get("verdict_overlay")
    overlay_array: list[dict[str, Any]] = (
        [entry for entry in overlay_raw if isinstance(entry, dict)]
        if isinstance(overlay_raw, list)
        else []
    )
    safe_array = parsed_object.get("validated_safe")
    safe_array = safe_array if isinstance(safe_array, list) else []
    needs_human_array = parsed_object.get("needs_human_judgment")
    needs_human_array = needs_human_array if isinstance(needs_human_array, list) else []
    observations_array = parsed_object.get("judge_observations")
    observations_array = observations_array if isinstance(observations_array, list) else []

    blocking_count = 0
    non_blocking_count = 0
    merged_subordinate_count = 0
    for entry in overlay_array:
        if entry.get("blocking") is True:
            blocking_count += 1
        else:
            non_blocking_count += 1
        merged = entry.get("merged_with")
        if isinstance(merged, list):
            merged_subordinate_count += len(merged)

    severity_counts = compute_overlay_severity_counts(overlay_array, configuration)
    verdict_overridden = False
    if severity_counts.get("critical", 0) > 0 and canonical != REJECT:
        canonical = REJECT
        verdict_overridden = True

    return JudgeSummary(
        verdict=canonical,
        verdict_overridden=verdict_overridden,
        verdict_icon=get_verdict_icon(canonical),
        blocking_count=blocking_count,
        non_blocking_count=non_blocking_count,
        safe_count=len(safe_array),
        severity_counts=severity_counts,
        published_primary_count=len(overlay_array),
        merged_subordinate_count=merged_subordinate_count,
        judge_observations_count=len(observations_array),
        needs_human_judgment_count=len(needs_human_array),
        parsed=True,
        raw_json=parsed_object,
    )


def _judge_unreadable(session_results: Mapping[str, Any]) -> str | None:
    return unreadable_reason(session_results.get("Judge"), agent="Judge")


def compute_verdict(
    session_results: Mapping[str, Any],
    configuration: Configuration,
    session_dir_path: str | None = None,
) -> VerdictResult:
    """Compute InspectorX's verdict from its Judge response."""
    unreadable = _judge_unreadable(session_results)
    if unreadable is not None:
        return VerdictResult(
            verdict=UNKNOWN,
            verdict_icon=get_verdict_icon(UNKNOWN),
            verdict_overridden=False,
            reason=unreadable,
            session_id=extract_session_id(session_dir_path),
        )

    summary = parse_judge_summary(response_of(session_results.get("Judge")) or "", configuration)
    if not summary.parsed or not isinstance(summary.raw_json, dict):
        return VerdictResult(
            verdict=UNKNOWN,
            verdict_icon=get_verdict_icon(UNKNOWN),
            verdict_overridden=False,
            reason=summary.parse_error or "Failed to parse Judge output",
            session_id="",
        )

    return VerdictResult(
        verdict=summary.verdict,
        verdict_icon=summary.verdict_icon,
        verdict_overridden=summary.verdict_overridden,
        reason=(
            f"Review verdict: {summary.verdict}. {summary.blocking_count} blocking, "
            f"{summary.non_blocking_count} non-blocking findings."
        ),
        session_id=extract_session_id(session_dir_path),
    )
