from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from roundtable import cli
from roundtable.decision import EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS
from roundtable.evaluation import (
    DraftPullRequest,
    EvaluationRepository,
    EvaluationRevision,
)


def _args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        from_pr="https://dev.azure.com/org/project/_git/repo/pullrequest/42",
        from_merge_commit=None,
        from_commit=None,
        repo=None,
        at_commit=None,
        base=None,
        name="reproduce-finding",
        artifacts_dir=str(tmp_path / "artifacts"),
        dump_prompts=True,
        session_reuse=True,
        max_attempts=None,
        concurrency=None,
    )


def _revision() -> EvaluationRevision:
    return EvaluationRevision(
        repository=EvaluationRepository(
            org="org",
            project="project",
            name="repo",
            host="dev.azure.com",
            remote_url="https://dev.azure.com/org/project/_git/repo",
        ),
        source_sha="s" * 40,
        base_sha="b" * 40,
        source_label="PR 42",
    )


def test_eval_pr_uses_local_review_then_remote_operations(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    workspace = SimpleNamespace(path=tmp_path, cleanup=lambda: None)
    session = tmp_path / "artifacts" / "session"

    monkeypatch.setattr(cli, "_resolve_evaluation_source", lambda _args: (_revision(), None))
    monkeypatch.setattr(
        cli,
        "_resolve_runtime_preflight_or_report",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(cli, "build_review_workspace", lambda *_args, **_kwargs: workspace)
    monkeypatch.setattr(cli, "resolve_commit", lambda _repo, value: value)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(workspace=SimpleNamespace()))

    def run_review(args):
        events.append("review")
        assert args.pr is None
        assert args.base_branch == "b" * 40
        args._result_callback(
            SimpleNamespace(
                persist=SimpleNamespace(session_dir=session),
                verdict=SimpleNamespace(verdict="REJECT"),
                exit_code=EXIT_FINDINGS,
            )
        )
        return EXIT_FINDINGS

    def push(_revision, _source, _target, *, repo_path):
        events.append("push")
        assert repo_path == tmp_path

    def create(_revision, _source, _target, _review):
        events.append("create")
        return DraftPullRequest(
            123,
            "https://dev.azure.com/org/project/_git/repo/pullrequest/123",
        )

    def publish(session_dir, options):
        events.append("publish")
        assert session_dir == str(session)
        assert options.pr_override.endswith("/pullrequest/123")
        assert options.min_severity is None
        return EXIT_CLEAN

    monkeypatch.setattr(cli, "_cmd_review", run_review)
    monkeypatch.setattr(cli, "push_exact_refs", push)
    monkeypatch.setattr(cli, "create_draft_pull_request", create)
    monkeypatch.setattr(cli, "_run_publish_for_session", publish)

    result = cli._cmd_eval_pr(_args(tmp_path))
    assert result == EXIT_CLEAN
    assert events == ["review", "push", "create", "publish"]
    assert events == ["review", "push", "create", "publish"]


def test_pr_description_is_not_carried_into_evaluation_revision(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "fetch_pr_metadata",
        lambda _pr: SimpleNamespace(
            title="Finding the reviewers must discover",
            description="SECRET BIASING DESCRIPTION",
            source_commit_sha="s" * 40,
            target_commit_sha="b" * 40,
        ),
    )

    revision, local_repo = cli._resolve_evaluation_source(
        SimpleNamespace(
            from_pr="https://dev.azure.com/org/project/_git/repo/pullrequest/42",
            from_merge_commit=None,
            from_commit=None,
            repo=None,
            at_commit=None,
            base=None,
        )
    )

    assert local_repo is None
    assert revision.source_label == "PR 42"
    assert "SECRET" not in repr(revision)


def test_eval_pr_review_failure_has_no_remote_side_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    workspace = SimpleNamespace(path=tmp_path, cleanup=lambda: None)
    monkeypatch.setattr(cli, "_resolve_evaluation_source", lambda _args: (_revision(), None))
    monkeypatch.setattr(
        cli,
        "_resolve_runtime_preflight_or_report",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(cli, "build_review_workspace", lambda *_args, **_kwargs: workspace)
    monkeypatch.setattr(cli, "resolve_commit", lambda _repo, value: value)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(workspace=SimpleNamespace()))

    def failed_review(args):
        events.append("review")
        args._result_callback(
            SimpleNamespace(
                persist=SimpleNamespace(session_dir=tmp_path / "session"),
                verdict=SimpleNamespace(verdict="UNKNOWN"),
                exit_code=EXIT_ERROR,
            )
        )
        return EXIT_ERROR

    monkeypatch.setattr(cli, "_cmd_review", failed_review)
    monkeypatch.setattr(
        cli,
        "push_exact_refs",
        lambda *_args, **_kwargs: events.append("push"),
    )

    assert cli._cmd_eval_pr(_args(tmp_path)) == EXIT_ERROR
    assert events == ["review"]


def test_eval_pr_capability_failure_stops_before_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "_resolve_evaluation_source", lambda _args: (_revision(), None))
    monkeypatch.setattr(cli, "_resolve_runtime_preflight_or_report", lambda *_args: None)
    monkeypatch.setattr(
        cli,
        "build_review_workspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("capability failure must stop before workspace creation")
        ),
    )

    assert cli._cmd_eval_pr(_args(tmp_path)) == EXIT_ERROR


