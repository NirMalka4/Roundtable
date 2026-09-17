"""Unit tests for the EventLoopThread primitive (pure coroutines, no SDK)."""

from __future__ import annotations

import asyncio
import threading

import pytest

from roundtable.backend.sdk.event_loop_thread import EventLoopThread


@pytest.fixture
def loop_thread():
    lt = EventLoopThread()
    lt.start()
    yield lt
    lt.close()


def test_runs_coroutine_and_returns_result(loop_thread: EventLoopThread) -> None:
    async def add() -> int:
        await asyncio.sleep(0)
        return 3 + 4

    assert loop_thread.run(add()) == 7


def test_running_flag(loop_thread: EventLoopThread) -> None:
    assert loop_thread.running is True


def test_timeout_cancels_pending_coroutine(loop_thread: EventLoopThread) -> None:
    cancelled = threading.Event()

    async def hang() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(TimeoutError):
        loop_thread.run(hang(), timeout=0.1)
    assert cancelled.wait(timeout=1.0)


def test_close_is_idempotent() -> None:
    lt = EventLoopThread()
    lt.start()
    lt.close()
    lt.close()  # must not raise
    assert lt.running is False


def test_start_then_run_multiple(loop_thread: EventLoopThread) -> None:
    async def echo(x: int) -> int:
        return x

    assert [loop_thread.run(echo(i)) for i in range(5)] == [0, 1, 2, 3, 4]
