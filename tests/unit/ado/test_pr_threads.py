"""Unit tests for ado.pr_threads — dedup, anchoring, and safe-retry (E2)."""

from __future__ import annotations

import json

import pytest

from roundtable.ado import pr_threads
from roundtable.ado.pr_threads import (
    PostResult,
    PreparedComment,
    PrThreadPoster,
    TransportError,
    parse_watermark_hashes,
)
from roundtable.inputs.pr_reference import PrReference


def _watermark(fid: str, h: str) -> str:
    return f"<!-- InspectorX-CLI:{fid}:H={h} -->"


def _watermark_new(fid: str, h: str) -> str:
    return f"<!-- Roundtable:{fid}:H={h} -->"


class FakeTransport:
    """Queues per-method responses. A queued Exception is raised; a tuple is
    returned as ``(status, body)``."""

    def __init__(self, get=None, post=None, patch=None, delete=None):
        self._get = list(get or [])
        self._post = list(post or [])
        self._patch = list(patch or [])
        self._delete = list(delete or [])
        self.posts: list[dict] = []
        self.patches: list[dict] = []
        self.deletes: list[str] = []
        self.requests: list[str] = []
        self.get_count = 0

    def request(self, method, url, *, headers, body, timeout):
        self.requests.append(method)
        if method == "GET":
            self.get_count += 1
            item = self._get.pop(0)
        elif method == "POST":
            self.posts.append(json.loads(body.decode("utf-8")))
            item = self._post.pop(0)
        elif method == "PATCH":
            self.patches.append(json.loads(body.decode("utf-8")))
            item = self._patch.pop(0)
        else:
            self.deletes.append(url)
            item = self._delete.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _threads_body(*watermarks: str) -> str:
    return json.dumps(
        {
            "value": [
                {
                    "id": 100,
                    "comments": [
                        {"id": index, "content": watermark}
                        for index, watermark in enumerate(watermarks, start=1)
                    ],
                }
            ]
        }
    )


def _summary_thread(content: str, *, thread_id: int = 131563337) -> str:
    return json.dumps({"value": [{"id": thread_id, "comments": [{"id": 1, "content": content}]}]})


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    # Avoid any credential resolution / network in the poster constructor.
    monkeypatch.setattr(pr_threads, "ado_auth_header", lambda: "Basic xxx")


def _poster(transport) -> PrThreadPoster:
    pr = PrReference(org="o", project="p", repo_name="r", pr_id=42, host="dev.azure.com")
    return PrThreadPoster(pr, transport=transport, max_get_retries=3)


# ── watermark parsing ────────────────────────────────────────────────────────
def test_parse_watermark_hashes_strict():
    text = f"{_watermark('F-1', 'abc123')} and a broken <!-- InspectorX-CLI:F-2:H=def456"
    assert parse_watermark_hashes(text) == {"abc123"}


# rebrand-compat: the reader recognizes BOTH the new ``Roundtable`` tag (emitted
# by writers now) and the legacy ``InspectorX-CLI`` tag (left by a pre-rebrand
# release) so dedup/unpublish stay correct across the rename.
def test_parse_watermark_hashes_recognizes_both_tags():
    text = "\n".join([_watermark_new("F-new", "aaaa"), _watermark("F-old", "bbbb")])
    assert parse_watermark_hashes(text) == {"aaaa", "bbbb"}


def test_parse_watermark_hashes_id_with_colons_and_spaces():
    # Regression: an id that itself contains ``:`` (synthesized ``Simulator:<hash>``)
    # or spaces (a scenario-derived id) must still parse — otherwise its watermark is
    # invisible to dedup/unpublish and the comment accumulates on every republish.
    text = "\n".join(
        [
            _watermark("Simulator:abcdef123456", "aaaa0000"),
            _watermark("ioc-type-converters: @wcd/domain enum (x)", "bbbb0000"),
            _watermark("plain-id", "cccc0000"),
        ]
    )
    assert parse_watermark_hashes(text) == {"aaaa0000", "bbbb0000", "cccc0000"}


# ── dedup ────────────────────────────────────────────────────────────────────
def test_publish_skips_already_posted_and_posts_new():
    body1 = _watermark("F-1", "aaa111")
    transport = FakeTransport(
        get=[(200, _threads_body(body1))],
        post=[(200, json.dumps({"id": 777}))],
    )
    poster = _poster(transport)
    results = poster.publish(
        [
            PreparedComment("F-1", "aaa111", body1, file_path="a.cs", start_line=3),
            PreparedComment("F-2", "bbb222", "body2", file_path="a.cs", start_line=5, end_line=6),
        ]
    )
    assert results[0] == PostResult("F-1", "skipped", detail="already posted")
    assert results[1].status == "posted" and results[1].thread_id == 777
    assert len(transport.posts) == 1  # only the new finding was posted


