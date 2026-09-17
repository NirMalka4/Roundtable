"""runtime.mcp_prewarm: best-effort pre-flight warm-up + health probe of injected MCP servers.

WHY: an opt-in MCP server (e.g. ``ado-work-items``) spawned for the FIRST time in a
review pays a cold-start cost — ``npx`` package resolution + first auth-token
acquisition — that can exceed the copilot MCP-startup budget, so the server is
marked ``failed`` and its tools never load, even though its ``## Tool Bindings``
are already injected into the agent prompt (the "listed but not callable" gap).

This module spawns each *resolved* server ONCE before the DAG and completes an MCP
``initialize`` handshake. Two effects, both server-agnostic (it acts on whatever
``resolve_mcp_config`` produced, so a newly-added server is handled with zero new
code):

* **Warm** — the handshake forces ``npx`` to fully resolve+cache the package and
  brings the server to a ready state, so every agent session afterwards starts it
  WARM (well within budget) and its tools actually load.
* **Probe** — a server that cannot even be reached (spawn error, immediate crash,
  stdout EOF before a handshake) is reported ``unreachable`` so the caller can
  drop its ``## Tool Bindings`` from the prompt and omit it from the per-agent
  config — agents are never told to use tools that will not load.

Best-effort and fail-open throughout: any error degrades to a benign verdict and
NEVER blocks the review (agents fall back exactly as before). A merely *slow*
server is reported ``warmed_slow`` (kept, now cached for the in-agent spawn) — only
a hard failure gates, so a slow-but-healthy server is never dropped.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .npm_env import child_env_with_registry_pin

#: Verdicts a probe can return. Only ``unreachable`` gates the prompt/config; the
#: rest keep the server (``ready`` = handshake ok, ``warmed_slow`` = still starting
#: at the budget but alive, so the package is now cached for the in-agent spawn).
READY = "ready"
WARMED_SLOW = "warmed_slow"
UNREACHABLE = "unreachable"

_DEFAULT_BUDGET_S = 120.0

#: How many trailing child-stderr lines to retain for diagnostics. The child's
#: stderr is otherwise discarded, so a server that fails to warm (e.g. an ADO
#: server stuck on ``authentication: interactive``) leaves NO trace of *why*.
#: Retaining the last few lines surfaces the cause in ``trace.json`` (see the
#: ``mcpPrewarm[].stderr`` field) without a manual re-probe.
_STDERR_TAIL_LINES = 10

#: A minimal MCP ``initialize`` request (newline-delimited JSON-RPC over stdio).
_INIT_REQUEST = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "roundtable-prewarm", "version": "0"},
            },
        }
    )
    + "\n"
)

_EOF = object()  # sentinel pushed by the reader thread when stdout closes

Log = Callable[[str], None]


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of warming one server: a :data:`READY`/:data:`WARMED_SLOW`/
    :data:`UNREACHABLE` verdict plus the last few child-stderr lines (empty for a
    clean handshake), so a failed warm-up carries its own diagnostic."""

    verdict: str
    stderr_tail: tuple[str, ...] = field(default_factory=tuple)


def _noop(_msg: str) -> None:  # default logger: silence
    return None


def unique_servers(configs: Iterable[Mapping[str, dict] | None]) -> dict[str, dict]:
    """Merge resolved per-agent server maps into one ``name -> spec`` mapping.

    Takes the union across the default config and all per-agent configs, keeping
    the first spec seen for a given name (identical for a given name by
    construction).     Empty entries are skipped.
    """
    out: dict[str, dict] = {}
    for cfg in configs:
        if not cfg:
            continue
        for name, spec in cfg.items():
            if isinstance(spec, dict):
                out.setdefault(name, spec)
    return out


def prune_servers(config: Mapping[str, dict] | None, drop: Iterable[str]) -> dict[str, dict] | None:
    """Return ``config`` without the named servers, or ``None`` when empty."""
    drop = set(drop)
    if not config:
        return None
    kept = {k: v for k, v in config.items() if k not in drop}
    return kept or None


