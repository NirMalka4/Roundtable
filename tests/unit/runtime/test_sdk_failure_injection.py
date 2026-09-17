"""Failure-mode injection against the SDK adapter (bridge + runner).

Complements the happy-path bridge tests by driving each failure the review-scoped
adapter must survive: a wedged turn that outlives its own timeout (guard), a
mid-turn transport death, cancellation, start-time auth/transport handshake
failures, structured model-error surfacing, and blast-radius containment (one
bad turn must not wedge the shared bridge). All offline — no live SDK, no premium.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

import pytest

from roundtable.backend.sdk import bridge as bridge_mod
from roundtable.backend.sdk.bridge import SdkBridge, SdkStartError
from roundtable.backend.sdk.errors import BackendOutcome
from roundtable.backend.sdk_runner import make_sdk_run_agent
from roundtable.runtime.agent_setup import NativeAgentFields
from tests.support.sdk_fakes import FakeClient, FakeSession, bridge_for


class _WedgedSession:
    """A session whose turn never completes and ignores its own timeout."""

    def __init__(self, on_event: Any) -> None:
        self.aborted = False
        self.disconnected = False

    async def send_and_wait(self, prompt: str, *, timeout: float) -> Any:
        await asyncio.sleep(3600)  # its own timeout never fires -> the guard must

    async def abort(self) -> None:
        self.aborted = True

    async def disconnect(self) -> None:
        self.disconnected = True


def _agents(*keys: str) -> dict[str, NativeAgentFields]:
    return {
        k: NativeAgentFields(name=k, description=f"{k} desc", prompt=f"{k} prompt") for k in keys
    }


def _started_bridge(client: FakeClient) -> SdkBridge:
    bridge = bridge_for(client)
    bridge.start()
    return bridge


# -- wedged turn: the outer guard fires when the inner timeout doesn't ---------


def test_guard_timeout_reports_ambiguous_timeout_and_marks_known(monkeypatch) -> None:
    monkeypatch.setattr(bridge_mod, "_GUARD_MARGIN_S", 0.05)
    client = FakeClient(session_factory=_WedgedSession)
    bridge = _started_bridge(client)
    try:
        result = bridge.run_turn("s", "p", timeout_s=0.05)
        assert result.outcome is BackendOutcome.TIMEOUT
        assert result.timed_out is True
        assert result.timeout_phase == "cleanup"
        # F7: the guard path records the real wall-clock spent waiting, not 0.
        assert result.wall_clock_s > 0
        # The turn's fate is unknown, so the id is treated as created (resume next).
        assert "s" in bridge._known_sessions
    finally:
        bridge.close()


# -- F6: a client constructor failure must tear down the already-started loop --


def test_client_build_failure_tears_down_loop_thread() -> None:
    from roundtable.backend.sdk.auth import AuthConfig, AuthMode

    def _boom_factory(**_kw):
        raise RuntimeError("connection refused")

    bridge = SdkBridge(AuthConfig(AuthMode.TOKEN, "explicit", "tok"), client_factory=_boom_factory)
    with pytest.raises(SdkStartError) as exc:
        bridge.start()
    assert exc.value.outcome is BackendOutcome.TRANSPORT
    # The loop thread started before the factory raised; start() must close it.
    assert bridge._loop.running is False


# -- mid-turn transport death (shared server dies while a turn is in flight) ---


def test_midturn_transport_death_aborts_and_classifies() -> None:
    client = FakeClient(
        session_factory=lambda on: FakeSession(
            on, raise_on_send=ConnectionError("server disconnected")
        )
    )
    bridge = _started_bridge(client)
    try:
        result = bridge.run_turn("s", "p", timeout_s=5)
        assert result.outcome is BackendOutcome.TRANSPORT
        assert "server disconnected" in (result.error_message or "")
        session = client.sessions[0]
        assert session.aborted is True
        assert session.disconnected is True
        # The session opened before it died, so the id is known (unlike an open failure).
        assert "s" in bridge._known_sessions
    finally:
        bridge.close()


# -- cancellation is a control-flow signal: clean up, then re-raise -----------


def test_cancelled_during_send_cleans_up_then_reraises() -> None:
    client = FakeClient(
        session_factory=lambda on: FakeSession(on, raise_on_send=asyncio.CancelledError())
    )
    bridge = _started_bridge(client)
    try:
        # Cancellation is a control-flow signal: the bridge re-raises it (never maps
        # it to a fake result). It surfaces through the loop future as the
        # concurrent-futures variant, which is distinct from asyncio.CancelledError.
        with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
            bridge.run_turn("s", "p", timeout_s=5)
        session = client.sessions[0]
        assert session.aborted is True
        assert session.disconnected is True
    finally:
        bridge.close()


# -- start-time handshake failures carry the right outcome + tear down --------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP 401 Unauthorized", BackendOutcome.AUTH),
        ("connection refused", BackendOutcome.TRANSPORT),
        ("protocol version mismatch", BackendOutcome.PROTOCOL_MISMATCH),
    ],
)
def test_start_failure_outcomes_and_teardown(message: str, expected: BackendOutcome) -> None:
    client = FakeClient(start_error=RuntimeError(message))
    bridge = bridge_for(client)
    with pytest.raises(SdkStartError) as exc:
        bridge.start()
    assert exc.value.outcome is expected
    assert client.stopped is True
    assert bridge._loop.running is False


# -- end-to-end: a model-level session error retains its structured outcome ----


def test_model_error_event_surfaces_outcome_end_to_end() -> None:
    err = _session_error('Model "x" is not available.')
    client = FakeClient(session_factory=lambda on: FakeSession(on, emit=[err], terminal="x"))
    bridge = _started_bridge(client)
    try:
        run_fn = make_sdk_run_agent(bridge, _agents("a"))
        result = run_fn(agent="a", prompt="p", session_id="s1")
        assert result.exit_code == 1
        assert not result.ok
        assert result.backend_outcome is BackendOutcome.MODEL_UNAVAILABLE
    finally:
        bridge.close()


# -- blast radius: one failed turn must not wedge the review-scoped bridge -----


def test_shared_bridge_survives_a_failed_turn() -> None:
    calls = {"n": 0}

    def factory(on: Any) -> FakeSession:
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeSession(on, raise_on_send=ConnectionError("server disconnected"))
        return FakeSession(on, emit=[_message("recovered")], terminal="ok")

    client = FakeClient(session_factory=factory)
    bridge = _started_bridge(client)
    try:
        run_fn = make_sdk_run_agent(bridge, _agents("a"))
        bad = run_fn(agent="a", prompt="p", session_id="bad")
        good = run_fn(agent="a", prompt="p", session_id="good")
        assert not bad.ok
        assert good.ok
        assert good.final_content == "recovered"
        assert client.created == ["bad", "good"]
    finally:
        bridge.close()


def _session_error(message: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(type="session.error", data=SimpleNamespace(message=message))


def _message(content: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        type="assistant.message",
        data=SimpleNamespace(content=content, tool_requests=None),
    )
