"""Integration tests for ado.publish_flow — the end-to-end publish pipeline.

Drives the flow with fake network touchpoints (no ADO): a fake poster and a
fake PR-iterations fetcher, plus injected ``subject`` provenance. Covers dry-run,
live posting, reviewed-iteration anchoring + the general-thread downgrade with
enumerated warnings, min-severity filtering, the --pr identity check (E10), and
partial-failure reporting (E5).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from roundtable.ado.comment_format import publication_footer
from roundtable.ado.pr_iterations import IterationRef
from roundtable.ado.pr_threads import PostResult, PreparedComment
from roundtable.ado.publish import PublishableFinding
from roundtable.ado.publish_flow import (
    PublishOptions,
    resolve_publish_pr,
)
from roundtable.ado.publish_flow import (
    run_publish as _run_publish,
)
from roundtable.ado.sink import _recorded_artifacts_root
from roundtable.bundle import resolve_bundle
from roundtable.delivery.publishable import PublishableResult
from roundtable.graph import DomainValues, get_configuration
from roundtable.inputs.pr_reference import PrReference
from roundtable.review.trace_overlay import OverlayKey as K

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def run_publish(result, subject, diff_stat, options, **kwargs):
    session_dir = kwargs.get("session_dir", "")
    kwargs.setdefault("artifacts_root", Path(session_dir).parent)
    return _run_publish(result, subject, diff_stat, options, CONFIGURATION, **kwargs)


def _iters(sha="sha_reviewed", ordinal=3):
    """A fake iteration_fetcher whose iteration list matches the reviewed SHA."""
    return lambda pr: [IterationRef(ordinal=ordinal, source_commit_sha=sha)]


def _changes(mapping=None):
    """A fake changes_fetcher returning a path→changeTrackingId map."""
    m = mapping if mapping is not None else {"svc/x.cs": 17}
    return lambda pr, iteration: dict(m)


def _source_ref(branch):
    """A fake pr_source_ref_fetcher returning a fixed branch (or None)."""
    return lambda pr: branch


def _finding(**kw) -> PublishableFinding:
    base = {
        "id": "F-1",
        "title": "t",
        "description": "d",
        "severity": "High",
        "file_path": "svc/x.cs",
        "start_line": 10,
        "end_line": 12,
        "location_index": 0,
        "total_locations": 1,
        "additional_locations": (),
        "stable_hash": "aaa111",
        "category": "blocking",
        "judge_category": None,
        "source_agents": ("Analyst",),
    }
    base.update(kw)
    return PublishableFinding(**base)


def _plan(findings, *, commenter=None) -> PublishableResult:
    """What ``run_publish`` actually receives in production: the neutral projection.

    ``PublishPlan`` is an internal detail of the InspectorX projector, so building
    one here would test a shape the flow never sees.
    """
    return PublishableResult(
        all_findings=list(findings),
        session_id="sess_1",
        verdict="REJECT",
        commenter=commenter,
    )


def _subject(**kw) -> dict:
    subj = {
        K.SUBJECT_MODE: "pr",
        K.SUBJECT_REPO: "r",
        K.SUBJECT_REMOTE_URL: "https://dev.azure.com/o/p/_git/r",
        K.SUBJECT_PR_ID: 42,
        K.SUBJECT_SOURCE_SHA: "sha_reviewed",
    }
    subj.update(kw)
    return subj


def _diff_stat(changed_files=("svc/x.cs",)) -> dict:
    return {"changedFiles": list(changed_files)}


class FakePoster:
    def __init__(self, existing=None, results=None):
        self._existing = set(existing or [])
        self._results = results
        self.published: list[list[PreparedComment]] = []

    def existing_watermark_hashes(self):
        return set(self._existing)

    def publish(self, comments, *, existing_hashes=None):
        self.published.append(list(comments))
        if self._results is not None:
            return self._results(comments)
        existing = set(existing_hashes or self._existing)
        return [
            PostResult(
                c.finding_id,
                "skipped" if c.stable_hash in existing else "posted",
                thread_id=1,
            )
            for c in comments
        ]

    def publish_summary(
        self,
        comment,
        *,
        summary_identity,
        legacy_summary_identities=(),
        force_recreate=False,
    ):
        self.published.append([comment])
        if self._results is not None:
            return self._results([comment])
        status = "skipped" if comment.stable_hash in self._existing else "posted"
        return [PostResult(comment.finding_id, status, thread_id=1)]


# ── dry-run ──────────────────────────────────────────────────────────────────
def test_dry_run_writes_threads_json(tmp_path):
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
    )
    assert report.dry_run_path == str(tmp_path / "threads.json")
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert payload["threads"][0]["findingId"] == "F-1"
    assert payload["threads"][0]["kind"] == "inline"
    assert payload["threads"][0]["iteration"] == 3
    assert payload["threads"][0]["changeTrackingId"] is None  # no changes_fetcher injected
    assert "summary" in payload
    assert payload["anchorWarnings"] == []
    assert report.results == []  # nothing posted in dry-run


def test_dry_run_honors_out_override(tmp_path):
    out = tmp_path / "custom.json"
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(dry_run=True, out_path=str(out)),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
    )
    assert report.dry_run_path == str(out) and out.exists()


# ── anchoring: general fallback when file not in changed set ─────────────────
def test_file_not_in_changed_set_downgrades_to_general(tmp_path):
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(["other.cs"]),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
    )
    assert report.inline == 0 and report.general == 1
    assert any("changed set" in w for w in report.anchor_warnings)


# ── live posting ─────────────────────────────────────────────────────────────
def test_live_posts_inline_then_closed_summary():
    plan = _plan([_finding()])
    poster = FakePoster()
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
    )
    assert report.ok and report.posted == 1
    assert report.inline == 1 and not report.anchor_warnings
    # ADO renders newest first, so the summary API call must be last.
    assert len(poster.published) == 2
    assert poster.published[0][0].iteration == 3
    assert poster.published[1][0].finding_id == "__summary__"
    assert poster.published[1][0].status == 4
    raw_markdown = poster.published[1][0].content
    assert "<!-- Roundtable:__summary__:x:H=" in raw_markdown
    assert "sess_1" not in raw_markdown


def test_buddies_api_calls_produce_summary_then_descending_severity_in_ui(tmp_path):
    buddies = get_configuration(resolve_bundle("buddies"))
    plan = _plan(
        [
            _finding(id="M-1", severity="medium", category="blocking"),
            _finding(id="H-1", severity="high", category="non_blocking"),
            _finding(id="H-2", severity="high", category="blocking"),
            _finding(id="L-1", severity="low", category="blocking"),
        ]
    )
    poster = FakePoster()

    _run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        buddies,
        session_dir=str(tmp_path),
        artifacts_root=tmp_path.parent,
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
    )

    assert [[comment.finding_id for comment in batch] for batch in poster.published] == [
        ["L-1", "M-1", "H-2", "H-1"],
        ["__summary__"],
    ]


def test_custom_severity_order_controls_publication_stably(tmp_path):
    custom = replace(
        CONFIGURATION,
        name="custom",
        product_name="Custom",
        domain_values=DomainValues(
            (("severity", ("minor", "major", "urgent")), ("verdict", ("PASS", "FAIL")))
        ),
    )
    plan = _plan(
        [
            _finding(id="A", severity="major", category="non_blocking"),
            _finding(id="B", severity="urgent", category="blocking"),
            _finding(id="C", severity="major", category="blocking"),
        ]
    )
    poster = FakePoster()

    _run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        custom,
        session_dir=str(tmp_path),
        artifacts_root=tmp_path.parent,
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
    )

    assert [comment.finding_id for comment in poster.published[0]] == ["C", "A", "B"]


@pytest.mark.parametrize("root_parts", [("Users", "private-user"), ("home", "private-user")])
@pytest.mark.parametrize(
    ("session_name", "safe_name"),
    [
        (
            "session_20260915184352_pr-12345-private-user-protection-lint-config",
            "session_20260915184352_pr-12345",
        ),
        (
            "session_20260915184352_private-user-protection-lint-config",
            "session_20260915184352",
        ),
    ],
)
def test_threads_have_privacy_safe_relative_artifact_footers(
    tmp_path, root_parts, session_name, safe_name
):
    buddies = get_configuration(resolve_bundle("buddies"))
    artifacts_root = tmp_path.joinpath(*root_parts, "Roundtable Artifacts")
    session_dir = artifacts_root / "fe-msecscc" / session_name
    poster = FakePoster()

    _run_publish(
        _plan([_finding(source_agents=("countercase", "north_star"))]),
        _subject(),
        _diff_stat(),
        PublishOptions(),
        buddies,
        session_dir=str(session_dir),
        artifacts_root=artifacts_root,
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
    )

    finding, summary = [batch[0] for batch in poster.published]
    response_lines = [
        line
        for line in finding.content.splitlines()
        if " full response (relative to Roundtable artifacts directory): " in line
    ]
    assert response_lines == [
        "- Counter Case full response (relative to Roundtable artifacts directory): "
        f"<code>fe-msecscc/{safe_name}/agents/countercase/response.md</code>",
        "- North Star full response (relative to Roundtable artifacts directory): "
        f"<code>fe-msecscc/{safe_name}/agents/north_star/response.md</code>",
    ]
    assert "\n\n---\n\n" + response_lines[0] in finding.content
    assert "Source agent directory" not in finding.content
    assert "countercase)" not in finding.content
    assert "Configuration:" not in finding.content
    assert "Local artifacts (relative" not in finding.content
    summary_lines = summary.content.splitlines()[-2:]
    assert summary_lines == [
        "- Local artifacts (relative to Roundtable artifacts directory): "
        f"<code>fe-msecscc/{safe_name}</code>",
        "- Open report (session path relative to Roundtable artifacts directory): "
        f"<code>roundtable report &quot;fe-msecscc/{safe_name}&quot; --open</code>",
    ]
    assert "roundtable view" not in summary.content
    assert "Configuration:" not in summary.content
    for comment in (finding.content, summary.content):
        assert str(artifacts_root) not in comment
        assert "private-user" not in comment
        assert "protection-lint-config" not in comment
        assert r"C:\Users" not in comment
        assert "/home/" not in comment


def test_footer_never_reintroduces_absolute_platform_prefixes():
    footer = publication_footer(
        get_configuration(resolve_bundle("buddies")),
        "fe-msecscc/session_20260915184352",
        source_agents=("countercase",),
    )
    assert footer == (
        "- Counter Case full response (relative to Roundtable artifacts directory): "
        "<code>fe-msecscc/session_20260915184352/agents/countercase/response.md</code>"
    )
    assert r"C:\Users\private-user" not in footer
    assert "/home/private-user" not in footer


def test_publish_recovers_one_run_artifacts_root_from_trace(tmp_path):
    session = tmp_path / "custom-root" / "repo" / "session_123"
    session.mkdir(parents=True)
    (session / "trace.json").write_text(
        json.dumps(
            {
                "provenance": {
                    "effectiveConfig": [
                        {
                            "name": "artifacts_dir",
                            "value": str(tmp_path / "custom-root"),
                            "source": "flag:--artifacts-dir",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert _recorded_artifacts_root(str(session)) == str(tmp_path / "custom-root")


def test_missing_session_directory_is_rejected_explicitly():
    with pytest.raises(ValueError, match="non-empty local session directory"):
        run_publish(
            _plan([_finding()]),
            _subject(),
            _diff_stat(),
            PublishOptions(),
            session_dir="",
        )


def test_republish_is_idempotent_and_flags_already_published(tmp_path):
    """When every watermark is already on the PR, run_publish sets
    ``already_published`` so the CLI can say so. (The real poster also skips the
    posts via watermark dedup — covered in the pr_threads tests; here we assert
    the flow-level flag that drives the message.)"""
    plan = _plan([_finding()])

    # First publish (empty PR) records exactly the watermarks we would post.
    first = FakePoster()
    r1 = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: first,
        iteration_fetcher=_iters(),
    )
    assert not r1.already_published
    posted_hashes = {c.stable_hash for batch in first.published for c in batch}

    # Re-publish against a PR that already holds those watermarks.
    second = FakePoster(existing=posted_hashes)
    r2 = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: second,
        iteration_fetcher=_iters(),
    )
    assert r2.already_published and r2.ok


def test_lows_only_still_posts_summary(tmp_path):
    """With the medium floor, a plan of only low findings inlines nothing but must
    still post the executive-summary thread so the lows remain visible."""
    plan = _plan(
        [
            _finding(id="L1", severity="Low", category="non_blocking"),
            _finding(id="L2", severity="Info", category="non_blocking"),
        ]
    )
    poster = FakePoster()
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(min_severity="medium"),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
    )
    assert report.eligible == 0 and report.inline == 0
    # No inline batch, but the summary thread is posted.
    assert len(poster.published) == 1
    assert poster.published[0][0].finding_id == "__summary__"


def test_change_tracking_id_flows_to_inline_comment(tmp_path):
    # The reviewed iteration's per-file changeTrackingId is attached to the anchor.
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
        changes_fetcher=_changes({"svc/x.cs": 17}),
    )
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert payload["threads"][0]["changeTrackingId"] == 17
    assert report.inline == 1


def test_change_tracking_id_absent_when_file_missing_from_changes(tmp_path):
    # A file with no entry in the changes map still anchors inline, sans ctid.
    plan = _plan([_finding()])
    run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
        changes_fetcher=_changes({"other.cs": 9}),
    )
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert payload["threads"][0]["kind"] == "inline"
    assert payload["threads"][0]["changeTrackingId"] is None


def test_change_tracking_fetch_failure_is_best_effort(tmp_path):
    # A failing changes_fetcher must not fail publish — inline anchors persist.
    def boom(pr, iteration):
        raise RuntimeError("no creds")

    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
        changes_fetcher=boom,
    )
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert report.inline == 1
    assert payload["threads"][0]["changeTrackingId"] is None


def test_partial_failure_sets_nonzero_ok_false():
    plan = _plan(
        [_finding(id="F-1", stable_hash="aaa111"), _finding(id="F-2", stable_hash="bbb222")]
    )

    def results(comments):
        return [
            PostResult(c.finding_id, "posted" if c.finding_id == "F-1" else "failed")
            for c in comments
        ]

    poster = FakePoster(results=results)
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
    )
    assert report.ok is False and report.failed >= 1


def test_summary_failure_is_reported_and_fails_publish() -> None:
    plan = _plan([_finding()])

    def results(comments):
        return [
            PostResult(
                c.finding_id,
                "failed" if c.finding_id == "__summary__" else "posted",
            )
            for c in comments
        ]

    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: FakePoster(results=results),
        iteration_fetcher=_iters(),
    )

    assert report.summary_status == "failed"
    assert report.failed == 1
    assert report.ok is False


# ── reviewed-iteration anchoring / downgrade ─────────────────────────────────
def test_unresolvable_iteration_downgrades_all_inline_to_general():
    plan = _plan([_finding()])
    poster = FakePoster()
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        # iterations don't contain the reviewed SHA ⇒ no ordinal resolves; the PR's
        # source branch is unknown ⇒ relatedness is unconfirmable (never a block).
        iteration_fetcher=lambda pr: [IterationRef(ordinal=1, source_commit_sha="other")],
        pr_source_ref_fetcher=_source_ref(None),
    )
    assert report.inline == 0 and report.general == 1
    assert report.relatedness_status == "unconfirmable"
    assert any("source branch could not be compared" in w for w in report.anchor_warnings)
    assert poster.published[0][0].iteration is None
    assert poster.published[0][0].file_path is None  # posted as general


def test_rolled_off_same_branch_downgrades_without_block():
    # Reviewed SHA gone from iterations but the source branch still matches ⇒
    # force-push/superseded: downgrade to general threads, never block.
    plan = _plan([_finding()])
    poster = FakePoster()
    report = run_publish(
        plan,
        _subject(**{K.SUBJECT_SOURCE_BRANCH: "user/x/feature"}),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=lambda pr: [IterationRef(ordinal=1, source_commit_sha="other")],
        pr_source_ref_fetcher=_source_ref("refs/heads/user/x/feature"),
    )
    assert report.relatedness_status == "related_rolled_off"
    assert report.inline == 0 and report.general == 1
    assert any("rolled off" in w for w in report.anchor_warnings)


def test_confirmed_unrelated_blocks_publish():
    # Reviewed SHA absent AND the source branches differ ⇒ proven wrong PR ⇒ block.
    plan = _plan([_finding()])
    poster = FakePoster()
    with pytest.raises(ValueError, match="do not relate to this PR"):
        run_publish(
            plan,
            _subject(**{K.SUBJECT_SOURCE_BRANCH: "user/x/feature"}),
            _diff_stat(),
            PublishOptions(),
            session_dir="/x",
            poster_factory=lambda pr: poster,
            iteration_fetcher=lambda pr: [IterationRef(ordinal=1, source_commit_sha="other")],
            pr_source_ref_fetcher=_source_ref("refs/heads/user/y/other-work"),
        )
    assert poster.published == []  # nothing posted


def test_confirmed_unrelated_dry_run_surfaces_without_block(tmp_path):
    # Dry-run computes + surfaces the verdict but still writes the preview.
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(**{K.SUBJECT_SOURCE_BRANCH: "user/x/feature"}),
        _diff_stat(),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=lambda pr: [IterationRef(ordinal=1, source_commit_sha="other")],
        pr_source_ref_fetcher=_source_ref("refs/heads/user/y/other-work"),
    )
    assert report.relatedness_status == "unrelated"
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert payload["relatedness"]["status"] == "unrelated"
    assert "do not relate to this PR" in payload["relatedness"]["reason"]


def test_anchored_path_skips_pr_source_ref_fetch():
    # The happy path must not make the second (branch) network call.
    plan = _plan([_finding()])
    poster = FakePoster()
    called = {"n": 0}

    def source_ref(pr):
        called["n"] += 1
        return "refs/heads/whatever"

    run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),  # SHA matches ⇒ anchored
        pr_source_ref_fetcher=source_ref,
    )
    assert called["n"] == 0


def test_pre_provenance_session_skips_fetch_and_downgrades():
    plan = _plan([_finding()])
    poster = FakePoster()
    called = {"n": 0}

    def fetcher(pr):
        called["n"] += 1
        return []

    report = run_publish(
        plan,
        _subject(**{K.SUBJECT_SOURCE_SHA: None}),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=fetcher,
    )
    assert called["n"] == 0  # no reviewed SHA ⇒ no network
    assert report.general == 1
    assert any("no reviewed commit" in w for w in report.anchor_warnings)


def test_iteration_fetch_failure_is_best_effort_downgrade():
    plan = _plan([_finding()])
    poster = FakePoster()

    def boom(pr):
        raise RuntimeError("offline")

    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=boom,
    )
    assert report.general == 1
    assert any("could not fetch PR #42 iterations" in w for w in report.anchor_warnings)


# ── min-severity view (E6) ───────────────────────────────────────────────────
def test_min_severity_filters_view_but_reports_total(tmp_path):
    plan = _plan([_finding(id="H", severity="High"), _finding(id="L", severity="Low")])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(min_severity="medium", dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
    )
    assert report.total == 2 and report.eligible == 1 and report.skipped_by_threshold == 1


# ── --pr override identity check (E10) ───────────────────────────────────────
def test_pr_override_mismatch_refused():
    with pytest.raises(ValueError, match="different repository"):
        resolve_publish_pr(
            _subject(),
            "https://dev.azure.com/o/p/_git/OTHER/pullrequest/9",
        )


def test_pr_override_same_repo_accepted():
    pr = resolve_publish_pr(
        _subject(),
        "https://dev.azure.com/o/p/_git/r/pullrequest/9",
    )
    assert isinstance(pr, PrReference) and pr.pr_id == 9


def test_no_provenance_and_no_override_raises():
    with pytest.raises(ValueError, match="no persisted PR provenance"):
        resolve_publish_pr(None, None)


# ── PR version-tag wiring ────────────────────────────────────────────────────
class _FakeLabelClient:
    def __init__(self, action="added"):
        self.action = action
        self.applied: list[str] = []

    def apply_adoption_label(self, label):
        self.applied.append(label)
        return self.action


def test_live_publish_stamps_version_label(monkeypatch):
    import roundtable

    monkeypatch.setattr(roundtable, "__version__", "1.2.3")
    client = _FakeLabelClient(action="added")
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: FakePoster(),
        iteration_fetcher=_iters(),
        label_client_factory=lambda pr: client,
    )
    assert report.version_label == "Roundtable-v1-1.2.3-inspectorx-local"
    assert report.label_action == "added"
    assert client.applied == ["Roundtable-v1-1.2.3-inspectorx-local"]


def test_live_publish_dev_version_records_local_label(monkeypatch):
    import roundtable

    monkeypatch.setattr(roundtable, "__version__", "0.0.1.dev9+gabc")

    client = _FakeLabelClient()

    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: FakePoster(),
        iteration_fetcher=_iters(),
        label_client_factory=lambda _pr: client,
    )
    assert report.version_label == "Roundtable-v1-0.0.1.dev9+gabc-inspectorx-local"
    assert report.label_action == "added"


def test_label_failure_never_fails_publish(monkeypatch):
    import roundtable

    monkeypatch.setattr(roundtable, "__version__", "1.2.3")

    def boom(pr):
        raise RuntimeError("labels API down")

    plan = _plan([_finding()])
    poster = FakePoster()
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=_iters(),
        label_client_factory=boom,
    )
    assert report.ok and report.posted == 1  # comments still posted
    assert report.version_label == "Roundtable-v1-1.2.3-inspectorx-local"
    assert report.label_action == "failed"


def test_dry_run_records_intended_version_label(tmp_path, monkeypatch):
    import roundtable

    monkeypatch.setattr(roundtable, "__version__", "1.2.3")
    plan = _plan([_finding()])
    report = run_publish(
        plan,
        _subject(),
        _diff_stat(),
        PublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        iteration_fetcher=_iters(),
    )
    assert report.version_label == "Roundtable-v1-1.2.3-inspectorx-local"
    assert report.label_action == "dry-run"
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert payload["versionLabel"] == "Roundtable-v1-1.2.3-inspectorx-local"


def test_explicit_publish_updates_same_summary_with_persisted_metadata(tmp_path):
    from roundtable.adoption import ReviewRecord, parse_metadata_markers

    artifacts_root = tmp_path / "artifacts"
    private_session_id = "session_20260906061115_example-repo-private-user-private-branch"
    session_dir = artifacts_root / "repo" / private_session_id
    session_dir.mkdir(parents=True)
    public_reference = "repo/session_20260906061115"
    record = ReviewRecord(
        session_id=private_session_id,
        recorded_at="2026-09-09T00:00:00Z",
        organization="o",
        project="p",
        repository="r",
        pull_request_id=42,
        source_sha="sha_reviewed",
        base_sha="base",
        tool_version="4.6.3",
        configuration_name="inspectorx",
        configuration_kind="shipped",
        graph_config_sha="abcdef",
        installation_source="feed",
        verdict="REJECT",
        findings_available=True,
        counts=(),
        findings=(),
    )
    (session_dir / "review-record.json").write_text(
        json.dumps({"record": record.to_dict()}),
        encoding="utf-8",
    )
    poster = FakePoster()
    client = _FakeLabelClient()

    report = _run_publish(
        _plan([_finding()]),
        _subject(),
        _diff_stat(),
        PublishOptions(),
        CONFIGURATION,
        session_dir=str(session_dir),
        artifacts_root=artifacts_root,
        poster_factory=lambda _pr: poster,
        iteration_fetcher=_iters(),
        label_client_factory=lambda _pr: client,
    )

    summaries = [
        comment
        for batch in poster.published
        for comment in batch
        if comment.finding_id == "__summary__"
    ]
    assert len(summaries) == 1
    raw_markdown = "\n".join(comment.content for batch in poster.published for comment in batch)
    assert public_reference in raw_markdown
    assert private_session_id not in raw_markdown
    assert "private-user" not in raw_markdown
    assert "private-branch" not in raw_markdown
    published_records = parse_metadata_markers(summaries[0].content)
    assert published_records == [replace(record, session_id=public_reference)]
    assert report.version_label == "Roundtable-v1-4.6.3-inspectorx-feed"
