"""Packaged capability metadata and authenticated runtime discovery.

Authenticated doctor and review invocations resolve models and built-in tools
from one SDK client lifecycle. Models are always listed live. Tool names are
probed with two one-turn sessions only when the versioned user cache misses.

The packaged ``runtime_inventory.yaml`` is reserved for deterministic static
validation in CI and packaging. MCP tools are validated separately through
:mod:`roundtable.mcp`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

_INVENTORY_PATH = Path(__file__).resolve().parent / "runtime_inventory.yaml"
_CACHE_SCHEMA_VERSION = 1
_CACHE_KEYS = {
    "schema_version",
    "cli_version",
    "sdk_package_version",
    "sdk_protocol_version",
    "captured",
    "builtin",
    "always_on",
}
_LOCK_TIMEOUT_SECONDS = 390.0
_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.02, 0.04, 0.08, 0.16)
_WINDOWS_REPLACE_RETRY_ERRORS = {5, 32}
_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Capabilities resolved from one authenticated SDK invocation."""

    model_ids: frozenset[str]
    builtin_tool_ids: frozenset[str]
    always_on_tool_ids: frozenset[str]
    cli_version: str
    sdk_package_version: str
    sdk_protocol_version: str
    tool_source: Literal["cache", "probe"]

    @property
    def provenance(self) -> str:
        return (
            f"cli={self.cli_version}, sdk={self.sdk_package_version}, "
            f"protocol={self.sdk_protocol_version}, tools={self.tool_source}"
        )


@dataclass(frozen=True)
class _ToolCapabilities:
    builtin: frozenset[str]
    always_on: frozenset[str]
    source: Literal["cache", "probe"]


@lru_cache(maxsize=1)
def _inventory() -> dict[str, object]:
    with _INVENTORY_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{_INVENTORY_PATH.name}: expected a mapping at the top level")
    return data


def _names(key: str) -> frozenset[str]:
    value = _inventory().get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{_INVENTORY_PATH.name}: `{key}` must be a list of strings")
    return frozenset(value)


def builtin_tool_names() -> frozenset[str]:
    """Every built-in tool name recorded in the packaged CI baseline."""
    return _names("builtin")


def always_on_tool_names() -> frozenset[str]:
    """Always-on tool names recorded in the packaged CI baseline."""
    return _names("always_on")


def inventory_provenance() -> str:
    """One line naming the CLI whose packaged baseline was probed."""
    data = _inventory()
    return f"packaged cli={data.get('cli_version', '?')}, captured={data.get('captured', '?')}"


def inventory_cli_version() -> str:
    """The Copilot CLI version recorded with the packaged tool metadata."""
    value = _inventory().get("cli_version")
    return value if isinstance(value, str) and value else "unknown"


