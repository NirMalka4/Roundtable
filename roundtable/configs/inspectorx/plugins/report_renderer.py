"""Render the InspectorX human-readable ``verdict.md`` report.

The slim persistence layer (``persistence/trace.py``) writes a one-line verdict
stub. This module promotes that stub to the full human ``verdict.md`` report
via :func:`render_review_report`:

    metadata header → Executive Summary (verdict label · TL;DR · Why this matters
    · Watch items table · Count Verification) → Blocking Findings → Non-Blocking
    Issues → Needs Human Judgment → footer.

Each finding renders Severity (emoji), Location, Category, Agent (display names),
Description, Evidence bullets and Fix. It reuses the deterministic
:func:`extract_publish_plan` resolution (overlay × specialist index) so the report
and the ADO publish plan agree on *which* findings exist and how they bucket. It
lives beside ``publish.py`` (same layer) because it consumes ``PublishPlan`` —
keeping it out of ``output/`` avoids an ``output ↔ ado`` cycle.

This is the **non-deterministic** (LLM-output) surface — the goal
is structural richness, not byte-stability.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from roundtable.ado import PublishableFinding, clean_markdown
from roundtable.decision import VerdictResult
from roundtable.delivery import ReportResult
from roundtable.extraction import CodeBlock, Remediation
from roundtable.graph import get_product_emoji, get_product_name, get_report_title
from roundtable.result_access import response_of
from roundtable.runtime import get_agent_display_name
from roundtable.types import severity_levels, severity_rank

from .configuration import inspectorx_configuration
from .verdict import parse_judge_summary
from .verdict_overlay import (
    OverlayRef,
    PublishPlan,
    check_count_parity,
    extract_publish_plan,
)

# Severity → emoji.
# (DEFAULT_SEVERITY_ICONS with critical overridden to the alert glyph).
_SEVERITY_ICON: dict[str, str] = {
    "critical": "🚨",
    "high": "🔶",
    "medium": "🟨",
    "low": "🟦",
    "info": "ℹ️",
    "style": "🎨",
}
_SEVERITY_FALLBACK_ICON = "▪️"


# Canonical severities are vocabulary-derived. ``warning`` (Historian) and
# ``style`` are non-vocab display tiers kept only to preserve report ordering.
def _severity_sort_sequence() -> list[str]:
    ordered = list(reversed(severity_levels(inspectorx_configuration())))
    ordered.insert(ordered.index("low"), "warning")
    ordered.append("style")
    return ordered


def _severity_order() -> dict[str, int]:
    return {name: index for index, name in enumerate(_severity_sort_sequence())}


def _fix_severities() -> frozenset[str]:
    return frozenset(
        severity
        for severity in severity_levels(inspectorx_configuration())
        if severity_rank(inspectorx_configuration(), severity)
        >= severity_rank(inspectorx_configuration(), "medium")
    )


_VERDICT_LABELS: dict[str, str] = {
    "APPROVE_WITH_SUGGESTIONS": "Approve with suggestions",
    "APPROVE": "Approve",
    "REJECT": "Reject",
}

_BROKEN_INTERNAL_LINK_RE = re.compile(r"\[([^\]]+)\]\((#[^)]+)\)")
_ANCHOR_STRIP_RE = re.compile(r"[^\w\s-]")
_ANCHOR_WS_RE = re.compile(r"\s+")
_ANCHOR_DASH_RE = re.compile(r"-+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CODE_SPAN_RE = re.compile(r"(`[^`]+`)")


# ── text helpers ──────────────────────────────


def _escape_html(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _strip_broken_internal_links(text: str) -> str:
    return _BROKEN_INTERNAL_LINK_RE.sub(r"\1", text)


def _format_inline(value: Any) -> str:
    """Escape non-code text, preserve backtick spans,
    newlines → ``<br>``."""
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = _strip_broken_internal_links(value)
        # Split on backtick-delimited segments; escape only non-code parts.
        out = "".join(
            seg if seg.startswith("`") else _escape_html(seg)
            for seg in _CODE_SPAN_RE.split(stripped)
        )
        return out.replace("\n", "<br>")
    if isinstance(value, bool):
        return _escape_html(str(value).lower())
    if isinstance(value, int | float):
        return _escape_html(str(value))
    if isinstance(value, list | tuple):
        return " | ".join(filter(None, (_format_inline(v) for v in value)))
    return _escape_html(str(value))


def _sanitize_cell(value: Any) -> str:
    return (
        _escape_html(str(value if value is not None else ""))
        .replace("|", "\\|")
        .replace("\n", " ")
        .strip()
    )


def _to_anchor_slug(text: str) -> str:
    slug = text.lower()
    slug = _ANCHOR_STRIP_RE.sub("", slug)
    slug = _ANCHOR_WS_RE.sub("-", slug)
    slug = _ANCHOR_DASH_RE.sub("-", slug)
    return slug.strip("-")


def _verdict_label(verdict: str) -> str:
    if verdict in _VERDICT_LABELS:
        return _VERDICT_LABELS[verdict]
    return verdict.replace("_", " ").strip() or "Unknown"


def _severity_icon(severity: str) -> str:
    return _SEVERITY_ICON.get(severity.lower(), _SEVERITY_FALLBACK_ICON)


def _build_intent_lines(value: str | None) -> list[str]:
    """Split the executive summary into sentence paragraphs."""
    text = ""
    if isinstance(value, str):
        text = _ANCHOR_WS_RE.sub(" ", _strip_broken_internal_links(value)).strip()
    if not text:
        return ["No clear change intent was provided by the Judge output."]
    sentences = [_ANCHOR_WS_RE.sub(" ", part).strip() for part in _SENTENCE_SPLIT_RE.split(text)]
    sentences = [s for s in sentences if s]
    return sentences or [text]


def judge_executive_summary(session_results: Mapping[str, Any]) -> str | None:
    """Return Judge's ``executive_summary`` string, or ``None`` if unavailable."""
    judge = session_results.get("Judge")
    if isinstance(judge, Mapping):
        response = judge.get("response")
    elif judge is not None:
        response = response_of(judge)
    else:
        response = None
    if not isinstance(response, str) or not response:
        return None

    summary = parse_judge_summary(response, inspectorx_configuration())
    if not summary.parsed or not isinstance(summary.raw_json, dict):
        return None
    text = summary.raw_json.get("executive_summary")
    return text.strip() if isinstance(text, str) and text.strip() else None


