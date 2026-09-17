from __future__ import annotations

from roundtable.backend import Backend, KeywordBackend
from roundtable.engine.agent_runner import run_agent_with_ovg as _run_agent_with_ovg
from roundtable.engine.dag_scheduler import run_graph_dag as _run_graph_dag
from roundtable.graph import Configuration
from roundtable.review.agent_inputs import ReviewAgentInputBuilder


def run_agent_with_ovg(
    *,
    configuration: Configuration,
    run_fn=None,
    backend: Backend | None = None,
    **kwargs,
):
    changed_files = kwargs.pop("changed_files", None)
    ovg_context = dict(kwargs.pop("ovg_context", None) or {})
    ovg_context.setdefault("changed_files", changed_files or [])
    return _run_agent_with_ovg(
        backend=backend or KeywordBackend(run_fn),
        configuration=configuration,
        ovg_context=ovg_context,
        **kwargs,
    )


def run_graph_dag(
    *,
    configuration: Configuration,
    run_fn=None,
    backend: Backend | None = None,
    **kwargs,
):
    config = configuration
    kwargs.setdefault(
        "agent_input_builder",
        ReviewAgentInputBuilder(config, "", {}, (), None),
    )
    return _run_graph_dag(
        backend=backend or KeywordBackend(run_fn),
        configuration=config,
        **kwargs,
    )
