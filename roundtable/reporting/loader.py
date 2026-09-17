"""Load and normalize a persisted review session for reporting.

The *artifacts are authoritative*: the set of agents that actually ran, their
telemetry and their dumped prompts come from the session directory. Static graph
metadata (emoji, runtime class, deps, delivery label, description) is read from
the session's ``graph.json`` **snapshot** — captured at run time, so it is
point-in-time correct and requires no engine-config import here. For legacy
sessions that predate the snapshot, an optional ``graph_provider`` supplies live
metadata; absent both, nodes simply render un-enriched (never assumed away).

Edges are the *observed* data-flow parsed from each agent's ``context.md``
delivery envelope. Every injected upstream section is a level-2 header of the
form ``## <label> [STATE]`` where STATE is ``REQUIRED``/``OPTIONAL``/
``REQUIRED — UNAVAILABLE`` (the suffix is appended by the injection path). That
state suffix is the discriminator: content headers embedded inside a delivered
payload never carry it, so anchoring on it avoids false edges.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from roundtable.backend import NOT_APPLICABLE_BILLING, BillingValue

from .snapshot import GraphProvider, GraphSnapshot, load_graph_snapshot

# ── Envelope header grammar ──────────────────────────────────────────────────
# ``## Context from <Agent> [REQUIRED]`` or ``## <delivery_label> [OPTIONAL]``.
# Anchored on the trailing state token so headings inside delivered content
# (which lack the suffix) are not mistaken for edges.
_STATE = r"REQUIRED(?:\s+[—-]\s+UNAVAILABLE)?|OPTIONAL"
_ENVELOPE_RE = re.compile(rf"^##\s+(?P<label>.+?)\s+\[(?P<state>{_STATE})\]\s*$")
_CONTEXT_FROM_RE = re.compile(r"^Context from\s+(?P<agent>\S+)$")


@dataclass(frozen=True)
class Edge:
    """A directed data-flow edge: ``src`` agent's output was injected into ``dst``."""

    src: str
    dst: str
    required: bool
    # observed-header | observed-label | inferred-static
    provenance: str


@dataclass
class AgentNode:
    """One agent that ran (or is known to the graph), with joined telemetry+dumps."""

    key: str
    emoji: str = ""
    runtime: str = "llm"  # 'llm' | 'deterministic' | 'unknown'
    known_in_graph: bool = True
    description: str | None = None  # authored purpose blurb (what this agent does)
    display_name: str | None = None  # human-readable report label (fallback: key)
    consumes: list[str] = field(default_factory=list)  # upstream agents feeding this one
    feeds: list[str] = field(default_factory=list)  # downstream agents this one feeds
    # Telemetry (from usage-summary perAgent / trace agents; None when absent)
    valid: bool | None = None
    gate: str | None = None
    attempts: int | None = None
    attempts_detail: list[dict] = field(default_factory=list)
    submission_status: str | None = None
    models: list[str] = field(default_factory=list)
    final_model: str | None = None
    observed_model: str | None = None  # what the runtime served (may differ from declared)
    billing: BillingValue = NOT_APPLICABLE_BILLING
    legacy_premium_requests: float | None = None
    output_tokens: int | None = None
    input_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    rounds: int | None = None
    api_duration_ms: float | None = None
    session_duration_ms: float | None = None
    wall_clock_ms: float | None = None
    tool_count: int | None = None
    tool_stats: dict[str, int] = field(default_factory=dict)
    declared_tools: list[str] = field(default_factory=list)  # graph grant (empty = unknown)
    declared_mcp_tools: list[str] = field(default_factory=list)  # that grant, CLI-named
    # Dumped prompt/response text (None when the file is absent/empty)
    system_md: str | None = None
    context_md: str | None = None
    response_md: str | None = None
    # Static graph deps (for inferred edges + reference)
    required_deps: list[str] = field(default_factory=list)
    optional_deps: list[str] = field(default_factory=list)

    @property
    def is_deterministic(self) -> bool:
        return self.runtime == "deterministic"


@dataclass
class RunMeta:
    """Session-level rollup metadata."""

    session_id: str
    verdict: str
    verdict_icon: str
    verdict_overridden: bool
    counts: dict
    agent_count: int
    started_at: str | None
    finished_at: str | None
    billing: BillingValue
    legacy_premium_requests: float | None
    output_tokens: int | None
    input_tokens: int | None
    total_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    rounds: int | None
    api_duration_ms: float | None
    session_duration_ms: float | None
    wall_clock_ms: float | None
    invalid_count: int | None
    performance: dict = field(default_factory=dict)
    validation_tax: dict = field(default_factory=dict)
    anomalies: dict = field(default_factory=dict)
    tool_usage: dict = field(default_factory=dict)
    mcp_usage: dict = field(default_factory=dict)
    # Subject/observability blocks are absent in legacy traces.
    subject: dict = field(default_factory=dict)
    diff_stat: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    session_uri: str | None = None  # file:// URI to the session dir on this host
    tool_name: str | None = None  # configuration display name (report title)


