"""Unit tests for the pure-DAG scheduler with an injected fake runner.

Mirrors test_wave_scheduler.py where the semantics overlap, and adds DAG-specific
coverage: depth-based ordering, hard-dep=success / soft-dep=terminal gating,
per-consumer ``Dossier_*`` nodes (terminal-last, deps-scoped corpus),
hard-dep-failure graceful degradation (no skip), cycle detection, and launch-order
determinism.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.engine.dag_scheduler import (
    _compute_depths,
)
from roundtable.engine.model import _runnable_graph_entries
from roundtable.graph.model import (
    Edge,
    get_configuration,
)
from roundtable.graph.model import (
    get_entry as _get_entry,
)
from tests.support.engine import run_graph_dag as _run_graph_dag

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
get_entry = partial(_get_entry, config=_CONFIG)
run_graph_dag = partial(_run_graph_dag, configuration=_CONFIG)


@dataclass
class FakeResult:
    final_content: str
    exit_code: int | None = 0
    timed_out: bool = False
    tool_call_count: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    backend_outcome: object | None = None

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and bool(self.final_content)


def _fake_runner(by_agent: dict[str, str], default: str = '{"findings":[]}') -> Callable:
    seen: list[str] = []
    prompts: dict[str, str] = {}

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **kw):
        seen.append(agent)
        prompts.setdefault(agent, prompt)
        result = FakeResult(by_agent.get(agent, default))
        submission = kw.get("submission")
        if submission is not None:
            submission.submit(json.loads(result.final_content), tool_calls=result.tool_calls)
        return result

    run_fn.seen = seen  # type: ignore[attr-defined]
    run_fn.prompts = prompts  # type: ignore[attr-defined]
    return run_fn


# ── OVG-valid producer outputs ───────────────────────────────────────────────
# Only OVG-valid producer output is delivered
# to consumers (schema/semantic-rejected output is no longer resurrected as raw).
# These builders emit the minimal output that actually passes each agent's gate.
_LOC = [{"filePath": "a.cs", "startLine": 1, "endLine": 2}]


def _security_finding(fid: str) -> dict:
    return {
        "id": fid,
        "title": "t",
        "severity": "low",
        "description": "d",
        "locations": _LOC,
        "impact": "i",
        "fix": "Escape the interpolated identifier before building the SQL string.",
    }


def _schema_drift_finding(fid: str) -> dict:
    return {
        "id": fid,
        "category": "schema_drift_contract",
        "severity": "high",
        "title": "Schema contract drift in changed SQL surface",
        "description": "The changed SQL contract diverges from a verified consumer and can fail at runtime.",
        "locations": _LOC,
        "evidence": ["src/x.py:1 shows the changed contract", "src/x.py:1 shows the consumer"],
        "fix": "Update the consumer contract to match the changed SQL surface.",
        "trace": [
            "Apply this PR's SQL contract change.",
            "Execute the verified consumer that depends on the previous contract.",
            "Observe the runtime contract mismatch failure.",
        ],
        "exploitability": {
            "rating": "unknown",
            "reasoning": "A SQL consumer reaches the changed contract. Concrete evidence: src/x.py:1 shows the changed contract and consumer.",
        },
        "impact": "The verified consumer can fail until the contract is updated.",
    }


def _valid_judge(verdict: str, *, verdict_overlay=None, validated_safe=None) -> dict:
    return {
        "verdict": verdict,
        "executive_summary": "s",
        "architectural_assessment": "a",
        "plan_compliance": "p",
        "verdict_overlay": verdict_overlay or [],
        "validated_safe": validated_safe or [],
        "needs_human_judgment": [],
        "count_verification": {},
    }


# ── Scheduling correctness ───────────────────────────────────────────────────


def test_excludes_non_graph_infra_agents():
    run_fn = _fake_runner({})
    res = run_graph_dag(run_fn=run_fn, concurrency=4)
    for infra in _CONFIG.non_graph_infra_agents:
        assert infra not in res.execution_order


def test_all_hard_deps_precede_dependents():
    run_fn = _fake_runner({})
    res = run_graph_dag(run_fn=run_fn, concurrency=4)
    pos = {key: i for i, key in enumerate(res.execution_order)}
    for e in _CONFIG.entries:
        if e.key not in pos:
            continue
        for dep in e.required_dep_keys:
            if dep in pos:  # non_graph_infra deps (SuggestionPublisher) are not scheduled
                assert pos[dep] < pos[e.key], f"{dep} must precede {e.key}"


def test_soft_deps_precede_dependents():
    """Soft deps gate ordering: a soft dep must reach terminal state first, so it
    launches strictly before its dependent."""
    run_fn = _fake_runner({})
    res = run_graph_dag(run_fn=run_fn, concurrency=4)
    pos = {key: i for i, key in enumerate(res.execution_order)}
    for e in _CONFIG.entries:
        if e.key not in pos:
            continue
        for dep in e.optional_dep_keys:
            if dep in pos:
                assert pos[dep] < pos[e.key], f"soft dep {dep} must precede {e.key}"


def test_terminal_agents_launch_last():
    run_fn = _fake_runner({})
    res = run_graph_dag(run_fn=run_fn, concurrency=4)
    pos = {key: i for i, key in enumerate(res.execution_order)}
    terminals = [k for k in pos if k in _CONFIG.terminal_agents]
    non_terminals = [k for k in pos if k not in _CONFIG.terminal_agents]
    assert terminals, "expected scheduled terminal agents"
    max_non_terminal = max(pos[k] for k in non_terminals)
    for t in terminals:
        assert pos[t] > max_non_terminal, f"terminal {t} did not launch last"
    # Verdict (the output-contract sink) depends on Judge -> it is dead last.
    assert res.execution_order[-1] == "Verdict"


def test_depth_strictly_greater_than_every_hard_dep():
    """Topological depth keeps the invariant dep.depth < my.depth for every hard dep,
    so ``execution_order`` (sorted by depth) always launches a hard dep first."""
    scheduled = frozenset(e.key for e in _runnable_graph_entries(_CONFIG))
    entries = [e for e in _CONFIG.entries if e.key in scheduled]
    non_terminal = frozenset(k for k in scheduled if k not in _CONFIG.terminal_agents)
    depth = _compute_depths(entries, scheduled, non_terminal)
    for e in entries:
        for dep in e.required_dep_keys:
            if dep in depth:
                assert depth[dep] < depth[e.key], f"{dep} depth !< {e.key}"


# ── Dossier nodes => terminal-last ordering ──────────────────────────────────


def test_severity_inflator_launches_after_all_non_terminals():
    """SeverityInflator depends on its ``Dossier_SeverityInflator`` node, which in
    turn depends on every finding-producer — so it lands deeper than every other
    non-terminal and launches after all of them (terminal-last)."""
    run_fn = _fake_runner({})
    res = run_graph_dag(run_fn=run_fn, concurrency=4)
    pos = {key: i for i, key in enumerate(res.execution_order)}
    sev = pos["SeverityInflator"]
    # Its dossier node must precede it.
    assert pos["Dossier_SeverityInflator"] < sev
    for k in pos:
        if k not in _CONFIG.terminal_agents:
            assert pos[k] < sev, f"{k} (non-terminal) did not precede SeverityInflator"


# ── Deps-scoped dossier ──────────────────────────────────────────────────────


def test_dossier_scoped_to_declared_deps_for_exploit_engineer():
    """ExploitEngineer's ``Dossier_ExploitEngineer`` node contains ONLY its declared
    deps' findings — not an unrelated agent's finding (the deps-scoping guarantee)."""
    ee = get_entry("Dossier_ExploitEngineer")
    dep_keys = set(ee.dep_keys)
    # Security is a declared dep; DocsKeeper is NOT a dep of Dossier_ExploitEngineer.
    assert "Security" in dep_keys and "DocsKeeper" not in dep_keys
    by_agent = {
        "Security": json.dumps({"findings": [_security_finding("SEC-1")]}),
        "DocsKeeper": json.dumps({"findings": [{"id": "DOC-1"}]}),
    }
    run_fn = _fake_runner(by_agent)
    run_graph_dag(run_fn=run_fn, concurrency=4)
    ee_prompt = run_fn.prompts["ExploitEngineer"]  # type: ignore[attr-defined]
    assert "security::SEC-1" in ee_prompt  # declared dep -> included
    assert "DOC-1" not in ee_prompt  # non-dep -> excluded


def test_dossier_judge_includes_non_soft_dep_finding():
    """Judge sees a non-terminal finding via ``Dossier_Judge`` even from an agent it
    does not declare as a direct soft dep (the dossier node deps on all producers)."""
    by_agent = {"DocsKeeper": json.dumps({"findings": [{"id": "DOC-9"}]})}
    run_fn = _fake_runner(by_agent)
    run_graph_dag(run_fn=run_fn, concurrency=4)
    judge_prompt = run_fn.prompts["Judge"]  # type: ignore[attr-defined]
    assert "docskeeper::DOC-9" in judge_prompt


# ── Inter-agent injection: data flows along the edges ────────────────────────
def test_injection_delivers_producer_output_to_consumer():
    """The reason the DAG exists: a consumer receives its hard dep's verbatim output
    as a `## Context from <Dep> [REQUIRED]` section (not just ordering)."""
    cm = get_entry("Profiler_CodeMap")
    hist = get_entry("Historian")  # hard_dep = Profiler_CodeMap
    assert "Profiler_CodeMap" in hist.required_dep_keys
    sentinel = json.dumps(
        {
            "critical_paths": [{"file": "a.cs", "method": "m", "risk": "high", "reason": "r"}],
            "data_flow_map": {"note": "NODE_A->NODE_B sentinel-XYZ"},
        }
    )
    run_fn = _fake_runner({"Profiler_CodeMap": sentinel})
    run_graph_dag(run_fn=run_fn, entries=[cm, hist], concurrency=1)
    hist_prompt = run_fn.prompts["Historian"]  # type: ignore[attr-defined]
    assert "## Context from Profiler_CodeMap [REQUIRED]" in hist_prompt
    assert "sentinel-XYZ" in hist_prompt  # verbatim producer output reached consumer


def test_injection_git_history_reaches_historian():
    """Git History (non-diff-derivable, previously gathered-then-dropped) is injected
    into Historian when the git_history source node supplies it via the snapshot."""
    from roundtable.graph.model import GraphEntry

    cm = get_entry("Profiler_CodeMap")
    hist = get_entry("Historian")
    git_src = GraphEntry(key="GitHistory", prompt_path="", edges=(), emoji="x", kind="source")
    run_fn = _fake_runner({})
    run_graph_dag(
        run_fn=run_fn,
        entries=[git_src, cm, hist],
        concurrency=1,
        source_payloads={"GitHistory": "\n=== Repository: demo ===\n--- a.ts ---\ndeadbee fix bug"},
    )
    hist_prompt = run_fn.prompts["Historian"]  # type: ignore[attr-defined]
    assert "## Git History (git log --oneline -10 per changed file)" in hist_prompt
    assert "deadbee fix bug" in hist_prompt
    # A non-flagged agent (Profiler_CodeMap) must NOT receive Git History.
    assert "## Git History" not in run_fn.prompts["Profiler_CodeMap"]  # type: ignore[attr-defined]


def test_injection_degraded_dep_renders_unavailable_stub():
    """A required dep that produced no valid output yields a [REQUIRED — UNAVAILABLE]
    stub in the consumer's context (graceful degradation, visible to the model)."""
    cm = replace(get_entry("Profiler_CodeMap"))
    hist = get_entry("Historian")

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **kw):
        run_fn.prompts.setdefault(agent, prompt)  # type: ignore[attr-defined]
        if agent == "Profiler_CodeMap":
            return FakeResult("", exit_code=1)  # invalid output
        return FakeResult('{"findings":[]}')

    run_fn.prompts = {}  # type: ignore[attr-defined]
    run_graph_dag(run_fn=run_fn, entries=[cm, hist], concurrency=1)
    hist_prompt = run_fn.prompts["Historian"]  # type: ignore[attr-defined]
    assert "## Context from Profiler_CodeMap [REQUIRED — UNAVAILABLE]" in hist_prompt


