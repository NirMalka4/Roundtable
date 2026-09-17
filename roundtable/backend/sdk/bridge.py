"""Review-scoped SDK client bridge (Phase 0 foundation).

One :class:`SdkBridge` per review owns a single ``CopilotClient`` running on a
dedicated :class:`EventLoopThread`, and exposes a **synchronous** ``run_turn`` the
existing ``ThreadPoolExecutor``-based scheduler can call from worker threads.

Session reuse (resume-by-id): every turn is addressed by a caller-supplied
``session_id``. The first turn for an id creates a session with the SDK session
store enabled; a later schema-submission retry *resumes* that id so the agent sees
the rejection and prior conversation. The bridge holds no live session between
turns — it tracks only the *set of ids it has created*, so a dropped/idle session
simply resumes from the store.

Cancel/cleanup contract: a turn that times out (or is cancelled) is aborted and
disconnected before returning, so no in-flight turn is left running. A timeout is
reported as :attr:`BackendOutcome.TIMEOUT` and is **ambiguous** (the model turn may
have been billed) — callers must NOT blindly re-send it as if nothing happened.

This module maps SDK outcomes but does **not** build a ``CopilotResult`` — that is
the Phase-1 runner's job. ``run_turn`` returns the raw collected events + terminal
event for the mapper to consume.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from ..result import OutputSubmission
from ..usage import NOT_APPLICABLE_BILLING, BillingValue
from .auth import AuthConfig
from .compat import require_sdk
from .errors import BackendOutcome, classify_error
from .event_loop_thread import EventLoopThread
from .events import event_data, event_type
from .result_map import map_result_fields
from .submission import build_submission_tool
from .tool_observability import (
    active_tool_snapshot,
    overdue_tool_snapshot,
    timeout_phase,
)

#: Extra wall-clock headroom over a turn's own ``timeout_s`` before the bridge
#: force-cancels a wedged create/resume/cleanup on the loop.
_GUARD_MARGIN_S = 15.0
_CLEANUP_TIMEOUT_S = 10.0
_BILLING_TIMEOUT_S = 5.0

_SESSION_ERROR_EVENT = "session.error"


class _ToolDeadlineExceeded(Exception):
    def __init__(self, snapshot: list[dict[str, Any]]) -> None:
        super().__init__("tool deadline exceeded")
        self.snapshot = snapshot


@dataclass
class TurnResult:
    """Raw outcome of one SDK turn — consumed by the Phase-1 result mapper."""

    session_id: str
    outcome: BackendOutcome
    events: list[Any] = field(default_factory=list)
    terminal_event: Any | None = None
    wall_clock_s: float = 0.0
    timed_out: bool = False
    timeout_phase: str | None = None
    timeout_snapshot: list[dict[str, Any]] = field(default_factory=list)
    working_directory: str | None = None
    error_message: str | None = None
    billing: BillingValue = NOT_APPLICABLE_BILLING


class SdkBridge:
    """Review-scoped client + loop thread + session registry + lifecycle."""

    def __init__(
        self,
        auth: AuthConfig,
        *,
        working_directory: str | None = None,
        base_directory: str | None = None,
        env: dict[str, str] | None = None,
        client_factory: Callable[..., Any] | None = None,
        permission_handler: Callable[..., Any] | None = None,
    ) -> None:
        self._auth = auth
        self._working_directory = working_directory
        self._base_directory = base_directory
        self._env = env
        self._client_factory = client_factory
        self._permission_handler = permission_handler
        self._loop = EventLoopThread()
        self._client: Any | None = None
        self._known_sessions: set[str] = set()

    # -- lifecycle -----------------------------------------------------------

    def start(self, *, timeout: float = 30.0) -> None:
        """Start the loop thread and the review-scoped client (raises on failure)."""
        self._loop.start()
        try:
            client = self._build_client()
            self._client = client
            self._loop.run(client.start(), timeout=timeout)
        except Exception as exc:  # protocol/auth/transport failure at handshake
            outcome, message = classify_error(exc)
            self.close()
            raise SdkStartError(outcome, message) from exc

    def close(self) -> None:
        """Stop the client and the loop thread. Idempotent, best-effort."""
        client = self._client
        if client is not None and self._loop.running:
            with suppress(Exception):  # teardown is best-effort — never mask the real error
                self._loop.run(client.stop(), timeout=10.0)
        self._client = None
        self._loop.close()

    def _build_client(self) -> Any:
        kwargs: dict[str, Any] = dict(self._auth.client_kwargs())
        if self._working_directory is not None:
            kwargs["working_directory"] = self._working_directory
        if self._base_directory is not None:
            kwargs["base_directory"] = self._base_directory
        if self._env is not None:
            kwargs["env"] = self._env
        factory = self._client_factory
        if factory is None:
            factory = require_sdk().CopilotClient
        return factory(**kwargs)

    # -- per-turn ------------------------------------------------------------

    def run_turn(
        self,
        session_id: str,
        prompt: str,
        *,
        timeout_s: float,
        shell_invocation_cap_s: float | None = None,
        session_kwargs: dict[str, Any] | None = None,
        submission: OutputSubmission | None = None,
    ) -> TurnResult:
        """Run (or resume) one turn synchronously and return its raw outcome."""
        if self._client is None:
            raise RuntimeError("SdkBridge.run_turn called before start()")
        is_new = session_id not in self._known_sessions
        kwargs = session_kwargs or {}
        working_directory = kwargs.get("working_directory") or self._working_directory
        events: list[Any] = []
        coro = self._turn(
            session_id,
            prompt,
            is_new,
            timeout_s,
            shell_invocation_cap_s,
            kwargs,
            events,
            working_directory,
            submission,
        )
        started = time.monotonic()
        try:
            return self._loop.run(coro, timeout=timeout_s + _GUARD_MARGIN_S)
        except TimeoutError:
            # The guard fired: the coroutine was cancelled on the loop. We cannot
            # confirm cleanup ran, so report an ambiguous timeout.
            self._known_sessions.add(session_id)
            snapshot = active_tool_snapshot(events, working_directory)
            return TurnResult(
                session_id=session_id,
                outcome=BackendOutcome.TIMEOUT,
                events=events,
                wall_clock_s=time.monotonic() - started,
                timed_out=True,
                timeout_phase=timeout_phase(events, working_directory) if snapshot else "cleanup",
                timeout_snapshot=snapshot,
                working_directory=working_directory,
                error_message="turn exceeded guard timeout",
                billing=BillingValue.unavailable(),
            )

    async def _turn(
        self,
        session_id: str,
        prompt: str,
        is_new: bool,
        timeout_s: float,
        shell_invocation_cap_s: float | None,
        session_kwargs: dict[str, Any],
        events: list[Any],
        working_directory: str | None,
        submission: OutputSubmission | None,
    ) -> TurnResult:
        if submission is not None:
            session_kwargs = dict(session_kwargs)
            session_kwargs["tools"] = [
                build_submission_tool(
                    submission,
                    lambda: (
                        map_result_fields(
                            events,
                            default_cwd=working_directory,
                            include_tool_output=submission.tool_output_names,
                        ).tool_calls
                    ),
                )
            ]
        try:
            session = await self._open_session(session_id, is_new, events, session_kwargs)
        except Exception as exc:
            outcome, message = classify_error(exc)
            return TurnResult(
                session_id,
                outcome,
                events=events,
                working_directory=working_directory,
                error_message=message,
            )

        # An id is "known" once its session exists — future turns resume it.
        self._known_sessions.add(session_id)
        started = time.monotonic()
        send_task: asyncio.Task[Any] | None = None
        try:
            send_task = asyncio.create_task(session.send_and_wait(prompt, timeout=timeout_s))
            terminal = await self._wait_for_turn(
                send_task,
                events,
                working_directory=working_directory,
                shell_invocation_cap_s=shell_invocation_cap_s,
            )
        except _ToolDeadlineExceeded as exc:
            phase = timeout_phase(events, working_directory)
            cleanup_finished, billing = await self._cleanup_with_deadline(session)
            if send_task is not None and not send_task.done():
                send_task.cancel()
            if send_task is not None:
                with suppress(BaseException):
                    await send_task
            if not cleanup_finished:
                phase = "cleanup"
            return TurnResult(
                session_id,
                BackendOutcome.TIMEOUT,
                events=events,
                wall_clock_s=time.monotonic() - started,
                timed_out=True,
                timeout_phase=phase,
                timeout_snapshot=exc.snapshot,
                working_directory=working_directory,
                error_message=f"{phase} exceeded tool deadline",
                billing=billing,
            )
        except TimeoutError:
            snapshot = active_tool_snapshot(events, working_directory)
            phase = timeout_phase(events, working_directory)
            cleanup_finished, billing = await self._cleanup_with_deadline(session)
            if not cleanup_finished:
                phase = "cleanup"
            return TurnResult(
                session_id,
                BackendOutcome.TIMEOUT,
                events=events,
                wall_clock_s=time.monotonic() - started,
                timed_out=True,
                timeout_phase=phase,
                timeout_snapshot=snapshot,
                working_directory=working_directory,
                error_message=f"{phase} timed out",
                billing=billing,
            )
        except BaseException as exc:  # includes CancelledError — clean up then re-raise
            _, billing = await self._cleanup_with_deadline(session)
            if isinstance(exc, Exception):
                outcome, message = classify_error(exc)
                return TurnResult(
                    session_id,
                    outcome,
                    events=events,
                    wall_clock_s=time.monotonic() - started,
                    working_directory=working_directory,
                    error_message=message,
                    billing=billing,
                )
            raise

        wall = time.monotonic() - started
        billing = await self._read_billing(session)
        await self._safe_disconnect(session)
        outcome, message = self._classify_events(events)
        return TurnResult(
            session_id,
            outcome,
            events=events,
            terminal_event=terminal,
            wall_clock_s=wall,
            working_directory=working_directory,
            error_message=message,
            billing=billing,
        )

    async def _wait_for_turn(
        self,
        send_task: asyncio.Task,
        events: list[Any],
        *,
        working_directory: str | None,
        shell_invocation_cap_s: float | None,
    ) -> Any:
        if shell_invocation_cap_s is None:
            return await send_task
        poll_s = max(0.01, min(0.25, shell_invocation_cap_s / 4))
        while not send_task.done():
            await asyncio.wait({send_task}, timeout=poll_s)
            overdue = overdue_tool_snapshot(
                events,
                shell_invocation_cap_s,
                default_cwd=working_directory,
            )
            if overdue:
                raise _ToolDeadlineExceeded(overdue)
        return await send_task

    async def _open_session(
        self,
        session_id: str,
        is_new: bool,
        events: list[Any],
        session_kwargs: dict[str, Any],
    ) -> Any:
        collector = events.append
        approve = self._approve_all_handler()
        client = self._client
        if client is None:
            raise RuntimeError("SdkBridge session opened before start()")
        if is_new:
            return await client.create_session(
                session_id=session_id,
                enable_session_store=True,
                on_event=collector,
                on_permission_request=approve,
                **session_kwargs,
            )
        return await client.resume_session(
            session_id,
            on_event=collector,
            on_permission_request=approve,
            **session_kwargs,
        )

    def _approve_all_handler(self) -> Any:
        """The auto-approve permission handler (grants all tool/read prompts).

        Without it the SDK denies every tool/read permission prompt, so agents
        can't open files under ``working_directory`` (proved in the live smoke).
        The tool surface stays bounded by the custom agent's ``tools`` allowlist
        plus the injected MCP set — approval only auto-answers prompts for tools
        already permitted.
        Injectable for tests; defaults to the SDK's ``PermissionHandler.approve_all``.
        """
        return self._permission_handler or require_sdk().PermissionHandler.approve_all

    async def _cleanup(self, session: Any) -> tuple[bool, BillingValue]:
        """Abort, read bounded billing metrics, and disconnect."""
        cleanup_ok = True
        try:
            await session.abort()
        except Exception:
            cleanup_ok = False
        billing = await self._read_billing(session)
        cleanup_ok = await self._safe_disconnect(session) and cleanup_ok
        if not cleanup_ok:
            billing = BillingValue.unavailable()
        return cleanup_ok, billing

    async def _cleanup_with_deadline(self, session: Any) -> tuple[bool, BillingValue]:
        try:
            return await asyncio.wait_for(self._cleanup(session), timeout=_CLEANUP_TIMEOUT_S)
        except TimeoutError:
            return False, BillingValue.unavailable()

    @staticmethod
    async def _safe_disconnect(session: Any) -> bool:
        try:
            await session.disconnect()
        except Exception:
            return False
        return True

    @staticmethod
    async def _read_billing(session: Any) -> BillingValue:
        try:
            metrics = await asyncio.wait_for(
                session.rpc.usage.get_metrics(timeout=_BILLING_TIMEOUT_S),
                timeout=_BILLING_TIMEOUT_S,
            )
        except Exception:
            return BillingValue.unavailable()
        nano = _metric_number(getattr(metrics, "total_nano_aiu", None))
        premium = _metric_number(getattr(metrics, "total_premium_request_cost", None))
        if nano is None:
            return BillingValue.unavailable(premium)
        return BillingValue.complete(nano, premium)

    @staticmethod
    def _classify_events(events: list[Any]) -> tuple[BackendOutcome, str | None]:
        for event in events:
            if event_type(event) == _SESSION_ERROR_EVENT:
                data = event_data(event)
                message = getattr(data, "message", None) or str(data)
                return classify_error(message=message, from_session_error=True)
        return BackendOutcome.SUCCESS, None


class SdkStartError(RuntimeError):
    """The review-scoped client failed to start (protocol/auth/transport)."""

    def __init__(self, outcome: BackendOutcome, message: str) -> None:
        super().__init__(f"[{outcome.value}] {message}")
        self.outcome = outcome
        self.detail = message


def _metric_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
