from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable import evaluation
from roundtable.evaluation import (
    DraftPullRequest,
    EvaluationError,
    EvaluationRepository,
    EvaluationReview,
    EvaluationRevision,
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


def test_run_evaluation_reviews_before_any_remote_mutation(tmp_path: Path) -> None:
    events: list[str] = []
    review = EvaluationReview(tmp_path / "session", "REJECT", 2)

    outcome = evaluation.run_evaluation(
        _revision(),
        name="finding-reproduction",
        review=lambda _revision: events.append("review") or review,
        push=lambda _revision, _source, _target: events.append("push"),
        create=lambda _revision, _source, _target, _review: (
            events.append("create")
            or DraftPullRequest(123, "https://dev.azure.com/org/project/_git/repo/pullrequest/123")
        ),
        publish=lambda _session, _url: events.append("publish") or 0,
    )

    assert events == ["review", "push", "create", "publish"]
    assert outcome.complete
    assert outcome.source_ref == "refs/heads/roundtable/eval/finding-reproduction/source"
    assert outcome.target_ref == "refs/heads/roundtable/eval/finding-reproduction/base"


def test_run_evaluation_preserves_draft_when_publish_fails(tmp_path: Path) -> None:
    outcome = evaluation.run_evaluation(
        _revision(),
        name="partial",
        review=lambda _revision: EvaluationReview(tmp_path / "session", "REJECT", 2),
        push=lambda *_args: None,
        create=lambda *_args: DraftPullRequest(
            123,
            "https://dev.azure.com/org/project/_git/repo/pullrequest/123",
        ),
        publish=lambda *_args: 1,
    )

    assert not outcome.complete
    assert outcome.draft.pr_id == 123
    assert outcome.publish_exit_code == 1


def _commit(repo: Path, text: str) -> str:
    (repo / "file.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", text], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _checkpoint_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    base = _commit(repo, "base")
    checkpoint = _commit(repo, "checkpoint")
    tip = _commit(repo, "tip")
    return repo, base, checkpoint, tip


def test_validate_pr_checkpoint_accepts_cumulative_source_history(tmp_path: Path) -> None:
    repo, base, checkpoint, tip = _checkpoint_repo(tmp_path)

    evaluation.validate_pr_checkpoint(
        repo,
        checkpoint_sha=checkpoint,
        source_tip_sha=tip,
        target_sha=base,
    )


def test_validate_pr_checkpoint_rejects_branch_point(tmp_path: Path) -> None:
    repo, base, _checkpoint, tip = _checkpoint_repo(tmp_path)

    with pytest.raises(EvaluationError, match="strictly after"):
        evaluation.validate_pr_checkpoint(
            repo,
            checkpoint_sha=base,
            source_tip_sha=tip,
            target_sha=base,
        )


def test_validate_pr_checkpoint_rejects_commit_outside_source_history(tmp_path: Path) -> None:
    repo, base, _checkpoint, tip = _checkpoint_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "other", base], cwd=repo, check=True)
    outside = _commit(repo, "outside")

    with pytest.raises(EvaluationError, match="not an ancestor"):
        evaluation.validate_pr_checkpoint(
            repo,
            checkpoint_sha=outside,
            source_tip_sha=tip,
            target_sha=base,
        )


def test_validate_checkpoint_before_later_target_merge(tmp_path: Path) -> None:
    repo = tmp_path / "merged-target"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    _commit(repo, "base")
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    checkpoint = _commit(repo, "checkpoint")
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True)
    (repo / "target.txt").write_text("target advanced", encoding="utf-8")
    subprocess.run(["git", "add", "target.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "target advanced"], cwd=repo, check=True)
    target = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "feature"], cwd=repo, check=True)
    subprocess.run(["git", "merge", "-q", "--no-edit", "master"], cwd=repo, check=True)
    source_tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    evaluation.validate_pr_checkpoint(
        repo,
        checkpoint_sha=checkpoint,
        source_tip_sha=source_tip,
        target_sha=target,
    )


def test_create_pull_request_is_draft_without_completion_or_work_items(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"pullRequestId": 123}'

    def open_request(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(evaluation, "ado_auth_header", lambda: "Basic token")
    monkeypatch.setattr(evaluation.urllib.request, "urlopen", open_request)

    draft = evaluation.create_draft_pull_request(
        _revision(),
        "refs/heads/roundtable/eval/test/source",
        "refs/heads/roundtable/eval/test/base",
        EvaluationReview(tmp_path / "session", "REJECT", 2),
    )

    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["isDraft"] is True
    assert payload["sourceRefName"].endswith("/source")
    assert payload["targetRefName"].endswith("/base")
    assert "completionOptions" not in payload
    assert "workItemRefs" not in payload
    assert draft.pr_id == 123


def test_invalid_evaluation_name_is_rejected() -> None:
    with pytest.raises(EvaluationError, match="letter or digit"):
        evaluation.evaluation_refs(" / ")


def test_push_uses_exact_shas_without_force(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        evaluation,
        "run_git",
        lambda args, *_pos, **_kwargs: calls.append(args) or SimpleNamespace(),
    )

    evaluation.push_exact_refs(
        _revision(),
        "refs/heads/roundtable/eval/test/source",
        "refs/heads/roundtable/eval/test/base",
        repo_path=tmp_path,
    )

    assert calls == [
        [
            "push",
            "origin",
            f"{'b' * 40}:refs/heads/roundtable/eval/test/base",
        ],
        [
            "push",
            "origin",
            f"{'s' * 40}:refs/heads/roundtable/eval/test/source",
        ],
    ]
    assert all("--force" not in call for call in calls)
