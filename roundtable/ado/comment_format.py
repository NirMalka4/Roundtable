"""ado.comment_format: the ONE agent-agnostic PR-comment template.

Every finding — from any agent — renders through the same skeleton; only which
optional lines appear differs, driven purely by field presence, never by which
agent produced the finding. The shape follows a human mental model:

    explain the claim in plain terms
      → ground it (an exploitability qualifier, the ordered execution path or
        evidence, and the impact conclusion — folded into ONE "Why this matters"
        section, because they are a qualifier and a conclusion OF the narrative,
        not three parallel siblings)
      → conclude with a single ready-to-use Fix (a one-click ``suggestion`` block
        when the change is localized, otherwise precise prose).

Determinism (E8): output is a pure function of the finding. Agent-supplied text
keeps its *balanced* fenced code blocks (so a ``suggestion``/``lang`` block renders
as real, one-click-appliable code) but has *unbalanced* fences defanged and the
watermark terminator ``-->`` always neutralized, so it can neither break the layout
nor forge the watermark; the whole comment is bounded to ADO's comment-size limit.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

from roundtable.extraction import CodeBlock, Remediation
from roundtable.graph import Configuration, get_product_name
from roundtable.runtime import get_agent_display_name, get_agent_label
from roundtable.types import severity_rank_map

from .anchor import AnchorDecision
from .markdown import clean_markdown as _clean
from .publish import (
    GroundingUnit,
    PublishableFinding,
    compute_stable_hash,
    watermark_hashed,
)

# ADO renders comments up to ~150k chars; stay well under with a safety margin.
_MAX_COMMENT_CHARS = 100_000
_TRUNCATION_MARKER = "\n\n*…comment truncated to fit the PR comment size limit.*"

_CATEGORY_LABEL = {"blocking": "🚫 Blocking", "non_blocking": "💡 Suggestion"}
_SUMMARY_ID = "__summary__"

# A headline is a single glanceable line; longer titles are truncated (the full
# text still renders in the Issue / Why-this-matters sections below).
_HEADLINE_MAX = 120

# Per-severity glyph, so a reader scans importance at a glance (in the comment
# headline and the executive-summary table). Keyed by canonical lowercase
# severity; an unknown value contributes no glyph (never breaks layout).
_SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "⚪",
}


# A leading enumerator an agent may bake into a trace step ("1. ", "2) ", "3 - ").
# The renderer emits its own ordered numbering, so this prefix is stripped to avoid
# a doubled "1. 1." (and a misleading "3. 4." where the agent's numbering skips).
_STEP_ENUMERATOR = re.compile(r"^\s*\d+\s*[.)\-]\s+")


def _denumber_step(step: str) -> str:
    """Strip an agent-supplied leading enumerator so the renderer owns the numbering."""
    return _STEP_ENUMERATOR.sub("", step, count=1)


def _severity_marker(severity: str) -> str:
    """The severity glyph, or ``""`` for an unrecognized value."""
    return _SEVERITY_EMOJI.get(severity.strip().lower(), "")


def _severity_rank(finding: PublishableFinding, configuration: Configuration) -> int:
    """Numeric severity rank for ordering; unknown sorts lowest."""
    return severity_rank_map(configuration).get(finding.severity.strip().lower(), -1)


def severity_ordered(
    findings: Sequence[PublishableFinding],
    configuration: Configuration,
) -> list[PublishableFinding]:
    """Findings by configured severity descending, preserving input order for ties."""
    return [
        f
        for _, f in sorted(
            enumerate(findings),
            key=lambda p: (-_severity_rank(p[1], configuration), p[0]),
        )
    ]


def _headline(text: str | None) -> str:
    """A one-line, length-bounded headline: collapse to the first line and
    truncate so a paragraph-valued title can never dominate the comment (the full
    text still appears in the Issue section below)."""
    cleaned = _clean(text)
    if not cleaned:
        return ""
    first_line = cleaned.splitlines()[0].strip()
    if len(first_line) > _HEADLINE_MAX:
        return first_line[: _HEADLINE_MAX - 1].rstrip() + "…"
    return first_line


def _cell(text: str) -> str:
    """Make agent text safe inside a markdown table cell: one line, pipes escaped."""
    return _headline(text).replace("|", "\\|")


def _fenced(language: str, body: str) -> str:
    """One fenced block, assembled as a single part.

    A fence is line-sensitive: every line between the markers is committed verbatim
    when a reader clicks Apply. Building it from separate parts lets the blank line
    that separates SECTIONS land *inside* the code, and a block scalar's trailing
    newline commits a trailing blank line — so a fence is assembled here, once, and
    only its own newlines are trimmed (leading spaces are the patch's indentation).
    """
    return f"```{language}\n{body.strip(chr(13) + chr(10))}\n```"


def _render_remediation(remediation: Remediation, *, inline: bool) -> list[str]:
    """Render the typed remediation union. ``suggestion`` becomes a one-click
    ``suggestion`` block on an inline thread (ADO applies ``replacement`` to the
    anchored span), a plain fenced block otherwise; ``draft`` renders as a clearly
    labelled illustrative fence that is NEVER a ``suggestion`` (applying it would
    overwrite live code); ``fix`` renders directional prose verbatim."""
    if remediation.kind == "suggestion":
        replacement = remediation.replacement
        if not (replacement and replacement.strip()):
            return []
        return ["**Fix**", _fenced("suggestion" if inline else "", replacement)]
    if remediation.kind == "draft":
        code = remediation.code
        if not (code and code.strip()):
            return []
        return [
            "**Draft** _(illustrative — not applied)_",
            _fenced(remediation.language or "", code),
        ]
    cleaned = _clean(remediation.prose)
    if cleaned:
        return ["**Fix**", cleaned]
    return []


def _render_fix(fix: CodeBlock | str | None, *, inline: bool) -> list[str]:
    """Render the single canonical Fix. A ``CodeBlock`` becomes a one-click
    ``suggestion`` block on an inline thread (ADO applies it to the anchored
    span), a language-fenced block otherwise; prose renders verbatim."""
    if isinstance(fix, CodeBlock):
        return ["**Fix**", _fenced("suggestion" if inline else (fix.language or ""), fix.code)]
    cleaned = _clean(fix if isinstance(fix, str) else None)
    if cleaned:
        return ["**Fix**", cleaned]
    return []


def _render_grounding_unit(unit: GroundingUnit, *, label: str | None) -> list[str]:
    """Render one provenance-tagged grounding unit: qualifier → ordered steps →
    conclusion. ``label`` prefixes the steps when several units coexist, so the
    reader never mistakes two agents' paths for one merged path (E4)."""
    lines: list[str] = []
    if unit.exploitability is not None:
        reason = _clean(unit.exploitability.reasoning)
        suffix = f" — {reason}" if reason else ""
        lines.append(f"▸ Exploitability: {_clean(unit.exploitability.rating)}{suffix}")
    if unit.trace:
        if label:
            lines.append(f"_{label}:_")
        lines.extend(
            f"{i}. {_denumber_step(_clean(step))}" for i, step in enumerate(unit.trace, start=1)
        )
    if unit.impact:
        lines.append(f"↳ Impact: {_clean(unit.impact)}")
    return lines


def _render_evidence_item(text: str) -> list[str]:
    """One evidence entry. A fenced code block renders as a standalone block (a
    ``- `` list prefix would break the fence); plain text renders as a bullet."""
    cleaned = _clean(text)
    if not cleaned:
        return []
    if "```" in cleaned:
        return [cleaned]
    return [f"- {cleaned}"]


def _render_why(finding: PublishableFinding) -> list[str]:
    """The folded 'Why this matters' section. Renders grounding units when present
    (labeled by agent if more than one carries steps), else evidence bullets;
    collapses entirely when the finding carries neither."""
    units = finding.grounding
    label_steps = sum(1 for u in units if u.trace) > 1
    body: list[str] = []
    for unit in units:
        body.extend(_render_grounding_unit(unit, label=unit.source_agent if label_steps else None))
    if not any(u.trace for u in units) and finding.evidence:
        for b in finding.evidence:
            body.extend(_render_evidence_item(b))
    if not body:
        return []
    return ["**Why this matters**", *body]


def _render_location_line(decision: AnchorDecision) -> list[str]:
    """A general (non-inline) thread names the location inline in the body, since
    ADO won't anchor it. A finding with no file is a genuinely PR-level comment;
    one with a file explains why it couldn't be anchored to that file."""
    if not decision.file_path:
        return ["**Location:** _not tied to a specific file — posted as a general PR comment._"]
    reason = f" — {decision.downgrade_reason}" if decision.downgrade_reason else ""
    return [f"**Location:** {decision.file_path}{reason}"]


def _also_affects(finding: PublishableFinding) -> list[str]:
    if not finding.additional_locations:
        return []
    sigs = [
        f"{loc.file_path or '(no file)'}:{loc.start_line or '?'}"
        for loc in finding.additional_locations
    ]
    return [f"📂 Also affects: {', '.join(sigs)}"]


def _bound(text: str) -> str:
    if len(text) <= _MAX_COMMENT_CHARS:
        return text
    keep = _MAX_COMMENT_CHARS - len(_TRUNCATION_MARKER)
    return text[:keep] + _TRUNCATION_MARKER


def _agent_path(session_reference: str, agent: str, *parts: str) -> str:
    return str(PurePosixPath(session_reference) / "agents" / agent / PurePosixPath(*parts))


def publication_footer(
    configuration: Configuration,
    session_reference: str,
    *,
    source_agents: Sequence[str] = (),
) -> str:
    """Minimal local follow-up details for a finding or summary thread."""
    if not session_reference or not session_reference.strip():
        raise ValueError("Publishing requires a non-empty local session reference.")
    if any(char in session_reference for char in "\r\n"):
        raise ValueError("Publishing requires a single-line local session reference.")

    escaped_reference = html.escape(session_reference, quote=True)
    if source_agents:
        lines: list[str] = []
        for agent in source_agents:
            response = html.escape(
                _agent_path(session_reference, agent, "response.md"),
                quote=True,
            )
            display_name = html.escape(
                get_agent_display_name(agent, configuration),
                quote=True,
            )
            lines.append(
                f"- {display_name} full response "
                f"(relative to Roundtable artifacts directory): <code>{response}</code>"
            )
        return "\n".join(lines)
    return "\n".join(
        [
            "- Local artifacts (relative to Roundtable artifacts directory): "
            f"<code>{escaped_reference}</code>",
            "- Open report (session path relative to Roundtable artifacts directory): "
            f"<code>roundtable report &quot;{escaped_reference}&quot; --open</code>",
        ]
    )


def with_publication_footer(
    content: str,
    configuration: Configuration,
    session_reference: str,
    *,
    source_agents: Sequence[str] = (),
) -> str:
    footer = publication_footer(
        configuration,
        session_reference,
        source_agents=source_agents,
    )
    suffix = f"\n\n---\n\n{footer}"
    if len(content) + len(suffix) <= _MAX_COMMENT_CHARS:
        return content.rstrip() + suffix
    keep = _MAX_COMMENT_CHARS - len(_TRUNCATION_MARKER) - len(suffix)
    return content[: max(0, keep)].rstrip() + _TRUNCATION_MARKER + suffix


def render_comment(
    finding: PublishableFinding,
    decision: AnchorDecision,
    *,
    session_id: str,
) -> str:
    """Render the full comment body for one finding, inline or general."""
    inline = decision.kind == "inline"
    headline = _headline(finding.title) or finding.id
    agents = (
        ", ".join(get_agent_display_name(a) for a in finding.source_agents) or get_product_name()
    )
    meta = " · ".join(
        [
            f"**{finding.id}**",
            _CATEGORY_LABEL.get(finding.category, "💡 Suggestion"),
            agents,
        ]
    )
    sev_marker = _severity_marker(finding.severity)
    sev_prefix = f"{sev_marker} " if sev_marker else ""
    parts: list[str] = [
        watermark_hashed(finding.id, finding.stable_hash),
        f"## {sev_prefix}{finding.severity} {headline}",
        meta,
    ]
    if not inline:
        parts.extend(_render_location_line(decision))
    # The Issue section carries the plain-terms explanation. Skip it when the
    # description is absent or merely repeats the title (headline already says it),
    # so the same text never appears twice in one comment.
    description = _clean(finding.description)
    if description and description != _clean(finding.title):
        parts.extend(["**Issue**", description])
    parts.extend(_render_why(finding))
    if finding.remediation is not None:
        parts.extend(_render_remediation(finding.remediation, inline=inline))
    else:
        parts.extend(_render_fix(finding.fix, inline=inline))
    parts.extend(_also_affects(finding))
    parts.append(f"<sub>{get_product_name()} · {session_id} · {agents}</sub>")
    return _bound("\n\n".join(p for p in parts if p))


def summary_stable_hash(session_id: str, findings: Sequence[PublishableFinding]) -> str:
    """Deterministic watermark hash for the executive-summary thread (its own
    identity, independent of any single finding — E5/R3)."""
    ids = sorted(f.id for f in findings)
    return compute_stable_hash([_SUMMARY_ID, session_id, *ids])


def format_executive_summary(
    session_id: str,
    verdict: str,
    findings: Sequence[PublishableFinding],
    configuration: Configuration,
) -> str:
    """A PR-level summary thread with its own deterministic watermark."""
    watermark = watermark_hashed(_SUMMARY_ID, summary_stable_hash(session_id, findings))
    blocking = [f for f in findings if f.category == "blocking"]
    ordered = severity_ordered(findings, configuration)
    lines = [
        watermark,
        "",
        f"## {get_product_name()} review — {verdict}",
        "",
        f"**{len(findings)}** finding(s) · **{len(blocking)}** blocking.",
        "",
        "| # | Severity | ID | Agent | Title | File |",
        "| - | -------- | -- | ----- | ----- | ---- |",
    ]
    for i, f in enumerate(ordered, start=1):
        where = f"{f.file_path}:{f.start_line}" if f.file_path and f.start_line else "—"
        block_flag = "🚫 " if f.category == "blocking" else ""
        sev_marker = _severity_marker(f.severity)
        sev_cell = f"{block_flag}{sev_marker} {f.severity}".strip()
        agents = ", ".join(get_agent_label(a) for a in f.source_agents)
        lines.append(
            f"| {i} | {sev_cell} | {f.id} | {_cell(agents)} | {_cell(f.title) or f.id} | {_cell(where)} |"
        )
    lines.append("")
    lines.append(f"<sub>{get_product_name()} · {session_id}</sub>")
    return _bound("\n".join(lines))


def min_severity_view(
    findings: Iterable[PublishableFinding],
    floor: str | None,
    configuration: Configuration,
) -> list[PublishableFinding]:
    """A filtered *view* (never a mutation, E6): keep findings at or above the
    ``floor`` severity. An unknown/absent floor keeps everything; a finding whose
    severity is unrecognized is treated as passing (fail-open, never silently
    dropped)."""
    if not floor:
        return list(findings)
    ranks = severity_rank_map(configuration)
    floor_rank = ranks.get(floor.strip().lower())
    if floor_rank is None:
        return list(findings)
    return [f for f in findings if ranks.get(f.severity.strip().lower(), len(ranks)) >= floor_rank]


class DefaultCommenter:
    """The engine's built-in renderer — this module's two templates as a
    :class:`~roundtable.delivery.commenter.Commenter`.

    Selected whenever a projector supplies no commenter of its own, so a config
    that never thinks about rendering gets exactly the behaviour it had before the
    seam existed. ``published`` is accepted and ignored: this summary is
    deliberately built over the FULL plan, so a below-floor finding still surfaces
    in the table even though it got no thread.
    """

    name = "default"

    def __init__(self, configuration: Configuration) -> None:
        self._configuration = configuration

    def render_thread(
        self,
        finding: PublishableFinding,
        decision: AnchorDecision,
        *,
        session_id: str,
    ) -> str:
        return render_comment(finding, decision, session_id=session_id)

    def render_summary(
        self,
        session_id: str,
        verdict: str,
        findings: Sequence[PublishableFinding],
        *,
        published: Sequence[PublishableFinding] = (),
    ) -> str:
        return format_executive_summary(session_id, verdict, findings, self._configuration)
