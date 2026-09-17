from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from roundtable import cli
from roundtable.decision import EXIT_BAD_ARGS
from roundtable.inputs import ReplayError, ReplaySession
from roundtable.inputs.replay import replay_context_from_dict
from roundtable.runtime.agent_setup import ValidationReport
from roundtable.settings import ReviewConfig


@pytest.mark.parametrize(
    ("extra", "name"),
    [
        ({"pr": "42"}, "--pr"),
        ({"repo": "repo"}, "repo"),
        ({"base_branch": "main"}, "--base-branch"),
    ],
)
def test_input_from_rejects_live_target_arguments(extra, name, capsys) -> None:
    values = {
        "input_from": "session",
        "pr": None,
        "repo": None,
        "base_branch": None,
        "backend": None,
        "simulate": True,
    }
    values.update(extra)
    args = SimpleNamespace(**values)

    assert cli._cmd_review(args) == EXIT_BAD_ARGS
    assert name in capsys.readouterr().err


def test_replay_target_surfaces_graph_drift_and_restores_workspace(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "recorded"
    source.mkdir()
    context = SimpleNamespace(
        repository=SimpleNamespace(name="Repo", fetch_url="remote"),
        revision=SimpleNamespace(source_branch="feature"),
    )
    saved = SimpleNamespace(
        session_dir=source,
        context=context,
        graph_config_sha="recorded",
    )
    workspace = SimpleNamespace(path=tmp_path / "worktree")
    monkeypatch.setattr(cli, "load_replay_session", lambda _path: saved)
    monkeypatch.setattr(
        cli,
        "restore_replay_session",
        lambda *_args, **_kwargs: SimpleNamespace(session=saved, workspace=workspace),
    )
    monkeypatch.setattr(cli, "graph_config_sha", lambda _root: "current")
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(workspace=SimpleNamespace()),
    )

    target = cli._resolve_replay_target(
        str(source),
        tmp_path / "artifacts",
        SimpleNamespace(root=tmp_path),
    )

    assert target.workspace is workspace
    assert target.replay_session is saved
    assert "recorded=recorded, current=current" in capsys.readouterr().err


