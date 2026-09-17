"""Regression guard: the ``--concurrency`` pool width must reach the executor.

Mirrors ``test_cli_max_attempts.py`` for the second operator-facing scheduling knob.
The scheduler tests prove ``run_graph_dag`` sizes its thread pool from the argument;
this file pins the *plumbing* that carries the operator's choice down to it — flag
validation, resolution from settings, and ``_cmd_review`` forwarding the value into
``run_review``.

This exists because the sibling failure mode is real: ``timeout_seconds`` was parsed into
``GraphEntry`` and then dropped by every caller, so a declared 30-minute budget was
inert for as long as nothing asserted the hop. A knob that never reaches its consumer
is indistinguishable from a hard-code.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from roundtable import cli
from roundtable.settings.workspace import DEFAULT_CONCURRENCY, Settings


def _cfg(settings: Settings, args) -> object:
    return cli.build_review_config(settings, args)


def test_concurrency_flag_defaults_to_none(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(concurrency=5))
    args = cli.build_parser().parse_args(["review", "some/repo"])
    assert args.concurrency is None


def test_concurrency_rejects_non_positive(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    import pytest

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["review", "some/repo", "--concurrency", "0"])


def test_concurrency_resolves_from_settings_when_flag_absent(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    args = cli.build_parser().parse_args(["review", "some/repo"])
    settings = Settings(concurrency=5, sources={"concurrency": "roundtable.yaml"})
    assert _cfg(settings, args).concurrency == 5


def test_concurrency_falls_back_to_declared_default(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    args = cli.build_parser().parse_args(["review", "some/repo"])
    assert _cfg(Settings(concurrency=None), args).concurrency == DEFAULT_CONCURRENCY


def test_concurrency_flag_overrides_settings(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    args = cli.build_parser().parse_args(["review", "some/repo", "--concurrency", "2"])
    assert args.concurrency == 2
    settings = Settings(concurrency=5, sources={"concurrency": "roundtable.yaml"})
    assert _cfg(settings, args).concurrency == 2


def test_review_forwards_concurrency_to_run_review(monkeypatch, tmp_path: Path) -> None:
    from tests.unit.test_cli_max_attempts import _stub_cmd_review

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    captured = _stub_cmd_review(monkeypatch, tmp_path)

    rc = cli._cmd_review(
        SimpleNamespace(
            repo=str(repo_path),
            pr=None,
            base_branch=None,
            artifacts_dir=str(tmp_path),
            dry_run=False,
            simulate=True,
            dump_prompts=False,
            session_reuse=True,
            max_attempts=None,
            concurrency=3,
        )
    )

    assert rc == 0
    assert captured.get("concurrency") == 3, (
        "review must forward the resolved --concurrency so the executor sizes its "
        "thread pool from the operator's choice"
    )
    prov = captured.get("provenance") or {}
    eff = {e["name"]: e for e in prov.get("effectiveConfig", [])}
    assert eff["concurrency"]["value"] == 3
    assert eff["concurrency"]["source"] == "flag:--concurrency"
    assert prov["invocation"]["concurrency"] == 3
