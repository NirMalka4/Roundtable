"""Unit tests for the ``kind: source`` corpus-provider node.

A source node is a graph ROOT: it emits pre-seeded content (``source_payloads``)
as a valid outcome. It has no deps, so it is ready in the first wave and runs as
an ordinary node — consumers declare an explicit ``kind: source`` edge to it and
read the corpus (diff / git-history) from the snapshot, so the source is naturally
ordered before them. These tests lock the per-kind structural contract, the node's
emit behaviour, and the edge-driven ordering.
"""

from __future__ import annotations

import dataclasses

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import Edge, GraphEntry, get_configuration, validate_graph_config
from tests.support.engine import run_graph_dag

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def _source(key: str, **over) -> GraphEntry:
    base = {"key": key, "prompt_path": "", "edges": (), "emoji": "x", "kind": "source"}
    base.update(over)
    return GraphEntry(**base)


def _llm(key: str, *, hard=()) -> GraphEntry:
    return GraphEntry(
        key=key,
        prompt_path=f"Agents/{key}.agent.md",
        edges=tuple(Edge(d) for d in hard),
        emoji="x",
    )


@dataclasses.dataclass
class _FakeResult:
    final_content: str
    exit_code: int | None = 0
    timed_out: bool = False
    tool_call_count: int = 0
    tool_calls: list[dict] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and bool(self.final_content)


# ─── per-kind structural contract (validate_graph_config) ───────────────────


def test_source_kind_is_valid_and_needs_no_prompt_model_or_fn():
    # A well-formed source node passes structural validation (no raise).
    validate_graph_config(entries=(_source("ReviewDiff"),), config=_CONFIG)


def test_source_must_not_declare_edges_prompt_model_or_fn():
    bad = _source(
        "Bad",
        edges=(Edge("X"),),
        prompt_path="Agents/Bad.agent.md",
        model="m",
        code_fn="f",
    )
    with pytest.raises(ValueError) as exc:
        validate_graph_config(entries=(bad, _llm("X")), config=_CONFIG)
    joined = str(exc.value)
    assert "must not declare edges" in joined
    assert "must not declare a prompt_path" in joined
    assert "must not declare a model" in joined
    assert "must not declare a code_fn" in joined


# ─── run_source_node emit + edge-driven ordering ────────────────────────────


def _no_llm_run_fn(**kw):  # pragma: no cover - some graphs never spawn an llm
    raise AssertionError("run_fn should not be called")


def test_source_emits_preseeded_payload_as_valid_outcome():
    src = _source("ReviewDiff")
    res = run_graph_dag(
        configuration=_CONFIG,
        run_fn=_no_llm_run_fn,
        entries=[src],
        concurrency=1,
        source_payloads={"ReviewDiff": "DIFF BODY"},
    )
    out = res.results["ReviewDiff"]
    assert out.valid is True
    assert out.response == "DIFF BODY"


def test_source_with_no_payload_emits_empty_valid_outcome():
    res = run_graph_dag(
        configuration=_CONFIG,
        run_fn=_no_llm_run_fn,
        entries=[_source("ReviewDiff")],
        concurrency=1,
    )
    out = res.results["ReviewDiff"]
    assert out.valid is True
    assert out.response == ""


def test_source_runs_before_consumers_in_execution_order():
    """A consumer declares an explicit ``kind: source`` edge, so the source (a root,
    ready in wave 1) is ordered before it — its corpus outcome is already in the
    snapshot when the consumer's prompt is rendered."""
    src = _source("ReviewDiff")
    consumer = _llm("Profiler_CodeMap", hard=("ReviewDiff",))  # explicit source edge

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **kw):
        return _FakeResult('{"x": 1}')

    res = run_graph_dag(
        configuration=_CONFIG,
        run_fn=run_fn,
        entries=[src, consumer],
        concurrency=1,
        source_payloads={"ReviewDiff": "DIFF BODY"},
    )
    assert res.execution_order[0] == "ReviewDiff"
    assert res.execution_order.index("ReviewDiff") < res.execution_order.index("Profiler_CodeMap")
    assert res.results["ReviewDiff"].response == "DIFF BODY"


def test_consumer_resolves_diff_from_its_source_edge():
    """The diff reaches the consumer's rendered prompt via its ``kind: source`` edge —
    the corpus is an explicit edge, not an ambient global."""
    seen: dict[str, str] = {}

    def run_fn(*, agent, prompt, add_dirs=None, timeout_s=600.0, **kw):
        seen.setdefault(agent, prompt)  # capture attempt 1 (full base context)
        return _FakeResult('{"x": 1}')

    src = _source("ReviewDiff")
    consumer = _llm("Profiler_CodeMap", hard=("ReviewDiff",))
    run_graph_dag(
        configuration=_CONFIG,
        run_fn=run_fn,
        entries=[src, consumer],
        concurrency=1,
        source_payloads={"ReviewDiff": "diff --git a/x b/x\n+MARKER"},
    )
    assert "MARKER" in seen["Profiler_CodeMap"]