def copilot_cli_version() -> str:
    """The active Copilot CLI version, or ``unknown`` when it cannot be detected."""
    try:
        result = subprocess.run(
            ["copilot", "--version"],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        lines = (result.stdout or result.stderr or "").splitlines()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    match = re.search(r"\d+\.\d+\.\S*\d", lines[0] if lines else "")
    return match.group(0) if match else "unknown"


def write_inventory(
    builtin: list[str],
    always_on: list[str],
    cli_version: str,
    captured: str,
) -> Path:
    """Rewrite the packaged CI baseline for release maintenance."""
    header = []
    for line in _INVENTORY_PATH.read_text(encoding="utf-8").split("\n"):
        if line.startswith("cli_version:"):
            break
        header.append(line)
    body = [
        f"cli_version: {cli_version}",
        f"captured: {captured}",
        "",
        "# Every built-in tool the runtime can grant.",
        "builtin:",
        *(f"- {name}" for name in sorted(builtin)),
        "",
        "# Present even under `tools: []`. Ungrantable and unrevokable, so a call to one is",
        "# never a grant violation and declaring one is a no-op.",
        "always_on:",
        *(f"- {name}" for name in sorted(always_on)),
        "",
    ]
    _INVENTORY_PATH.write_text("\n".join([*header, *body]), encoding="utf-8", newline="\n")
    _inventory.cache_clear()
    return _INVENTORY_PATH


def _cache_path(cli_version: str, sdk_protocol_version: str) -> Path:
    from roundtable.bundle import home_root

    key = hashlib.sha256(f"{cli_version}\0{sdk_protocol_version}".encode()).hexdigest()[:24]
    return home_root() / "capabilities" / "tools" / f"v{_CACHE_SCHEMA_VERSION}-{key}.json"


def _validate_tool_sets(
    builtin: frozenset[str], always_on: frozenset[str]
) -> tuple[frozenset[str], frozenset[str]]:
    if not builtin:
        raise ValueError("runtime tool probe returned no built-in tools")
    invalid = sorted(name for name in builtin | always_on if not _TOOL_ID_RE.fullmatch(name))
    if invalid:
        raise ValueError(f"runtime tool probe returned invalid bare tool IDs: {invalid}")
    if not always_on <= builtin:
        raise ValueError(
            "runtime tool probe returned always-on tools absent from the built-in set: "
            f"{sorted(always_on - builtin)}"
        )
    return builtin, always_on


def _read_tool_cache(
    path: Path,
    *,
    cli_version: str,
    sdk_protocol_version: str,
) -> _ToolCapabilities | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != _CACHE_KEYS:
        return None
    if (
        data.get("schema_version") != _CACHE_SCHEMA_VERSION
        or data.get("cli_version") != cli_version
        or data.get("sdk_protocol_version") != sdk_protocol_version
        or not isinstance(data.get("sdk_package_version"), str)
        or not isinstance(data.get("captured"), str)
    ):
        return None
    builtin = data.get("builtin")
    always_on = data.get("always_on")
    if (
        not isinstance(builtin, list)
        or not all(isinstance(name, str) for name in builtin)
        or not isinstance(always_on, list)
        or not all(isinstance(name, str) for name in always_on)
    ):
        return None
    try:
        valid_builtin, valid_always_on = _validate_tool_sets(
            frozenset(builtin), frozenset(always_on)
        )
    except ValueError:
        return None
    return _ToolCapabilities(valid_builtin, valid_always_on, "cache")


def _write_tool_cache(
    path: Path,
    *,
    cli_version: str,
    sdk_package_version: str,
    sdk_protocol_version: str,
    builtin: frozenset[str],
    always_on: frozenset[str],
) -> None:
    data = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "cli_version": cli_version,
        "sdk_package_version": sdk_package_version,
        "sdk_protocol_version": sdk_protocol_version,
        "captured": datetime.now(UTC).isoformat(),
        "builtin": sorted(builtin),
        "always_on": sorted(always_on),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_cache_file(Path(temporary), path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def _replace_cache_file(temporary: Path, path: Path) -> None:
    for delay in (*_REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as err:
            if (
                sys.platform != "win32"
                or getattr(err, "winerror", None) not in _WINDOWS_REPLACE_RETRY_ERRORS
                or delay is None
            ):
                raise
            time.sleep(delay)


def _try_lock(handle: Any) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import importlib

    fcntl = importlib.import_module("fcntl")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import importlib

    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _cache_lock(path: Path, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for runtime capability cache lock {path}")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


async def _probe_session_tool_names(
    client: Any,
    tools: list[str] | None,
    model: str,
) -> frozenset[str]:
    agent: dict[str, object] = {
        "name": "roundtable-tool-probe",
        "prompt": "You are a probe. Reply with one word.",
        "infer": False,
    }
    if tools is not None:
        agent["tools"] = tools
    session = await client.create_session(
        agent="roundtable-tool-probe",
        model=model,
        custom_agents=[agent],
        enable_config_discovery=False,
        skip_custom_instructions=True,
        enable_skills=False,
        custom_agents_local_only=True,
        enable_session_store=False,
        on_permission_request=lambda _request: {"kind": "allow"},
    )
    try:
        await session.send_and_wait("Reply with exactly: ok", timeout=180)
        metadata = await session.rpc.tools.get_current_metadata()
        return frozenset(tool.name for tool in (metadata.tools or ()) if not tool.mcp_server_name)
    finally:
        await session.disconnect()


def _probe_model_id(model_ids: frozenset[str]) -> str:
    allowed = sorted(
        model_id for model_id in model_ids if model_id != "auto" and "astra" not in model_id.lower()
    )
    for preferred in ("gpt-5.6-luna", "gpt-5-mini", "gpt-5.6-terra", "gpt-5.6-sol"):
        if preferred in allowed:
            return preferred
    if allowed:
        return allowed[0]
    raise RuntimeError("tool discovery requires a live non-Astra model")


async def _probe_tool_capabilities(
    client: Any,
    model_ids: frozenset[str],
) -> _ToolCapabilities:
    model = _probe_model_id(model_ids)
    builtin = await _probe_session_tool_names(client, None, model)
    always_on = await _probe_session_tool_names(client, [], model)
    builtin, always_on = _validate_tool_sets(builtin, always_on)
    return _ToolCapabilities(builtin, always_on, "probe")


async def _resolve_tool_capabilities(
    client: Any,
    *,
    cli_version: str,
    sdk_package_version: str,
    sdk_protocol_version: str,
    model_ids: frozenset[str],
    force_probe: bool,
) -> _ToolCapabilities:
    cache_path = _cache_path(cli_version, sdk_protocol_version)
    with _cache_lock(cache_path.with_suffix(".lock")):
        if not force_probe:
            cached = _read_tool_cache(
                cache_path,
                cli_version=cli_version,
                sdk_protocol_version=sdk_protocol_version,
            )
            if cached is not None:
                return cached
        probed = await _probe_tool_capabilities(client, model_ids)
        _write_tool_cache(
            cache_path,
            cli_version=cli_version,
            sdk_package_version=sdk_package_version,
            sdk_protocol_version=sdk_protocol_version,
            builtin=probed.builtin,
            always_on=probed.always_on,
        )
        return probed


async def _resolve_runtime_capabilities(force_tool_probe: bool) -> RuntimeCapabilities:
    from roundtable.backend import require_sdk, resolve_auth, sdk_versions

    client = require_sdk().CopilotClient(**resolve_auth().client_kwargs())
    await client.start()
    try:
        model_ids = frozenset(model.id for model in await client.list_models())
        cli_version = copilot_cli_version()
        versions = sdk_versions()
        if cli_version == "unknown":
            raise RuntimeError("could not detect the active Copilot CLI version")
        if versions.package == "unknown" or versions.protocol == "unknown":
            raise RuntimeError(
                "could not detect the installed Copilot SDK package/protocol version"
            )
        tools = await _resolve_tool_capabilities(
            client,
            cli_version=cli_version,
            sdk_package_version=versions.package,
            sdk_protocol_version=versions.protocol,
            model_ids=model_ids,
            force_probe=force_tool_probe,
        )
        return RuntimeCapabilities(
            model_ids=model_ids,
            builtin_tool_ids=tools.builtin,
            always_on_tool_ids=tools.always_on,
            cli_version=cli_version,
            sdk_package_version=versions.package,
            sdk_protocol_version=versions.protocol,
            tool_source=tools.source,
        )
    finally:
        await client.stop()


def resolve_runtime_capabilities(*, force_tool_probe: bool = False) -> RuntimeCapabilities:
    """Resolve live models and version-matched built-in tools in one SDK lifecycle."""
    return asyncio.run(_resolve_runtime_capabilities(force_tool_probe))
