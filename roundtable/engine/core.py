"""Run-scoped graph engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from roundtable.backend import BackendOptions, BackendProvider, backend_provider
from roundtable.graph import Configuration, register_config_plugins
from roundtable.settings import DEFAULT_CONCURRENCY, DEFAULT_MAX_ATTEMPTS

from .executor import get_executor
from .inputs import AgentInputBuilder, default_agent_input
from .model import RunResult, _runnable_graph_entries


@dataclass(frozen=True)
class RunOptions:
    concurrency: int = DEFAULT_CONCURRENCY
    add_dirs: tuple[str, ...] = ()
    session_reuse: bool = True
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    cwd: str | None = None
    agent_input_builder: AgentInputBuilder = default_agent_input
    changed_files: tuple[str, ...] = ()
    mcp_servers_by_key: Mapping[str, dict[str, dict] | None] | None = None


class Engine:
    def __init__(
        self,
        backend: str | BackendProvider,
        *,
        options: RunOptions | None = None,
    ) -> None:
        self._backend_provider = backend_provider(backend) if isinstance(backend, str) else backend
        self._options = options or RunOptions()

    def run(
        self,
        config: Configuration,
        inputs: Mapping[str, str],
    ) -> RunResult:
        register_config_plugins(config)
        options = self._options
        backend_options = BackendOptions(
            configuration=config,
            cwd=options.cwd,
            changed_files=options.changed_files,
            mcp_servers_by_key=options.mcp_servers_by_key,
        )
        with self._backend_provider(backend_options) as backend:
            return get_executor(config.executor).run(
                backend=backend,
                configuration=config,
                entries=_runnable_graph_entries(config),
                concurrency=options.concurrency,
                add_dirs=list(options.add_dirs) or None,
                agent_input_builder=options.agent_input_builder,
                source_payloads=inputs,
                session_reuse=options.session_reuse,
                max_attempts=options.max_attempts,
                cwd=options.cwd,
                max_steps=config.max_steps,
            )