def warm_and_probe(
    servers: Mapping[str, dict],
    *,
    budget_s: float = _DEFAULT_BUDGET_S,
    log: Log = _noop,
) -> dict[str, ProbeResult]:
    """Warm + probe every server concurrently; return ``name -> ProbeResult``.

    Each server is spawned once with a per-server ``budget_s`` handshake budget.
    Never raises — a worker error degrades to an :data:`UNREACHABLE` result.
    """
    if not servers:
        return {}
    results: dict[str, ProbeResult] = {}
    with ThreadPoolExecutor(max_workers=len(servers)) as ex:
        futures = {
            ex.submit(_probe_one, name, spec, budget_s): name for name, spec in servers.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:  # fail-open: any worker error ⇒ unreachable
                results[name] = ProbeResult(UNREACHABLE)
            log(f"[review] mcp-prewarm: {name} -> {results[name].verdict}")
    return results


def unreachable(results: Mapping[str, ProbeResult]) -> set[str]:
    """The set of server names that could not be reached (the ones to gate)."""
    return {name for name, res in results.items() if res.verdict == UNREACHABLE}


def _spawn(command: str, args: list[str]) -> subprocess.Popen:
    """Spawn a stdio MCP server. ``shell=True`` on Windows so ``npx``→``npx.cmd``
    resolves (and to dodge the Node/Windows ``spawn`` EINVAL quirk). Child stderr
    is captured (not discarded) so a failed warm-up can carry its diagnostic. The
    env carries an ``npm_config_registry`` pin (see ``runtime.npm_env``) so package
    resolution is immune to the review target's project ``.npmrc``."""
    argv = [command, *args]
    return subprocess.Popen(  # args come from our own trusted mcp_servers.yaml spec
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        shell=(os.name == "nt"),
        env=child_env_with_registry_pin(),
    )


def _pump_lines(stream, out: queue.Queue) -> None:
    """Read stdout lines onto ``out`` until EOF, then push the :data:`_EOF` sentinel."""
    try:
        for line in stream:
            out.put(line)
    except (ValueError, OSError):  # stream closed under us during teardown
        pass
    finally:
        out.put(_EOF)


def _drain_stderr(stream, tail: deque) -> None:
    """Keep the last :data:`_STDERR_TAIL_LINES` non-empty stderr lines in ``tail``."""
    try:
        for line in stream:
            stripped = line.rstrip("\n")
            if stripped:
                tail.append(stripped)
    except (ValueError, OSError):  # stream closed under us during teardown
        pass


def _is_init_response(line: str) -> bool:
    """True when ``line`` is the JSON-RPC response to our ``initialize`` (id == 1).

    An ``error`` reply still proves the server is *reachable* (it spoke the
    protocol), so it counts — the goal is reachability + warmth, not a successful
    capability negotiation.
    """
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("id") == 1 and ("result" in obj or "error" in obj)


def _teardown(proc: subprocess.Popen) -> None:
    """Best-effort shutdown: close stdin (stdio servers exit on EOF), then kill."""
    try:
        if proc.stdin:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            proc.kill()


def _probe_one(name: str, spec: Mapping, budget_s: float) -> ProbeResult:
    """Spawn one server, drive an ``initialize`` handshake, classify the outcome.

    Returns a :class:`ProbeResult` carrying the verdict plus the last few
    child-stderr lines (the diagnostic for a warm-up that never answered).
    """
    command = spec.get("command")
    args = list(spec.get("args") or [])
    if not command:
        return ProbeResult(UNREACHABLE)
    try:
        proc = _spawn(str(command), [str(a) for a in args])
    except (OSError, ValueError):
        return ProbeResult(UNREACHABLE)

    stderr_tail: deque = deque(maxlen=_STDERR_TAIL_LINES)
    lines: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_pump_lines, args=(proc.stdout, lines), daemon=True)
    reader.start()
    err_reader = threading.Thread(
        target=_drain_stderr, args=(proc.stderr, stderr_tail), daemon=True
    )
    err_reader.start()
    if proc.stdin is None:  # stdin=PIPE at spawn guarantees a pipe; narrows the Optional type
        _teardown(proc)
        return ProbeResult(UNREACHABLE, tuple(stderr_tail))
    try:
        proc.stdin.write(_INIT_REQUEST)
        proc.stdin.flush()
    except (OSError, ValueError):
        _teardown(proc)
        return ProbeResult(UNREACHABLE, tuple(stderr_tail))

    deadline = time.monotonic() + max(budget_s, 0.0)
    status = WARMED_SLOW
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Budget elapsed: kept if still alive (package now cached), else dead.
                status = WARMED_SLOW if proc.poll() is None else UNREACHABLE
                break
            try:
                item = lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if proc.poll() is not None:  # exited without answering the handshake
                    status = UNREACHABLE
                    break
                continue
            if item is _EOF:  # stdout closed ⇒ the server died
                status = UNREACHABLE
                break
            if _is_init_response(item):
                status = READY
                break
    finally:
        _teardown(proc)
    return ProbeResult(status, tuple(stderr_tail))
