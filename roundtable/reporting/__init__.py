"""Offline HTML visualization of a persisted multi-agent review session.

Turns a ``--dump-prompts`` session directory into one self-contained, clickable
HTML report: run metadata, the agent execution DAG (deterministic vs LLM nodes,
observed data-flow edges), and a per-agent system/context/response inspector.
"""

from __future__ import annotations

from pathlib import Path

from .html import render_html
from .layout import compute_layout
from .loader import AgentNode, Edge, ReportModel, RunMeta, load_report_model
from .snapshot import AgentInfo, GraphProvider, GraphSnapshot, load_graph_snapshot, snapshot_to_json
from .tool_surface import diff_tool_surface, read_tool_surface

__all__ = [
    "AgentInfo",
    "AgentNode",
    "Edge",
    "GraphProvider",
    "GraphSnapshot",
    "ReportModel",
    "RunMeta",
    "build_report",
    "compute_layout",
    "diff_tool_surface",
    "load_graph_snapshot",
    "load_report_model",
    "read_tool_surface",
    "render_html",
    "snapshot_to_json",
]


def build_report(session_dir: str | Path, *, graph_provider: GraphProvider | None = None) -> str:
    """Load a session directory and return the rendered HTML document string.

    ``graph_provider`` supplies live graph metadata as a fallback for legacy
    sessions that predate the persisted ``graph.json`` snapshot; new sessions
    ignore it (they carry their own snapshot).
    """
    return render_html(load_report_model(session_dir, graph_provider=graph_provider))
