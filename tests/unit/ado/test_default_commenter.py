"""The commenter seam must not change what a config that never asked for it renders.

``DefaultCommenter`` was introduced so a bundle can supply its own PR wording
(:mod:`roundtable.delivery.commenter`). Everything that did not opt in — InspectorX —
must keep rendering byte-identically, so these pin the delegation rather than the
template: the seam is a redirect, not a rewrite.

Also pins that the publish severity floor offers THIS config's severity vocabulary.
A hardcoded scale would accept a level ``min_severity_view`` cannot rank, which
fails open and silently publishes everything.
"""

from __future__ import annotations

from roundtable import cli
from roundtable.ado.anchor import AnchorDecision
from roundtable.ado.comment_format import (
    DefaultCommenter,
    format_executive_summary,
    render_comment,
)
from roundtable.ado.publish import PublishableFinding
from roundtable.bundle import resolve_bundle
from roundtable.graph import Configuration, get_configuration
from roundtable.types.severity import severity_rank_map

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def _finding(fid: str = "F-1", severity: str = "high") -> PublishableFinding:
    return PublishableFinding(
        id=fid,
        title="A finding that must render the same either way",
        description="Body text.",
        severity=severity,
        file_path="src/app.py",
        start_line=10,
        end_line=12,
        location_index=1,
        total_locations=1,
        additional_locations=(),
        stable_hash="deadbeefcafe",
        category="blocking",
        judge_category="correctness",
        source_agents=("architect",),
    )


def test_default_commenter_thread_is_the_engine_template_verbatim() -> None:
    finding = _finding()
    decision = AnchorDecision("inline", "src/app.py", 10, 12, None)
    assert DefaultCommenter(CONFIGURATION).render_thread(
        finding, decision, session_id="s1"
    ) == render_comment(finding, decision, session_id="s1")


def test_default_commenter_summary_is_the_engine_template_verbatim() -> None:
    findings = [_finding("F-1"), _finding("F-2", severity="low")]
    assert DefaultCommenter(CONFIGURATION).render_summary(
        "s1", "REJECT", findings, published=findings[:1]
    ) == format_executive_summary("s1", "REJECT", findings, CONFIGURATION)


def test_default_commenter_summary_ignores_the_published_view() -> None:
    """The executive summary is built over the FULL plan on purpose: a below-floor
    finding gets no thread but must still appear in the table."""
    findings = [_finding("F-1"), _finding("F-2", severity="low")]
    commenter = DefaultCommenter(CONFIGURATION)
    assert commenter.render_summary("s1", "REJECT", findings, published=()) == (
        commenter.render_summary("s1", "REJECT", findings, published=findings)
    )


def test_publish_severity_floor_offers_this_configs_vocabulary(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_configuration", lambda: CONFIGURATION)
    parser = cli.build_parser()
    action = next(
        a
        for a in parser._subparsers._group_actions[0].choices["publish"]._actions
        if a.dest == "min_severity"
    )
    assert list(action.choices) == list(severity_rank_map(CONFIGURATION))
    assert action.default == "medium"


def _severity_default(parser, command: str, destination: str) -> str | None:
    subparser = parser._subparsers._group_actions[0].choices[command]
    return next(action.default for action in subparser._actions if action.dest == destination)


def test_buddies_owns_low_as_both_publish_defaults(monkeypatch) -> None:
    buddies = get_configuration(resolve_bundle("buddies"))
    monkeypatch.setattr(cli, "get_configuration", lambda: buddies)
    parser = cli.build_parser()

    assert _severity_default(parser, "publish", "min_severity") == "low"
    assert _severity_default(parser, "review", "publish_min_severity") == "low"
    assert (
        parser.parse_args(["publish", "session", "--min-severity", "high"]).min_severity == "high"
    )
    assert (
        parser.parse_args(["review", "123", "--publish-min-severity", "high"]).publish_min_severity
        == "high"
    )


def test_null_policy_defaults_both_publish_paths_to_all(monkeypatch, tmp_path) -> None:
    configuration = Configuration.from_document(
        {
            "name": "unfiltered",
            "domain_values": {"values": {"severity": ["notice", "danger"]}},
            "publishing": {"default_min_severity": None},
            "agents": [{"key": "Source", "kind": "source", "emoji": "S"}],
        },
        root=tmp_path,
    )
    monkeypatch.setattr(cli, "get_configuration", lambda: configuration)
    parser = cli.build_parser()

    assert _severity_default(parser, "publish", "min_severity") is None
    assert _severity_default(parser, "review", "publish_min_severity") is None
    subparsers = parser._subparsers._group_actions[0].choices
    assert "default: all severities" in subparsers["publish"].format_help()
    assert "default: all severities" in subparsers["review"].format_help()