@dataclass
class ReportModel:
    """The full, render-ready model for one session."""

    meta: RunMeta
    nodes: list[AgentNode]
    edges: list[Edge]
    warnings: list[str] = field(default_factory=list)
    # Every MCP tool the run's registry could serve, CLI-named (see snapshot.py).
    mcp_tool_inventory: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if text.strip() else None


def _billing_from(*containers: dict) -> BillingValue:
    for container in containers:
        value = container.get("billing")
        if isinstance(value, dict):
            return BillingValue.from_dict(value)
    return NOT_APPLICABLE_BILLING


def _dir_uri(path: Path) -> str | None:
    """Absolute ``file://`` URI for a directory on this host, or ``None``."""
    try:
        return path.resolve().as_uri()
    except (OSError, ValueError):
        return None


def _first_prose_line(md: str) -> str | None:
    """First non-heading, non-blockquote, non-fence prose line of a markdown body."""
    for line in md.splitlines():
        s = line.strip()
        if not s or s[0] in "#>" or s.startswith("```"):
            continue
        return s
    return None


def _fallback_description(runtime: str, system_md: str | None) -> str | None:
    """Description used when the snapshot carries none.

    Falls back to the dumped ``system.md`` intro line, then a generic note for
    deterministic (prompt-less) nodes. The authored ``.agent.md`` description is
    the snapshot's job (resolved at run time); this only fills the gaps.
    """
    if system_md:
        return _first_prose_line(system_md)
    if runtime == "deterministic":
        return "Deterministic node — a pure-Python enricher that reduces upstream outputs (no LLM)."
    return None


def _merge_attempts_detail(usage_attempts: list[dict], trace_attempts: list[dict]) -> list[dict]:
    """Join analytics-rich usage attempts with trace-only diagnostic fields."""
    trace_by_attempt = {
        item.get("attempt"): item
        for item in trace_attempts
        if isinstance(item, dict) and item.get("attempt") is not None
    }
    merged: list[dict] = []
    seen: set[object] = set()
    for item in usage_attempts:
        if not isinstance(item, dict):
            continue
        attempt = item.get("attempt")
        merged.append({**trace_by_attempt.get(attempt, {}), **item})
        seen.add(attempt)
    merged.extend(
        dict(item)
        for item in trace_attempts
        if isinstance(item, dict) and item.get("attempt") not in seen
    )
    return merged


def _parse_edges(
    node_key: str,
    context_md: str,
    label_map: dict[str, str],
    known_agents: set[str],
    warnings: list[str],
) -> list[Edge]:
    """Extract observed inbound edges from an agent's context.md envelope."""
    edges: list[Edge] = []
    seen: set[tuple[str, bool]] = set()
    for line in context_md.splitlines():
        match = _ENVELOPE_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip()
        required = match.group("state").startswith("REQUIRED")
        cf = _CONTEXT_FROM_RE.match(label)
        if cf:
            src = cf.group("agent")
            provenance = "observed-header"
        else:
            src = label_map.get(label)
            provenance = "observed-label"
        if src is None:
            warnings.append(f"{node_key}: unrecognized delivery label {label!r}")
            continue
        if src == node_key or (src, required) in seen:
            continue
        seen.add((src, required))
        if src not in known_agents:
            warnings.append(f"{node_key}: edge from unknown agent {src!r}")
        edges.append(Edge(src=src, dst=node_key, required=required, provenance=provenance))
    return edges


