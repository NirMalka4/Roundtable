"""A background asyncio event loop on a dedicated daemon thread.

The pipeline drives agents from a synchronous ``ThreadPoolExecutor``, but the SDK
is async and a resumable ``CopilotSession`` must live on one stable loop. This
primitive owns that loop on its own thread and bridges sync callers to it via
``run_coroutine_threadsafe``. It is deliberately tiny and SDK-agnostic so it can
be unit-tested with plain coroutines.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


class EventLoopThread:
    """Own an asyncio loop on a daemon thread; run coroutines on it synchronously."""

    def __init__(self, name: str = "sdk-event-loop") -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._ready = threading.Event()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def start(self, *, timeout: float = 10.0) -> None:
        """Start the thread and block until the loop is running."""
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("event loop thread did not start in time")

    def run(self, coro: Coroutine[Any, Any, _T], *, timeout: float | None = None) -> _T:
        """Run ``coro`` on the loop and block for its result.

        On ``timeout`` the pending coroutine is cancelled on the loop before the
        :class:`TimeoutError` propagates, so nothing is left running.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and self._loop.is_running()

    def close(self, *, timeout: float = 10.0) -> None:
        """Stop the loop and join the thread. Idempotent."""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)