# ── finding helpers ───────────────────────────────────────────────────────────


def _agents_display(finding: PublishableFinding) -> str:
    """Map a finding's source-agent keys to their friendly display names."""
    config = inspectorx_configuration()
    return ", ".join(get_agent_display_name(key, config) for key in finding.source_agents if key)


def _location_inline(finding: PublishableFinding) -> str | None:
    """``file:line`` for the per-finding **Location** line (repo-relative)."""
    if not finding.file_path:
        return None
    loc = finding.file_path
    if finding.start_line is not None:
        loc += f":{finding.start_line}"
    return loc


def _watch_file_cell(finding: PublishableFinding) -> str:
    """File column for the watch-items table — a repo-relative file:line link."""
    if not finding.file_path:
        return "—"
    rel = finding.file_path
    if finding.start_line is not None:
        label = f"{rel}:{finding.start_line}"
        target = f"{rel}#L{finding.start_line}"
    else:
        label = rel
        target = rel
    return f"[{_sanitize_cell(label)}]({target})"


def _append_watch_table(lines: list[str], findings: Sequence[PublishableFinding]) -> None:
    rows: list[tuple[int, list[str]]] = []
    for f in findings:
        severity = (f.severity or "unknown").lower()
        heading = f"{f.id}: {f.title}" if f.id else f.title
        anchor = _to_anchor_slug(heading)
        title_cell = (
            f"[{_sanitize_cell(f.title)}](#{anchor})" if anchor else _sanitize_cell(f.title)
        )
        row = [
            f"{_severity_icon(severity)} {_sanitize_cell(severity)}",
            title_cell,
            _watch_file_cell(f),
        ]
        rows.append((_severity_order().get(severity, 99), row))
    rows.sort(key=lambda r: r[0])

    lines.append("| Severity | Finding | File |")
    lines.append("| --- | --- | --- |")
    for _, row in rows:
        lines.append("| " + " | ".join(row) + " |")


def _append_markdown_block(lines: list[str], label: str, text: str) -> None:
    """Emit a labelled block of agent-authored Markdown structurally verbatim.

    ``prose``/``fix`` are contract-typed as Markdown, so they are *block* values:
    routing them through :func:`_format_inline` would collapse their newlines to
    ``<br>`` and destroy the lists, paragraphs and fenced blocks that make them
    Markdown. Mirrors ``comment_format``, which renders the same fields as blocks.
    """
    lines.append(label)
    lines.append("")
    lines.append(clean_markdown(text))
    lines.append("")


