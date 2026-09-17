"""Integration tests for ado.unpublish_flow — the delete-only dual of publish.

Drives the flow with a fake poster (no ADO): it exposes the watermarked comments
on the PR and records deletes. Covers dry-run preview, deleting a session's
finding + summary comments, leaving another session's / a human's comments
untouched (only-our-watermark targeting), idempotent 404→absent, a failed delete
propagating a nonzero result, the --pr override, and severity-independent targeting.
"""

from __future__ import annotations

import json

import pytest

from roundtable.ado import unpublish_flow
from roundtable.ado.comment_format import summary_stable_hash
from roundtable.ado.pr_iterations import IterationRef
from roundtable.ado.pr_threads import PostResult
from roundtable.ado.publish import PublishableFinding
from roundtable.ado.unpublish_flow import (
    UnpublishOptions,
    run_unpublish,
)
from roundtable.configs.inspectorx.plugins.verdict_overlay import (
    PublishPlan,
    PublishPlanDiagnostics,
)
from roundtable.review.trace_overlay import OverlayKey as K


# A fake iteration_fetcher whose list contains the reviewed SHA ⇒ RELATED_ANCHORED,
# so the relatedness guard passes without any (real) network. Injected into every
# call below to keep the suite hermetic.
def _anchored(pr):
    return [IterationRef(ordinal=1, source_commit_sha="sha_reviewed")]


@pytest.fixture(autouse=True)
def _hermetic_relatedness(monkeypatch):
    """Default the (real) network fetchers to an anchored, offline result so tests
    that don't inject their own fetchers never touch ADO. Tests asserting a
    specific relatedness verdict inject explicit fetchers, which take precedence."""
    monkeypatch.setattr(unpublish_flow, "fetch_pr_iterations", _anchored)
    monkeypatch.setattr(unpublish_flow, "fetch_pr_source_ref", lambda pr: None)


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


def _plan(findings, *, session_id="sess_1") -> PublishPlan:
    return PublishPlan(
        verdict="REJECT",
        verdict_icon="X",
        session_id=session_id,
        safe_count=0,
        original_findings_count=len(findings),
        blocking_findings=[f for f in findings if f.category == "blocking"],
        non_blocking_findings=[f for f in findings if f.category != "blocking"],
        all_findings=list(findings),
        security_findings=[],
        published_primary_count=len(findings),
        judge_observations=[],
        validated_safe_refs=[],
        needs_human_judgment_refs=[],
        unresolved_refs=[],
        diagnostics=PublishPlanDiagnostics(),
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


def _summary_hash(plan) -> str:
    return summary_stable_hash(plan.session_id, plan.all_findings)


def _summary_comment(plan, thread_id, comment_id):
    content = f"<!-- Roundtable:__summary__:{plan.session_id}:H={_summary_hash(plan)} -->"
    return thread_id, comment_id, f"__summary__:{plan.session_id}", _summary_hash(plan), content


class FakePoster:
    """Serves watermarked comments and records deletes.

    ``comments`` accepts hash-only triples or full watermark-detail tuples. ``fail``
    is a set of ``(thread_id, comment_id)`` that error; ``absent`` returns 404.
    """

    def __init__(self, comments, *, fail=None, absent=None):
        self._comments = list(comments)
        self._fail = set(fail or [])
        self._absent = set(absent or [])
        self.deleted: list[tuple[int, int]] = []
        self.summaries = []

    def find_watermarked_comments(self):
        return list(self._comments)

    def find_watermarked_comment_details(self):
        return [
            item if len(item) == 5 else (item[0], item[1], "", item[2], "")
            for item in self._comments
        ]

    def delete_comment(self, thread_id, comment_id):
        self.deleted.append((thread_id, comment_id))
        key = (thread_id, comment_id)
        if key in self._fail:
            return "failed"
        if key in self._absent:
            return "absent"
        return "removed"

    def publish_summary(self, comment, *, summary_identity, force_recreate=False):
        self.summaries.append((comment, summary_identity))
        return [PostResult("__summary__", "posted", thread_id=99)]


# ── dry-run ──────────────────────────────────────────────────────────────────
def test_dry_run_writes_unpublish_json(tmp_path):
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111"), _summary_comment(plan, 12, 102)])
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: poster,
    )
    assert report.dry_run_path == str(tmp_path / "unpublish.json")
    assert poster.deleted == []  # nothing deleted in dry-run
    payload = json.loads((tmp_path / "unpublish.json").read_text(encoding="utf-8"))
    assert payload["matched"] == 2
    ids = {c["findingId"] for c in payload["comments"]}
    assert ids == {"F-1", "__summary__"}
    assert payload["retained"] == {
        "metadataSummary": False,
        "currentStateLabel": True,
    }


