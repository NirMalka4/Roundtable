"""The `eval-cleanup` verb's surface, where a mistake is irreversible.

`--all` can remove every evaluation branch in a repository, so what the command *refuses*
to do matters as much as what it does. These tests pin the argument contract and the
reported plan; the reclamation logic itself is covered in
`tests/unit/evaluation/test_teardown.py`.
"""

from __future__ import annotations

import json

import pytest

from roundtable import cli
from roundtable.decision import EXIT_CLEAN
from roundtable.evaluation import EvaluationRepository
from roundtable.evaluation import evaluation_refs as cli_evaluation_refs


def _repository() -> EvaluationRepository:
    return EvaluationRepository(
        org="org",
        project="project",
        name="repo",
        host="dev.azure.com",
        remote_url="https://dev.azure.com/org/project/_git/repo",
    )


def _stub_ado(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    monkeypatch.setattr(cli, "_evaluation_repository_input", lambda _arg: (_repository(), None))
    monkeypatch.setattr(
        cli,
        "list_evaluation_refs",
        lambda _repo: (
            "refs/heads/roundtable/eval/alpha/source",
            "refs/heads/roundtable/eval/alpha/base",
            "refs/heads/roundtable/eval/beta/source",
            "refs/heads/roundtable/eval/beta/base",
        ),
    )
    monkeypatch.setattr(cli, "find_evaluation_pull_requests", lambda _repo, _ref: (99,))
    monkeypatch.setattr(
        cli, "abandon_pull_request", lambda _repo, pr_id: calls.append(f"abandon:{pr_id}")
    )
    monkeypatch.setattr(cli, "delete_evaluation_ref", lambda _repo, ref: calls.append(ref) or True)


def _run(**overrides: object) -> int:
    fields: dict[str, object] = {
        "repo": "/repo",
        "name": None,
        "all": False,
        "dry_run": False,
    }
    fields.update(overrides)
    return cli._cmd_eval_cleanup(argparse_namespace(**fields))


def argparse_namespace(**kwargs: object):
    from types import SimpleNamespace

    return SimpleNamespace(**kwargs)


def test_requires_a_scope_so_a_bare_invocation_cannot_sweep(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--name` and `--all` are mutually exclusive and one is required. An argument-less
    invocation must never be read as "reclaim everything"."""
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["eval-cleanup", "--repo", "/repo"])

    parsed = parser.parse_args(["eval-cleanup", "--repo", "/repo", "--all"])
    assert parsed.all is True and parsed.name is None


def test_name_and_all_cannot_be_combined() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["eval-cleanup", "--repo", "/repo", "--name", "x", "--all"])


def test_sweep_reclaims_every_evaluation_in_the_repository(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    _stub_ado(monkeypatch, calls)

    code = _run(all=True)
    report = json.loads(capsys.readouterr().out)

    assert code == EXIT_CLEAN
    assert report["evaluations"] == ["alpha", "beta"]
    assert len(report["deletedRefs"]) == 4
    assert calls[0] == "abandon:99"


def test_dry_run_changes_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    _stub_ado(monkeypatch, calls)

    code = _run(all=True, dry_run=True)
    report = json.loads(capsys.readouterr().out)

    assert code == EXIT_CLEAN
    assert calls == []
    assert report["status"] == "planned"
    assert len(report["deletedRefs"]) == 4


def test_a_foreign_ref_from_the_server_contributes_no_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sweep's input is whatever the server's filter returned. If that filter ever
    widens, the refs it hands back must still produce no work — the command reclaims
    nothing rather than deleting a branch it was never meant to see."""
    calls: list[str] = []
    _stub_ado(monkeypatch, calls)
    monkeypatch.setattr(
        cli,
        "list_evaluation_refs",
        lambda _repo: ("refs/heads/main", "refs/heads/release/2026-08"),
    )

    code = _run(all=True)
    report = json.loads(capsys.readouterr().out)

    assert code == EXIT_CLEAN
    assert report["evaluations"] == []
    assert report["deletedRefs"] == []
    assert calls == []


def test_an_evaluation_name_can_never_escape_the_namespace() -> None:
    """Names reach the ref builder from a sweep or the command line. `evaluation_refs`
    sanitizes them, so a traversal-shaped name lands inside the namespace rather than
    above it — and the guard would refuse it if that ever stopped being true."""
    source_ref, _ = cli_evaluation_refs("../../../main")

    assert source_ref.startswith("refs/heads/roundtable/eval/")
    assert ".." not in source_ref
