"""Self-contained HTML projection of collected Roundtable adoption records."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Protocol

from roundtable.decision import APPROVE

from .model import AdoptionRecord
from .query import query_records, read_jsonl

_SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

_BREAKDOWNS = (
    ("Reviews by repository", "reviews", "repository"),
    ("Reviews by configuration", "reviews", "configurationName"),
    ("Reviews by acquisition source", "reviews", "installationSource"),
    ("Reviews by version", "reviews", "toolVersion"),
    ("Reviews by verdict", "reviews", "verdict"),
    ("Findings by severity", "findings", "severity"),
    ("Findings by category", "findings", "category"),
    ("Findings by agent", "findings", "agent"),
)


class LinkResolver(Protocol):
    def __call__(self, record: AdoptionRecord) -> str | None: ...


def _unique_prs(records: tuple[AdoptionRecord, ...]) -> int:
    return len(
        {
            (
                record.organization,
                record.project,
                record.repository,
                record.pull_request_id,
            )
            for record in records
        }
    )


def _finding_count(records: tuple[AdoptionRecord, ...]) -> int:
    return sum(len(record.findings) for record in records)


def _table(title: str, result: dict[str, object]) -> str:
    rows = result["groups"]
    assert isinstance(rows, list)
    body = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['key']))}</td>"
        f'<td class="number">{int(row["value"]):,}</td>'
        "</tr>"
        for row in rows
        if isinstance(row, dict)
    )
    if not body:
        body = '<tr><td colspan="2" class="empty">No data</td></tr>'
    return (
        '<section class="panel">'
        f"<h2>{html.escape(title)}</h2>"
        "<table><thead><tr><th>Dimension</th><th>Count</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity.strip().lower(), -1)


def _highest_severity(record: AdoptionRecord) -> str:
    if not record.findings_available:
        return "N/A"
    if not record.findings:
        return "None"
    finding = max(
        record.findings,
        key=lambda item: (_severity_rank(item.severity), item.severity.casefold()),
    )
    return finding.severity


def _record_link(record: AdoptionRecord, resolver: LinkResolver | None) -> str | None:
    if record.canonical_url is not None:
        return record.canonical_url
    if resolver is not None:
        return resolver(record)
    return None


def _review_row(record: AdoptionRecord, resolver: LinkResolver | None) -> str:
    severity = _highest_severity(record)
    link = _record_link(record, resolver)
    pr_cell = html.escape(str(record.pull_request_id))
    if link is not None:
        pr_cell = f'<a href="{html.escape(link, quote=True)}">{pr_cell}</a>'
    return (
        "<tr>"
        f"<td>{html.escape(record.recorded_at)}</td>"
        f"<td>{html.escape(record.repository)}</td>"
        f'<td class="number">{pr_cell}</td>'
        f"<td>{html.escape(record.configuration_name)}</td>"
        f"<td>{html.escape(record.tool_version)}</td>"
        f"<td>{html.escape(record.verdict)}</td>"
        f"<td>{html.escape(severity)}</td>"
        "</tr>"
    )


def _review_priority(record: AdoptionRecord) -> tuple[bool, int, str]:
    return (
        record.verdict != APPROVE,
        _severity_rank(_highest_severity(record)),
        record.recorded_at,
    )


def _recent_reviews(
    records: tuple[AdoptionRecord, ...],
    resolver: LinkResolver | None,
) -> str:
    rows = "".join(
        _review_row(record, resolver)
        for record in sorted(records, key=_review_priority, reverse=True)
    )
    if not rows:
        rows = '<tr><td colspan="7" class="empty">No review records</td></tr>'
    return (
        '<section class="panel wide"><h2>Review execution audit queue</h2>'
        "<table><thead><tr><th>Recorded</th><th>Repository</th><th>PR</th>"
        "<th>Configuration</th><th>Version</th><th>Verdict</th><th>Highest severity</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></section>"
    )


def render_html(
    records: tuple[AdoptionRecord, ...],
    *,
    link_resolver: LinkResolver | None = None,
) -> str:
    available = sum(record.findings_available for record in records)
    cards = (
        ("Reviewed PRs", _unique_prs(records)),
        ("Review executions", len(records)),
        ("Findings", _finding_count(records)),
        ("Finding coverage", f"{available}/{len(records)}"),
    )
    card_html = "".join(
        '<div class="card">'
        f"<span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong>"
        "</div>"
        for label, value in cards
    )
    breakdowns = "".join(
        _table(title, query_records(records, metric, group_by))
        for title, metric, group_by in _BREAKDOWNS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Roundtable adoption report</title>
<style>
:root {{ color-scheme: light dark; font-family: Segoe UI, sans-serif; }}
body {{ margin: 0; background: #f4f6f8; color: #17202a; }}
main {{ max-width: 1400px; margin: auto; padding: 32px; }}
h1 {{ margin: 0 0 8px; }} .subtitle {{ color: #586069; margin: 0 0 24px; }}
.cards, .grid {{ display: grid; gap: 16px; }}
.cards {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 16px; }}
.grid {{ grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); }}
.card, .panel {{ background: white; border: 1px solid #d8dee4; border-radius: 8px; padding: 20px; }}
.card span {{ display: block; color: #586069; }} .card strong {{ font-size: 30px; }}
.wide {{ margin-top: 16px; overflow-x: auto; }} h2 {{ font-size: 18px; margin-top: 0; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ border-bottom: 1px solid #d8dee4; padding: 8px; text-align: left; }}
th {{ color: #586069; }} .number {{ text-align: right; }} .empty {{ color: #586069; text-align: center; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #0d1117; color: #e6edf3; }}
  .card, .panel {{ background: #161b22; border-color: #30363d; }}
  .subtitle, .card span, th, .empty {{ color: #8b949e; }}
  th, td {{ border-color: #30363d; }}
}}
</style>
</head>
<body><main>
<h1>Roundtable adoption report</h1>
<p class="subtitle">Offline aggregate generated from collected PR review metadata.</p>
<div class="cards">{card_html}</div>
<div class="grid">{breakdowns}</div>
{_recent_reviews(records, link_resolver)}
</main>
</body></html>
"""


def write_report(
    input_path: str | Path,
    output_path: str | Path,
    *,
    link_resolver: LinkResolver | None = None,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_html(read_jsonl(input_path), link_resolver=link_resolver),
        encoding="utf-8",
    )
    return target