def test_publish_updates_same_hash_when_rendered_provenance_changes() -> None:
    old = f"{_watermark_new('F-1', 'aaa111')}\nold footer"
    new = f"{_watermark_new('F-1', 'aaa111')}\nnew footer"
    transport = FakeTransport(
        get=[(200, _threads_body(old))],
        patch=[(200, "{}")],
    )

    result = _poster(transport).publish([PreparedComment("F-1", "aaa111", new)])

    assert result == [PostResult("F-1", "posted", thread_id=100, detail="updated existing comment")]
    assert transport.patches == [{"content": new}]
    assert transport.posts == []


def test_summary_content_change_recreates_closed_thread_to_restore_ui_focus() -> None:
    old = f"{_watermark_new('__summary__', 'abc123')}\nold view\nsession-7"
    new = f"{_watermark_new('__summary__', 'abc123')}\nnew threshold view\nsession-7"
    transport = FakeTransport(
        get=[(200, _summary_thread(old))],
        delete=[(204, "")],
        post=[(201, '{"id": 99}')],
    )

    result = _poster(transport).publish_summary(
        PreparedComment("__summary__", "abc123", new, status=4),
        summary_identity="session-7",
    )

    assert result == [
        PostResult(
            "__summary__",
            "posted",
            thread_id=99,
        )
    ]
    assert transport.requests == ["GET", "DELETE", "POST"]
    assert transport.posts == [
        {
            "comments": [{"parentCommentId": 0, "content": new, "commentType": 1}],
            "status": 4,
        }
    ]


def test_current_summary_is_closed_without_creating_a_duplicate() -> None:
    content = f"{_watermark_new('__summary__:session-7', 'abc123')}\ncurrent"
    transport = FakeTransport(
        get=[(200, _summary_thread(content))],
        patch=[(200, "{}")],
    )

    result = _poster(transport).publish_summary(
        PreparedComment("__summary__", "abc123", content, status=4),
        summary_identity="session-7",
    )

    assert result == [
        PostResult(
            "__summary__",
            "skipped",
            thread_id=131563337,
            detail="summary already current",
        )
    ]
    assert transport.patches == [{"status": 4}]
    assert transport.posts == []


def test_privacy_safe_summary_identity_replaces_legacy_full_session_identity() -> None:
    legacy = "session_20260906061115_example-repo-private-user-private-branch"
    safe = "repo/session_20260906061115"
    old = f"{_watermark_new(f'__summary__:{legacy}', 'abc123')}\ncurrent"
    new = f"{_watermark_new(f'__summary__:{safe}', 'def456')}\nupdated"
    transport = FakeTransport(
        get=[(200, _summary_thread(old))],
        delete=[(204, "")],
        post=[(201, '{"id": 99}')],
    )

    result = _poster(transport).publish_summary(
        PreparedComment("__summary__", "def456", new, status=4),
        summary_identity=safe,
        legacy_summary_identities=(legacy,),
    )

    assert result == [PostResult("__summary__", "posted", thread_id=99)]
    assert transport.requests == ["GET", "DELETE", "POST"]


def test_publish_preserves_supplied_api_call_order() -> None:
    transport = FakeTransport(
        get=[(200, _threads_body())],
        post=[(201, '{"id": 1}'), (201, '{"id": 2}'), (201, '{"id": 3}')],
    )

    _poster(transport).publish(
        [
            PreparedComment("LOW", "low", "low"),
            PreparedComment("MEDIUM", "medium", "medium"),
            PreparedComment("HIGH", "high", "high"),
        ]
    )

    assert [post["comments"][0]["content"] for post in transport.posts] == [
        "low",
        "medium",
        "high",
    ]


def test_summary_identity_does_not_replace_another_session() -> None:
    old = f"{_watermark_new('__summary__', 'abc123')}\nold view\nsession-other"
    new = f"{_watermark_new('__summary__', 'def456')}\nnew view\nsession-7"
    transport = FakeTransport(
        get=[(200, _summary_thread(old))],
        post=[(201, '{"id": 99}')],
    )

    result = _poster(transport).publish_summary(
        PreparedComment("__summary__", "def456", new),
        summary_identity="session-7",
    )

    assert result[0].status == "posted"
    assert result[0].thread_id == 99
    assert transport.patches == []


# ── anchoring ────────────────────────────────────────────────────────────────
def test_inline_thread_context_shape():
    transport = FakeTransport(get=[(200, _threads_body())], post=[(201, "{}")])
    poster = _poster(transport)
    poster.publish(
        [PreparedComment("F", "h", "b", file_path="svc/x.cs", start_line=10, end_line=12)]
    )
    ctx = transport.posts[0]["threadContext"]
    assert ctx["filePath"] == "/svc/x.cs"
    assert ctx["rightFileStart"] == {"line": 10, "offset": 1}
    assert ctx["rightFileEnd"] == {"line": 12, "offset": 1}
    assert transport.posts[0]["status"] == 1
    assert transport.posts[0]["comments"][0]["commentType"] == 1