def test_dry_run_honors_out_override(tmp_path):
    out = tmp_path / "custom.json"
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(dry_run=True, out_path=str(out)),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: poster,
    )
    assert report.dry_run_path == str(out) and out.exists()


# ── live delete ──────────────────────────────────────────────────────────────
def test_removes_finding_and_summary():
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111"), _summary_comment(plan, 12, 102)])
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
    )
    assert report.targets == 2 and report.matched == 2
    assert report.removed == 2 and report.failed == 0 and report.absent == 0
    assert report.ok
    assert set(poster.deleted) == {(11, 101), (12, 102)}


def test_removes_all_legacy_and_scoped_summaries_for_session():
    plan = _plan([_finding()])
    session = plan.session_id
    comments = [
        (11, 101, "F-1", "aaa111", "<!-- Roundtable:F-1:H=aaa111 -->"),
        (
            12,
            102,
            "__summary__",
            "old111",
            f"<!-- Roundtable:__summary__:H=old111 -->\n<sub>Roundtable · {session}</sub>",
        ),
        (
            13,
            103,
            f"__summary__:{session}",
            "new222",
            f"<!-- Roundtable:__summary__:{session}:H=new222 -->",
        ),
        (
            14,
            104,
            "__summary__:another-session",
            "other333",
            "<!-- Roundtable:__summary__:another-session:H=other333 -->",
        ),
    ]
    poster = FakePoster(comments)

    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
    )

    assert report.matched == 3
    assert set(poster.deleted) == {(11, 101), (12, 102), (13, 103)}


def test_removes_privacy_safe_summary_without_exposing_legacy_session_name(tmp_path):
    legacy = "session_20260906061115_example-repo-private-user-private-branch"
    safe = "repo/session_20260906061115"
    plan = _plan([_finding()], session_id=legacy)
    session_dir = tmp_path / "repo" / legacy
    session_dir.mkdir(parents=True)
    content = f"<!-- Roundtable:__summary__:{safe}:H=new222 -->"
    poster = FakePoster([(13, 103, f"__summary__:{safe}", "new222", content)])

    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir=str(session_dir),
        artifacts_root=tmp_path,
        poster_factory=lambda pr: poster,
    )

    assert report.matched == 1
    assert poster.deleted == [(13, 103)]


def test_leaves_other_session_and_human_comments():
    plan = _plan([_finding()])
    poster = FakePoster(
        [
            (11, 101, "aaa111"),  # ours
            (20, 200, "zzz999"),  # another session's watermark
        ]
    )
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
    )
    # only our finding matched (the summary hash isn't present on the PR here)
    assert report.matched == 1
    assert poster.deleted == [(11, 101)]  # never touched the other session's comment


def test_idempotent_absent_is_ok():
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")], absent=[(11, 101)])
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
    )
    assert report.absent == 1 and report.removed == 0
    assert report.ok  # already-gone is a clean no-op


def test_failed_delete_is_not_ok():
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")], fail=[(11, 101)])
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
    )
    assert report.failed == 1
    assert not report.ok


def test_pr_override_resolves_target():
    plan = _plan([_finding()])
    seen = {}

    def factory(pr):
        seen["pr"] = pr
        return FakePoster([(11, 101, "aaa111")])

    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(pr_override="https://dev.azure.com/o/p/_git/r/pullrequest/99"),
        session_dir="/x",
        poster_factory=factory,
    )
    assert seen["pr"].pr_id == 99
    assert report.removed == 1


def test_unpublish_is_severity_independent():
    """Unpublish targets EVERY finding + the summary, regardless of severity — no
    ``--min-severity`` coupling. A low finding that publish would not inline
    is still a target here; its hash simply matches whatever we posted."""
    low = _finding(id="F-low", severity="Low", stable_hash="lowhash", category="non_blocking")
    high = _finding(id="F-1", severity="High", stable_hash="aaa111")
    plan = _plan([high, low])
    poster = FakePoster(
        [
            (11, 101, "aaa111"),
            (12, 102, "lowhash"),
            _summary_comment(plan, 13, 103),
        ]
    )
    run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
    )
    assert (11, 101) in poster.deleted
    assert (12, 102) in poster.deleted
    assert (13, 103) in poster.deleted


