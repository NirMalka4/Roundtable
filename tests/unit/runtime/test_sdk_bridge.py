"""Unit tests for SdkBridge using a fake async client (no live SDK calls)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from roundtable.backend import BillingStatus, OutputSubmission, SubmissionValidation
from roundtable.backend.sdk.auth import AuthConfig, AuthMode
from roundtable.backend.sdk.bridge import SdkBridge, SdkStartError
from roundtable.backend.sdk.errors import BackendOutcome
from tests.support.sdk_fakes import FakeClient, FakeSession, bridge_for, record_kwargs


def _bridge(client: FakeClient) -> SdkBridge:
    return bridge_for(client)


@pytest.fixture
def started():
    holder: dict[str, Any] = {}

    def make(client: FakeClient) -> SdkBridge:
        bridge = _bridge(client)
        bridge.start()
        holder["bridge"] = bridge
        return bridge

    yield make
    b = holder.get("bridge")
    if b is not None:
        b.close()


def test_start_passes_auth_kwargs(started: Callable[[FakeClient], SdkBridge]) -> None:
    client = FakeClient()
    started(client)
    assert client.started is True
    assert client.kwargs["github_token"] == "tok"
    assert client.kwargs["use_logged_in_user"] is False


def test_first_turn_creates_then_retry_resumes(started: Callable[[FakeClient], SdkBridge]) -> None:
    client = FakeClient(session_factory=lambda on: FakeSession(on, terminal="done"))
    bridge = started(client)

    r1 = bridge.run_turn("sid-1", "hello", timeout_s=5)
    r2 = bridge.run_turn("sid-1", "feedback", timeout_s=5)

    assert client.created == ["sid-1"]
    assert client.resumed == ["sid-1"]
    assert r1.outcome is BackendOutcome.SUCCESS
    assert r2.outcome is BackendOutcome.SUCCESS
    assert r1.terminal_event == "done"


def test_submission_tool_and_handler_are_registered_on_create_and_resume(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    client = FakeClient(session_factory=lambda on: FakeSession(on, terminal="done"))
    bridge = started(client)
    submission = OutputSubmission(
        {"type": "object", "properties": {"output": {}}},
        lambda _value, _calls: SubmissionValidation(True),
    )
    bridge.run_turn("sid-1", "hello", timeout_s=5, submission=submission)
    created_tool = client.last_open_kw["tools"][0]
    bridge.run_turn("sid-1", "feedback", timeout_s=5, submission=submission)
    resumed_tool = client.last_open_kw["tools"][0]
    assert created_tool.name == resumed_tool.name == "roundtable_submit_output"
    assert callable(created_tool.handler) and callable(resumed_tool.handler)


def test_submission_validation_receives_ephemeral_read_output(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    request = SimpleNamespace(name="view", arguments={"path": "src/a.py"}, tool_call_id="c1")
    events = [
        SimpleNamespace(
            type="assistant.message",
            data=SimpleNamespace(content="", tool_requests=[request]),
        ),
        SimpleNamespace(
            type="tool.execution_start",
            data=SimpleNamespace(
                tool_call_id="c1",
                tool_name="view",
                arguments={"path": "src/a.py"},
            ),
        ),
        SimpleNamespace(
            type="tool.execution_complete",
            data=SimpleNamespace(
                tool_call_id="c1",
                success=True,
                error=None,
                result=SimpleNamespace(
                    contents=[SimpleNamespace(type="text", text="exact source excerpt")],
                    content="",
                ),
            ),
        ),
    ]
    captured: dict[str, Any] = {}

    def validate(_value: Any, calls: list[dict[str, Any]]) -> SubmissionValidation:
        captured["calls"] = calls
        return SubmissionValidation(True)

    client = FakeClient(session_factory=lambda on: FakeSession(on, emit=events, terminal="done"))
    bridge = started(client)
    submission = OutputSubmission(
        {"type": "object"},
        validate,
        tool_output_names=frozenset({"view"}),
    )
    bridge.run_turn("sid-1", "hello", timeout_s=5, submission=submission)
    tool = client.last_open_kw["tools"][0]
    asyncio.run(tool.handler(SimpleNamespace(arguments={"output": {}})))

    assert captured["calls"][0]["output"] == "exact source excerpt"


def test_permission_handler_wired_into_create_and_resume(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    sentinel = object()
    client = FakeClient(session_factory=lambda on: FakeSession(on, terminal="done"))
    bridge = SdkBridge(
        AuthConfig(AuthMode.TOKEN, "explicit", "tok"),
        client_factory=lambda **kw: record_kwargs(client, kw),
        permission_handler=sentinel,
    )
    bridge.start()
    try:
        bridge.run_turn("sid-1", "hello", timeout_s=5)
        assert client.last_open_kw["on_permission_request"] is sentinel
        bridge.run_turn("sid-1", "feedback", timeout_s=5)
        assert client.last_open_kw["on_permission_request"] is sentinel
    finally:
        bridge.close()


def test_success_disconnects_without_abort(started: Callable[[FakeClient], SdkBridge]) -> None:
    metrics = SimpleNamespace(
        total_nano_aiu=1_250_000_000.0,
        total_premium_request_cost=1.75,
    )
    client = FakeClient(session_factory=lambda on: FakeSession(on, terminal="ok", metrics=metrics))
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5)
    session = client.sessions[0]
    assert session.lifecycle == ["send", "metrics", "disconnect"]
    assert session.disconnected is True
    assert session.aborted is False
    assert result.billing.total_nano_aiu == 1_250_000_000.0
    assert result.billing.total_premium_request_cost == 1.75


def test_session_error_event_is_classified(started: Callable[[FakeClient], SdkBridge]) -> None:
    err_event = SimpleNamespace(type="session.error", data=SimpleNamespace(message="HTTP 429"))
    client = FakeClient(session_factory=lambda on: FakeSession(on, emit=[err_event], terminal="x"))
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5)
    assert result.outcome is BackendOutcome.RATE_LIMITED
    assert result.error_message == "HTTP 429"
    assert result.events == [err_event]


def test_send_timeout_triggers_abort_and_disconnect(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    client = FakeClient(
        session_factory=lambda on: FakeSession(on, raise_on_send=TimeoutError("slow"))
    )
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5)
    assert result.outcome is BackendOutcome.TIMEOUT
    assert result.timed_out is True
    assert result.timeout_phase == "model_wait"
    assert result.timeout_snapshot == []
    session = client.sessions[0]
    assert session.lifecycle == ["send", "abort", "metrics", "disconnect"]
    assert session.aborted is True
    assert session.disconnected is True
    assert result.billing.status is BillingStatus.COMPLETE


def test_billing_rpc_failure_is_advisory(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    client = FakeClient(
        session_factory=lambda on: FakeSession(
            on,
            terminal="ok",
            metrics_error=RuntimeError("sensitive SDK detail"),
        )
    )
    result = started(client).run_turn("s", "p", timeout_s=5)
    assert result.outcome is BackendOutcome.SUCCESS
    assert result.error_message is None
    assert result.billing.status is BillingStatus.UNAVAILABLE
    assert result.billing.warning_code == "sdk_usage_metrics_unavailable"
    assert "sensitive SDK detail" not in str(result.billing.to_dict())


def test_billing_rpc_is_bounded(
    started: Callable[[FakeClient], SdkBridge],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roundtable.backend.sdk.bridge as bridge_module

    class HangingUsage:
        async def get_metrics(self, *, timeout: float | None = None) -> Any:
            await asyncio.sleep(3600)

    class HangingMetricsSession(FakeSession):
        def __init__(self, on_event: Callable[[Any], None]) -> None:
            super().__init__(on_event, terminal="ok")
            self.rpc = SimpleNamespace(usage=HangingUsage())

    monkeypatch.setattr(bridge_module, "_BILLING_TIMEOUT_S", 0.01)
    client = FakeClient(session_factory=HangingMetricsSession)
    result = started(client).run_turn("s", "p", timeout_s=5)
    assert result.outcome is BackendOutcome.SUCCESS
    assert result.billing.status is BillingStatus.UNAVAILABLE
    assert client.sessions[0].disconnected is True


def test_send_timeout_snapshots_running_powershell_before_cleanup(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    tool_start = SimpleNamespace(
        type="tool.execution_start",
        timestamp=datetime(2026, 8, 11, tzinfo=UTC),
        data=SimpleNamespace(
            tool_call_id="c1",
            tool_name="powershell",
            arguments={"command": "pytest --token secret"},
        ),
    )
    client = FakeClient(
        session_factory=lambda on: FakeSession(
            on, emit=[tool_start], raise_on_send=TimeoutError("slow")
        )
    )
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5)
    assert result.timeout_phase == "tool_running"
    assert result.timeout_snapshot == [
        {
            "name": "powershell",
            "startedAt": "2026-08-11T00:00:00Z",
            "state": "running",
            "activeAtTimeout": True,
        }
    ]
    assert "secret" not in str(result.timeout_snapshot)


def test_tool_deadline_aborts_active_shell_before_agent_timeout(
    started: Callable[[FakeClient], SdkBridge],
) -> None:
    class HangingToolSession(FakeSession):
        async def send_and_wait(self, prompt: str, *, timeout: float) -> Any:
            self._on_event(
                SimpleNamespace(
                    type="tool.execution_start",
                    timestamp=datetime.now(UTC),
                    data=SimpleNamespace(
                        tool_call_id="c1",
                        tool_name="powershell",
                        arguments={"command": "pytest tests/unit/test_x.py"},
                    ),
                )
            )
            await asyncio.sleep(3600)

        async def abort(self) -> None:
            await super().abort()
            self._on_event(
                SimpleNamespace(
                    type="tool.execution_complete",
                    timestamp=datetime.now(UTC),
                    data=SimpleNamespace(
                        tool_call_id="c1",
                        success=False,
                        error=SimpleNamespace(message="aborted"),
                        result=SimpleNamespace(contents=[], content=""),
                    ),
                )
            )

    client = FakeClient(session_factory=HangingToolSession)
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5, shell_invocation_cap_s=0.05)
    assert result.timed_out is True
    assert result.timeout_phase == "tool_running"
    assert result.wall_clock_s < 1
    assert result.timeout_snapshot[0]["name"] == "powershell"
    assert client.sessions[0].aborted is True
    assert client.sessions[0].disconnected is True


def test_cleanup_timeout_is_reported_as_cleanup_phase(
    started: Callable[[FakeClient], SdkBridge], monkeypatch
) -> None:
    import roundtable.backend.sdk.bridge as bridge_module

    class SlowAbortSession(FakeSession):
        async def abort(self) -> None:
            await asyncio.sleep(1)

    monkeypatch.setattr(bridge_module, "_CLEANUP_TIMEOUT_S", 0.01)
    client = FakeClient(
        session_factory=lambda on: SlowAbortSession(on, raise_on_send=TimeoutError("slow"))
    )
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5)
    assert result.timeout_phase == "cleanup"
    assert result.error_message == "cleanup timed out"
    assert result.billing.status is BillingStatus.UNAVAILABLE


def test_open_failure_is_classified(started: Callable[[FakeClient], SdkBridge]) -> None:
    client = FakeClient(open_error=ConnectionError("connection refused"))
    bridge = started(client)
    result = bridge.run_turn("s", "p", timeout_s=5)
    assert result.outcome is BackendOutcome.TRANSPORT
    assert result.billing.status is BillingStatus.NOT_APPLICABLE
    assert "connection refused" in (result.error_message or "")
    # A failed open must NOT mark the id known (so a later attempt still creates).
    assert "s" not in bridge._known_sessions


def test_start_failure_raises_and_tears_down() -> None:
    client = FakeClient(start_error=RuntimeError("protocol version mismatch"))
    bridge = _bridge(client)
    with pytest.raises(SdkStartError) as exc:
        bridge.start()
    assert exc.value.outcome is BackendOutcome.PROTOCOL_MISMATCH
    assert client.stopped is True
    assert bridge._loop.running is False


def test_run_turn_before_start_raises() -> None:
    bridge = _bridge(FakeClient())
    with pytest.raises(RuntimeError, match="before start"):
        bridge.run_turn("s", "p", timeout_s=5)
