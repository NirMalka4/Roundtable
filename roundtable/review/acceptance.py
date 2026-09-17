"""Live-run acceptance grader — post-hoc, whole-session QA harness.

Grades an already-produced ``roundtable review`` session directory. It is **inspect-only**:
it never launches the LLM or reads the reviewed repository, so it can be re-run over a live or
zero-token ``--simulate`` session.

Tiers (GATE = affects acceptance; advisory = report-only):

* **T1  Completion / Structure**   GATE   — run finished, well-formed, verdict invariants.
* **T2  Internal Correctness**     GATE   — output integrity, Judge overlay.
* **T4  Perf / Cost**              advisory.

Gate failure means a T1/T2 check failed. T4 observations never change acceptance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roundtable.decision import verdict_to_exit_code
from roundtable.graph import (
    Configuration,
    evaluate_predicate,
    get_configuration,
    resolve_session_configuration,
)
from roundtable.mcp import READY, WARMED_SLOW, is_publish_server
from roundtable.persistence import TraceKey as K
from roundtable.settings import DEFAULT_MAX_ATTEMPTS
from roundtable.types import verdict_values

from .trace_overlay import OverlayKey as O

GATE_TIERS = {"T1", "T2"}
# Pre-flight MCP verdicts (runtime.mcp_prewarm) that count as a usable connection:
# READY (clean handshake) and WARMED_SLOW (handshook, just over the fast budget).
# UNREACHABLE is the only not-connected verdict.
HEALTHY_MCP_VERDICTS = {READY, WARMED_SLOW}

# Stable report-contract id for the publish MCP-connectivity check. Retains
# its historical "ado-mcp-connectivity" name (publishing runs over the ADO family)
# so existing acceptance consumers and tests are unaffected by the code rename.
PUBLISH_MCP_CHECK = "ado-mcp-connectivity"


def _predicate_output(agent: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if agent is None:
        return {}
    response = agent.get(K.RESPONSE)
    if not isinstance(response, str) or not response:
        return {}
    try:
        parsed = json.loads(response)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def roster(configuration: Configuration, trace: Mapping[str, Any] | None = None) -> list[str]:
    """Agents expected in a trace, excluding branches proven inactive by ``when:``."""
    candidates = [
        entry
        for entry in configuration.entries
        if entry.key not in configuration.non_graph_infra_agents
    ]
    if trace is None:
        return [entry.key for entry in candidates]

    observed = {
        agent.get(K.AGENT): agent
        for agent in trace.get(K.AGENTS, [])
        if isinstance(agent, Mapping) and isinstance(agent.get(K.AGENT), str)
    }
    skipped: set[str] = set()
    changed = True
    while changed:
        changed = False
        for entry in candidates:
            if entry.key in observed or entry.key in skipped:
                continue
            for edge in entry.forward_edges:
                if edge.when is None:
                    continue
                if edge.source in observed:
                    output = _predicate_output(observed[edge.source])
                elif edge.source in skipped:
                    output = {}
                else:
                    continue
                if not evaluate_predicate(edge.when, output):
                    skipped.add(entry.key)
                    changed = True
                    break
    return [entry.key for entry in candidates if entry.key not in skipped]


PASS, FAIL, WARN, NA = "PASS", "FAIL", "WARN", "NA"


@dataclass
class Check:
    tier: str
    check: str
    status: str
    message: str = ""

    @property
    def is_gate_fail(self) -> bool:
        return self.tier in GATE_TIERS and self.status == FAIL


@dataclass
class Report:
    session: str
    checks: list[Check] = field(default_factory=list)

    def add(self, tier: str, check: str, status: str, message: str = "") -> None:
        self.checks.append(Check(tier, check, status, message))

    @property
    def gate_failed(self) -> bool:
        return any(c.is_gate_fail for c in self.checks)


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_trace(session_dir: Path) -> dict[str, Any]:
    return json.loads((session_dir / "trace.json").read_text(encoding="utf-8"))


# ── T1: completion / structure ───────────────────────────────────────────────


def check_t1(
    report: Report,
    session_dir: Path,
    trace: dict[str, Any],
    observed_exit: int | None,
    configuration: Configuration,
) -> None:
    valid_verdicts = frozenset(verdict_values(configuration)) | {"UNKNOWN"}
    verdict = trace.get(O.VERDICT)
    # T1.2 artifacts
    report.add(
        "T1",
        "artifacts-present",
        PASS if (session_dir / "verdict.md").exists() else FAIL,
        "verdict.md present" if (session_dir / "verdict.md").exists() else "verdict.md missing",
    )
    # T1.3 verdict in set
    report.add(
        "T1", "verdict-valid", PASS if verdict in valid_verdicts else FAIL, f"verdict={verdict}"
    )
    # T1.1 / T1.4 exit mapping
    expected_exit = verdict_to_exit_code(verdict) if verdict in valid_verdicts else None
    if observed_exit is None:
        report.add(
            "T1",
            "exit-mapping",
            NA,
            f"--exit-code not supplied; expected {expected_exit} from verdict",
        )
    else:
        ok = expected_exit is not None and observed_exit == expected_exit
        report.add(
            "T1",
            "exit-mapping",
            PASS if ok else FAIL,
            f"observed={observed_exit} expected={expected_exit}",
        )
    # T1.5 roster
    agents = {a.get(K.AGENT) for a in trace.get(K.AGENTS, [])}
    roster_keys = roster(configuration, trace)
    unknown = sorted(agents - set(roster_keys))
    missing = sorted(set(roster_keys) - agents)
    count_ok = trace.get(K.AGENT_COUNT) == len(trace.get(K.AGENTS, []))
    if unknown:
        report.add("T1", "roster", FAIL, f"unknown agents in trace: {unknown}")
    elif missing:
        report.add("T1", "roster", FAIL, f"missing required agents: {missing}")
    elif not count_ok:
        report.add(
            "T1",
            "roster",
            FAIL,
            f"agentCount={trace.get(K.AGENT_COUNT)} != {len(trace.get(K.AGENTS, []))}",
        )
    else:
        report.add("T1", "roster", PASS, f"{len(agents)} agents, count consistent")
    # T1.6 verdict-requires-Judge
    judge = next((a for a in trace.get(K.AGENTS, []) if a.get(K.AGENT) == "Judge"), None)
    judge_ok = (judge is not None and bool(judge.get(K.RESPONSE))) or verdict == "UNKNOWN"
    report.add(
        "T1",
        "verdict-requires-judge",
        PASS if judge_ok else FAIL,
        "Judge response present or verdict UNKNOWN"
        if judge_ok
        else "non-UNKNOWN verdict with empty/absent Judge response",
    )
    # T1.7 critical-finding-forces-REJECT
    counts = trace.get(O.COUNTS) or {}
    blocking = int(counts.get(O.BLOCKING, 0) or 0)
    if blocking > 0:
        ok = verdict == "REJECT"
        report.add(
            "T1",
            "critical-forces-reject",
            PASS if ok else FAIL,
            f"blocking={blocking} verdict={verdict} overridden={trace.get(O.VERDICT_OVERRIDDEN)}",
        )
    else:
        report.add("T1", "critical-forces-reject", NA, "no blocking findings")


# ── T2: internal correctness ─────────────────────────────────────────────────


def check_t2(report: Report, session_dir: Path, trace: dict[str, Any]) -> None:
    # T2.2 output integrity per agent
    ovg_problems: list[str] = []
    for a in trace.get(K.AGENTS, []):
        key = a.get(K.AGENT)
        if not a.get(K.VALID):
            ovg_problems.append(f"{key}:invalid")
        if int(a.get(K.ATTEMPTS, 0) or 0) > DEFAULT_MAX_ATTEMPTS:
            ovg_problems.append(f"{key}:attempts>{DEFAULT_MAX_ATTEMPTS}")
    report.add(
        "T2",
        "output-integrity",
        FAIL if ovg_problems else PASS,
        "; ".join(ovg_problems) if ovg_problems else "all agents valid",
    )
    # T2.2 retry tax: extra backend attempts beyond the first. Current validation
    # retries apply only to schema-backed agents; model-wait recovery can also retry.
    retry_tax = sum(max(0, int(a.get(K.ATTEMPTS, 0) or 0) - 1) for a in trace.get(K.AGENTS, []))
    retried = [
        a.get(K.AGENT) for a in trace.get(K.AGENTS, []) if int(a.get(K.ATTEMPTS, 0) or 0) > 1
    ]
    report.add(
        "T2",
        "ovg-retry-tax",
        PASS if retry_tax == 0 else WARN,
        f"retryTax={retry_tax}" + (f" ({', '.join(retried)})" if retried else ""),
    )
    # T2.3 Judge overlay gate
    judge = next((a for a in trace.get(K.AGENTS, []) if a.get(K.AGENT) == "Judge"), None)
    if judge is None:
        report.add("T2", "judge-overlay", FAIL, "Judge absent")
    else:
        ok = bool(judge.get(K.VALID)) and not (judge.get(K.GATE) and not judge.get(K.VALID))
        report.add(
            "T2",
            "judge-overlay",
            PASS if ok else FAIL,
            f"valid={judge.get(K.VALID)} gate={judge.get(K.GATE)}",
        )
    # T2.5 no agent gate failure
    gate_fails = [
        a.get(K.AGENT) for a in trace.get(K.AGENTS, []) if a.get(K.GATE) and not a.get(K.VALID)
    ]
    report.add(
        "T2",
        "no-gate-failures",
        FAIL if gate_fails else PASS,
        f"gate failures: {gate_fails}" if gate_fails else "none",
    )
    # Publish MCP connectivity is a gate only when the publish path
    # was actually requested. "Requested" is inferred from trace truth — the
    # publish server appearing in the pre-flight probe (mcpPrewarm) and/or an agent
    # invoking one of its namespaced tools. When neither is present (nothing to
    # publish, or --no-enable-ado-mcp) the check is NA. Publishing runs over the ADO
    # family today (see :func:`is_publish_server`).
    check_publish_mcp(report, trace)


def _invoked_publish_tool(tool_stats: Any) -> bool:
    """True when any invoked tool in ``toolStats`` belongs to the publish MCP server.

    MCP tools are namespaced ``<server>-<tool>``, so the publish-server prefix that
    identifies the server (:func:`is_publish_server`) also identifies its tools.
    """
    if not isinstance(tool_stats, Mapping):
        return False
    return any(is_publish_server(str(name)) for name in tool_stats)


def check_publish_mcp(report: Report, trace: dict[str, Any]) -> None:
    """Gate: when the publish path is requested, its MCP server must be connected.

    "Requested" is inferred from trace truth — the publish server appearing in the
    pre-flight probe (``mcpPrewarm``) and/or an agent invoking one of its namespaced
    tools. Connectivity is the probe verdict: ``ready``/``warmed_slow`` count as
    connected, ``unreachable`` does not. Publishing currently runs over the ADO
    family (see :func:`is_publish_server`).
    """
    prewarm = trace.get(K.MCP_PREWARM) or []
    publish = next(
        (e for e in prewarm if isinstance(e, dict) and is_publish_server(str(e.get(K.NAME, "")))),
        None,
    )
    invokers = [
        a.get(K.AGENT)
        for a in trace.get(K.AGENTS, [])
        if _invoked_publish_tool(a.get(K.TOOL_STATS))
    ]
    if publish is None and not invokers:
        report.add(
            "T2",
            PUBLISH_MCP_CHECK,
            NA,
            "publish MCP not requested (no publish server, no publish-tool invocations)",
        )
        return
    verdict = str((publish or {}).get("verdict", "")).lower()
    connected = publish is not None and verdict in HEALTHY_MCP_VERDICTS
    if invokers and not connected:
        # Agents invoked publish tools but the server never warmed healthy — the
        # exact inconsistency this gate exists to catch.
        report.add(
            "T2",
            PUBLISH_MCP_CHECK,
            FAIL,
            f"agents {invokers} invoked publish tools but publish server verdict={verdict or 'absent'}",
        )
    elif not connected:
        report.add(
            "T2",
            PUBLISH_MCP_CHECK,
            FAIL,
            f"publish MCP requested but publish server verdict={verdict or 'absent'}",
        )
    else:
        report.add(
            "T2",
            PUBLISH_MCP_CHECK,
            PASS,
            f"publish server connected (verdict={verdict}); "
            f"{len(invokers)} agent(s) invoked publish tools",
        )


# ── T4: advisory ─────────────────────────────────────────────────────────────


def check_t4(report: Report, trace: dict[str, Any]) -> None:
    retried = [
        a.get(K.AGENT) for a in trace.get(K.AGENTS, []) if int(a.get(K.ATTEMPTS, 0) or 0) > 1
    ]
    report.add(
        "T4",
        "retry-pressure",
        PASS if not retried else WARN,
        f"agents needing >1 attempt: {retried}" if retried else "no retries",
    )


# ── orchestration ────────────────────────────────────────────────────────────


def grade(session_dir: Path, observed_exit: int | None = None) -> Report:
    """Grade a produced session dir against T1 (gate) + T2 (gate) + T4 (advisory)."""
    report = Report(session=str(session_dir))
    trace = _load_trace(session_dir)
    identity = resolve_session_configuration(session_dir)
    configuration = get_configuration(identity.root)
    check_t1(report, session_dir, trace, observed_exit, configuration)
    check_t2(report, session_dir, trace)
    check_t4(report, trace)
    return report


def format_table(report: Report) -> str:
    """Human-readable PASS/FAIL table for a graded report."""
    icon = {PASS: "[+]", FAIL: "[x]", WARN: "[!]", NA: "[ ]"}
    lines = [f"\nAcceptance grade - session={report.session}", "-" * 72]
    for c in report.checks:
        gate = "GATE" if c.tier in GATE_TIERS else "    "
        lines.append(
            f"  {icon.get(c.status, '[?]')} [{c.tier} {gate}] {c.check}: {c.status}  {c.message}"
        )
    lines.append("-" * 72)
    gate_fails = [c for c in report.checks if c.is_gate_fail]
    verdict = "REJECTED" if gate_fails else "ACCEPTED"
    lines.append(f"  {verdict} - {len(gate_fails)} gate failure(s)\n")
    return "\n".join(lines)
