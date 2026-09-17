"""Regression guard: the ``review`` command must grant the review subprocess
file-system access to the repo under review.

Reviewer agents run in a fresh Copilot subprocess whose default working set is
its own CWD, not the reviewed tree. Unless the CLI passes the repo path through
``run_review`` → ``build_command`` as ``--add-dir``, agents can only see the
inlined diff and cannot open files beyond the hunks (counterpart tables,
callers, docs) that the rubrics require. ``build_command`` already emits
``--add-dir`` for every ``add_dirs`` entry (see ``test_build_command_shape``);
this test pins the missing link — that ``_cmd_review`` actually supplies the
repo path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from roundtable import cli


def _stub_cmd_review(monkeypatch, tmp_path: Path) -> dict:
    """Stub every collaborator ``_cmd_review`` touches before ``run_review`` and
    capture the kwargs it forwards. Returns the capture dict."""
    captured: dict = {"_events": []}

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
        lambda **_kwargs: (
            captured["_events"].append("capture") or SimpleNamespace(to_dict=lambda: {"version": 1})
        ),
    )
    from roundtable.runtime.agent_setup import ValidationReport

    monkeypatch.setattr(cli, "validate_agents", lambda *a, **k: ValidationReport())
    monkeypatch.setattr(cli, "load_mcp_needs_map", lambda *a, **k: {})
    monkeypatch.setattr(cli, "resolve_mcp_config", lambda *a, **k: None)

    def _fake_run_review(**kwargs):
        captured["_events"].append("execute")
        captured.update(kwargs)
        return SimpleNamespace(
            persist=SimpleNamespace(session_dir=tmp_path, report_path=tmp_path / "verdict.md")
        )

    monkeypatch.setattr(cli, "run_review", _fake_run_review)
    return captured


def _args(repo_path: Path, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo=str(repo_path),
        pr=None,
        base_branch=None,
        artifacts_dir=str(tmp_path),
        dry_run=False,
        simulate=True,
        dump_prompts=False,
        session_reuse=True,
    )


def test_review_grants_repo_file_access_via_add_dirs(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    captured = _stub_cmd_review(monkeypatch, tmp_path)

    rc = cli._cmd_review(_args(repo_path, tmp_path))

    assert rc == 0
    assert captured.get("add_dirs") == [str(repo_path.resolve())], (
        "review must forward the repo path as add_dirs so build_command emits "
        "--add-dir and the subprocess can read files beyond the diff"
    )


def test_review_persists_git_context_observability(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    captured = _stub_cmd_review(monkeypatch, tmp_path)

    rc = cli._cmd_review(_args(repo_path, tmp_path))

    assert rc == 0
    gc = captured.get("git_context")
    assert gc is not None, "review must persist a git-context observability block"
    # Local review is reviewed in place at the working tree; add_dirs/cwd both
    # pin the reviewed path so the record proves what the agents could read.
    assert gc["workspacePath"] == str(repo_path.resolve())
    assert gc["addDirs"] == [str(repo_path.resolve())]
    assert gc["cwd"] == str(repo_path.resolve())
    assert gc["mode"]  # a concrete resolved mode, never empty


def test_review_captures_replay_context_before_agent_execution(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    captured = _stub_cmd_review(monkeypatch, tmp_path)

    assert cli._cmd_review(_args(repo_path, tmp_path)) == 0
    assert captured["_events"] == ["capture", "execute"]
    assert captured["replay_context"] == {"version": 1}