def test_input_from_requires_complete_session_directory(tmp_path) -> None:
    payload = tmp_path / "source-payloads.json"
    payload.write_text(json.dumps({"ReviewDiff": "frozen"}), encoding="utf-8")

    with pytest.raises(ReplayError, match="session directory"):
        cli.load_replay_session(payload)

    session = tmp_path / "session"
    session.mkdir()
    (session / "source-payloads.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReplayError, match="incomplete replay session"):
        cli.load_replay_session(session)


def test_input_from_help_requires_session_and_lists_conflicts() -> None:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    review_help = " ".join(sub.choices["review"].format_help().split())

    assert "SESSION_DIR" in review_help
    assert "cannot be combined with repo, --pr, or --base-branch" in review_help
    assert "SESSION_DIR_OR_FILE" not in review_help


def test_replay_restoration_failure_never_executes_saved_payloads(
    tmp_path, monkeypatch, capsys
) -> None:
    executed = []
    args = SimpleNamespace(
        input_from=str(tmp_path / "session"),
        pr=None,
        repo=None,
        base_branch=None,
        backend=None,
        simulate=True,
    )
    configuration = SimpleNamespace(entries={})
    monkeypatch.setattr(cli, "_resolve_backend_name", lambda **_kwargs: "mock")
    monkeypatch.setattr(cli, "get_configuration", lambda: configuration)
    monkeypatch.setattr(cli, "_require_review_domain_values", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_preflight_graph", lambda _configuration: None)
    monkeypatch.setattr(
        cli,
        "build_review_config",
        lambda *_args: ReviewConfig(3, 2, tmp_path / "artifacts", False, ()),
    )
    monkeypatch.setattr(cli, "render_params", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        cli,
        "_resolve_replay_target",
        lambda *_args: (_ for _ in ()).throw(ReplayError("restore failed")),
    )
    monkeypatch.setattr(cli, "_execute_review", lambda *_args: executed.append(True))

    assert cli._cmd_review(args) == EXIT_BAD_ARGS
    assert executed == []
    assert "restore failed" in capsys.readouterr().err


def test_replay_restores_payload_ado_mcp_cwd_and_add_dirs(tmp_path, monkeypatch) -> None:
    recorded_workspace = r"C:\recorded\worktree"
    context = replay_context_from_dict(
        {
            "version": 1,
            "repository": {
                "name": "Repo",
                "fetchUrl": "https://dev.azure.com/org/project/_git/Repo",
                "normalizedRemoteUrl": "dev.azure.com/org/project/repo",
            },
            "revision": {
                "mode": "pr",
                "sourceSha": "a" * 40,
                "baseSha": "b" * 40,
                "sourceBranch": "feature",
                "baseBranch": "main",
            },
            "adoIdentities": [
                {
                    "org": "org",
                    "project": "project",
                    "repoName": "Repo",
                    "remoteUrl": "https://dev.azure.com/org/project/_git/Repo",
                    "host": "dev.azure.com",
                    "repositoryId": "rid",
                    "projectId": "pid",
                }
            ],
            "changedFiles": ["src/file.py"],
            "sessionHeader": f"workspace_path: {json.dumps(recorded_workspace)}\n",
            "workspacePath": recorded_workspace,
            "hintArtifact": None,
            "workspaceOverlay": None,
        }
    )
    saved = ReplaySession(
        session_dir=tmp_path / "old-session",
        context=context,
        source_payloads={"ReviewDiff": "saved diff\r\n", "GitHistory": "saved history\n"},
        graph_config_sha="graph",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    workspace = SimpleNamespace(
        path=worktree,
        mode="local-worktree",
        source_sha="a" * 40,
        base_sha="b" * 40,
        clone_path=tmp_path / "clone",
        discovery=None,
    )
    target = cli._ReviewTarget(
        workspace=workspace,
        repo=SimpleNamespace(
            name="Repo", branch="feature", remote_url=context.repository.fetch_url
        ),
        pr=None,
        metadata=None,
        label="replay",
        base_dir=tmp_path / "artifacts",
        session_id="session",
        replay_session=saved,
    )
    captured = {}
    mcp_contexts = []
    monkeypatch.setattr(cli, "validate_agents", lambda *a, **k: ValidationReport())
    monkeypatch.setattr(
        cli,
        "graph_custom_agents",
        lambda *_args: {"agent": SimpleNamespace(tools=("ado-work-items/wit_get_work_item",))},
    )
    monkeypatch.setattr(cli, "load_mcp_needs_map", lambda *_args: {"agent": ["ado-work-items"]})

    def resolve(_servers, mcp_context):
        mcp_contexts.append(mcp_context)
        return {"ado-work-items": {}}

    monkeypatch.setattr(cli, "resolve_mcp_config", resolve)
    monkeypatch.setattr(cli, "ado_servers", lambda servers: servers)
    monkeypatch.setattr(cli, "_print_session_links", lambda _persist: None)

    def run_review(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            persist=SimpleNamespace(
                session_dir=tmp_path / "new-session",
                report_path=tmp_path / "new-session" / "verdict.md",
            )
        )

    monkeypatch.setattr(cli, "run_review", run_review)
    args = SimpleNamespace(
        hint=None,
        hint_path=None,
        simulate=True,
        dry_run=False,
        dump_prompts=False,
        session_reuse=True,
        base_branch=None,
        pr=None,
        max_attempts=None,
        concurrency=None,
    )
    review_config = ReviewConfig(3, 2, tmp_path / "artifacts", False, ())
    configuration = SimpleNamespace(entries={})

    assert cli._execute_review(args, target, review_config, configuration) == 0
    assert captured["config"] is configuration
    assert captured["source_payloads"] == saved.source_payloads
    assert json.dumps(str(worktree)) in captured["session_header"]
    assert json.dumps(recorded_workspace) not in captured["session_header"]
    assert captured["changed_files"] == ["src/file.py"]
    assert captured["cwd"] == str(worktree)
    assert captured["add_dirs"] == [str(worktree)]
    assert captured["replay_context"] == context.to_dict()
    assert mcp_contexts[0].ado_org == "org"
    assert "org" in captured["ado_context_by_key"]["agent"]