def _render_report_remediation(lines: list[str], remediation: Remediation) -> None:
    """Render the typed remediation union into the offline report. The report is
    read-only (no ADO "Apply Change"), so a ``suggestion``'s replacement renders as
    a plain fenced block; ``draft`` as a labelled illustrative fence; ``fix`` as
    inline prose."""
    if remediation.kind == "suggestion":
        if remediation.replacement and remediation.replacement.strip():
            lines.append("**Fix**:")
            lines.append("```")
            lines.append(remediation.replacement)
            lines.append("```")
            lines.append("")
    elif remediation.kind == "draft":
        if remediation.code and remediation.code.strip():
            lines.append("**Draft** (illustrative — not applied):")
            lines.append(f"```{remediation.language or ''}")
            lines.append(remediation.code)
            lines.append("```")
            lines.append("")
    elif remediation.prose and remediation.prose.strip():
        _append_markdown_block(lines, "**Fix**:", remediation.prose)


def _render_finding(
    lines: list[str],
    finding: PublishableFinding,
) -> None:
    title = finding.title or "Untitled"
    lines.append(f"### {_escape_html(finding.id)}: {_escape_html(title)}")
    lines.append("")
    sev = (finding.severity or "low").lower()
    lines.append(f"**Severity**: {_severity_icon(sev)} {_escape_html(sev)}")
    location = _location_inline(finding)
    if location:
        lines.append(f"**Location**: {_escape_html(location)}")
    if finding.judge_category:
        lines.append(f"**Category**: {_escape_html(finding.judge_category)}")
    agents = _agents_display(finding)
    if agents:
        lines.append(f"**Agent**: {_escape_html(agents)}")
    lines.append("")
    lines.append("#### Description")
    lines.append("")
    lines.append(_format_inline(finding.description or finding.title or ""))
    lines.append("")
    if finding.evidence:
        lines.append("#### Evidence")
        for bullet in finding.evidence:
            lines.append(f"- {_format_inline(bullet)}")
        lines.append("")
    if finding.remediation is not None:
        _render_report_remediation(lines, finding.remediation)
    elif isinstance(finding.fix, CodeBlock):
        lines.append("**Fix**:")
        lines.append(f"```{finding.fix.language or ''}")
        lines.append(finding.fix.code)
        lines.append("```")
        lines.append("")
    elif isinstance(finding.fix, str) and finding.fix.strip():
        _append_markdown_block(lines, "**Fix**:", finding.fix)
    lines.append("---")
    lines.append("")


def _render_findings_section(
    lines: list[str],
    heading: str,
    findings: Sequence[PublishableFinding],
) -> None:
    lines.append(heading)
    lines.append("")
    if not findings:
        lines.append("None.")
        lines.append("")
        return
    for finding in findings:
        _render_finding(lines, finding)


def _render_human_judgment(lines: list[str], refs: Sequence[OverlayRef]) -> None:
    lines.append("## 🤔 Needs Human Judgment")
    lines.append("")
    for ref in refs:
        heading = ref.title or f"{ref.source_agent}::{ref.finding_id}"
        lines.append(f"### {_escape_html(ref.finding_id)}: {_escape_html(heading)}")
        lines.append(f"**Source**: {_escape_html(ref.source_agent)}")
        if ref.reason:
            lines.append(f"**Reason**: {_escape_html(ref.reason)}")
        lines.append("")
    lines.append("---")
    lines.append("")


