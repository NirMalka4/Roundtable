"""Configuration-neutral verdict primitives, rendering, and process mappings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Canonical verdict labels shared by configuration-owned verdict derivations.
APPROVE = "APPROVE"
APPROVE_WITH_SUGGESTIONS = "APPROVE_WITH_SUGGESTIONS"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"

# Process exit codes. Single source of truth for the verdict→exit mapping.
EXIT_CLEAN = 0  # verdict APPROVE
EXIT_FINDINGS = 1  # APPROVE_WITH_SUGGESTIONS / REJECT / UNKNOWN
EXIT_ERROR = 2  # pipeline failure (exception)
EXIT_ABORTED = 3  # user cancel / global timeout / guard reject
EXIT_BAD_ARGS = 4  # invalid CLI arguments


@dataclass
class VerdictResult:
    """The verdict surface produced by the persist/verdict phase."""

    verdict: str
    verdict_icon: str
    verdict_overridden: bool
    reason: str
    session_id: str = ""


def get_verdict_icon(verdict: str) -> str:
    """Verdict label to display icon."""
    return {
        APPROVE: "\u2705",
        APPROVE_WITH_SUGGESTIONS: "\u26a0\ufe0f",
        REJECT: "\u274c",
    }.get(verdict, "\U0001f534")


def extract_session_id(session_dir_path: str | None) -> str:
    """Session folder name from a path."""
    if not session_dir_path:
        return ""
    normalized = session_dir_path.replace("\\", "/").rstrip("/")
    segments = normalized.split("/")
    return segments[-1] if segments else ""


def verdict_to_exit_code(verdict: str) -> int:
    """Verdict → process exit code.

    APPROVE → CLEAN(0); everything else (AWS / REJECT / UNKNOWN) → FINDINGS(1).
    ERROR/ABORTED/BAD_ARGS are raised by the CLI for non-verdict conditions.
    """
    return EXIT_CLEAN if verdict == APPROVE else EXIT_FINDINGS


def render_verdict_md(verdict: VerdictResult, counts: Mapping[str, int] | None = None) -> str:
    """Render the slim human-readable ``verdict.md`` (no-Judge / fallback stub).

    The rich report is rendered by the ADO report layer from the resolved publish
    plan; this stub is the fallback used when no plan is available.
    """
    lines = [
        f"# Review verdict: {verdict.verdict_icon} {verdict.verdict}",
        "",
        verdict.reason,
    ]
    if verdict.verdict_overridden:
        lines += ["", "> Verdict was auto-overridden to REJECT due to a Critical finding."]
    if counts is not None:
        lines += [
            "",
            "## Findings",
            f"- Blocking: {counts.get('blocking', 0)}",
            f"- Non-blocking: {counts.get('nonBlocking', 0)}",
            f"- Total: {counts.get('all', 0)}",
            f"- Security: {counts.get('security', 0)}",
        ]
    return "\n".join(lines) + "\n"