def test_simulate_mock_populates_judge_dossier():
    """End-to-end with the --simulate mock runner: the full graph runs offline and
    Judge's context carries the populated AgentDossier (delivered by its dedicated
    ``Dossier_Judge`` node under ``## agent_dossier`` — the section whose absence was
    the original fidelity bug)."""
    from roundtable.backend.mock_runner import build_valid_stub, make_mock_run_agent

    base_run = make_mock_run_agent(configuration=_CONFIG)
    prompts: dict[str, str] = {}

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **kw):
        prompts.setdefault(agent, prompt)
        return base_run(agent=agent, prompt=prompt, add_dirs=add_dirs, timeout_s=timeout_s, **kw)

    run_graph_dag(run_fn=run_fn, concurrency=4)
    judge_prompt = prompts["Judge"]
    assert "## agent_dossier" in judge_prompt
    # A simulated specialist finding (from its schema example) is indexed as `<agent>::<id>`.
    sec_fid = build_valid_stub("Security", configuration=_CONFIG)["findings"][0]["id"]
    assert f"::{sec_fid}" in judge_prompt


def test_curated_subgraph_order():
    entries = [get_entry("SchemaDrift"), get_entry("SeverityInflator"), get_entry("Judge")]
    by_agent = {
        "SchemaDrift": json.dumps({"findings": [_schema_drift_finding("SDR-1")]}),
        "SeverityInflator": json.dumps(
            {
                "inflated_findings": [
                    {
                        "finding_id": "INF-1",
                        "source_agent": "schema_drift",
                        "original_severity": "low",
                        "severity": "high",
                    }
                ]
            }
        ),
        # Judge reconciles the specialist corpus, which EXCLUDES SeverityInflator
        # (its inflated_findings re-severity existing findings — counting them would
        # double the zero-drop total). So Judge references the original producer.
        "Judge": json.dumps(
            _valid_judge(
                "REJECT",
                verdict_overlay=[
                    {
                        "source_agent": "SchemaDrift",
                        "finding_id": "SDR-1",
                        "blocking": True,
                        "judge_justification": "j",
                    }
                ],
            )
        ),
    }
    run_fn = _fake_runner(by_agent)
    res = run_graph_dag(run_fn=run_fn, entries=entries, concurrency=2)

    assert res.execution_order == ["SchemaDrift", "SeverityInflator", "Judge"]


