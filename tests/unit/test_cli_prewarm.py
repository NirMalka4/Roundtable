"""Regression guard: the MCP pre-warm plumbing in ``_cmd_review``.

``runtime.mcp_prewarm`` is unit-tested on its own; this file pins the *wiring* —
pre-warm is unconditional (no flags; skipped only under dry-run/simulate), it
PRUNES an unreachable server out of the resolved MCP config before the DAG runs,
and it threads a per-server ``mcp_prewarm`` verdict trace into ``run_review``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from roundtable import cli
from roundtable.mcp import prewarm as mp


# ── _cmd_review invokes the probe and prunes an unreachable server ───────────
def _stub_review(monkeypatch, tmp_path: Path, mcp_servers: dict[str, dict]) -> dict:
    captured: dict = {}
    repo = SimpleNamespace(name="R", branch="feat", remote_url="")
    monkeypatch.setattr(cli, "detect_repos", lambda _p: [repo])
    monkeypatch.setattr(
        cli,
        "_resolve_runtime_preflight_or_report",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(
        cli,
        "_build_review_context",
        lambda *a, **k: (
            "CONTEXT",
            "## Change Under Review\n",
            SimpleNamespace(ado_org="contoso"),
            SimpleNamespace(),
            ["x"],
            [],  # identities → empty, so ado-context loop is skipped
            {
                "mode": "branch",
                "repo": "repo",
                "sourceSha": "a" * 40,
                "baseSha": "b" * 40,
                "sourceBranch": "feat",
                "targetBranch": "main",
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "capture_replay_context",
        lambda **_kwargs: SimpleNamespace(to_dict=lambda: {"version": 1}),
    )
    from roundtable.runtime.agent_setup import ValidationReport

    monkeypatch.setattr(cli, "validate_agents", lambda *a, **k: ValidationReport())
    monkeypatch.setattr(cli, "load_mcp_needs_map", lambda *a, **k: {"Agent": ["ado-work-items"]})
    monkeypatch.setattr(cli, "resolve_mcp_config", lambda *a, **k: mcp_servers)

    def _fake_run_review(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            persist=SimpleNamespace(session_dir=tmp_path, report_path=tmp_path / "verdict.md"),
            verdict=SimpleNamespace(verdict_icon="✅", verdict="APPROVE"),
            exit_code=0,
        )

    monkeypatch.setattr(cli, "run_review", _fake_run_review)
    return captured


def _args(repo_path: Path, tmp_path: Path, *, dry_run: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        repo=str(repo_path),
        pr=None,
        base_branch=None,
        artifacts_dir=str(tmp_path),
        dry_run=dry_run,
        simulate=False,
        dump_prompts=False,
        session_reuse=True,
        max_attempts=3,
    )


def test_review_prunes_unreachable_server_from_config(monkeypatch, tmp_path: Path):
    mcp_servers = {"ado-work-items": {"command": "npx"}}
    captured = _stub_review(monkeypatch, tmp_path, mcp_servers)
    # Probe reports the only server unreachable → it must be pruned to nothing.
    monkeypatch.setattr(
        cli, "warm_and_probe", lambda *a, **k: {"ado-work-items": mp.ProbeResult(mp.UNREACHABLE)}
    )

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    rc = cli._cmd_review(_args(repo_path, tmp_path))

    assert rc == 0
    assert captured.get("mcp_servers_by_key") == {"Agent": None}, (
        "an unreachable MCP server must be dropped from the config before the DAG "
        "so agents are never told to use tools that will not load"
    )
    # The per-server verdict trace is threaded into run_review for trace.json.
    trace = captured.get("mcp_prewarm")
    assert trace == [{"name": "ado-work-items", "verdict": mp.UNREACHABLE, "pruned": True}]


def test_review_surfaces_stderr_tail_for_unreachable(monkeypatch, tmp_path: Path):
    mcp_servers = {"ado-work-items": {"command": "npx"}}
    captured = _stub_review(monkeypatch, tmp_path, mcp_servers)
    monkeypatch.setattr(
        cli,
        "warm_and_probe",
        lambda *a, **k: {
            "ado-work-items": mp.ProbeResult(mp.UNREACHABLE, ("authentication: interactive",))
        },
    )

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    rc = cli._cmd_review(_args(repo_path, tmp_path))

    assert rc == 0
    # The captured child-stderr tail rides along in the trace entry for debugging.
    assert captured.get("mcp_prewarm") == [
        {
            "name": "ado-work-items",
            "verdict": mp.UNREACHABLE,
            "pruned": True,
            "stderr": ["authentication: interactive"],
        }
    ]


def test_review_keeps_reachable_server_and_records_trace(monkeypatch, tmp_path: Path):
    mcp_servers = {"ado-work-items": {"command": "npx"}}
    captured = _stub_review(monkeypatch, tmp_path, mcp_servers)
    monkeypatch.setattr(
        cli, "warm_and_probe", lambda *a, **k: {"ado-work-items": mp.ProbeResult(mp.READY)}
    )

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    rc = cli._cmd_review(_args(repo_path, tmp_path))

    assert rc == 0
    assert captured.get("mcp_servers_by_key") == {"Agent": mcp_servers}, (
        "a reachable server must pass through untouched"
    )
    # A ready server carries no stderr key — the entry stays clean/parity-stable.
    assert captured.get("mcp_prewarm") == [
        {"name": "ado-work-items", "verdict": mp.READY, "pruned": False}
    ]


def test_review_skips_probe_under_dry_run(monkeypatch, tmp_path: Path):
    mcp_servers = {"ado-work-items": {"command": "npx"}}
    captured = _stub_review(monkeypatch, tmp_path, mcp_servers)
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(cli, "warm_and_probe", _boom)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    rc = cli._cmd_review(_args(repo_path, tmp_path, dry_run=True))

    assert rc == 0
    assert called["n"] == 0, "dry-run must skip the probe entirely — nothing to warm"
    # dry-run returns before run_review, so no config/trace is threaded at all.
    assert captured == {}
