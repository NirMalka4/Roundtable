from __future__ import annotations

import json

import pytest

import roundtable.adoption as adoption
from roundtable import cli
from roundtable.adoption import ReviewRecord


def test_adoption_query_cli_emits_json_contract(tmp_path, capsys) -> None:
    record = ReviewRecord(
        session_id="s1",
        recorded_at="2026-09-09T00:00:00Z",
        organization="o",
        project="p",
        repository="Repo",
        pull_request_id=1,
        source_sha="s",
        base_sha="b",
        tool_version="1.0.0",
        configuration_name="buddies",
        configuration_kind="shipped",
        graph_config_sha="abc",
        installation_source="feed",
        verdict="APPROVE",
        findings_available=True,
        counts=(),
        findings=(),
    )
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record.to_dict()) + "\n", encoding="utf-8")
    args = cli.build_parser().parse_args(
        [
            "adoption",
            "query",
            "--input",
            str(source),
            "--metric",
            "reviews",
            "--group-by",
            "repository",
            "--format",
            "json",
        ]
    )

    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["groups"] == [{"key": "Repo", "value": 1}]


def test_adoption_parser_exposes_collect_report_and_retry() -> None:
    parser = cli.build_parser()

    collect = parser.parse_args(
        [
            "adoption",
            "collect",
            "--org",
            "o",
            "--project",
            "p",
            "--repository",
            "Repo",
            "--pr",
            "42",
            "--quiet",
        ]
    )
    query = parser.parse_args(
        [
            "adoption",
            "query",
            "--input",
            "records.jsonl",
            "--metric",
            "prs",
            "--group-by",
            "installationSource",
        ]
    )
    report = parser.parse_args(
        [
            "adoption",
            "report",
            "--input",
            "records.jsonl",
            "--out",
            "adoption.html",
        ]
    )
    retry = parser.parse_args(["adoption", "retry", "session"])

    assert collect.func is cli._cmd_adoption_collect
    assert collect.pr == 42
    assert collect.quiet is True
    assert query.func is cli._cmd_adoption_query
    assert report.func is cli._cmd_adoption_report
    assert retry.func is cli._cmd_adoption_retry


def test_adoption_collect_pr_requires_repository(capsys) -> None:
    args = cli.build_parser().parse_args(
        ["adoption", "collect", "--org", "o", "--project", "p", "--pr", "42"]
    )

    assert args.func(args) == cli.EXIT_BAD_ARGS
    assert capsys.readouterr().err == "[adoption collect] --pr requires --repository\n"


def test_adoption_collect_pr_must_be_numeric() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "adoption",
                "collect",
                "--org",
                "o",
                "--project",
                "p",
                "--repository",
                "Repo",
                "--pr",
                "not-a-number",
            ]
        )


def test_adoption_collect_cli_passes_direct_and_quiet_options(monkeypatch) -> None:
    captured = {}
    instance = object()
    monkeypatch.setattr(adoption, "AdoptionCollector", lambda org, project: instance)

    def collect_to(collector, output, **options):
        captured.update(collector=collector, output=output, **options)

    monkeypatch.setattr(adoption, "collect_to", collect_to)
    args = cli.build_parser().parse_args(
        [
            "adoption",
            "collect",
            "--org",
            "o",
            "--project",
            "p",
            "--repository",
            "Repo",
            "--pr",
            "42",
            "--out",
            "records.jsonl",
            "--quiet",
        ]
    )

    assert args.func(args) == cli.EXIT_CLEAN
    assert captured == {
        "collector": instance,
        "output": "records.jsonl",
        "repository": "Repo",
        "pull_request_id": 42,
        "since": None,
        "until": None,
        "quiet": True,
    }
