"""Render a :class:`ReportModel` into a single self-contained, offline HTML file.

No network, no CDN, no build step. Python computes the SVG geometry and heat
colours; all agent text is embedded once as a JSON island and injected into the
DOM via ``textContent`` (never ``innerHTML``) so reviewed-code content can never
execute. The ``<`` in the JSON payload is escaped so an embedded ``</script>``
cannot break out of the data island.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from datetime import datetime

from roundtable.backend import BillingValue, format_ai_credits

from .layout import BundledEdge, LayoutResult, compute_layout
from .loader import AgentNode, ReportModel, RunMeta

# ── Metric tooltips (shown on hover to disambiguate the time bases) ───────────
_TIP_ELAPSED = (
    "Real time from run start to finish (finishedAt − startedAt) — what you actually waited for."
)
_TIP_COMPUTE = (
    "Sum of time spent inside LLM API calls across all agents. Exceeds elapsed "
    "because agents run concurrently."
)
_TIP_SESSION = "Click to open this run's artifacts folder on your machine (per-agent system / context / response)."
# ── Geometry ─────────────────────────────────────────────────────────────────
_LAYER_GAP_X = 230
_NODE_GAP_Y = 78
_MARGIN = 60
_NODE_R = 26


def _fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "—"
    secs = ms / 1000.0
    if secs < 60:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {secs % 60:04.1f}s"


def _elapsed_ms(started: str | None, finished: str | None) -> float | None:
    """Real wall-clock elapsed between two ISO-8601 instants, in ms."""
    if not started or not finished:
        return None
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t1 - t0).total_seconds() * 1000.0


def _fmt_num(n) -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"


def _non_api_ms(node: AgentNode) -> float | None:
    if node.wall_clock_ms is None or node.api_duration_ms is None:
        return None
    return max(0.0, node.wall_clock_ms - node.api_duration_ms)


def _heat(value: float | None, vmax: float) -> str:
    """Light-to-hot fill for a node keyed on its wall-clock share of the max."""
    if not value or vmax <= 0:
        return "#eef2f7"
    t = max(0.0, min(1.0, value / vmax))
    # Interpolate pale-blue → amber → red.
    stops = [(0.0, (238, 242, 247)), (0.5, (255, 224, 138)), (1.0, (233, 90, 68))]
    from itertools import pairwise

    for (t0, c0), (t1, c1) in pairwise(stops):
        if t <= t1:
            f = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            r = int(c0[0] + (c1[0] - c0[0]) * f)
            g = int(c0[1] + (c1[1] - c0[1]) * f)
            b = int(c0[2] + (c1[2] - c0[2]) * f)
            return f"rgb({r},{g},{b})"
    return "#e95a44"


def _layer_geometry(layout: LayoutResult) -> tuple[dict[str, tuple[float, float]], int, int]:
    """Absolute (x, y) per node, vertically centring each layer, plus canvas size."""
    tallest = max((len(layer) for layer in layout.layers), default=1)
    canvas_h = _MARGIN * 2 + max(tallest - 1, 0) * _NODE_GAP_Y
    canvas_w = _MARGIN * 2 + max(len(layout.layers) - 1, 0) * _LAYER_GAP_X
    coords: dict[str, tuple[float, float]] = {}
    for li, layer in enumerate(layout.layers):
        x = _MARGIN + li * _LAYER_GAP_X
        span = (len(layer) - 1) * _NODE_GAP_Y
        y0 = (canvas_h - span) / 2
        for order, key in enumerate(layer):
            coords[key] = (x, y0 + order * _NODE_GAP_Y)
    return coords, int(canvas_w), int(canvas_h)


def _node_glyph(node: AgentNode, x: float, y: float, fill: str) -> str:
    """A circle (LLM) or diamond (deterministic); red ring if invalid."""
    stroke = "#c0392b" if node.valid is False else "#38506e"
    sw = 3 if node.valid is False else 1.5
    label = html.escape((node.emoji + " " if node.emoji else "") + (node.display_name or node.key))
    common = f'class="node" data-key="{html.escape(node.key)}" tabindex="0"'
    if node.is_deterministic:
        d = _NODE_R
        pts = f"{x},{y - d} {x + d},{y} {x},{y + d} {x - d},{y}"
        shape = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    else:
        shape = (
            f'<circle cx="{x}" cy="{y}" r="{_NODE_R}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )
    badge = ""
    if node.attempts and node.attempts > 1:
        badge = (
            f'<circle cx="{x + _NODE_R - 4}" cy="{y - _NODE_R + 4}" r="8" fill="#c0392b"/>'
            f'<text x="{x + _NODE_R - 4}" y="{y - _NODE_R + 7}" class="badge-t">'
            f"{node.attempts}</text>"
        )
    txt = f'<text x="{x}" y="{y + _NODE_R + 13}" class="node-label">{label}</text>'
    return f"<g {common}>{shape}{badge}{txt}</g>"


def _edge_path(x1: float, y1: float, x2: float, y2: float, provenance: str, required: bool) -> str:
    dash = ""
    if provenance == "inferred-static":
        dash, opacity = 'stroke-dasharray="2 4"', "0.5"
    elif not required:
        dash, opacity = 'stroke-dasharray="6 5"', "0.75"
    else:
        opacity = "0.9"
    mx = (x1 + x2) / 2
    d = f"M {x1 + _NODE_R} {y1} C {mx} {y1}, {mx} {y2}, {x2 - _NODE_R - 6} {y2}"
    return (
        f'<path d="{d}" fill="none" stroke="#7a8ba3" stroke-width="1.4" '
        f'opacity="{opacity}" {dash} marker-end="url(#arrow)" '
        f'data-src="{html.escape("")}"/>'
    )


def _svg(model: ReportModel, layout: LayoutResult) -> str:
    coords, w, h = _layer_geometry(layout)
    vmax = max((n.wall_clock_ms or 0) for n in model.nodes) or 1.0
    node_by_key = {n.key: n for n in model.nodes}

    parts: list[str] = []
    parts.append(
        f'<svg id="dag" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        'xmlns="http://www.w3.org/2000/svg">'
    )
    parts.append(
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" '
        'orient="auto" markerUnits="strokeWidth">'
        '<path d="M0,0 L6,3 L0,6 Z" fill="#7a8ba3"/></marker></defs>'
    )
    # Edges first (under nodes).
    for edge in layout.edges:
        if edge.src in coords and edge.dst in coords:
            x1, y1 = coords[edge.src]
            x2, y2 = coords[edge.dst]
            parts.append(_edge_path(x1, y1, x2, y2, edge.provenance, edge.required))
    # Bundle stubs into dossier nodes.
    for bundle in layout.bundles:
        if bundle.dst in coords:
            x2, y2 = coords[bundle.dst]
            x1, y1 = x2 - _LAYER_GAP_X * 0.6, y2
            d = f"M {x1} {y1} L {x2 - _NODE_R - 6} {y2}"
            parts.append(
                f'<path d="{d}" fill="none" stroke="#b07cc6" stroke-width="4" '
                f'opacity="0.6" marker-end="url(#arrow)"/>'
            )
            parts.append(
                f'<text x="{x1 - 4}" y="{y1 - 8}" class="bundle-label" text-anchor="end">'
                f"\u2b1a {len(bundle.sources)} producers</text>"
            )
    # Nodes.
    for key, (x, y) in coords.items():
        node = node_by_key[key]
        fill = _heat(node.wall_clock_ms, vmax)
        parts.append(_node_glyph(node, x, y, fill))
    parts.append("</svg>")
    return "".join(parts)


def _node_payload(node: AgentNode, bundles: dict[str, BundledEdge]) -> dict:
    data = asdict(node)
    data.pop("billing", None)
    data.pop("legacy_premium_requests", None)
    if node.billing.is_complete:
        assert node.billing.total_nano_aiu is not None
        data["ai_credits"] = format_ai_credits(node.billing.total_nano_aiu)
    data.pop("required_deps", None)
    data.pop("optional_deps", None)
    if data.get("attempts_detail"):
        # Drop the verbose per-tool list; keep a count so the island stays small.
        trimmed = []
        for att in data["attempts_detail"]:
            tools = att.get("toolCalls") or att.get("toolsInvoked")
            failed_tools = [
                {
                    key: call[key]
                    for key in (
                        "name",
                        "state",
                        "durationMs",
                        "exitCode",
                        "toolError",
                        "toolErrorRetention",
                        "error",
                    )
                    if call.get(key) is not None
                }
                for call in (att.get("toolCalls") or [])
                if isinstance(call, dict)
                and (call.get("ok") is False or call.get("state") == "failed")
            ]
            if not failed_tools:
                failed_tools = [
                    dict(item)
                    for item in (att.get("toolErrors") or [])
                    if isinstance(item, dict) and item.get("name")
                ]
            att = {
                k: v
                for k, v in att.items()
                if k not in ("toolCalls", "toolsInvoked", "toolErrors", "billing")
            }
            if isinstance(att.get("timeoutSnapshot"), list):
                att["timeoutSnapshot"] = [
                    {
                        key: item[key]
                        for key in ("name", "state", "activeAtTimeout")
                        if item.get(key) is not None
                    }
                    for item in att["timeoutSnapshot"]
                    if isinstance(item, dict)
                ]
            else:
                att.pop("timeoutSnapshot", None)
            if tools:
                att["toolCount"] = len(tools)
            if failed_tools:
                att["failedToolCalls"] = failed_tools
            trimmed.append(att)
        data["attempts_detail"] = trimmed
    if node.key in bundles:
        data["bundle_sources"] = bundles[node.key].sources
    return data


def _kv_table(rows: list[tuple]) -> str:
    cells = []
    for row in rows:
        k, v = row[0], row[1]
        tip = row[2] if len(row) > 2 else None
        kcell = (
            f"<td class='k tip' title=\"{html.escape(tip)}\">{html.escape(k)}</td>"
            if tip
            else f"<td class='k'>{html.escape(k)}</td>"
        )
        cells.append(f"<tr>{kcell}<td>{v}</td></tr>")
    return f"<table class='kv'>{''.join(cells)}</table>"


def _pr_url(remote: str | None, pr_id) -> str | None:
    """Best-effort web URL for a PR from the git remote + PR id (ADO / GitHub)."""
    if not remote or not pr_id:
        return None
    r = str(remote).strip()
    if r.endswith(".git"):
        r = r[:-4]
    if "/_git/" in r:  # Azure DevOps: .../_git/<repo>/pullrequest/<id>
        return f"{r}/pullrequest/{pr_id}"
    if "github.com" in r:  # GitHub: .../<org>/<repo>/pull/<id>
        return f"{r}/pull/{pr_id}"
    return None


def _link(url: str | None, text: str, *, external: bool) -> str:
    """An anchor when ``url`` is set, else the escaped text alone."""
    if not url:
        return text
    extra = ' target="_blank" rel="noopener"' if external else ""
    return f'<a href="{html.escape(url, quote=True)}"{extra}>{text}</a>'


def _subject_card(meta: RunMeta) -> str:
    """What was reviewed: repo / branch / PR (+ clickable links) + diff + provenance.

    Renders only the rows actually present, and nothing at all for a legacy trace
    with no subject block — the local-artifacts link lives on the run card's
    clickable ``Session`` row, so a near-empty tile is never allocated.
    """
    s, d, p = meta.subject or {}, meta.diff_stat or {}, meta.provenance or {}
    remote = s.get("remoteUrl")
    rows: list[tuple[str, str]] = []
    if s.get("repo"):
        rows.append(("Repo", _link(remote, html.escape(str(s["repo"])), external=True)))
    if s.get("mode"):
        rows.append(("Mode", html.escape(str(s["mode"]))))
    if s.get("prId"):
        title = html.escape(str(s.get("prTitle") or ""))
        label = f"#{html.escape(str(s['prId']))}" + (f" {title}" if title else "")
        rows.append(("PR", _link(_pr_url(remote, s["prId"]), label, external=True)))
    if s.get("sourceBranch") or s.get("targetBranch"):
        src = html.escape(str(s.get("sourceBranch") or "?"))
        tgt = html.escape(str(s.get("targetBranch") or "?"))
        rows.append(("Branch", f"{src} &rarr; {tgt}"))
    if s.get("baseSha") or s.get("sourceSha"):
        base = html.escape(str(s.get("baseSha") or "")[:10])
        head = html.escape(str(s.get("sourceSha") or "")[:10])
        rows.append(("Commit", f"<code>{base or '—'}</code> .. <code>{head or '—'}</code>"))
    if d:
        rows.append(
            (
                "Diff",
                f"{_fmt_num(d.get('filesChanged'))} files &nbsp;"
                f"<span class='ins'>+{_fmt_num(d.get('insertions'))}</span> / "
                f"<span class='del'>&minus;{_fmt_num(d.get('deletions'))}</span>",
            )
        )
    if p.get("toolName") or p.get("toolVersion"):
        tool = f"{html.escape(str(p.get('toolName') or ''))} {html.escape(str(p.get('toolVersion') or ''))}".strip()
        if p.get("graphConfigSha"):
            tool += f" &middot; graph <code>{html.escape(str(p['graphConfigSha'])[:8])}</code>"
        rows.append(("Reviewer", tool))
    if not rows:
        return ""
    files_detail = ""
    changed = d.get("changedFiles")
    if isinstance(changed, list) and changed:
        items = "".join(f"<div>{html.escape(str(x))}</div>" for x in changed)
        files_detail = (
            f"<details class='small changed'><summary>{len(changed)} changed file(s)</summary>"
            f"{items}</details>"
        )
    return f'<div class="card"><h3>Subject</h3>{_kv_table(rows)}{files_detail}</div>'


def _overview(model: ReportModel) -> str:
    meta = model.meta
    elapsed_ms = _elapsed_ms(meta.started_at, meta.finished_at)
    concurrency = ""
    if elapsed_ms and meta.wall_clock_ms:
        # Summed per-agent wall time ÷ real elapsed ≈ average concurrency.
        concurrency = (
            f" &nbsp;<span class='muted'>({meta.wall_clock_ms / elapsed_ms:.1f}"
            "\u00d7 agent concurrency)</span>"
        )
    rows = [
        (
            "Session",
            _link(meta.session_uri, html.escape(meta.session_id), external=False),
            _TIP_SESSION,
        ),
        (
            "Verdict",
            f"<b>{html.escape(meta.verdict)}</b> {html.escape(meta.verdict_icon)}"
            + (" <span class='tag warn'>overridden</span>" if meta.verdict_overridden else ""),
        ),
        ("Findings", html.escape(json.dumps(meta.counts))),
        ("Agents", str(meta.agent_count)),
        ("Started", html.escape(meta.started_at or "—")),
        ("Finished", html.escape(meta.finished_at or "—")),
        ("Elapsed (wall clock)", _fmt_ms(elapsed_ms) + concurrency, _TIP_ELAPSED),
        ("Compute (summed API)", _fmt_ms(meta.api_duration_ms), _TIP_COMPUTE),
        ("Input tokens", _fmt_num(meta.input_tokens)),
        ("Cached input", _fmt_num(meta.cache_read_tokens)),
        ("Cache write", _fmt_num(meta.cache_write_tokens)),
        ("Reasoning", _fmt_num(meta.reasoning_tokens)),
        ("Output tokens", _fmt_num(meta.output_tokens)),
        ("Total tokens", _fmt_num(meta.total_tokens)),
        ("Tool rounds", _fmt_num(meta.rounds)),
        ("Invalid agents", _fmt_num(meta.invalid_count)),
    ]
    if meta.billing.is_complete:
        assert meta.billing.total_nano_aiu is not None
        rows.insert(
            8,
            (
                "AI Credits consumed",
                format_ai_credits(meta.billing.total_nano_aiu),
            ),
        )
    slowest = meta.performance.get("slowestByWallMs") or []
    slow_rows = "".join(
        f"<tr><td>{html.escape(s.get('agent', ''))}</td><td>{_fmt_ms(s.get('wallClockMs'))}</td></tr>"
        for s in slowest[:5]
    )
    slow_non_api = sorted(
        ((node.key, non_api) for node in model.nodes if (non_api := _non_api_ms(node)) is not None),
        key=lambda item: -item[1],
    )[:5]
    non_api_rows = "".join(
        f"<tr><td>{html.escape(agent)}</td><td>{_fmt_ms(duration)}</td></tr>"
        for agent, duration in slow_non_api
    )
    tax = meta.validation_tax or {}
    retried = ", ".join(tax.get("retriedAgents", [])) or "none"
    tax_credits = _billing_credits(tax.get("billingOnRejects"))
    tax_cost = (
        f"<br>AI Credits spent on rejects: {html.escape(tax_credits)}"
        if tax_credits is not None
        else ""
    )
    return f"""
    <section class="overview">
      {_subject_card(meta)}
      <div class="card">{_kv_table(rows)}</div>
      <div class="card">
        <h3>Slowest (wall clock)</h3>
        <table class="kv"><tbody>{slow_rows or "<tr><td>—</td></tr>"}</tbody></table>
        <h3>Slowest (non-API)</h3>
        <table class="kv"><tbody>{non_api_rows or "<tr><td>—</td></tr>"}</tbody></table>
        <h3>Validation tax</h3>
        <div class="muted small">retried: {html.escape(retried)}{tax_cost}</div>
      </div>
      <div class="card">
        <h3>Retries</h3>
        <div class="muted small">agents that needed another backend attempt</div>
        <div>{html.escape(retried)}</div>
      </div>
      {_tools_card(model)}
      {_anomalies_card(model)}
    </section>
    """


def _billing_credits(value: object) -> str | None:
    billing = BillingValue.from_dict(value if isinstance(value, dict) else None)
    if not billing.is_complete:
        return None
    assert billing.total_nano_aiu is not None
    return format_ai_credits(billing.total_nano_aiu)


def _anomalies_card(model: ReportModel) -> str:
    anomalies = model.meta.anomalies or {}
    timed_out = [str(agent) for agent in anomalies.get("timedOutAgents") or []]
    abnormal = [
        f"{item.get('agent', '—')}: {', '.join(map(str, item.get('reasons') or []))}"
        for item in anomalies.get("truncatedOrFilteredAgents") or []
        if isinstance(item, dict)
    ]
    rerouted = [
        f"{item.get('agent', '—')}: {item.get('declared', '—')} → {item.get('observed', '—')}"
        for item in anomalies.get("modelReroutedAgents") or []
        if isinstance(item, dict)
    ]
    rows = [
        ("Timed out", ", ".join(timed_out)),
        ("Abnormal finish", "; ".join(abnormal)),
        ("Model rerouted", "; ".join(rerouted)),
    ]
    visible = [(label, value) for label, value in rows if value]
    if not visible:
        return ""
    body = "".join(
        f"<tr><td class='k'>{html.escape(label)}</td><td>{html.escape(value)}</td></tr>"
        for label, value in visible
    )
    return (
        "<div class='card anomaly'><h3>Runtime anomalies</h3>"
        f"<table class='kv'><tbody>{body}</tbody></table></div>"
    )


def _fmt_tool_calls(tools: dict) -> str:
    """Render a ``{tool: {calls, agents}}`` map as ``tool: N``, busiest first."""
    rows = sorted(tools.items(), key=lambda kv: -((kv[1] or {}).get("calls", 0)))
    return ", ".join(f"{html.escape(t)}: {(r or {}).get('calls', 0)}" for t, r in rows) or "—"


def _tools_card(model: ReportModel) -> str:
    """Session-wide tool-usage rollup — builtin and MCP split out — plus the MCP
    servers loaded this session (flagging any that loaded but no agent invoked)
    and the busiest agents."""
    by_agent = [(n.key, n.tool_count) for n in model.nodes if n.tool_count]
    busiest = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{_fmt_num(c)}</td></tr>"
        for a, c in sorted(by_agent, key=lambda kv: -kv[1])[:5]
    )
    busiest_block = (
        f'<h3>Busiest agents</h3><table class="kv"><tbody>{busiest}</tbody></table>'
        if busiest
        else ""
    )

    tu = model.meta.tool_usage or {}
    servers = (model.meta.mcp_usage or {}).get("servers") or []

    # Legacy sessions predate the builtin/MCP split — fall back to a flat histogram.
    if not tu:
        flat: dict[str, int] = {}
        for n in model.nodes:
            for tool, count in (n.tool_stats or {}).items():
                flat[tool] = flat.get(tool, 0) + count
        if not flat and not by_agent:
            return ""
        grand = sum(flat.values()) or sum(c for _, c in by_agent)
        top = sorted(flat.items(), key=lambda kv: -kv[1])[:8]
        tools_html = ", ".join(f"{html.escape(t)}: {c}" for t, c in top) or "—"
        return f"""
      <div class="card">
        <h3>Tool usage ({_fmt_num(grand)} calls)</h3>
        <div class="muted small">{tools_html}</div>
        {busiest_block}
      </div>"""

    totals = tu.get("totals") or {}
    b_calls = int(totals.get("builtinCalls", 0))
    m_calls = int(totals.get("mcpCalls", 0))
    grand = b_calls + m_calls
    if not grand and not servers and not by_agent:
        return ""
    builtin_html = _fmt_tool_calls(tu.get("builtin") or {})
    mcp_html = _fmt_tool_calls(tu.get("mcp") or {}) if m_calls else "none invoked"

    servers_block = ""
    if servers:
        invoked = {
            t
            for tools in ((model.meta.mcp_usage or {}).get("byAgent") or {}).values()
            for t in tools
        }
        srv_rows = ""
        for s in servers:
            name = str(s.get("name", ""))
            verdict = html.escape(str(s.get("verdict", "—")))
            used = any(t.startswith(f"{name}-") for t in invoked)
            note = verdict if used else f'{verdict} <span class="muted">· not invoked</span>'
            srv_rows += f"<tr><td>{html.escape(name)}</td><td>{note}</td></tr>"
        servers_block = f'<h3>MCP servers</h3><table class="kv"><tbody>{srv_rows}</tbody></table>'

    return f"""
      <div class="card">
        <h3>Tool usage ({_fmt_num(grand)} calls)</h3>
        <div class="muted small"><b>Builtin ({_fmt_num(b_calls)}):</b> {builtin_html}</div>
        <div class="muted small"><b>MCP ({_fmt_num(m_calls)}):</b> {mcp_html}</div>
        {servers_block}
        {busiest_block}
      </div>"""


def render_html(model: ReportModel) -> str:
    layout = compute_layout(model)
    bundle_map = {b.dst: b for b in layout.bundles}
    payload = {n.key: _node_payload(n, bundle_map) for n in model.nodes}
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    svg = _svg(model, layout)
    overview = _overview(model)
    warnings_html = ""
    if model.warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in model.warnings)
        warnings_html = f"<details class='warnings'><summary>{len(model.warnings)} warning(s)</summary><ul>{items}</ul></details>"

    tool = html.escape(model.meta.tool_name or "Agent")
    title = f"{tool} report — {html.escape(model.meta.session_id)}"
    return _TEMPLATE.format(
        title=title,
        overview=overview,
        svg=svg,
        warnings=warnings_html,
        data_json=data_json,
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg:#f5f7fa; --panel:#fff; --line:#dbe2ea; --ink:#1f2d3d; --muted:#6b7a8d; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; color:var(--ink); background:var(--bg); }}
  header.top {{ padding:14px 20px; background:#22304a; color:#fff; }}
  header.top h1 {{ margin:0; font-size:16px; font-weight:600; }}
  .overview {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; padding:16px 20px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }}
  .card h3 {{ margin:10px 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  .card a {{ color:#1668e3; text-decoration:none; }} .card a:hover {{ text-decoration:underline; }}
  .card code {{ font:12px/1 ui-monospace,Consolas,monospace; background:#eef2f6; padding:1px 4px; border-radius:3px; }}
  .ins {{ color:#1c7a3e; }} .del {{ color:#a11; }}
  details.changed {{ margin-top:6px; }} details.changed div {{ font:12px/1.5 ui-monospace,Consolas,monospace; color:var(--muted); }}
  table.kv {{ border-collapse:collapse; width:100%; }}
  table.kv td {{ padding:3px 6px; vertical-align:top; border-bottom:1px solid #eef2f6; }}
  table.kv td.k {{ color:var(--muted); white-space:nowrap; width:42%; }}
  .muted {{ color:var(--muted); }} .small {{ font-size:12px; }}
  .tip {{ cursor:help; border-bottom:1px dotted var(--muted); }}
  .ovg {{ display:flex; flex-direction:column; gap:6px; margin:4px 0; }}
  .ovg .att {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:12px; }}
  .ovg .att-n {{ font-weight:700; color:var(--muted); }}
  .ovg .att-out {{ font-size:11px; padding:1px 7px; border-radius:10px; text-transform:uppercase; letter-spacing:.03em; }}
  .ovg .att-out.ok {{ background:#d6f5df; color:#1c7a3e; }}
  .ovg .att-out.rej {{ background:#ffd9d4; color:#a11; }}
  .ovg .att-gate {{ color:#a11; }} .ovg .att-model, .ovg .att-t {{ color:var(--muted); }}
  .ovg .att-model.att-reroute {{ color:#a11; font-weight:700; }}
  .ovg .att-reason {{ margin:0 0 4px 22px; padding:6px 8px; background:#fff4f2; border-left:3px solid #e0897c; border-radius:4px; color:#7a2a1e; font:12px/1.4 ui-monospace,Consolas,monospace; white-space:pre-wrap; word-break:break-word; }}
  .ovg .att-signal {{ margin:0 0 4px 22px; padding:5px 8px; background:#fff7e6; border-left:3px solid #d69e2e; border-radius:4px; font-size:12px; }}
  .ovg .att-retention {{ margin:0 0 4px 22px; color:#8a5700; font-size:12px; font-weight:600; }}
  .anomaly {{ border-color:#e4b04f; background:#fffaf0; }}
  .tag {{ font-size:11px; padding:1px 6px; border-radius:10px; }} .tag.warn {{ background:#ffe0a3; }}
  .graph-wrap {{ display:flex; gap:0; align-items:stretch; }}
  .graph {{ flex:1; overflow:auto; padding:10px 20px 30px; }}
  .legend {{ padding:6px 20px; color:var(--muted); font-size:12px; display:flex; gap:18px; flex-wrap:wrap; }}
  .legend b {{ color:var(--ink); }}
  svg#dag text.node-label {{ font-size:11px; text-anchor:middle; fill:#26364d; }}
  svg#dag text.badge-t {{ font-size:10px; text-anchor:middle; fill:#fff; font-weight:700; }}
  svg#dag text.bundle-label {{ font-size:10px; fill:#8e5aa8; }}
  svg#dag g.node {{ cursor:pointer; }}
  svg#dag g.node:hover circle, svg#dag g.node:hover polygon {{ filter:brightness(0.94); }}
  svg#dag g.node.sel circle, svg#dag g.node.sel polygon {{ stroke:#1668e3; stroke-width:3.5; }}
  svg#dag {{ display:block; max-width:100%; height:auto; }}
  aside#panel {{ width:0; transition:width .15s; background:var(--panel); border-left:1px solid var(--line); overflow:hidden; }}
  aside#panel.open {{ width:min(46vw,640px); }}
  .panel-in {{ width:min(46vw,640px); padding:14px 16px; }}
  .panel-in h2 {{ margin:0 0 4px; font-size:16px; }}
  .tabs {{ display:flex; gap:4px; margin:10px 0; border-bottom:1px solid var(--line); }}
  #p-role {{ margin:8px 0 4px; }}
  .role-desc {{ margin:0 0 6px; font-size:13px; line-height:1.45; color:var(--ink); border-left:3px solid #1668e3; padding-left:9px; }}
  .role-flow {{ color:var(--muted); line-height:1.5; }}
  .tabs button {{ border:0; background:none; padding:6px 10px; cursor:pointer; color:var(--muted); border-bottom:2px solid transparent; }}
  .tabs button.active {{ color:var(--ink); border-bottom-color:#1668e3; }}
  #tabbody pre {{ white-space:pre-wrap; word-break:break-word; background:#0f1826; color:#d6e2f0; padding:12px; border-radius:6px; max-height:62vh; overflow:auto; font:12px/1.5 ui-monospace,Consolas,monospace; }}
  .close {{ float:right; cursor:pointer; border:0; background:none; font-size:18px; color:var(--muted); }}
  details.warnings {{ margin:0 20px 14px; }} details.warnings ul {{ margin:6px 0; }}
  .prod-list {{ columns:16em; font-size:12px; }}
  @media (max-width:720px) {{
    .overview {{ grid-template-columns:1fr; }}
    .graph-wrap {{ flex-direction:column; }}
    aside#panel {{ position:fixed; top:0; right:0; bottom:0; z-index:20; border-left:0; box-shadow:-2px 0 14px rgba(0,0,0,.28); }}
    aside#panel.open {{ width:100vw; }}
    .panel-in {{ width:100vw; box-sizing:border-box; }}
    .prod-list {{ columns:1; }}
  }}
</style></head>
<body>
<header class="top"><h1>{title}</h1></header>
{warnings}
{overview}
<div class="legend">
  <span><b>●</b> LLM agent</span><span><b>◆</b> deterministic</span>
  <span>— required</span><span>– – optional</span><span>· · inferred (static)</span>
  <span style="color:#8e5aa8">▨ dossier bundle</span>
  <span>heat = wall-clock</span><span>red ring = invalid</span>
  <span class="badge-t" style="background:#c0392b;color:#fff;border-radius:8px;padding:0 5px">N</span><span>= retries</span>
</div>
<div class="graph-wrap">
  <div class="graph">{svg}</div>
  <aside id="panel"><div class="panel-in">
    <button class="close" onclick="closePanel()">×</button>
    <h2 id="p-title"></h2>
    <div id="p-meta" class="small"></div>
    <div id="p-role"></div>
    <div class="tabs" id="p-tabs"></div>
    <div id="tabbody"></div>
  </div></aside>
</div>
<script type="application/json" id="report-data">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById('report-data').textContent);
const TABS = [['metrics','Metrics'],['system','System'],['context','Context'],['response','Response']];
let current=null, curTab='metrics';

function fmtMs(ms){{ if(ms==null) return '—'; const s=ms/1000; return s<60? s.toFixed(1)+'s' : Math.floor(s/60)+'m '+(s%60).toFixed(1)+'s'; }}
function num(n){{ return n==null? '—' : (typeof n==='number'? n.toLocaleString() : n); }}
function esc(t){{ const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }}
function nm(k){{ return (DATA[k]&&DATA[k].display_name)||k; }}
function modelText(dec,obs){{
  const d=dec||'', o=obs||'';
  return (d&&o&&d!==o) ? d+' → '+o : (o||d||'—');
}}
function rerouted(dec,obs){{ return !!(dec&&obs&&dec!==obs); }}
function nonApiMs(d){{
  if(d.wall_clock_ms==null||d.api_duration_ms==null) return null;
  return Math.max(0,d.wall_clock_ms-d.api_duration_ms);
}}
function policyText(p){{
  if(!p) return '';
  const parts=[];
  if(p.shellInvocationCapSeconds!=null) parts.push('shell invocation ≤ '+p.shellInvocationCapSeconds+'s');
  if(p.backgroundPollCapSeconds!=null) parts.push('background poll ≤ '+p.backgroundPollCapSeconds+'s');
  if(p.detachedAllowed!=null) parts.push('detached: '+(p.detachedAllowed?'allowed':'disabled'));
  return parts.join(' · ');
}}
function toolSignalText(t){{
  const parts=[t.name||'tool'];
  if(t.state) parts.push(t.state);
  if(t.durationMs!=null) parts.push(fmtMs(t.durationMs));
  if(t.exitCode!=null) parts.push('exit '+t.exitCode);
  const e=t.toolError;
  if(e){{
    parts.push(e.source?'source '+e.source:'source unavailable');
    if(e.type) parts.push('type '+e.type);
    if(e.code!=null) parts.push('code '+e.code);
    if(e.timeoutOwner) parts.push('timeout owner '+e.timeoutOwner);
    if(e.message) parts.push(e.message);
  }} else if(t.error) parts.push(t.error);
  if(t.toolErrorRetention&&t.toolErrorRetention.dropped&&t.toolErrorRetention.dropped.message)
    parts.push('message redacted');
  return parts.join(' · ');
}}
function snapshotText(t){{
  return [t.name||'tool',t.state].filter(Boolean).join(' · ');
}}

function metricsHtml(d){{
  const rows=[
    ['Runtime', d.runtime + (d.known_in_graph? '' : ' (not in current graph)')],
    ['Valid', d.valid==null?'—':String(d.valid)], ['Gate', d.gate||'—'],
    ['Attempts', num(d.attempts)],
    ['Submission', d.submission_status||'—', 'Schema-backed agents are valid only after an accepted roundtable_submit_output call.'],
    ['Model', modelText(d.final_model, d.observed_model), 'Declared in agent_graph.yaml. An arrow means the runtime served a different model than the one declared.'],
    ['Wall clock', fmtMs(d.wall_clock_ms), 'Total time for this agent: LLM API + tool/MCP execution + submission validation/retries when schema-backed.'],
    ['API time', fmtMs(d.api_duration_ms), 'Time inside LLM API calls only (excludes tool execution).'],
    ['Non-API time', fmtMs(nonApiMs(d)), 'Wall clock minus LLM API time. Includes tools, MCP, SDK waits, validation, and retry overhead.'],
    ['Session time', fmtMs(d.session_duration_ms), 'Copilot session duration for this agent (setup + all turns).'],
    ['Input tokens', num(d.input_tokens)], ['Cached input', num(d.cache_read_tokens)],
    ['Cache write', num(d.cache_write_tokens)], ['Reasoning', num(d.reasoning_tokens)],
    ['Output tokens', num(d.output_tokens)], ['Total tokens', num(d.total_tokens)],
    ['Tool rounds', num(d.rounds)], ['Tool calls', num(d.tool_count)],
  ];
  if(d.ai_credits!=null) rows.splice(10,0,['AI Credits consumed',d.ai_credits]);
  let h='<table class="kv">'+rows.map(r=>{{
    const tip=r[2]?` title="${{esc(r[2])}}"`:'';
    return `<tr><td class="k${{r[2]?' tip':''}}"${{tip}}>${{esc(r[0])}}</td><td>${{esc(String(r[1]))}}</td></tr>`;
  }}).join('')+'</table>';
  const ad=d.attempts_detail||[];
  const policies=ad.map(a=>a.executionPolicy).filter(Boolean);
  const uniquePolicies=[...new Set(policies.map(policyText))].filter(Boolean);
  if(uniquePolicies.length){{
    h+='<h3>Backend execution policy</h3><div class="small muted">'
      +uniquePolicies.map(esc).join('<br>')+'</div>';
  }}
  const hasRuntime = ad.some(a=>a.timeoutPhase || (a.timeoutSnapshot||[]).length
    || (a.failedToolCalls||[]).length || a.toolCallRetention || a.sdkEventRetention
    || a.backendOutcome);
  const showAttempts = ad.length>1 || hasRuntime || ad.some(a=>!['valid','raw_output','submission_valid'].includes(a.outcome) || a.rejectReason || a.submissionStatus);
  if(ad.length && showAttempts){{
    h+='<h3>'+(hasRuntime?'Attempt diagnostics':'Submission attempts')+'</h3><div class="ovg">';
    ad.forEach(a=>{{
      const ok=['valid','raw_output','submission_valid'].includes(a.outcome);
      h+='<div class="att">'
        +'<span class="att-n">#'+num(a.attempt)+'</span>'
        +'<span class="att-out '+(ok?'ok':'rej')+'">'+esc(a.outcome||'—')+'</span>'
        +(a.gate?'<span class="att-gate">gate: '+esc(a.gate)+'</span>':'')
        +(a.submissionStatus?'<span class="att-gate">submission: '+esc(a.submissionStatus)+'</span>':'')
        +(a.timeoutPhase?'<span class="att-gate">timeout: '+esc(a.timeoutPhase)+'</span>':'')
        +(a.backendOutcome?'<span class="att-gate">backend: '+esc(a.backendOutcome)+'</span>':'')
        +((a.model||a.observedModel)?'<span class="att-model'+(rerouted(a.model,a.observedModel)?' att-reroute':'')+'">'+esc(modelText(a.model,a.observedModel))+'</span>':'')
        +(a.wallClockMs!=null?'<span class="att-t">'+fmtMs(a.wallClockMs)+'</span>':'')
        +'</div>';
      if(a.rejectReason) h+='<div class="att-reason">'+esc(a.rejectReason)+'</div>';
      if(a.retryFeedback) h+='<div class="att-signal"><b>Feedback provided for this retry:</b><pre>'
        +esc(a.retryFeedback)+'</pre></div>';
      const snap=a.timeoutSnapshot||[];
      if(snap.length) h+='<div class="att-signal"><b>Active at timeout:</b> '
        +snap.map(snapshotText).map(esc).join('; ')+'</div>';
      const sig=a.failedToolCalls||[];
      if(sig.length) h+='<div class="att-signal"><b>Tool failures:</b> '
        +sig.map(toolSignalText).map(esc).join('; ')+'</div>';
      const retention=[];
      if(a.toolCallRetention&&a.toolCallRetention.truncated)
        retention.push(num(a.toolCallRetention.omittedCount)+' tool call(s) omitted');
      if(a.sdkEventRetention&&a.sdkEventRetention.truncated)
        retention.push(num(a.sdkEventRetention.omittedCount)+' SDK event(s) omitted');
      if(retention.length) h+='<div class="att-retention">Telemetry truncated: '
        +retention.map(esc).join('; ')+'</div>';
    }});
    h+='</div>';
  }}
  const ts=d.tool_stats||{{}}; const tk=Object.keys(ts);
  if(tk.length||d.tool_count!=null) h+='<h3>Tools'+(d.tool_count!=null?' ('+num(d.tool_count)+' calls)':'')+'</h3><div class="small muted">'+(tk.length?tk.map(k=>esc(k)+': '+ts[k]).join(', '):'—')+'</div>';
  if(d.bundle_sources){{ h+='<h3>Bundled producers ('+d.bundle_sources.length+')</h3><div class="prod-list">'+d.bundle_sources.map(esc).join('<br>')+'</div>'; }}
  return h;
}}
function renderTab(){{
  const d=DATA[current], body=document.getElementById('tabbody');
  if(curTab==='metrics'){{ body.innerHTML=metricsHtml(d); return; }}
  const map={{system:'system_md',context:'context_md',response:'response_md'}};
  const txt=d[map[curTab]];
  body.innerHTML='';
  const pre=document.createElement('pre');
  pre.textContent = txt==null ? '(empty — deterministic node or not dumped)' : txt;
  body.appendChild(pre);
}}
function openPanel(key){{
  current=key; const d=DATA[key];
  document.querySelectorAll('g.node.sel').forEach(g=>g.classList.remove('sel'));
  const g=document.querySelector('g.node[data-key="'+CSS.escape(key)+'"]'); if(g) g.classList.add('sel');
  document.getElementById('p-title').textContent=(d.emoji?d.emoji+' ':'')+nm(key);
  document.getElementById('p-meta').textContent=(d.observed_model||d.final_model||d.runtime)+' · '+fmtMs(d.wall_clock_ms)+' · '+num(d.output_tokens)+' out tok';
  const roleEl=document.getElementById('p-role');
  let roleH='';
  if(d.description) roleH+='<p class="role-desc">'+esc(d.description)+'</p>';
  const flow=[];
  if((d.consumes||[]).length) flow.push('<b>Consumes from:</b> '+d.consumes.map(nm).map(esc).join(', '));
  if((d.feeds||[]).length) flow.push('<b>Feeds:</b> '+d.feeds.map(nm).map(esc).join(', '));
  if(flow.length) roleH+='<div class="role-flow small">'+flow.join('<br>')+'</div>';
  roleEl.innerHTML=roleH;
  roleEl.style.display=roleH?'block':'none';
  const tabsEl=document.getElementById('p-tabs'); tabsEl.innerHTML='';
  TABS.forEach(([id,label])=>{{ const b=document.createElement('button'); b.textContent=label; b.className=id===curTab?'active':''; b.onclick=()=>{{curTab=id; document.querySelectorAll('#p-tabs button').forEach(x=>x.classList.remove('active')); b.classList.add('active'); renderTab();}}; tabsEl.appendChild(b); }});
  renderTab();
  document.getElementById('panel').classList.add('open');
}}
function closePanel(){{ document.getElementById('panel').classList.remove('open'); document.querySelectorAll('g.node.sel').forEach(g=>g.classList.remove('sel')); }}
document.querySelectorAll('g.node').forEach(g=>{{
  const key=g.getAttribute('data-key');
  g.addEventListener('click',()=>openPanel(key));
  g.addEventListener('keydown',e=>{{ if(e.key==='Enter'||e.key===' ') {{e.preventDefault(); openPanel(key);}} }});
}});
document.addEventListener('keydown',e=>{{ if(e.key==='Escape') closePanel(); }});
</script>
</body></html>
"""