def test_remote_input_is_canonicalized_before_clone() -> None:
    repository = cli._evaluation_repo_from_remote(
        "https://evil.example/dev.azure.com/org/project/_git/repo"
    )

    assert repository.remote_url == "https://dev.azure.com/org/project/_git/repo"


def test_eval_pr_parser_requires_repo_for_commit_at_execution() -> None:
    args = cli.build_parser().parse_args(["eval-pr", "--from-commit", "abc", "--name", "test"])

    assert cli._cmd_eval_pr(args) != EXIT_CLEAN


def test_pr_checkpoint_keeps_pr_target_as_cumulative_base(monkeypatch) -> None:
    checkpoint = "35ca335cf8bdde7b61b3063f7b2b3b7ceacf4694"
    target = "0c46ad58baa9c7791fdfb799690bdb182bc20100"
    source_tip = "4d0fc7459520da36257ed5d2dada39970ae82655"
    monkeypatch.setattr(
        cli,
        "fetch_pr_metadata",
        lambda _pr: SimpleNamespace(
            source_commit_sha=source_tip,
            target_commit_sha=target,
        ),
    )
    args = SimpleNamespace(
        from_pr="https://dev.azure.com/org/project/_git/repo/pullrequest/42",
        from_merge_commit=None,
        from_commit=None,
        repo=None,
        at_commit=checkpoint,
        base=None,
    )

    revision, _local_repo = cli._resolve_evaluation_source(args)

    assert revision.source_sha == checkpoint
    assert revision.base_sha == target
    assert revision.base_sha != "15281925b73a11bf5277579ebfb7f5c4d5ff0be9"
    assert revision.source_tip_sha == source_tip
    assert revision.mode == "pr-checkpoint"


def test_merge_commit_resolves_recorded_pr_pair(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        cli,
        "detect_repos",
        lambda _path: [
            SimpleNamespace(
                root_path=str(repo),
                remote_url="https://dev.azure.com/org/project/_git/repo",
            )
        ],
    )
    monkeypatch.setattr(
        cli,
        "fetch_pr_by_merge_commit",
        lambda _repository, _sha: (
            SimpleNamespace(pr_id=55),
            SimpleNamespace(source_commit_sha="source", target_commit_sha="target"),
        ),
    )
    args = SimpleNamespace(
        from_pr=None,
        from_merge_commit="merge",
        from_commit=None,
        repo=str(repo),
        at_commit=None,
        base=None,
    )

    revision, local_repo = cli._resolve_evaluation_source(args)

    assert local_repo == str(repo)
    assert revision.source_sha == "source"
    assert revision.base_sha == "target"
    assert revision.resolved_pr_id == 55
    assert revision.mode == "merge-commit"
