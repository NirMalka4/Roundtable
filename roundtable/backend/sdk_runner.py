"""sdk_runner: the SDK-backed ``run_fn`` for every LLM agent run.

:func:`make_sdk_run_agent` closes over one review-scoped :class:`SdkBridge` and
returns a callable with the same keyword surface the scheduler injects at
``cli.py``. Each call runs (or resumes, by ``session_id``) one turn on the bridge
and maps the raw :class:`TurnResult` into a :class:`CopilotResult`, so nothing
downstream (run loop, OVG, trace, reporting) needs to change.

Failure surfacing: a non-ok turn's structured :class:`BackendOutcome` is carried
on ``CopilotResult.backend_outcome``; its message is kept in ``raw_stderr`` for
diagnostics/trace. ``events`` is left empty (not load-bearing, verified m86).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from roundtable.runtime import NativeAgentFields, graph_custom_agents
from roundtable.validation import build_submission_schema

from .registry import Backend, BackendOptions, normalize_backend_result
from .result import CopilotResult, ExecutionPolicy, OutputSubmission, RunRequest, RunResult
from .sdk import SdkBridge, TurnResult, require_terminal_tool_support, resolve_auth
from .sdk.config_map import build_session_kwargs
from .sdk.errors import BackendOutcome
from .sdk.events import project_events
from .sdk.result_map import map_result_fields
from .sdk.tool_policy import resolve_execution_policy
from .sdk.usage_map import SessionUsageAccumulator, extract_turn_usage


def _exit_code_for(outcome: BackendOutcome, timed_out: bool) -> int | None:
    """Map a backend outcome to a CLI-style exit code (drives ``CopilotResult.ok``)."""
    if timed_out or outcome is BackendOutcome.TIMEOUT:
        return None  # timed_out already forces ok=False
    return 0 if outcome is BackendOutcome.SUCCESS else 1


def _to_copilot_result(
    turn: TurnResult,
    usage_acc: SessionUsageAccumulator,
    agent: str | None = None,
    execution_policy: ExecutionPolicy | None = None,
    submission: OutputSubmission | None = None,
) -> CopilotResult:
    mapped = map_result_fields(
        turn.events,
        agent,
        default_cwd=turn.working_directory,
        timeout_snapshot=turn.timeout_snapshot,
    )
    event_projection = project_events(turn.events)
    usage = usage_acc.record(
        turn.session_id,
        extract_turn_usage(turn.events),
        turn.wall_clock_s,
        turn.billing,
    )
    result = CopilotResult(
        final_content=mapped.final_content,
        tool_call_count=mapped.tool_call_count,
        rounds=mapped.rounds,
        exit_code=_exit_code_for(turn.outcome, turn.timed_out),
        session_id=turn.session_id,
        usage={},
        tools_used=mapped.tools_used,
        tool_calls=mapped.tool_calls,
        events=event_projection.events,
        events_truncated=event_projection.truncated,
        events_omitted_count=event_projection.omitted_count,
        timed_out=turn.timed_out,
        timeout_phase=turn.timeout_phase,
        timeout_snapshot=turn.timeout_snapshot,
        wall_clock_s=turn.wall_clock_s,
        raw_stdout=mapped.final_content,
        raw_stderr=turn.error_message or "",
        backend_outcome=turn.outcome,
        mcp_servers=[],
        token_usage=usage,
        execution_policy=execution_policy,
    )
    if submission is None:
        return result
    return normalize_backend_result(
        RunRequest(agent=agent or "", prompt="", submission=submission),
        result,
    )


def make_sdk_run_agent(bridge: SdkBridge, agents: dict[str, NativeAgentFields]):
    """Build a ``run_fn`` over a review-scoped :class:`SdkBridge` + agent registry.

    The returned callable is the ``run_fn`` the scheduler injects (keyword surface
    defined at ``cli.py``). ``agents`` maps each agent key to its composed prompt +
    description (the graph
    SSOT), passed programmatically as ``custom_agents`` — so no on-disk agents dir
    is needed. ``session_id`` (when the scheduler enables session reuse) addresses
    the turn: the first use creates the session, a later use resumes it.
    """
    usage_acc = SessionUsageAccumulator()

    def run_fn(
        *,
        agent: str,
        prompt: str,
        timeout_s: float = 600.0,
        session_id: str | None = None,
        cwd: str | None = None,
        submission: OutputSubmission | None = None,
        **_kw: Any,
    ) -> CopilotResult:
        spec = agents.get(agent)
        if spec is None:
            raise KeyError(f"SDK backend: no programmatic agent registered for {agent!r}")
        sid = session_id or str(uuid4())
        execution_policy = resolve_execution_policy(spec.tool_policy)
        shell_cap = execution_policy.shell_invocation_cap_s if execution_policy else None
        session_kwargs = build_session_kwargs(
            agent=agent,
            prompt=spec.prompt,
            description=spec.description,
            display_name=spec.display_name,
            tools=spec.tools,
            model=spec.model,
            mcp_servers=spec.mcp_servers,
            cwd=cwd,
            execution_policy=execution_policy,
            submission_enabled=submission is not None,
        )
        turn_kwargs: dict[str, Any] = {
            "timeout_s": timeout_s,
            "shell_invocation_cap_s": shell_cap,
            "session_kwargs": session_kwargs,
        }
        if submission is not None:
            turn_kwargs["submission"] = submission
        turn = bridge.run_turn(sid, prompt, **turn_kwargs)
        return _to_copilot_result(turn, usage_acc, agent, execution_policy, submission)

    return run_fn


class SdkBackend:
    def __init__(self, bridge: SdkBridge, agents: dict[str, NativeAgentFields]) -> None:
        self._bridge = bridge
        self._agents = agents
        self._usage = SessionUsageAccumulator()

    def run(self, request: RunRequest) -> RunResult:
        spec = self._agents.get(request.agent)
        if spec is None:
            raise KeyError(f"SDK backend: no programmatic agent registered for {request.agent!r}")
        session_id = request.session_id or str(uuid4())
        execution_policy = resolve_execution_policy(spec.tool_policy)
        shell_cap = execution_policy.shell_invocation_cap_s if execution_policy else None
        session_kwargs = build_session_kwargs(
            agent=request.agent,
            prompt=spec.prompt,
            description=spec.description,
            display_name=spec.display_name,
            tools=spec.tools,
            model=spec.model,
            mcp_servers=spec.mcp_servers,
            cwd=request.cwd,
            execution_policy=execution_policy,
            submission_enabled=request.submission is not None,
        )
        turn_kwargs: dict[str, Any] = {
            "timeout_s": request.timeout_s,
            "shell_invocation_cap_s": shell_cap,
            "session_kwargs": session_kwargs,
        }
        if request.submission is not None:
            turn_kwargs["submission"] = request.submission
        turn = self._bridge.run_turn(session_id, request.prompt, **turn_kwargs)
        return _to_copilot_result(
            turn,
            self._usage,
            request.agent,
            execution_policy,
            request.submission,
        )


@contextmanager
def sdk_backend(
    options: BackendOptions,
) -> Iterator[Backend]:
    """Own one review-scoped :class:`SdkBridge` and yield its ``run_fn``.

    Builds the programmatic agent registry from ``bundle_root`` (the graph SSOT),
    resolves auth, starts the client on entry, and disconnects it on exit (even on
    error), so the scheduler gets a plain ``run_fn`` with the bridge lifecycle
    bounded to the review. Raises before yielding if auth or the handshake fails.

    The client runs against an ephemeral ``base_directory`` (``COPILOT_HOME``) so the
    session store + SDK state are per-review and never touch the operator's ambient
    ``~/.copilot``; it is removed on exit, after the bridge is closed.
    """
    root = options.configuration.root / "prompts" / "Reviewer"
    require_terminal_tool_support()
    for entry in options.configuration.entries:
        if entry.is_llm and entry.output_schema is not None:
            build_submission_schema(entry.output_schema, options.configuration.root / "schemas")
    agents = graph_custom_agents(root, options.configuration.entries)
    if options.mcp_servers_by_key:
        agents = {
            key: replace(spec, mcp_servers=options.mcp_servers_by_key.get(key))
            for key, spec in agents.items()
        }
    env = dict(options.env) if options.env is not None else None
    auth = resolve_auth(env=env, allow_logged_in_user=options.allow_logged_in_user)
    with TemporaryDirectory(prefix="roundtable-sdk-", ignore_cleanup_errors=True) as base_dir:
        bridge = SdkBridge(
            auth,
            working_directory=options.cwd,
            base_directory=base_dir,
            env=env,
        )
        bridge.start()
        try:
            yield SdkBackend(bridge, agents)
        finally:
            bridge.close()
