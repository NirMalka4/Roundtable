"""executor: the pluggable graph-execution strategy seam.

An :class:`Executor` turns a graph (``entries``) plus per-run inputs into a
:class:`SchedulerResult`. The DAG scheduler is the sole strategy today
(:class:`DagExecutor`); iterative / other strategies plug in behind this same
contract and are selected by a config's ``executor:`` key (wired in the executor
registry, a following increment).

Separation of concerns:

  * The **executor** owns *ordering / readiness* — which node runs when.
  * Per-node *execution* is delegated to the ``kind``-handler registry
    (:func:`roundtable.engine.nodes.dispatch_node`), shared by every
    executor, so a new strategy never re-implements how an ``llm`` or ``code`` node
    runs.

``validate(graph)`` — the executor-specific offline structural check (a DAG
requires acyclicity; an iterative executor would not) — is run by ``doctor``
alongside the generic, executor-agnostic graph checks in
:func:`graph.validate_graph_config`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .dag_scheduler import run_graph_dag
from .model import SchedulerResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from roundtable.graph import GraphEntry


@runtime_checkable
class Executor(Protocol):
    """A named graph-execution strategy: ``run(**inputs) -> SchedulerResult``.

    ``run`` accepts the keyword contract of :func:`run_graph_dag` (the canonical
    signature); an executor is free to interpret a subset. Kept permissive here so
    the seam does not restate — and drift from — that signature.

    ``validate`` is the executor's **own** offline structural check over the parsed
    graph — the checks that depend on the execution model (a DAG needs acyclicity;
    an iterative executor would not). ``doctor`` runs it alongside the
    executor-agnostic :func:`graph.validate_graph_config`. Returns a
    (possibly empty) list of human-readable error strings — it never raises, so the
    caller can collect violations across layers and report them together.

    ``sinks`` reports the graph's **out-degree-0 nodes** (no other node in the given
    set depends on them) for the executor's topology. This is pure structure — the
    engine has NO opinion on how many sinks are allowed or what a sink *means*; a
    consumer that reads a single domain result asserts its own cardinality contract
    (Roundtable's ``doctor`` requires exactly one over the runnable graph).
    """

    name: str

    def run(self, **kwargs: Any) -> SchedulerResult: ...

    def validate(self, entries: Sequence[GraphEntry]) -> list[str]: ...

    def sinks(self, entries: Sequence[GraphEntry]) -> list[str]: ...


class DagExecutor:
    """The pure dependency-driven DAG executor — the sole strategy today.

    A thin adapter over :func:`run_graph_dag` so callers depend on the
    :class:`Executor` seam rather than a concrete function.
    """

    name = "dag"

    def run(self, **kwargs: Any) -> SchedulerResult:
        return run_graph_dag(**kwargs)

    def validate(self, entries: Sequence[GraphEntry]) -> list[str]:
        """The DAG's one execution-model requirement: the graph must be acyclic.

        (An iterative executor would instead permit cycles and bound them by a step
        budget.) Ignores deps outside the graph — they are always-satisfied roots.
        """
        from roundtable.graph import find_dep_cycle

        by_key = {e.key: e for e in entries}
        cycle = find_dep_cycle(by_key, set(by_key))
        if cycle:
            return ["dependency cycle: " + " -> ".join(cycle)]
        return []

    def sinks(self, entries: Sequence[GraphEntry]) -> list[str]:
        """The DAG's out-degree-0 nodes: keys no other node in ``entries`` depends on.

        Pure topology over the given set — edges to nodes outside it are ignored
        (they are always-satisfied roots, as in :meth:`validate`). No cardinality
        opinion: a general DAG may have many leaves; a single-result contract is the
        *consumer's* to assert.
        """
        keys = {e.key for e in entries}
        consumed = {d for e in entries for d in e.dep_keys if d in keys}
        return sorted(keys - consumed)


# ─── Registry ───────────────────────────────────────────────────────────────
# Maps a config's ``executor:`` name to its strategy class. Mirrors the enricher /
# node-handler registries: one home, resolved by name. New strategies register here.
EXECUTOR_REGISTRY: dict[str, type] = {
    DagExecutor.name: DagExecutor,
}


def get_executor(name: str = "dag") -> Executor:
    """Instantiate the executor a config's ``executor:`` key names.

    An unregistered name is a config error surfaced loudly (like an unregistered
    node ``kind`` or ``code_fn``), naming the known strategies.
    """
    try:
        cls = EXECUTOR_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(EXECUTOR_REGISTRY))
        raise ValueError(f"unknown executor {name!r} (known: {known})") from None
    return cls()
