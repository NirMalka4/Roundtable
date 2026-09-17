"""dag_scheduler: the DAG graph-execution strategy — a pure dependency-driven scheduler.

Agents are scheduled **purely from the dependency edges** declared in
``agent_graph.yaml``: an agent launches as soon as its deps reach a terminal state,
with no fixed wave numbers and no wave-by-wave barrier. ``run_graph_dag`` is the
DAG implementation; callers reach it through the :class:`~.executor.Executor` seam
(``DagExecutor``), not by importing this function directly.

Scheduling semantics:

  * **Readiness.** An agent is ready when **every dep (hard ∪ soft) has reached a
    terminal state** (completed, success OR failure). Deps gate *ordering* — they do
    NOT gate *success*: a consumer always runs once its deps have finished, even if a
    dep failed. Deps that are not
    in the scheduled set (e.g. ``DeterministicPreScan`` / other ``non_graph_infra``
    agents that run observe-only outside the graph) are always satisfied. Hard and
    soft deps are **identical for scheduling** — the difference is observability.
  * **Terminal-last via explicit consolidation nodes.** A terminal reconciler node
    hard-depends on its per-consumer consolidation (``consolidation:``) node, which in
    turn soft-depends on every upstream producer it aggregates. That explicit edge
    chain (no implicit "collect-all" wait) guarantees that the terminal reconciler
    runs last and receives complete consolidated input in a sparse DAG.
  * **Hard vs soft (observability, not control flow).** Both wait for terminal state.
    The split survives as a declared **required-input contract** — if a hard dep
    ends missing/invalid the consumer still runs but is flagged ``degraded``
    (incomplete input). A missing soft dep is silent.
  * **No skip-cascade (graceful degradation).** A failed dep never removes its
    dependents from the schedule. Every agent receives the source payload(s) as its
    PRIMARY context (a consolidated upstream digest is only supplementary), so a
    consumer can still run on a degraded consolidated input. Skipping would silently
    drop a declared consumer's output — degraded-but-run is always preferred to
    absent.
  * **Topological depth.** A topological depth (longest dependency chain) is computed
    so ``execution_order`` sorts hard deps before their consumers — a hard dep always
    has strictly smaller depth.
  * **Consolidation as a first-class node.** A consumer's consolidated input is
    produced by a dedicated deterministic consolidation node whose own dep list *is*
    its scope (the generic ``context/enrichers.consolidate`` reducer, wired by the
    node's declarative ``consolidation:`` block), and delivered through the generic
    injection path like any other node output — never a race-dependent "all upstream
    so far" snapshot assembled inline.

Bounded parallelism via a thread pool (``copilot`` runs as a subprocess; threads give
real concurrency on the I/O wait). The ready-set is launched in **declaration order**
so ``execution_order`` stays reproducible for a given dependency-completion pattern.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from roundtable.backend import Backend
from roundtable.graph import Configuration, GraphEntry, evaluate_predicate
from roundtable.settings import DEFAULT_CONCURRENCY, DEFAULT_MAX_ATTEMPTS

from .agent_runner import AgentRunOutcome
from .inputs import AgentInputBuilder, default_agent_input
from .model import (
    Activation,
    SchedulerResult,
    _runnable_graph_entries,
)
from .nodes import NodeContext, dispatch_node


def _log(msg: str) -> None:
    print(f"[dag_scheduler] {msg}", file=sys.stderr)


def _parse_output(outcome: AgentRunOutcome | None) -> Mapping[str, Any]:
    """The producer's JSON output as a mapping for predicate evaluation.

    Best-effort: an absent/invalid/non-JSON-object response yields ``{}`` — so a
    predicate over it simply evaluates false (the condition is unmet), never an
    error. Conditional routing must never crash the run.
    """
    if outcome is None or not outcome.response:
        return {}
    try:
        parsed = json.loads(outcome.response)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _effective_dep_keys(
    entry: GraphEntry,
    scheduled: frozenset[str],
    non_terminal: frozenset[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(hard, soft)`` dep keys that are actually in the scheduled set."""
    hard = frozenset(d for d in entry.required_dep_keys if d in scheduled)
    soft = frozenset(d for d in entry.optional_dep_keys if d in scheduled)
    return hard, soft