# ── Hard-dep-failure: graceful degradation (no skip) ─────────────────────────


def test_hard_dep_failure_runs_dependent_degraded():
    """If a hard dep produces no valid output, the dependent STILL runs (no
    skip-cascade) — graceful degradation, never a skip-cascade."""
    a = get_entry("Profiler_CodeMap")
    cc = get_entry("CodeCorrectness")
    al = get_entry("Analyst_Logic")  # hard deps: Profiler_CodeMap, CodeCorrectness

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **kw):
        if agent == "CodeCorrectness":
            return FakeResult("", exit_code=1)  # not ok -> no content -> invalid
        return FakeResult('{"findings":[{"id":"X-1"}]}')

    res = run_graph_dag(run_fn=run_fn, entries=[a, cc, al], concurrency=2)
    assert "Analyst_Logic" in res.execution_order  # ran, not skipped
    # CodeCorrectness itself ran (just failed) and its consumer still launched.
    assert "CodeCorrectness" in res.execution_order
    assert res.results["CodeCorrectness"].valid is False


def test_no_agent_skipped_on_full_graph():
    """Graceful degradation: a full DAG run never skips an agent (every agent's deps
    reach terminal state, so every agent launches)."""
    run_fn = _fake_runner({})
    res = run_graph_dag(run_fn=run_fn, concurrency=4)
    runnable = {e.key for e in _runnable_graph_entries(_CONFIG)}
    assert runnable.issubset(set(res.execution_order))