def load_report_model(
    session_dir: str | Path, *, graph_provider: GraphProvider | None = None
) -> ReportModel:
    """Build the render-ready :class:`ReportModel` from a session directory.

    Static graph metadata comes from the session's ``graph.json`` snapshot. When
    that artifact is absent (legacy session), ``graph_provider`` — if supplied —
    yields live metadata as a fallback. Raises ``FileNotFoundError`` when
    ``trace.json`` is missing (the one hard requirement); everything else
    degrades gracefully into warnings.
    """
    root = Path(session_dir)
    warnings: list[str] = []

    trace = _read_json(root / "trace.json")
    if trace is None:
        raise FileNotFoundError(f"no readable trace.json in {root}")
    usage = _read_json(root / "usage-summary.json") or {}

    snapshot = load_graph_snapshot(root)
    if snapshot is None and graph_provider is not None:
        snapshot = graph_provider()
    if snapshot is None:
        snapshot = GraphSnapshot()
    graph = snapshot.agents
    per_agent = {a.get("agent"): a for a in usage.get("perAgent", []) if a.get("agent")}
    trace_agents = {a.get("agent"): a for a in trace.get("agents", []) if a.get("agent")}

    # Node set = union of everything that left a footprint in the artifacts.
    keys: list[str] = []
    for k in list(trace_agents) + list(per_agent):
        if k not in keys:
            keys.append(k)
    agents_dir = root / "agents"
    if agents_dir.is_dir():
        for child in sorted(p.name for p in agents_dir.iterdir() if p.is_dir()):
            if child not in keys:
                keys.append(child)

    nodes: list[AgentNode] = []
    for key in keys:
        info = graph.get(key)
        node = AgentNode(key=key, known_in_graph=info is not None)
        if info is not None:
            node.emoji = info.emoji
            node.runtime = info.runtime
            node.required_deps = list(info.required_deps)
            node.optional_deps = list(info.optional_deps)
            node.display_name = info.display_name
        else:
            node.runtime = "unknown"
            warnings.append(f"agent {key!r} ran but is absent from the graph snapshot")

        ta = trace_agents.get(key, {})
        pa = per_agent.get(key, {})
        usage_obj = ta.get("usage") or {}
        node.valid = pa.get("valid", ta.get("valid"))
        node.gate = pa.get("gate", ta.get("gate"))
        node.attempts = pa.get("attempts", ta.get("attempts"))
        node.attempts_detail = _merge_attempts_detail(
            list(pa.get("attemptsDetail") or []),
            list(ta.get("attemptsDetail") or []),
        )
        node.submission_status = pa.get("submissionStatus", ta.get("submissionStatus"))
        node.models = list(pa.get("models") or [])  # legacy sessions
        node.final_model = pa.get("model") or pa.get("finalModel")
        node.observed_model = pa.get("observedModel") or None
        if node.final_model and node.observed_model and node.observed_model != node.final_model:
            warnings.append(
                f"agent {key!r} declared model {node.final_model!r} "
                f"but the runtime served {node.observed_model!r}"
            )
        node.billing = _billing_from(pa, usage_obj)
        node.legacy_premium_requests = pa.get(
            "premiumRequests",
            usage_obj.get("premiumRequests"),
        )
        node.output_tokens = pa.get("outputTokens", usage_obj.get("outputTokens"))
        node.input_tokens = pa.get("inputTokens", usage_obj.get("inputTokens"))
        node.total_tokens = pa.get("totalTokens", usage_obj.get("totalTokens"))
        node.cache_read_tokens = pa.get("cacheReadTokens", usage_obj.get("cacheReadTokens"))
        node.cache_write_tokens = pa.get("cacheWriteTokens", usage_obj.get("cacheWriteTokens"))
        node.reasoning_tokens = pa.get("reasoningTokens", usage_obj.get("reasoningTokens"))
        node.rounds = pa.get("rounds", usage_obj.get("rounds"))
        node.api_duration_ms = pa.get("apiDurationMs", usage_obj.get("durationMs"))
        node.session_duration_ms = pa.get("sessionDurationMs", usage_obj.get("sessionDurationMs"))
        node.wall_clock_ms = pa.get("wallClockMs", ta.get("wallClockMs"))
        node.tool_count = pa.get("toolCount")
        node.tool_stats = dict(ta.get("toolStats") or {})
        if not node.tool_stats and node.attempts_detail:
            # trace toolStats is usually absent; derive the per-tool histogram
            # from each attempt's calls (``toolCalls`` names, or ``toolsInvoked``).
            counter: dict[str, int] = {}
            for att in node.attempts_detail:
                names = [
                    c.get("name")
                    for c in (att.get("toolCalls") or [])
                    if isinstance(c, dict) and c.get("name")
                ] or (att.get("toolsInvoked") or [])
                for tool in names:
                    if not isinstance(tool, str):
                        continue
                    counter[tool] = counter.get(tool, 0) + 1
            node.tool_stats = dict(sorted(counter.items(), key=lambda kv: -kv[1]))
        if node.tool_count is None and node.tool_stats:
            node.tool_count = sum(node.tool_stats.values())
        if node.runtime == "unknown" and not usage_obj and node.output_tokens is None:
            # No LLM usage recorded ⇒ most likely a deterministic node.
            node.runtime = "deterministic"

        adir = agents_dir / key
        if adir.is_dir():
            node.system_md = _read_text(adir / "system.md")
            node.context_md = _read_text(adir / "context.md")
            node.response_md = _read_text(adir / "response.md")
            manifest = _read_json(adir / "manifest.json") or {}
            if not node.models and manifest.get("modelsTried"):
                node.models = list(manifest["modelsTried"])
            if node.final_model is None:
                node.final_model = manifest.get("model")
        node.description = info.description if info else None
        node.declared_tools = list(info.declared_tools) if info else []
        node.declared_mcp_tools = list(info.declared_mcp_tools) if info else []
        if node.description is None:
            node.description = _fallback_description(node.runtime, node.system_md)
        nodes.append(node)

    known = {n.key for n in nodes}
    label_map = snapshot.label_to_producer()

    edges: list[Edge] = []
    edge_keys: set[tuple[str, str]] = set()
    for node in nodes:
        if node.context_md:
            observed = _parse_edges(node.key, node.context_md, label_map, known, warnings)
            for edge in observed:
                edges.append(edge)
                edge_keys.add((edge.src, edge.dst))
        # Topology-agnostic: the report DAG must reflect the DECLARED graph, not only
        # prompt-text parsing. Union every static dep the observed pass did not already
        # capture — e.g. a `kind: source` node whose delivery header carries no
        # `[STATE]` suffix (``## Git Context``) is never observed as an edge, yet the
        # graph declares the dependency. Drawn distinctly as inferred-static.
        for dep in node.required_deps + node.optional_deps:
            if dep in known and (dep, node.key) not in edge_keys:
                required = dep in node.required_deps
                edges.append(
                    Edge(src=dep, dst=node.key, required=required, provenance="inferred-static")
                )
                edge_keys.add((dep, node.key))

    # Derive each node's graph neighbourhood from the observed edges (order-stable).
    node_by_key = {n.key: n for n in nodes}
    for edge in edges:
        dst = node_by_key.get(edge.dst)
        src = node_by_key.get(edge.src)
        if dst is not None and edge.src not in dst.consumes:
            dst.consumes.append(edge.src)
        if src is not None and edge.dst not in src.feeds:
            src.feeds.append(edge.dst)

    cost = usage.get("costRollup", {})
    trace_usage = trace.get("usage", {})
    billing = _billing_from(cost, trace_usage)
    meta = RunMeta(
        session_id=trace.get("sessionId", root.name),
        verdict=trace.get("verdict", "?"),
        verdict_icon=trace.get("verdictIcon", ""),
        verdict_overridden=bool(trace.get("verdictOverridden", False)),
        counts=trace.get("counts") or {},
        agent_count=trace.get("agentCount", len(nodes)),
        started_at=trace.get("startedAt"),
        finished_at=trace.get("finishedAt"),
        billing=billing,
        legacy_premium_requests=cost.get(
            "premiumRequests",
            trace_usage.get("premiumRequests"),
        ),
        output_tokens=cost.get("outputTokens", trace_usage.get("outputTokens")),
        input_tokens=cost.get("inputTokens", trace_usage.get("inputTokens")),
        total_tokens=cost.get("totalTokens", trace_usage.get("totalTokens")),
        cache_read_tokens=cost.get("cacheReadTokens", trace_usage.get("cacheReadTokens")),
        cache_write_tokens=cost.get("cacheWriteTokens", trace_usage.get("cacheWriteTokens")),
        reasoning_tokens=cost.get("reasoningTokens", trace_usage.get("reasoningTokens")),
        rounds=cost.get("rounds", trace_usage.get("rounds")),
        api_duration_ms=cost.get("apiDurationMs", trace_usage.get("durationMs")),
        session_duration_ms=cost.get("sessionDurationMs", trace_usage.get("sessionDurationMs")),
        wall_clock_ms=cost.get("wallClockMs"),
        invalid_count=cost.get("invalidCount"),
        performance=usage.get("performance") or {},
        validation_tax=usage.get("validationTax") or {},
        anomalies=usage.get("anomalies") or {},
        tool_usage=usage.get("toolUsage") or {},
        mcp_usage=usage.get("mcpUsage") or {},
        subject=trace.get("subject") or {},
        diff_stat=trace.get("diffStat") or {},
        provenance=trace.get("provenance") or {},
        session_uri=_dir_uri(root),
        tool_name=snapshot.display_name,
    )
    return ReportModel(
        meta=meta,
        nodes=nodes,
        edges=edges,
        warnings=warnings,
        mcp_tool_inventory=list(snapshot.mcp_tool_inventory),
    )