def _compute_depths(
    entries: list[GraphEntry],
    scheduled: frozenset[str],
    non_terminal: frozenset[str],
) -> dict[str, float]:
    """Longest-path depth per agent over the effective (hard∪soft) dep graph.

    Used to sort ``execution_order``. Raises ``ValueError`` on a
    dependency cycle (the DAG must be acyclic for the executor to terminate).
    """
    entry_by_key = {e.key: e for e in entries}
    depth: dict[str, float] = {}
    visiting: set[str] = set()

    def _depth(key: str) -> float:
        if key in depth:
            return depth[key]
        if key in visiting:
            raise ValueError(f"dependency cycle detected through agent '{key}'")
        entry = entry_by_key.get(key)
        if entry is None:
            return -1.0  # dep outside the scheduled set: always-satisfied root
        visiting.add(key)
        hard, soft = _effective_dep_keys(entry, scheduled, non_terminal)
        dep_keys = hard | soft
        d = 0.0 if not dep_keys else 1.0 + max(_depth(k) for k in dep_keys)
        visiting.discard(key)
        depth[key] = d
        return d

    for e in entries:
        _depth(e.key)
    return depth


def run_graph_dag(
    *,
    backend: Backend,
    configuration: Configuration,
    entries: list[GraphEntry] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    add_dirs: list[str] | None = None,
    agent_input_builder: AgentInputBuilder = default_agent_input,
    source_payloads: Mapping[str, str] | None = None,
    session_reuse: bool = True,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    cwd: str | None = None,
    max_steps: int | None = None,
) -> SchedulerResult:
    """Execute the agent graph as a pure DAG (no waves) with bounded parallelism.

    Source payloads run as ordinary graph roots. The caller-provided
    ``agent_input_builder`` maps the current snapshot to each LLM input; readiness
    remains derived only from dependency edges.
    """
    source_payloads = source_payloads or {}
    graph_entries = entries if entries is not None else _runnable_graph_entries(configuration)
    entry_by_key: dict[str, GraphEntry] = {e.key: e for e in graph_entries}
    decl_index = {e.key: i for i, e in enumerate(graph_entries)}
    scheduled = frozenset(entry_by_key)
    non_terminal = frozenset(k for k, e in entry_by_key.items() if not e.terminal)
    depth = _compute_depths(graph_entries, scheduled, non_terminal)

    results: dict[str, AgentRunOutcome] = {}
    completed: set[str] = set()
    skipped: set[str] = set()
    launched: set[str] = set()
    meta_map: dict[str, dict[str, Any]] = {}
    budget_hit = False

    def _is_ready(entry: GraphEntry) -> bool:
        """True when every dep (hard ∪ soft) has reached a terminal state.

        Deps gate ordering only — a failed/skipped dep still counts as terminal, so
        readiness never blocks on a dep's *success* (no skip-cascade). The hard/soft
        distinction is observability (degraded flag), not control
        flow, so both are treated identically here.
        """
        hard, soft = _effective_dep_keys(entry, scheduled, non_terminal)
        return all(not (d not in completed and d not in skipped) for d in hard | soft)

    def _degraded_hard_deps(entry: GraphEntry) -> tuple[str, ...]:
        """Declared (scheduled) hard deps that are missing/invalid at launch — the
        consumer runs anyway, but on incomplete input (degraded)."""
        hard, _ = _effective_dep_keys(entry, scheduled, non_terminal)
        bad = [
            d
            for d in hard
            if d in skipped or (d in results and not results[d].valid) or d not in completed
        ]
        return tuple(sorted(bad, key=lambda k: decl_index.get(k, 0)))

    def _inactive_conditional_dep(entry: GraphEntry) -> str | None:
        """The first conditional edge whose ``when:`` predicate is FALSE, or None.

        A conditional (required) edge activates the consumer only when its predicate
        holds over the producer's schema-validated output. Evaluated at readiness
        (the producer is already terminal): a producer that was skipped or produced
        no parseable JSON yields ``{}`` — so its predicate is false and the consumer
        is skipped. A false predicate ⇒ the consumer is skipped (branch/skip),
        reusing the existing ``skipped``/``na`` tri-state.
        """
        for edge in entry.forward_edges:
            if edge.when is None:
                continue
            outcome = results.get(edge.source)
            output = _parse_output(outcome)
            if not evaluate_predicate(edge.when, output):
                return edge.source
        return None

    node_ctx = NodeContext(
        backend=backend,
        configuration=configuration,
        add_dirs=add_dirs,
        cwd=cwd,
        agent_input_builder=agent_input_builder,
        source_payloads=source_payloads,
        session_reuse=session_reuse,
        max_attempts=max_attempts,
        depth=depth,
        scheduled=scheduled,
    )

    workers = max(1, concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_key: dict[Any, str] = {}
        while len(completed) + len(skipped) < len(scheduled):
            # Collect the ready-set in declaration order (no skip decisions: a failed
            # dep degrades but never removes its dependents — graceful degradation).
            ready = sorted(
                (
                    e
                    for e in graph_entries
                    if e.key not in launched and e.key not in skipped and _is_ready(e)
                ),
                key=lambda e: decl_index[e.key],
            )

            # Launch every ready agent (the pool bounds real concurrency). Snapshot
            # of completed results feeds each deterministic node's enricher (incl. the
            # per-consumer consolidation nodes) and inter-agent context injection.
            snapshot = dict(results)
            skipped_before = len(skipped)
            for e in ready:
                # Universal step budget: never launch beyond it. In a validated DAG
                # this is inert (one activation per node ≤ node count); it exists so a
                # deliberately tight budget — and, later, bounded feedback loops —
                # halt deterministically instead of running unbounded.
                if max_steps is not None and len(launched) >= max_steps:
                    budget_hit = True
                    break
                # Conditional forward edge: a false when: predicate skips this node
                # (branch/skip) via the existing skipped/na tri-state — it never
                # launches, and downstream degrades gracefully as for any skipped dep.
                inactive = _inactive_conditional_dep(e)
                if inactive is not None:
                    _log(f"{e.key}: skipped — conditional edge on {inactive} is inactive")
                    skipped.add(e.key)
                    continue
                degraded_deps = _degraded_hard_deps(e)
                if degraded_deps:
                    _log(
                        f"{e.key}: running degraded — missing/invalid hard dep(s): "
                        f"{', '.join(degraded_deps)}"
                    )
                launched.add(e.key)
                fut = pool.submit(dispatch_node, e, snapshot, degraded_deps, node_ctx)
                future_to_key[fut] = e.key

            if budget_hit:
                # Budget exhausted mid-run: launch nothing further. Skip every
                # not-yet-launched node so the loop terminates, then let the
                # in-flight futures drain below (their results are still recorded).
                _log(f"step budget {max_steps} reached — skipping unlaunched nodes")
                for e in graph_entries:
                    if e.key not in launched and e.key not in skipped:
                        skipped.add(e.key)

            if not future_to_key:
                # Nothing is in-flight. If a conditional skip just fired this
                # iteration, re-loop: skipping a node makes its dependents ready
                # (readiness treats skipped as terminal), so progress is still
                # possible. Only when nothing launched, nothing skipped, and nothing
                # runs is the remainder genuinely stuck (a cycle is already caught in
                # _compute_depths) — skip it defensively rather than spin forever.
                if len(skipped) > skipped_before:
                    continue
                for e in graph_entries:
                    if e.key not in completed and e.key not in skipped:
                        skipped.add(e.key)
                break

            done, _ = wait(future_to_key, return_when=FIRST_COMPLETED)
            for fut in done:
                key, outcome, meta = fut.result()
                results[key] = outcome
                completed.add(key)
                meta_map[key] = meta
                del future_to_key[fut]

    # execution_order is derived deterministically from (depth, declaration index),
    # NOT from wall-clock launch timing — so the observable order is reproducible
    # regardless of thread scheduling, while a hard dep (strictly smaller depth)
    # always precedes its consumer.
    execution_order = sorted(launched, key=lambda k: (depth.get(k, 0.0), decl_index.get(k, 0)))

    # The activation log is ordered by the deterministic execution_order (a DAG run
    # emits one activation per launched node). results-as-projection then reads back
    # in this stable order instead of nondeterministic future-completion order.
    # A ``map`` node fans out: it contributes its N per-item sub-activations (step
    # 0..N-1) followed by its aggregated outcome (step N, so the results-projection —
    # last activation per key — stays the aggregated body downstream nodes consumed).
    activations: list[Activation] = []
    for key in execution_order:
        subs = meta_map.get(key, {}).get("fan_out_activations")
        if subs:
            for i, sub_outcome in enumerate(subs):
                activations.append(Activation(node_key=key, step_index=i, outcome=sub_outcome))
            activations.append(Activation(node_key=key, step_index=len(subs), outcome=results[key]))
        else:
            activations.append(Activation(node_key=key, step_index=0, outcome=results[key]))
    return SchedulerResult(
        activations=activations,
        execution_order=execution_order,
        max_steps=max_steps,
        budget_hit=budget_hit,
    )
