"""A finished review must say where it landed, in a form the terminal can open.

Covers the completion output: clickable ``file://`` links to the artifacts dir, the
verdict and the HTML report, and the rule that rendering the report can never change
the review's outcome.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable import cli


def _persist(tmp_path: Path) -> SimpleNamespace:
    session = tmp_path / "session"
    session.mkdir()
    return SimpleNamespace(session_dir=session, report_path=session / "verdict.md")


def test_completion_links_the_artifacts_dir_verdict_and_report(tmp_path, monkeypatch, capsys):
    persist = _persist(tmp_path)
    monkeypatch.setattr(cli, "write_report_html", lambda s, out=None: s / "report.html")

    cli._print_session_links(persist)

    err = capsys.readouterr().err
    assert cli._link(persist.session_dir) in err
    assert cli._link(persist.report_path) in err
    assert cli._link(persist.session_dir / "report.html") in err
    assert err.count("file:///") == 3


def test_a_report_that_cannot_render_does_not_lose_the_other_links(tmp_path, monkeypatch, capsys):
    """The report is a convenience; the artifacts are the result. A render failure
    must degrade to a note, never swallow the paths or raise into the exit code."""
    persist = _persist(tmp_path)

    def _boom(_session, out=None):
        raise RuntimeError("no usage-summary.json")

    monkeypatch.setattr(cli, "write_report_html", _boom)

    cli._print_session_links(persist)

    err = capsys.readouterr().err
    assert cli._link(persist.session_dir) in err
    assert "report.html not rendered: no usage-summary.json" in err


def test_a_link_is_a_uri_a_terminal_can_open(tmp_path):
    assert cli._link(tmp_path).startswith("file:///")


def test_a_path_with_no_uri_form_degrades_to_the_plain_path():
    class _NoUri(type(Path())):
        def as_uri(self):
            raise ValueError("relative path can't be expressed as a file URI")

    assert cli._link(_NoUri("rel")) == str(_NoUri("rel"))


def test_the_review_and_the_report_command_share_one_renderer(monkeypatch, tmp_path):
    """Two renderers would drift; a review's report must be the same artifact
    ``roundtable report`` produces."""
    calls: list[Path] = []
    monkeypatch.setattr(cli, "write_report_html", lambda s, out=None: (calls.append(s), s)[1])

    cli._cmd_report(SimpleNamespace(session=str(tmp_path), output=None, open=False))
    cli._print_session_links(_persist(tmp_path))

    assert len(calls) == 2


def test_report_help_warns_that_generated_content_must_be_redacted():
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, cli.argparse._SubParsersAction)
    )

    help_text = " ".join(subparsers.choices["report"].format_help().split())

    for content in (
        "local paths",
        "repository or PR URLs",
        "branches",
        "commit identifiers",
        "prompts",
        "full agent responses",
    ):
        assert content in help_text
    assert "Inspect and redact them before sharing." in help_text


def test_view_and_report_resolve_safe_session_alias_from_any_cwd(monkeypatch, tmp_path, capsys):
    artifacts_root = tmp_path / "configured-artifacts"
    session = (
        artifacts_root
        / "repo"
        / "session_20260915184352_pr-12345-private-user-protection-lint-config"
    )
    session.mkdir(parents=True)
    (session / "trace.json").write_text('{"sessionId":"private-local-name"}', encoding="utf-8")
    monkeypatch.setenv("ROUNDTABLE_ARTIFACTS_DIR", str(artifacts_root))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    from roundtable.settings.workspace import reset_settings_cache

    reset_settings_cache()
    rendered: list[Path] = []
    monkeypatch.setattr(
        cli,
        "write_report_html",
        lambda s, out=None: (rendered.append(s), s / "report.html")[1],
    )
    reference = "repo/session_20260915184352_pr-12345"

    assert cli._cmd_view(SimpleNamespace(session=reference)) == 0
    assert cli._cmd_report(SimpleNamespace(session=reference, output=None, open=False)) == 0

    output = capsys.readouterr()
    assert "session: private-local-name" in output.out
    assert str(session / "trace.json") not in output.err
    assert rendered == [session]
    assert os.getcwd() == str(elsewhere)
    reset_settings_cache()


def test_existing_relative_session_path_wins_over_prefix_matches(monkeypatch, tmp_path):
    artifacts_root = tmp_path / "artifacts"
    exact = artifacts_root / "repo" / "session_20260915184352"
    exact.mkdir(parents=True)
    (artifacts_root / "repo" / "session_20260915184352_private-user-main").mkdir()
    monkeypatch.setenv("ROUNDTABLE_ARTIFACTS_DIR", str(artifacts_root))
    from roundtable.settings.workspace import reset_settings_cache, resolve_artifact_path

    reset_settings_cache()

    assert resolve_artifact_path("repo/session_20260915184352") == exact
    reset_settings_cache()


def test_safe_non_pr_alias_resolves_agent_path(monkeypatch, tmp_path):
    artifacts_root = tmp_path / "artifacts"
    response = (
        artifacts_root
        / "repo"
        / "session_20260915184352_private-user-protection-lint-config"
        / "agents"
        / "reviewer"
        / "response.md"
    )
    response.parent.mkdir(parents=True)
    response.touch()
    monkeypatch.setenv("ROUNDTABLE_ARTIFACTS_DIR", str(artifacts_root))
    from roundtable.settings.workspace import reset_settings_cache, resolve_artifact_path

    reset_settings_cache()
    resolved = resolve_artifact_path("repo/session_20260915184352/agents/reviewer/response.md")

    assert resolved == response
    reset_settings_cache()


@pytest.mark.parametrize("command", ["view", "report"])
@pytest.mark.parametrize("match_count", [0, 2])
def test_cli_rejects_missing_or_ambiguous_safe_alias(
    monkeypatch, tmp_path, capsys, command, match_count
):
    artifacts_root = tmp_path / "artifacts"
    repo = artifacts_root / "repo"
    repo.mkdir(parents=True)
    for branch in ("private-user-main", "user-other-feature")[:match_count]:
        (repo / f"session_20260915184352_{branch}").mkdir()
    monkeypatch.setenv("ROUNDTABLE_ARTIFACTS_DIR", str(artifacts_root))
    from roundtable.settings.workspace import reset_settings_cache

    reset_settings_cache()
    args = SimpleNamespace(
        session="repo/session_20260915184352",
        output=None,
        open=False,
    )
    result = cli._cmd_view(args) if command == "view" else cli._cmd_report(args)

    assert result == cli.EXIT_BAD_ARGS
    expected = "not found" if match_count == 0 else "ambiguous (2 matches)"
    assert expected in capsys.readouterr().err
    reset_settings_cache()
