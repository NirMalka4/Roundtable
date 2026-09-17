"""Packaged inventory and automatic authenticated capability resolution."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import roundtable.backend as backend
import roundtable.bundle as bundle
from roundtable.backend.sdk.auth import AuthConfig, AuthMode
from roundtable.backend.sdk.compat import SdkVersions
from roundtable.capabilities import inventory


class _ToolApi:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def get_current_metadata(self) -> Any:
        tools = [SimpleNamespace(name=name, mcp_server_name=None) for name in self._names]
        return SimpleNamespace(tools=tools)


class _ProbeSession:
    def __init__(self, names: list[str], send_error: BaseException | None = None) -> None:
        self.rpc = SimpleNamespace(tools=_ToolApi(names))
        self.send_error = send_error
        self.prompts: list[str] = []
        self.disconnected = False

    async def send_and_wait(self, prompt: str, *, timeout: float) -> None:
        self.prompts.append(prompt)
        if self.send_error is not None:
            raise self.send_error

    async def disconnect(self) -> None:
        self.disconnected = True


class _ProbeClient:
    def __init__(
        self,
        *,
        models: list[str] | None = None,
        builtin: list[str] | None = None,
        always_on: list[str] | None = None,
        list_error: BaseException | None = None,
        send_error: BaseException | None = None,
        models_barrier: threading.Barrier | None = None,
    ) -> None:
        self.models = ["gpt-5.6-sol"] if models is None else models
        self.builtin = ["view", "powershell", "sql"] if builtin is None else builtin
        self.always_on = ["sql"] if always_on is None else always_on
        self.list_error = list_error
        self.send_error = send_error
        self.models_barrier = models_barrier
        self.lifecycle: list[str] = []
        self.sessions: list[_ProbeSession] = []
        self.session_kwargs: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.lifecycle.append("start")

    async def list_models(self) -> list[Any]:
        self.lifecycle.append("list_models")
        if self.models_barrier is not None:
            self.models_barrier.wait(timeout=5)
        if self.list_error is not None:
            raise self.list_error
        return [SimpleNamespace(id=model) for model in self.models]

    async def create_session(self, **kwargs: Any) -> _ProbeSession:
        self.session_kwargs.append(kwargs)
        agent = kwargs["custom_agents"][0]
        names = self.always_on if agent.get("tools") == [] else self.builtin
        session = _ProbeSession(names, self.send_error)
        self.sessions.append(session)
        return session

    async def stop(self) -> None:
        self.lifecycle.append("stop")


def _install_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    factory,
) -> None:
    monkeypatch.setattr(bundle, "home_root", lambda: tmp_path)
    monkeypatch.setattr(inventory, "copilot_cli_version", lambda: "1.0.84-1")
    monkeypatch.setattr(
        backend,
        "sdk_versions",
        lambda: SdkVersions(package="1.0.10rc1", protocol="3"),
    )
    monkeypatch.setattr(
        backend,
        "require_sdk",
        lambda: SimpleNamespace(CopilotClient=lambda **_kwargs: factory()),
    )
    monkeypatch.setattr(
        backend,
        "resolve_auth",
        lambda: AuthConfig(AuthMode.LOGGED_IN_USER, "test"),
    )


def _cache_file(tmp_path):
    files = list((tmp_path / "capabilities" / "tools").glob("*.json"))
    assert len(files) == 1
    return files[0]


def test_the_packaged_inventory_is_a_valid_internal_baseline() -> None:
    builtin = inventory.builtin_tool_names()
    always_on = inventory.always_on_tool_names()
    assert {"view", "rg", "glob", "powershell", "web_fetch"} <= builtin
    assert not {"search", "web", "todo", "read", "execute", "agent"} & builtin
    assert always_on == {"sql"}
    assert always_on <= builtin
    assert all(inventory._TOOL_ID_RE.fullmatch(name) for name in builtin)
    assert inventory.inventory_cli_version() != "unknown"


def test_cache_miss_probes_exactly_twice_then_cache_hit_creates_no_sessions(
    monkeypatch, tmp_path
) -> None:
    clients: list[_ProbeClient] = []

    def factory() -> _ProbeClient:
        client = _ProbeClient()
        clients.append(client)
        return client

    _install_runtime(monkeypatch, tmp_path, factory)

    first = inventory.resolve_runtime_capabilities()
    second = inventory.resolve_runtime_capabilities()

    assert first.tool_source == "probe"
    assert second.tool_source == "cache"
    assert first.model_ids == second.model_ids == {"gpt-5.6-sol"}
    assert [client.lifecycle for client in clients] == [
        ["start", "list_models", "stop"],
        ["start", "list_models", "stop"],
    ]
    assert [len(client.sessions) for client in clients] == [2, 0]
    assert [session.prompts for session in clients[0].sessions] == [
        ["Reply with exactly: ok"],
        ["Reply with exactly: ok"],
    ]
    assert all(session.disconnected for session in clients[0].sessions)
    assert [kwargs["model"] for kwargs in clients[0].session_kwargs] == [
        "gpt-5.6-sol",
        "gpt-5.6-sol",
    ]


def test_probe_never_selects_astra(monkeypatch, tmp_path) -> None:
    client = _ProbeClient(models=["auto", "gpt-6-astra", "gpt-5.4"])
    _install_runtime(monkeypatch, tmp_path, lambda: client)

    inventory.resolve_runtime_capabilities()

    assert [kwargs["model"] for kwargs in client.session_kwargs] == ["gpt-5.4", "gpt-5.4"]


def test_probe_prefers_low_cost_model(monkeypatch, tmp_path) -> None:
    client = _ProbeClient(models=["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5-mini"])
    _install_runtime(monkeypatch, tmp_path, lambda: client)

    inventory.resolve_runtime_capabilities()

    assert [kwargs["model"] for kwargs in client.session_kwargs] == [
        "gpt-5.6-luna",
        "gpt-5.6-luna",
    ]


@pytest.mark.parametrize(
    "data",
    [
        "{not-json",
        json.dumps(
            {
                "schema_version": 1,
                "cli_version": "wrong",
                "sdk_package_version": "1.0.10rc1",
                "sdk_protocol_version": "3",
                "captured": "now",
                "builtin": ["view"],
                "always_on": [],
            }
        ),
        json.dumps({"schema_version": 1, "builtin": ["view"]}),
    ],
)
def test_invalid_cache_shapes_reprobe_automatically(monkeypatch, tmp_path, data: str) -> None:
    client = _ProbeClient()
    _install_runtime(monkeypatch, tmp_path, lambda: client)
    path = inventory._cache_path("1.0.84-1", "3")
    path.parent.mkdir(parents=True)
    path.write_text(data, encoding="utf-8")

    result = inventory.resolve_runtime_capabilities()

    assert result.tool_source == "probe"
    assert len(client.sessions) == 2


def test_invalid_cache_read_and_rewrite_are_both_inside_cache_lock(monkeypatch, tmp_path) -> None:
    client = _ProbeClient()
    _install_runtime(monkeypatch, tmp_path, lambda: client)
    path = inventory._cache_path("1.0.84-1", "3")
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    locked = False
    real_lock = inventory._cache_lock
    real_read = inventory._read_tool_cache

    @contextmanager
    def observed_lock(*args: Any, **kwargs: Any):
        nonlocal locked
        with real_lock(*args, **kwargs):
            locked = True
            try:
                yield
            finally:
                locked = False

    def observed_read(*args: Any, **kwargs: Any):
        assert locked
        return real_read(*args, **kwargs)

    monkeypatch.setattr(inventory, "_cache_lock", observed_lock)
    monkeypatch.setattr(inventory, "_read_tool_cache", observed_read)

    inventory.resolve_runtime_capabilities()

    assert json.loads(path.read_text(encoding="utf-8"))["builtin"] == [
        "powershell",
        "sql",
        "view",
    ]


def test_cache_replace_retries_windows_sharing_violation_until_success(
    monkeypatch, tmp_path
) -> None:
    temporary = tmp_path / "cache.tmp"
    destination = tmp_path / "cache.json"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    monkeypatch.setattr(inventory.sys, "platform", "win32")
    real_replace = inventory.os.replace
    attempts = 0

    def sharing_violation(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError("sharing violation")
            error.winerror = 32
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(inventory.os, "replace", sharing_violation)
    monkeypatch.setattr(inventory.time, "sleep", lambda _delay: None)

    inventory._replace_cache_file(temporary, destination)

    assert attempts == 3
    assert destination.read_text(encoding="utf-8") == "new"


def test_cache_replace_does_not_retry_unrelated_permission_error(monkeypatch, tmp_path) -> None:
    temporary = tmp_path / "cache.tmp"
    destination = tmp_path / "cache.json"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    monkeypatch.setattr(inventory.sys, "platform", "win32")
    attempts = 0

    def unrelated_error(*_args):
        nonlocal attempts
        attempts += 1
        error = PermissionError("invalid operation")
        error.winerror = 87
        raise error

    monkeypatch.setattr(inventory.os, "replace", unrelated_error)

    with pytest.raises(PermissionError, match="invalid operation"):
        inventory._replace_cache_file(temporary, destination)

    assert attempts == 1
    assert destination.read_text(encoding="utf-8") == "old"


def test_cache_replace_sharing_violation_retry_is_bounded(monkeypatch, tmp_path) -> None:
    temporary = tmp_path / "cache.tmp"
    destination = tmp_path / "cache.json"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    attempts = 0
    delays: list[float] = []

    def sharing_violation(*_args):
        nonlocal attempts
        attempts += 1
        error = PermissionError("sharing violation")
        error.winerror = 5
        raise error

    monkeypatch.setattr(inventory.sys, "platform", "win32")
    monkeypatch.setattr(inventory.os, "replace", sharing_violation)
    monkeypatch.setattr(inventory.time, "sleep", delays.append)

    with pytest.raises(PermissionError, match="sharing violation"):
        inventory._replace_cache_file(temporary, destination)

    assert attempts == len(inventory._REPLACE_RETRY_DELAYS_SECONDS) + 1
    assert delays == list(inventory._REPLACE_RETRY_DELAYS_SECONDS)
    assert destination.read_text(encoding="utf-8") == "old"


def test_concurrent_cache_misses_share_one_tool_probe(monkeypatch, tmp_path) -> None:
    clients: list[_ProbeClient] = []
    clients_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def factory() -> _ProbeClient:
        client = _ProbeClient(models_barrier=barrier)
        with clients_lock:
            clients.append(client)
        return client

    _install_runtime(monkeypatch, tmp_path, factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _index: inventory.resolve_runtime_capabilities(), range(2))
        )

    assert sorted(result.tool_source for result in results) == ["cache", "probe"]
    assert sum(len(client.sessions) for client in clients) == 2
    assert all(client.lifecycle == ["start", "list_models", "stop"] for client in clients)


def test_cache_lock_excludes_another_process(tmp_path) -> None:
    path = tmp_path / "tools.lock"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = (
        "import pathlib,time\n"
        "from roundtable.capabilities.inventory import _cache_lock\n"
        f"path=pathlib.Path({str(path)!r})\n"
        f"ready=pathlib.Path({str(ready)!r})\n"
        f"release=pathlib.Path({str(release)!r})\n"
        "with _cache_lock(path, timeout=5):\n"
        " ready.touch()\n"
        " deadline=time.monotonic()+10\n"
        " while not release.exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script])
    deadline = time.monotonic() + 5
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), f"lock helper exited with {process.poll()}"
    timer = threading.Timer(0.25, release.touch)
    timer.start()
    started = time.monotonic()
    try:
        with inventory._cache_lock(path, timeout=5):
            elapsed = time.monotonic() - started
    finally:
        release.touch(exist_ok=True)
        timer.join()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)

    assert process.returncode == 0
    assert elapsed >= 0.15


def test_cache_lock_does_not_mutate_the_lock_file(tmp_path) -> None:
    path = tmp_path / "tools.lock"

    with inventory._cache_lock(path, timeout=5):
        assert path.stat().st_size == 0

    assert path.read_bytes() == b""


def test_model_failure_stops_without_tool_sessions(monkeypatch, tmp_path) -> None:
    client = _ProbeClient(list_error=RuntimeError("models unavailable"))
    _install_runtime(monkeypatch, tmp_path, lambda: client)

    with pytest.raises(RuntimeError, match="models unavailable"):
        inventory.resolve_runtime_capabilities()

    assert client.lifecycle == ["start", "list_models", "stop"]
    assert client.sessions == []


def test_probe_failure_disconnects_session_and_stops_client(monkeypatch, tmp_path) -> None:
    client = _ProbeClient(send_error=RuntimeError("probe failed"))
    _install_runtime(monkeypatch, tmp_path, lambda: client)

    with pytest.raises(RuntimeError, match="probe failed"):
        inventory.resolve_runtime_capabilities()

    assert client.lifecycle == ["start", "list_models", "stop"]
    assert len(client.sessions) == 1
    assert client.sessions[0].disconnected


def test_lock_failure_blocks_before_probe_and_stops_client(monkeypatch, tmp_path) -> None:
    client = _ProbeClient()
    _install_runtime(monkeypatch, tmp_path, lambda: client)

    @contextmanager
    def fail_lock(*_args: Any, **_kwargs: Any):
        raise TimeoutError("lock unavailable")
        yield

    monkeypatch.setattr(inventory, "_cache_lock", fail_lock)
    with pytest.raises(TimeoutError, match="lock unavailable"):
        inventory.resolve_runtime_capabilities()

    assert client.lifecycle == ["start", "list_models", "stop"]
    assert client.sessions == []


def test_cache_write_failure_blocks_and_preserves_previous_file(monkeypatch, tmp_path) -> None:
    client = _ProbeClient()
    _install_runtime(monkeypatch, tmp_path, lambda: client)
    path = inventory._cache_path("1.0.84-1", "3")
    path.parent.mkdir(parents=True)
    path.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(inventory, "_read_tool_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        inventory.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("no"))
    )

    with pytest.raises(OSError, match="no"):
        inventory.resolve_runtime_capabilities()

    assert path.read_text(encoding="utf-8") == "previous"
    assert client.lifecycle == ["start", "list_models", "stop"]
    assert len(client.sessions) == 2
    assert all(session.disconnected for session in client.sessions)


def test_cache_contains_only_version_provenance_and_tool_names(monkeypatch, tmp_path) -> None:
    _install_runtime(monkeypatch, tmp_path, _ProbeClient)
    inventory.resolve_runtime_capabilities()

    data = json.loads(_cache_file(tmp_path).read_text(encoding="utf-8"))
    assert set(data) == inventory._CACHE_KEYS
    assert set(data) == inventory._CACHE_KEYS
    assert data["builtin"] == ["powershell", "sql", "view"]
    assert data["always_on"] == ["sql"]


@pytest.mark.parametrize(
    ("builtin", "always_on", "message"),
    [
        ([], [], "no built-in tools"),
        (["view"], ["sql"], "absent from the built-in set"),
        (["bad/name"], [], "invalid bare tool IDs"),
    ],
)
def test_invalid_probe_results_are_rejected(
    monkeypatch, tmp_path, builtin, always_on, message
) -> None:
    client = _ProbeClient(builtin=builtin, always_on=always_on)
    _install_runtime(monkeypatch, tmp_path, lambda: client)

    with pytest.raises(ValueError, match=message):
        inventory.resolve_runtime_capabilities()

    assert client.lifecycle == ["start", "list_models", "stop"]
    assert all(session.disconnected for session in client.sessions)


def test_tool_refresh_writes_only_packaged_static_metadata(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runtime_inventory.yaml"
    path.write_text("# guidance\ncli_version: old\ncaptured: old\n", encoding="utf-8")
    monkeypatch.setattr(inventory, "_INVENTORY_PATH", path)
    inventory._inventory.cache_clear()

    inventory.write_inventory(["view"], ["sql"], "1.0.84-1", "2026-09-08")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert str(data.pop("captured")) == "2026-09-08"
    assert data == {
        "cli_version": "1.0.84-1",
        "builtin": ["view"],
        "always_on": ["sql"],
    }
    inventory._inventory.cache_clear()
