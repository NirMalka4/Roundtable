"""Shared async test doubles for the SDK bridge/runner (no live SDK calls).

A :class:`FakeClient` stands in for ``CopilotClient`` and hands out
:class:`FakeSession` instances whose behaviour (emitted events, terminal value,
raised exception, start/open failure) is injected per test — so bridge and
failure-injection suites drive the same seams without duplicating doubles.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from roundtable.backend.sdk.auth import AuthConfig, AuthMode
from roundtable.backend.sdk.bridge import SdkBridge


class FakeSession:
    def __init__(
        self,
        on_event: Callable[[Any], None],
        *,
        emit: list[Any] | None = None,
        terminal: Any = None,
        raise_on_send: BaseException | None = None,
        metrics: Any | None = None,
        metrics_error: BaseException | None = None,
    ) -> None:
        self._on_event = on_event
        self._emit = emit or []
        self._terminal = terminal
        self._raise = raise_on_send
        self._metrics = metrics or SimpleNamespace(
            total_nano_aiu=0.0,
            total_premium_request_cost=0.0,
        )
        self._metrics_error = metrics_error
        self.aborted = False
        self.disconnected = False
        self.lifecycle: list[str] = []
        self.rpc = SimpleNamespace(usage=_FakeUsageApi(self))

    async def send_and_wait(self, prompt: str, *, timeout: float) -> Any:
        self.lifecycle.append("send")
        for ev in self._emit:
            self._on_event(ev)
        if self._raise is not None:
            raise self._raise
        return self._terminal

    async def abort(self) -> None:
        self.lifecycle.append("abort")
        self.aborted = True

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")
        self.disconnected = True


class _FakeUsageApi:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def get_metrics(self, *, timeout: float | None = None) -> Any:
        self._session.lifecycle.append("metrics")
        if self._session._metrics_error is not None:
            raise self._session._metrics_error
        return self._session._metrics


class FakeClient:
    def __init__(
        self,
        *,
        session_factory: Callable[[Callable[[Any], None]], FakeSession] | None = None,
        start_error: BaseException | None = None,
        open_error: BaseException | None = None,
        models: list[str] | None = None,
        list_models_error: BaseException | None = None,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs
        self._session_factory = session_factory or (lambda on_event: FakeSession(on_event))
        self._start_error = start_error
        self._open_error = open_error
        self._models = models or []
        self._list_models_error = list_models_error
        self.started = False
        self.stopped = False
        self.lifecycle: list[str] = []
        self.created: list[str] = []
        self.resumed: list[str] = []
        self.sessions: list[FakeSession] = []

    async def start(self) -> None:
        self.lifecycle.append("start")
        if self._start_error is not None:
            raise self._start_error
        self.started = True

    async def stop(self) -> None:
        self.lifecycle.append("stop")
        self.stopped = True

    async def list_models(self) -> list[Any]:
        self.lifecycle.append("list_models")
        if self._list_models_error is not None:
            raise self._list_models_error
        return [SimpleNamespace(id=model) for model in self._models]

    def _open(self, on_event: Callable[[Any], None]) -> FakeSession:
        if self._open_error is not None:
            raise self._open_error
        session = self._session_factory(on_event)
        self.sessions.append(session)
        return session

    async def create_session(
        self,
        *,
        session_id: str,
        enable_session_store: bool,
        on_event: Callable[[Any], None],
        **kw: Any,
    ) -> FakeSession:
        self.created.append(session_id)
        self.last_open_kw = kw
        assert enable_session_store is True
        return self._open(on_event)

    async def resume_session(
        self, session_id: str, *, on_event: Callable[[Any], None], **kw: Any
    ) -> FakeSession:
        self.resumed.append(session_id)
        self.last_open_kw = kw
        return self._open(on_event)


def record_kwargs(client: FakeClient, kw: dict[str, Any]) -> FakeClient:
    client.kwargs = kw
    return client


def bridge_for(client: FakeClient, **bridge_kwargs: Any) -> SdkBridge:
    """Build an :class:`SdkBridge` whose client factory returns ``client``."""
    return SdkBridge(
        AuthConfig(AuthMode.TOKEN, "explicit", "tok"),
        client_factory=lambda **kw: record_kwargs(client, kw),
        **bridge_kwargs,
    )
