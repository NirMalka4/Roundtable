"""scheduler_common: symbols shared by the graph executor(s).

Extracted from the retired ``wave_scheduler`` so
the executor and its consumers (``review.py``) depend on a neutral module rather than
on a specific scheduler implementation. Holds the result/record dataclasses, the LLM
seam type and the runnable-graph filter — neither of which is
wave- or DAG-specific.
"""

from __future__ import annotations

from dataclasses import dataclass

from roundtable.backend import Backend
from roundtable.graph import Configuration, GraphEntry

from .agent_runner import AgentRunOutcome

__all__ = [
    "Activation",
    "ActivationLog",
    "Backend",
    "RunResult",
    "SchedulerResult",
    "_runnable_graph_entries",
]


@dataclass(frozen=True)
class Activation:
    """One execution of a node — the atom of the ordered run log.

    A DAG run appends exactly one per launched node, in execution order. Dynamic
    strategies (Send fan-out, bounded feedback loops) append several for the same
    ``node_key`` with an increasing ``step_index``. Cost/validity/gate all live on
    the wrapped ``outcome`` — this adds only the node's identity + step, so there is
    one source of truth per fact (no field is copied out of the outcome).

    **Iteration substrate (contract, not yet exercised).** ``step_index`` is the
    counter a macro *revise* loop budgets against: when a downstream node emits
    :class:`~roundtable.feedback.Feedback` back along a reserved
    ``backward: true`` edge (see :attr:`graph.Edge.budget`), a future
    iterative executor re-activates the target node — appending another
    ``Activation`` for the same ``node_key`` — and stops once its re-activation count
    reaches that edge's ``budget``. The DAG executor produces one activation/node, so
    this log is already the right shape; only the executor that grows it is deferred.
    """

    node_key: str
    step_index: int
    outcome: AgentRunOutcome


ActivationLog = list[Activation]


@dataclass
class RunResult:
    """Outcome of a full graph run.

    The ordered :attr:`activations` log is the source of truth; :attr:`results` is a
    per-node projection over it (see the property). A DAG run is 1 activation/node,
    so the projection is 1:1 today; the log generalizes to multiple activations per
    node without changing the per-node consumers.
    """

    activations: ActivationLog  # ordered: one entry per node execution
    execution_order: list[str]  # launch order (depth, declaration index)
    # Universal step budget in effect for this run (None = unbounded). The executor
    # caps total activations at it; ``budget_hit`` records whether that cap actually
    # stopped work.
    max_steps: int | None = None
    budget_hit: bool = False

    @property
    def steps(self) -> int:
        """Total node activations in this run (the atom the budget bounds)."""
        return len(self.activations)

    @property
    def terminated_within_budget(self) -> bool:
        """The run finished all its work without the step budget cutting it short.

        ``True`` when unbounded or when the executor never hit the cap; ``False``
        only when the budget truncated the run (``budget_hit``). A validated DAG is
        always ``True`` (one activation per node); this generalizes to bounded loops.
        """
        return not self.budget_hit

    @property
    def results(self) -> dict[str, AgentRunOutcome]:
        """Node-projection of the activation log: ``node_key -> its outcome``.

        Groups the ordered log by node and keeps the LAST activation (1:1 for a DAG
        run). Per-node consumers (verdict, publish, trace) read this;
        the log preserves the full ordered history for multi-activation strategies.
        """
        out: dict[str, AgentRunOutcome] = {}
        for a in self.activations:
            out[a.node_key] = a.outcome
        return out


SchedulerResult = RunResult


def _runnable_graph_entries(config: Configuration) -> list[GraphEntry]:
    """Graph-scheduled executable nodes (LLM or code enricher).

    Excludes ``non_graph_infra`` agents (they run observe-only outside the graph).
    ``kind`` — not ``is_llm`` — is the gate: a ``code`` node is scheduled and
    executed like any producer, but by a pure fn rather than Copilot. ``config``
    defaults to the ambient configuration; pass an explicit one to compute the
    runnable set of a specific (e.g. draft) configuration.
    """
    from .nodes import NODE_HANDLERS

    return [
        entry
        for entry in config.entries
        if entry.kind in NODE_HANDLERS and entry.key not in config.non_graph_infra_agents
    ]
