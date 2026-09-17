"""Regression guard: the ``--max-attempts`` retry budget must reach the runner.

``run_agent_with_ovg`` already honours ``max_attempts`` (see the agent_runner
tests); this file pins the *plumbing* that carries the operator's choice down to
it — the CLI flag's validation, its default resolving from settings, and
``_cmd_review`` forwarding the value into ``run_review``. Without these, the flag
would parse but be silently dropped before it ever influences a review.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable import cli
from roundtable.settings.workspace import DEFAULT_MAX_ATTEMPTS, Settings


# ── flag validation (_positive_int) ─────────────────────────────────────────
@pytest.mark.parametrize("good,expected", [("1", 1), ("3", 3), ("10", 10)])
def test_positive_int_accepts_whole_positive_values(good, expected):
    assert cli._positive_int(good) == expected


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "3.5", ""])
def test_positive_int_rejects_non_positive_or_non_integer(bad):
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli._positive_int(bad)


# ── parser leaves the flag unset (None); resolution happens in effective ─────
# The flag default is now None so an explicit --max-attempts is detectable; the
# flag>env>file>default precedence is resolved by config.effective, not argparse.
def test_max_attempts_flag_defaults_to_none(monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(max_attempts=7))
    args = cli.build_parser().parse_args(["review", "some/repo"])
    assert args.max_attempts is None


def test_default_model_flag_is_removed(monkeypatch):
    # F4 (breaking): --default-model was dead config (never fed routing) and is gone;
    # argparse must reject it now.
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["review", "some/repo", "--default-model", "m"])


def test_max_attempts_resolves_from_settings_when_flag_absent(monkeypatch):
    settings = Settings(max_attempts=7, sources={"max_attempts": "roundtable.yaml"})
    args = cli.build_parser().parse_args(["review", "some/repo"])
    cfg = cli.build_review_config(settings, args)
    assert cfg.max_attempts == 7


def test_max_attempts_falls_back_to_constant_when_unset(monkeypatch):
    args = cli.build_parser().parse_args(["review", "some/repo"])
    cfg = cli.build_review_config(Settings(max_attempts=None), args)
    assert cfg.max_attempts == DEFAULT_MAX_ATTEMPTS


def test_max_attempts_flag_overrides_settings(monkeypatch):
    settings = Settings(max_attempts=7, sources={"max_attempts": "roundtable.yaml"})
    args = cli.build_parser().parse_args(["review", "some/repo", "--max-attempts", "2"])
    assert args.max_attempts == 2
    cfg = cli.build_review_config(settings, args)
    assert cfg.max_attempts == 2


# ── _cmd_review forwards the value into run_review ───────────────────────────
def _stub_cmd_review(monkeypatch, tmp_path: Path) -> dict:
    captured: dict = {}
    repo = SimpleNamespace(name="R", branch="feat", remote_url="")
    monkeypatch.setattr(cli, "detect_repos", lambda _p: [repo])
    monkeypatch.setattr(
        cli,
        "_build_review_context",
        lambda *a, **k: (
            "CONTEXT",
            "## Change Under Review\n",
            SimpleNamespace(ado_org=None),
            SimpleNamespace(),
            ["x"],
            [],
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
    monkeypatch.setattr(cli, "load_mcp_needs_map", lambda *a, **k: {})
    monkeypatch.setattr(cli, "resolve_mcp_config", lambda *a, **k: None)

    def _fake_run_review(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            persist=SimpleNamespace(session_dir=tmp_path, report_path=tmp_path / "verdict.md")
        )

    monkeypatch.setattr(cli, "run_review", _fake_run_review)
    return captured


def _args(repo_path: Path, tmp_path: Path, max_attempts: int) -> SimpleNamespace:
    return SimpleNamespace(
        repo=str(repo_path),
        pr=None,
        base_branch=None,
        artifacts_dir=str(tmp_path),
        dry_run=False,
        simulate=True,
        dump_prompts=False,
        session_reuse=True,
        max_attempts=max_attempts,
    )


def test_review_forwards_max_attempts_to_run_review(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    captured = _stub_cmd_review(monkeypatch, tmp_path)

    rc = cli._cmd_review(_args(repo_path, tmp_path, max_attempts=9))

    assert rc == 0
    assert captured.get("max_attempts") == 9, (
        "review must forward the resolved --max-attempts so the OVG retry budget "
        "reaches run_agent_with_ovg"
    )
    # The resolved config is recorded to trace.json for reproducibility, and the
    # invocation block reflects the *resolved* max-attempts (not the None default).
    prov = captured.get("provenance") or {}
    eff = {e["name"]: e for e in prov.get("effectiveConfig", [])}
    assert eff["max_attempts"]["value"] == 9
    assert eff["max_attempts"]["source"] == "flag:--max-attempts"
    assert prov["invocation"]["maxAttempts"] == 9