def render_review_report(
    verdict: VerdictResult,
    plan: PublishPlan,
    *,
    repo_name: str | None = None,
    source_branch: str | None = None,
    session_id: str | None = None,
    review_date: str | None = None,
    executive_summary: str | None = None,
) -> str:
    """Render the full human ``verdict.md`` from a resolved publish plan."""
    date = review_date or datetime.now(UTC).strftime("%Y-%m-%d")
    sid = session_id or plan.session_id

    criticals = list(plan.blocking_findings)
    non_blocking = list(plan.non_blocking_findings)
    fix_severities = _fix_severities()
    substantive_nb = [f for f in non_blocking if (f.severity or "low").lower() in fix_severities]
    non_substantive_nb = [
        f for f in non_blocking if (f.severity or "low").lower() not in fix_severities
    ]
    needs_human = plan.needs_human_judgment_refs
    validated_safe = plan.validated_safe_refs
    judge_obs = plan.judge_observations

    lines: list[str] = []

    # Header
    lines.append(
        "# "
        + " ".join(
            p
            for p in (
                get_product_emoji(inspectorx_configuration().root),
                get_product_name(inspectorx_configuration().root),
                get_report_title(inspectorx_configuration().root),
            )
            if p
        )
    )
    lines.append("")
    lines.append(f"**Repository**: {_escape_html(repo_name or 'Unknown')}  ")
    lines.append(f"**Source Branch**: {_escape_html(source_branch or 'Unknown')}  ")
    lines.append(f"**Review Date**: {date}  ")
    lines.append(f"**Session ID**: {sid}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Executive Summary
    lines.append("## 📊 Executive Summary")
    lines.append("")
    lines.append(f"**Verdict**: {verdict.verdict_icon} {_verdict_label(verdict.verdict)}")
    lines.append("")
    lines.append("### TL;DR")
    lines.append("")
    intent = _build_intent_lines(executive_summary)
    for i, sentence in enumerate(intent):
        lines.append(_format_inline(sentence))
        if i < len(intent) - 1:
            lines.append("")
    lines.append("")

    # Why this matters
    lines.append("### Why this matters")
    lines.append("")
    lines.append(
        f"- {len(criticals)} blocking issue{'' if len(criticals) == 1 else 's'} "
        "still require merge stop."
        if criticals
        else "- No blocking issues were found."
    )
    lines.append(
        f"- {len(substantive_nb)} item{'' if len(substantive_nb) == 1 else 's'} "
        "should be fixed before merge."
        if substantive_nb
        else "- No pre-merge fixes were flagged."
    )
    follow_up = len(non_substantive_nb) + len(needs_human)
    lines.append(
        f"- {follow_up} follow-up item{'' if follow_up == 1 else 's'} remain after merge."
        if follow_up
        else "- No follow-up work was flagged."
    )
    lines.append(
        f"- {len(validated_safe)} item{'' if len(validated_safe) == 1 else 's'} "
        "were reviewed and marked safe."
        if validated_safe
        else "- No items were explicitly marked safe."
    )
    lines.append("")

    # Watch items
    lines.append("### Watch items")
    lines.append("")
    all_findings = criticals + non_blocking
    if not all_findings:
        lines.append("No watch items.")
        lines.append("")
    else:
        _append_watch_table(lines, all_findings)
        lines.append("")

    # Count Verification
    displayed_detailed = len(criticals) + len(non_blocking)
    displayed_flagged = displayed_detailed + len(validated_safe) + len(needs_human)
    if displayed_flagged > 0 or judge_obs:
        displayed_sum = displayed_detailed + len(validated_safe) + len(needs_human)
        passed = displayed_sum == displayed_flagged
        lines.append(f"**Count Verification**: {'✅ Passed' if passed else '❌ Failed'}")
        lines.append("")
        lines.append(f"- Total flagged: {displayed_flagged}")
        lines.append(f"- In findings: {displayed_detailed}")
        lines.append(f"- Validated safe: {len(validated_safe)}")
        lines.append(f"- Needs human judgment: {len(needs_human)}")
        lines.append(f"- Judge observations: {len(judge_obs)}")
        lines.append(
            f"- Sum: {displayed_detailed} + {len(validated_safe)} + "
            f"{len(needs_human)} = {displayed_sum}"
        )
        lines.append("")

    lines.append("---")
    lines.append("")

    if verdict.verdict_overridden:
        lines.append("> Verdict was auto-overridden to REJECT due to a Critical finding.")
        lines.append("")

    _render_findings_section(
        lines,
        "## 🎯 Blocking Findings",
        criticals,
    )
    _render_findings_section(
        lines,
        "## ⚠️ Non-Blocking Issues",
        non_blocking,
    )

    if needs_human:
        _render_human_judgment(lines, needs_human)

    lines.append("---")
    lines.append("")
    config_root = inspectorx_configuration().root
    lines.append(f"*Generated by {get_product_name(config_root)} {get_report_title(config_root)}*")

    return "\n".join(lines).rstrip("\n") + "\n"


class VerdictOverlayReport:
    """``report: verdict_overlay`` — render ``verdict.md`` from the ADO publish plan.

    The report and the publish plan resolve findings the *same* way (overlay ×
    specialist index), so they cannot disagree on which findings exist or how they
    bucket. Best-effort by contract: any failure returns ``None`` and the caller
    falls back to the slim verdict stub — a render bug must never change a verdict.
    """

    name = "verdict_overlay"

    def render(
        self,
        verdict: VerdictResult,
        counts: Mapping[str, int] | None,
        session_results: Mapping[str, Any],
        *,
        repo_name: str | None,
        source_branch: str | None,
        session_id: str,
    ) -> ReportResult | None:
        plan = extract_publish_plan(session_results, session_dir_path=session_id)
        if plan is None:
            return None
        return ReportResult(
            markdown=render_review_report(
                verdict,
                plan,
                repo_name=repo_name,
                source_branch=source_branch,
                session_id=session_id,
                executive_summary=judge_executive_summary(session_results),
            ),
            # Output-integrity guard: the same count-parity check the publish
            # gate runs, evaluated at review time so a 0-vs-N mismatch is recorded
            # in-band rather than first surfacing at publish. 0/0 is clean.
            integrity_warning=check_count_parity(plan),
        )
