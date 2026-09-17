"""Unit tests for the SDK run_fn (make_sdk_run_agent) over a fake bridge."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable.backend import (
    BackendOptions,
    BillingValue,
    OutputSubmission,
    RunRequest,
    SubmissionValidation,
    sdk_runner,
)
from roundtable.backend.sdk.bridge import TurnResult
from roundtable.backend.sdk.errors import BackendOutcome
from roundtable.backend.sdk_runner import make_sdk_run_agent, sdk_backend
from roundtable.bundle import resolve_bundle
from roundtable.graph import (
    PowershellToolPolicy,
    ReadPowershellToolPolicy,
    ToolPolicy,
    get_configuration,
)
from roundtable.runtime.agent_setup import NativeAgentFields


def _agents(
    *keys: str,
    tool_policy: ToolPolicy | None = None,
) -> dict[str, NativeAgentFields]:
    return {
        k: NativeAgentFields(
            name=k,
            display_name=f"{k} display",
            description=f"{k} desc",
            prompt=f"{k} prompt",
            tools=("read",),
            tool_policy=tool_policy,
            model="model-a",
            mcp_servers={"ado": {"command": "x"}},
        )
        for k in keys
    }


def _msg(content, tool_requests=None):
    return SimpleNamespace(
        type="assistant.message",
        data=SimpleNamespace(content=content, tool_requests=tool_requests),
    )


def _usage(output_tokens=0, cost=0.0, duration=0.0):
    return SimpleNamespace(
        type="assistant.usage",
        data=SimpleNamespace(output_tokens=output_tokens, cost=cost, duration=duration),
    )


class _FakeBridge:
    """Records run_turn calls and returns queued TurnResults."""

    def __init__(self, results: list[TurnResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def run_turn(
        self,
        session_id,
        prompt,
        *,
        timeout_s,
        shell_invocation_cap_s=None,
        session_kwargs=None,
    ):
        self.calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "timeout_s": timeout_s,
                "shell_invocation_cap_s": shell_invocation_cap_s,
                "session_kwargs": session_kwargs,
            }
        )
        return self._results.pop(0)


def test_success_maps_result_fields() -> None:
    turn = TurnResult(
        session_id="s1",
        outcome=BackendOutcome.SUCCESS,
        events=[
            _msg("hello", [SimpleNamespace(name="view", arguments={})]),
            _usage(output_tokens=7, cost=1.0),
        ],
        wall_clock_s=1.5,
        billing=BillingValue.complete(1_500_000_000.0, 1.25),
    )
    run_fn = make_sdk_run_agent(_FakeBridge([turn]), _agents("a"))
    result = run_fn(agent="a", prompt="p", session_id="s1")
    assert result.final_content == "hello"
    assert result.tools_used == ["view"]
    assert result.rounds == 1
    assert result.exit_code == 0
    assert result.ok
    assert result.token_usage.output_tokens == 7
    assert result.token_usage.billing == BillingValue.complete(1_500_000_000.0, 1.25)
    assert result.session_id == "s1"
    assert result.events == [
        {
            "type": "assistant.message",
            "sensitiveValues": [{"field": "content", "classification": "high", "retained": False}],
        },
        {"type": "assistant.usage"},
    ]


def test_generates_session_id_when_absent() -> None:
    turn = TurnResult(session_id="unused", outcome=BackendOutcome.SUCCESS, events=[_msg("x")])
    bridge = _FakeBridge([turn])
    run_fn = make_sdk_run_agent(bridge, _agents("a"))
    run_fn(agent="a", prompt="p")
    generated = bridge.calls[0]["session_id"]
    assert generated and generated != "unused"


def test_passes_session_id_through() -> None:
    turn = TurnResult(session_id="s9", outcome=BackendOutcome.SUCCESS, events=[_msg("x")])
    bridge = _FakeBridge([turn])
    make_sdk_run_agent(bridge, _agents("a"))(agent="a", prompt="p", session_id="s9")
    assert bridge.calls[0]["session_id"] == "s9"


def test_timeout_maps_to_none_exit_and_not_ok() -> None:
    turn = TurnResult(
        session_id="s1",
        outcome=BackendOutcome.TIMEOUT,
        timed_out=True,
        timeout_phase="background_tool_wait",
        timeout_snapshot=[{"name": "read_powershell", "shellId": "s", "activeAtTimeout": True}],
        error_message="background_tool_wait timed out",
    )
    result = make_sdk_run_agent(_FakeBridge([turn]), _agents("a"))(agent="a", prompt="p")
    assert result.exit_code is None
    assert result.timed_out
    assert result.timeout_phase == "background_tool_wait"
    assert result.timeout_snapshot[0]["shellId"] == "s"
    assert not result.ok


def test_failure_message_lands_in_raw_stderr() -> None:
    turn = TurnResult(
        session_id="s1",
        outcome=BackendOutcome.MODEL_UNAVAILABLE,
        events=[],
        error_message='Model "x" is not available.',
    )
    result = make_sdk_run_agent(_FakeBridge([turn]), _agents("a"))(agent="a", prompt="p")
    assert result.exit_code == 1
    assert not result.ok
    assert "not available" in result.raw_stderr


def test_config_map_wired_into_session_kwargs() -> None:
    turn = TurnResult(session_id="s1", outcome=BackendOutcome.SUCCESS, events=[_msg("x")])
    bridge = _FakeBridge([turn])
    make_sdk_run_agent(bridge, _agents("security-review"))(
        agent="security-review",
        prompt="p",
        cwd="/repo",
    )
    kwargs = bridge.calls[0]["session_kwargs"]
    assert kwargs == {
        "agent": "security-review",
        "custom_agents": [
            {
                "name": "security-review",
                "prompt": "security-review prompt",
                "infer": False,
                "display_name": "security-review display",
                "description": "security-review desc",
                "tools": ["read"],
                "mcp_servers": {"ado": {"command": "x"}},
            }
        ],
        "model": "model-a",
        "working_directory": "/repo",
        "enable_config_discovery": False,
        "skip_custom_instructions": True,
        "enable_skills": False,
        "custom_agents_local_only": True,
    }


def test_declared_policy_gets_shell_safety_independent_of_agent_name() -> None:
    turn = TurnResult(session_id="s1", outcome=BackendOutcome.SUCCESS, events=[_msg("x")])
    bridge = _FakeBridge([turn])
    policy = ToolPolicy(
        powershell=PowershellToolPolicy(120, False),
        read_powershell=ReadPowershellToolPolicy(30),
    )
    backend = sdk_runner.SdkBackend(bridge, _agents("arbitrary", tool_policy=policy))
    result = backend.run(RunRequest(agent="arbitrary", prompt="p", timeout_s=720))
    hooks = bridge.calls[0]["session_kwargs"]["hooks"]
    assert callable(hooks["on_pre_tool_use"])
    assert bridge.calls[0]["shell_invocation_cap_s"] == 120
    hook = bridge.calls[0]["session_kwargs"]["hooks"]["on_pre_tool_use"]
    assert (
        hook(
            {"toolName": "powershell", "toolArgs": {"command": "pytest", "initial_wait": 120}},
            {},
        )
        is None
    )
    assert result.execution_policy is not None
    assert result.execution_policy.to_dict()["shellInvocationCapSeconds"] == 120


def test_policy_free_agent_gets_no_hooks_or_shell_watchdog() -> None:
    turn = TurnResult(session_id="s1", outcome=BackendOutcome.SUCCESS, events=[_msg("x")])
    bridge = _FakeBridge([turn])
    result = sdk_runner.SdkBackend(bridge, _agents("redgreen")).run(
        RunRequest(agent="redgreen", prompt="p")
    )
    assert "hooks" not in bridge.calls[0]["session_kwargs"]
    assert bridge.calls[0]["shell_invocation_cap_s"] is None
    assert result.execution_policy is None


def test_sdk_backend_quarantines_rejected_submission_assistant_content() -> None:
    raw_candidate = '{"secret":"unvalidated SDK candidate"}'

    class RejectingBridge:
        def run_turn(self, session_id, _prompt, *, submission, **_kwargs):
            submission.submit({"secret": "unvalidated SDK candidate"})
            return TurnResult(
                session_id=session_id,
                outcome=BackendOutcome.SUCCESS,
                events=[_msg(raw_candidate)],
            )

    submission = OutputSubmission(
        {"type": "object"},
        lambda _value, _calls: SubmissionValidation(
            False,
            diagnostics=(
                {
                    "gate": "json_schema",
                    "path": "secret",
                    "keyword": "const",
                    "message": "must not be submitted",
                },
            ),
        ),
    )

    result = sdk_runner.SdkBackend(RejectingBridge(), _agents("a")).run(
        RunRequest(agent="a", prompt="p", submission=submission)
    )

    assert result.submission is not None and result.submission.status == "rejected"
    assert result.final_content == ""
    assert result.raw_stdout == raw_candidate


def test_unregistered_agent_raises() -> None:
    turn = TurnResult(session_id="s1", outcome=BackendOutcome.SUCCESS, events=[_msg("x")])
    run_fn = make_sdk_run_agent(_FakeBridge([turn]), _agents("known"))
    with pytest.raises(KeyError, match="unknown"):
        run_fn(agent="unknown", prompt="p")


def test_sdk_billing_snapshot_stays_session_cumulative_across_resumed_turns() -> None:
    turns = [
        TurnResult(
            session_id="s1",
            outcome=BackendOutcome.SUCCESS,
            events=[_msg("a"), _usage(cost=100.0)],
            billing=BillingValue.complete(1_000_000_000.0, 1.0),
        ),
        TurnResult(
            session_id="s1",
            outcome=BackendOutcome.SUCCESS,
            events=[_msg("b"), _usage(cost=200.0)],
            billing=BillingValue.complete(2_000_000_000.0, 2.0),
        ),
    ]
    run_fn = make_sdk_run_agent(_FakeBridge(turns), _agents("a"))
    first = run_fn(agent="a", prompt="p", session_id="s1")
    second = run_fn(agent="a", prompt="feedback", session_id="s1")
    assert first.token_usage.billing.total_nano_aiu == 1_000_000_000.0
    assert second.token_usage.billing.total_nano_aiu == 2_000_000_000.0


def test_sdk_backend_starts_then_closes(monkeypatch):
    order: list[str] = []
    captured: dict[str, object] = {}

    class _FakeLifecycleBridge:
        def __init__(self, auth, **kwargs):
            order.append("init")
            self.kwargs = kwargs
            captured["base_directory"] = kwargs.get("base_directory")

        def start(self, **_kw):
            order.append("start")

        def close(self):
            order.append("close")

    monkeypatch.setattr(sdk_runner, "resolve_auth", lambda **_kw: "AUTH")
    monkeypatch.setattr(sdk_runner, "SdkBridge", _FakeLifecycleBridge)
    monkeypatch.setattr(sdk_runner, "graph_custom_agents", lambda _root, _entries: {})

    with sdk_backend(
        BackendOptions(get_configuration(resolve_bundle("inspectorx")), cwd="/repo")
    ) as backend:
        assert hasattr(backend, "run")
        # F2: the client runs against an ephemeral base_directory (COPILOT_HOME) that
        # exists for the duration of the review.
        base_dir = captured["base_directory"]
        assert base_dir is not None
        assert Path(base_dir).is_dir()
        order.append("body")

    assert order == ["init", "start", "body", "close"]
    # F2: the ephemeral base_directory is removed on exit (after the bridge closed).
    assert not Path(captured["base_directory"]).exists()


def test_sdk_backend_closes_on_body_error(monkeypatch):
    order: list[str] = []

    class _FakeLifecycleBridge:
        def __init__(self, auth, **kwargs):
            pass

        def start(self, **_kw):
            order.append("start")

        def close(self):
            order.append("close")

    monkeypatch.setattr(sdk_runner, "resolve_auth", lambda **_kw: "AUTH")
    monkeypatch.setattr(sdk_runner, "SdkBridge", _FakeLifecycleBridge)
    monkeypatch.setattr(sdk_runner, "graph_custom_agents", lambda _root, _entries: {})

    with (
        contextlib.suppress(RuntimeError),
        sdk_backend(BackendOptions(get_configuration(resolve_bundle("inspectorx")), cwd="/repo")),
    ):
        raise RuntimeError("boom")

    assert order == ["start", "close"]  # close still runs on error
