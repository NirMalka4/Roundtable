"""Run-scoped, configuration-decoupled graph execution."""

from roundtable.backend import Backend

from .agent_runner import (
    DEFAULT_AGENT_TIMEOUT_S,
    AgentRunOutcome,
    AttemptDetail,
    build_output_contract,
    build_schema_hint,
    run_agent_with_ovg,
)
from .core import Engine, RunOptions
from .executor import Executor, get_executor
from .inputs import AgentInput, AgentInputBuilder, default_agent_input
from .model import Activation, RunResult, SchedulerResult, _runnable_graph_entries
from .nodes import (
    NODE_HANDLERS,
    NodeContext,
    dispatch_node,
    get_node_handler,
    validate_node_entry,
)

__all__ = [
    "DEFAULT_AGENT_TIMEOUT_S",
    "NODE_HANDLERS",
    "Activation",
    "AgentInput",
    "AgentInputBuilder",
    "AgentRunOutcome",
    "AttemptDetail",
    "Backend",
    "Engine",
    "Executor",
    "NodeContext",
    "RunOptions",
    "RunResult",
    "SchedulerResult",
    "_runnable_graph_entries",
    "build_output_contract",
    "build_schema_hint",
    "default_agent_input",
    "dispatch_node",
    "get_executor",
    "get_node_handler",
    "run_agent_with_ovg",
    "validate_node_entry",
]