def test_general_thread_has_no_context():
    transport = FakeTransport(get=[(200, _threads_body())], post=[(201, "{}")])
    poster = _poster(transport)
    poster.publish([PreparedComment("F", "h", "b", file_path=None)])
    assert "threadContext" not in transport.posts[0]
    assert "pullRequestThreadContext" not in transport.posts[0]


def test_inline_with_iteration_carries_pr_thread_context():
    transport = FakeTransport(get=[(200, _threads_body())], post=[(201, "{}")])
    poster = _poster(transport)
    poster.publish(
        [
            PreparedComment(
                "F",
                "h",
                "b",
                file_path="svc/x.cs",
                start_line=10,
                end_line=12,
                iteration=4,
                change_tracking_id=17,
            )
        ]
    )
    prctx = transport.posts[0]["pullRequestThreadContext"]
    assert prctx["changeTrackingId"] == 17
    assert prctx["iterationContext"] == {
        "firstComparingIteration": 4,
        "secondComparingIteration": 4,
    }


def test_inline_iteration_without_change_tracking_id_omits_it():
    # No resolved changeTrackingId ⇒ the key is omitted; ADO tracks on
    # iterationContext alone. It is never fabricated.
    transport = FakeTransport(get=[(200, _threads_body())], post=[(201, "{}")])
    poster = _poster(transport)
    poster.publish(
        [PreparedComment("F", "h", "b", file_path="svc/x.cs", start_line=10, iteration=4)]
    )
    prctx = transport.posts[0]["pullRequestThreadContext"]
    assert "changeTrackingId" not in prctx
    assert prctx["iterationContext"]["secondComparingIteration"] == 4


def test_inline_without_iteration_omits_pr_thread_context():
    transport = FakeTransport(get=[(200, _threads_body())], post=[(201, "{}")])
    poster = _poster(transport)
    poster.publish([PreparedComment("F", "h", "b", file_path="svc/x.cs", start_line=10)])
    assert "threadContext" in transport.posts[0]
    assert "pullRequestThreadContext" not in transport.posts[0]


def test_general_with_iteration_still_omits_pr_thread_context():
    # An iteration ordinal on a general (no-file) comment must not synthesize context.
    transport = FakeTransport(get=[(200, _threads_body())], post=[(201, "{}")])
    poster = _poster(transport)
    poster.publish([PreparedComment("F", "h", "b", file_path=None, iteration=4)])
    assert "pullRequestThreadContext" not in transport.posts[0]


# ── safe retry / ambiguity (E2) ──────────────────────────────────────────────
def test_ambiguous_post_recovers_when_watermark_lands():
    # POST raises; the recovery re-GET shows the watermark present → posted.
    transport = FakeTransport(
        get=[(200, _threads_body()), (200, _threads_body(_watermark("F", "abcdef")))],
        post=[TransportError("timeout")],
    )
    poster = _poster(transport)
    res = poster.publish([PreparedComment("F", "abcdef", "b", file_path="a.cs", start_line=1)])
    assert res[0].status == "posted" and "recovered" in res[0].detail


def test_ambiguous_post_fails_when_watermark_absent():
    transport = FakeTransport(
        get=[(200, _threads_body()), (200, _threads_body())],
        post=[TransportError("timeout")],
    )
    poster = _poster(transport)
    res = poster.publish([PreparedComment("F", "h", "b", file_path="a.cs", start_line=1)])
    assert res[0].status == "failed"


def test_retryable_status_is_not_blindly_reposted():
    # A 503 POST is ambiguous, not retried; recovery re-GET decides (absent → failed).
    transport = FakeTransport(
        get=[(200, _threads_body()), (200, _threads_body())],
        post=[(503, "busy")],
    )
    poster = _poster(transport)
    res = poster.publish([PreparedComment("F", "h", "b", file_path="a.cs", start_line=1)])
    assert res[0].status == "failed"
    assert len(transport.posts) == 1  # never a second POST


def test_non_retryable_status_fails_fast():
    transport = FakeTransport(get=[(200, _threads_body())], post=[(400, "bad request")])
    poster = _poster(transport)
    res = poster.publish([PreparedComment("F", "h", "b", file_path="a.cs", start_line=1)])
    assert res[0].status == "failed" and "400" in res[0].detail


# ── GET retry ────────────────────────────────────────────────────────────────
def test_get_retries_on_503_then_succeeds():
    transport = FakeTransport(
        get=[(503, ""), (200, _threads_body(_watermark("F", "eee000")))],
    )
    poster = _poster(transport)
    assert poster.existing_watermark_hashes() == {"eee000"}
    assert transport.get_count == 2