# ── Determinism ──────────────────────────────────────────────────────────────


def test_execution_order_deterministic_concurrency_one():
    run_a = _fake_runner({})
    run_b = _fake_runner({})
    res_a = run_graph_dag(run_fn=run_a, concurrency=1)
    res_b = run_graph_dag(run_fn=run_b, concurrency=1)
    assert res_a.execution_order == res_b.execution_order


# ── Cycle detection ──────────────────────────────────────────────────────────


def test_cycle_detection_raises():
    a = replace(get_entry("Profiler_Intent"), edges=(Edge("Profiler_CodeMap"),))
    b = replace(get_entry("Profiler_CodeMap"), edges=(Edge("Profiler_Intent"),))
    with pytest.raises(ValueError, match="cycle"):
        _compute_depths([a, b], frozenset({a.key, b.key}), frozenset({a.key, b.key}))


# ── concurrency: the pool width must come from the caller, end to end ─────────
# Regression guard in the shape of the graph-timeout bug: a knob that is resolved
# in config but never reaches its consumer is indistinguishable from a hard-code.
# Assert at the consumer (the thread pool), not at the config object.


def test_scheduler_pool_width_comes_from_concurrency_argument(monkeypatch) -> None:
    import roundtable.engine.dag_scheduler as sched

    seen: list[int] = []
    real_pool = sched.ThreadPoolExecutor

    def spy(max_workers=None, **kw):
        seen.append(max_workers)
        return real_pool(max_workers=max_workers, **kw)

    monkeypatch.setattr(sched, "ThreadPoolExecutor", spy)
    run_graph_dag(run_fn=_fake_runner({}), concurrency=3)
    assert seen == [3]


def test_scheduler_pool_width_defaults_to_declared_constant(monkeypatch) -> None:
    import roundtable.engine.dag_scheduler as sched
    from roundtable.settings.workspace import DEFAULT_CONCURRENCY

    seen: list[int] = []
    real_pool = sched.ThreadPoolExecutor

    def spy(max_workers=None, **kw):
        seen.append(max_workers)
        return real_pool(max_workers=max_workers, **kw)

    monkeypatch.setattr(sched, "ThreadPoolExecutor", spy)
    run_graph_dag(run_fn=_fake_runner({}))
    assert seen == [DEFAULT_CONCURRENCY]
