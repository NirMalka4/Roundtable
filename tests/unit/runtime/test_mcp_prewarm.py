"""Tests for runtime.mcp_prewarm — pure config helpers + real-subprocess probing.

The pure helpers (``unique_servers``/``prune_servers``/``unreachable``) are tested
directly. The probe (``warm_and_probe``/``_probe_one``) is exercised against tiny
*real* stdio processes (spawned via ``sys.executable``) that mimic the three
outcomes a server can produce: a well-behaved MCP server (``ready``), a process
that exits immediately without answering (``unreachable``), and one that stays
alive but never answers within the budget (``warmed_slow``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from roundtable.mcp import prewarm as mp

# ── a minimal stdio MCP server: read the initialize request, answer id==1, idle ──
_READY_SERVER = (
    "import sys, json\n"
    "sys.stdin.readline()\n"
    'sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":1,"result":{"ok":True}}) + "\\n")\n'
    "sys.stdout.flush()\n"
    "for _ in sys.stdin:\n"
    "    pass\n"
)
# never answers, but stays alive until stdin closes → warmed_slow within a short budget.
_HANG_SERVER = "import sys\nfor _ in sys.stdin:\n    pass\n"
# exits immediately without answering → unreachable.
_DEAD_SERVER = "import sys\nsys.exit(1)\n"


@pytest.fixture(autouse=True)
def _isolated_child_environment(monkeypatch) -> None:
    monkeypatch.setattr(mp, "child_env_with_registry_pin", dict)


def _spec(tmp_path: Path, name: str, body: str) -> dict:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return {"command": sys.executable, "args": [str(path)]}


# ── unique_servers ──────────────────────────────────────────────────────────
def test_unique_servers_merges_and_dedupes():
    a = {"s1": {"command": "x"}, "s2": {"command": "y"}}
    b = {"s1": {"command": "x"}, "s3": {"command": "z"}}
    out = mp.unique_servers([a, None, b])
    assert set(out) == {"s1", "s2", "s3"}


def test_unique_servers_skips_malformed_and_empty():
    assert mp.unique_servers([None, {}]) == {}


# ── prune_servers ───────────────────────────────────────────────────────────
def test_prune_servers_preserves_mapping_when_nothing_dropped():
    cfg = {"s1": {"command": "x"}}
    assert mp.prune_servers(cfg, set()) == cfg
    assert mp.prune_servers(cfg, {"other"}) == cfg


def test_prune_servers_removes_named_server():
    cfg = {"s1": {"command": "x"}, "s2": {"command": "y"}}
    pruned = mp.prune_servers(cfg, {"s1"})
    assert pruned == {"s2": {"command": "y"}}


def test_prune_servers_all_dropped_returns_none():
    cfg = {"s1": {"command": "x"}}
    assert mp.prune_servers(cfg, {"s1"}) is None


def test_prune_servers_handles_none():
    assert mp.prune_servers(None, {"s1"}) is None


# ── unreachable extraction ──────────────────────────────────────────────────
def test_unreachable_selects_only_unreachable():
    results = {
        "a": mp.ProbeResult(mp.READY),
        "b": mp.ProbeResult(mp.UNREACHABLE),
        "c": mp.ProbeResult(mp.WARMED_SLOW),
    }
    assert mp.unreachable(results) == {"b"}


# ── real-subprocess probes ──────────────────────────────────────────────────
def test_probe_ready_server(tmp_path: Path):
    spec = _spec(tmp_path, "ready", _READY_SERVER)
    assert mp._probe_one("ready", spec, budget_s=10.0).verdict == mp.READY


def test_probe_dead_server_is_unreachable(tmp_path: Path):
    spec = _spec(tmp_path, "dead", _DEAD_SERVER)
    assert mp._probe_one("dead", spec, budget_s=10.0).verdict == mp.UNREACHABLE


def test_probe_hanging_server_is_warmed_slow(tmp_path: Path):
    spec = _spec(tmp_path, "hang", _HANG_SERVER)
    assert mp._probe_one("hang", spec, budget_s=0.5).verdict == mp.WARMED_SLOW


def test_probe_missing_command_is_unreachable():
    assert mp._probe_one("nocmd", {"args": []}, budget_s=1.0).verdict == mp.UNREACHABLE


def test_probe_retains_stderr_tail_on_failure(tmp_path: Path):
    # A server that logs a diagnostic to stderr then dies without answering: the
    # verdict is unreachable AND the stderr line is retained for trace.json.
    body = (
        "import sys\n"
        'sys.stderr.write("authentication: interactive\\n")\n'
        "sys.stderr.flush()\n"
        "sys.exit(1)\n"
    )
    spec = _spec(tmp_path, "noisy", body)
    res = mp._probe_one("noisy", spec, budget_s=10.0)
    assert res.verdict == mp.UNREACHABLE
    assert "authentication: interactive" in res.stderr_tail


def test_warm_and_probe_classifies_all_and_logs(tmp_path: Path):
    servers = {
        "ready": _spec(tmp_path, "r", _READY_SERVER),
        "dead": _spec(tmp_path, "d", _DEAD_SERVER),
    }
    logged: list[str] = []
    results = mp.warm_and_probe(servers, budget_s=10.0, log=logged.append)
    assert results["ready"].verdict == mp.READY
    assert results["dead"].verdict == mp.UNREACHABLE
    assert mp.unreachable(results) == {"dead"}
    assert any("mcp-prewarm" in line for line in logged)


def test_warm_and_probe_empty_is_noop():
    assert mp.warm_and_probe({}) == {}