# ── relatedness guard, symmetric with publish ────────────────────────────────
def test_confirmed_unrelated_blocks_unpublish():
    # Reviewed SHA absent from iterations AND branches differ ⇒ proven wrong PR.
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])

    with pytest.raises(ValueError, match="do not relate to this PR"):
        run_unpublish(
            plan,
            _subject(**{K.SUBJECT_SOURCE_BRANCH: "user/x/feature"}),
            UnpublishOptions(),
            session_dir="/x",
            poster_factory=lambda pr: poster,
            iteration_fetcher=lambda pr: [IterationRef(ordinal=1, source_commit_sha="other")],
            pr_source_ref_fetcher=lambda pr: "refs/heads/user/y/other-work",
        )
    assert poster.deleted == []  # nothing deleted on a blocked run


def test_confirmed_unrelated_dry_run_surfaces_without_block(tmp_path):
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])
    report = run_unpublish(
        plan,
        _subject(**{K.SUBJECT_SOURCE_BRANCH: "user/x/feature"}),
        UnpublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: poster,
        iteration_fetcher=lambda pr: [IterationRef(ordinal=1, source_commit_sha="other")],
        pr_source_ref_fetcher=lambda pr: "refs/heads/user/y/other-work",
    )
    assert report.relatedness_status == "unrelated"
    payload = json.loads((tmp_path / "unpublish.json").read_text(encoding="utf-8"))
    assert payload["relatedness"]["status"] == "unrelated"
    assert poster.deleted == []


def test_unconfirmable_still_deletes():
    # Iterations unfetchable (offline) ⇒ unconfirmable ⇒ never block; delete proceeds.
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])

    def boom(pr):
        raise RuntimeError("offline")

    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        iteration_fetcher=boom,
    )
    assert report.relatedness_status == "unconfirmable"
    assert report.removed == 1
    assert report.warnings  # the reason is surfaced


# ── PR version-tag removal wiring ────────────────────────────────────────────
class _FakeLabelClient:
    def __init__(self, count=1):
        self.count = count
        self.calls = 0

    def remove_roundtable_labels(self):
        self.calls += 1
        return self.count


def test_live_unpublish_retains_version_labels():
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])
    client = _FakeLabelClient(count=1)
    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        label_client_factory=lambda pr: client,
    )
    assert report.labels_removed == 0
    assert client.calls == 0


def test_dry_run_does_not_remove_labels(tmp_path):
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])

    def boom(pr):
        raise AssertionError("labels must not be touched in dry-run")

    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(dry_run=True),
        session_dir=str(tmp_path),
        poster_factory=lambda pr: poster,
        label_client_factory=boom,
    )
    assert report.labels_removed == 0


def test_label_removal_failure_never_fails_unpublish():
    plan = _plan([_finding()])
    poster = FakePoster([(11, 101, "aaa111")])

    def boom(pr):
        raise RuntimeError("labels API down")

    report = run_unpublish(
        plan,
        _subject(),
        UnpublishOptions(),
        session_dir="/x",
        poster_factory=lambda pr: poster,
        label_client_factory=boom,
    )
    assert report.removed == 1 and report.ok  # comment delete unaffected
    assert report.labels_removed == 0


def test_unpublish_does_not_restore_compact_adoption_comment(tmp_path):
    from roundtable.adoption import ReviewRecord

    record = ReviewRecord(
        session_id="sess_1",
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
    (tmp_path / "review-record.json").write_text(
        json.dumps({"record": record.to_dict()}),
        encoding="utf-8",
    )
    poster = FakePoster([(11, 101, "aaa111")])

    report = run_unpublish(
        _plan([_finding()]),
        _subject(),
        UnpublishOptions(),
        session_dir=str(tmp_path),
        poster_factory=lambda _pr: poster,
        label_client_factory=lambda _pr: pytest.fail("label must be retained"),
    )

    assert poster.deleted == [(11, 101)]
    assert poster.summaries == []
    assert not report.metadata_retained
