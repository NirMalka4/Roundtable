"""ado.pr_threads: post Roundtable comment threads onto an ADO pull request.

Wraps the ADO PR *threads* REST endpoint with the two safety properties the plan
requires:

* **Idempotent re-publish (dedup).** Before posting, GET the PR's existing threads
  and collect every Roundtable watermark hash already present. A finding whose
  watermark hash is already on the PR is *skipped*, so re-running publish never
  duplicates a thread.
* **Safe retry (E2).** ADO's thread POST is non-idempotent, so it is never blindly
  retried. Only GETs (and the transport's own 429/5xx) are retried. If a POST
  fails ambiguously (timeout / 5xx after the request left), we re-GET the threads
  and match the watermark: present ⇒ it landed (``posted``); absent ⇒ ``failed``.

Transport is injected (:class:`Transport`) so tests exercise the dedup/retry
logic against a fake REST layer with no network.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from roundtable.ado_client import ado_auth_header, build_ado_base_url, encode_segment
from roundtable.inputs import PrReference

# Strict watermark reader — only a fully-formed marker parses (agent text that
# omits the closing ``-->`` or mangles the shape is ignored, matching E8). The id
# segment is matched non-greedily up to the anchored ``:H=<hex> -->`` suffix so an
# id that itself contains ``:`` (e.g. the synthesized ``Simulator:<hash>``) or
# spaces still parses — otherwise its watermark is invisible to dedup/unpublish and
# the comment silently accumulates on every republish.
# rebrand-compat (Q5): the tag alternation accepts both the new ``Roundtable`` tag
# and the legacy ``InspectorX-CLI`` tag so dedup/unpublish still see comments left
# by a pre-rebrand release; writers emit only the new tag (see publish.py).
_WATERMARK_RE = re.compile(r"<!-- (?:Roundtable|InspectorX-CLI):([^\n]+?):H=([0-9a-f]+) -->")

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class PreparedComment:
    """A rendered comment ready to post, plus its anchor and dedup identity."""

    finding_id: str
    stable_hash: str
    content: str
    file_path: str | None = None  # normalized (no leading slash); None ⇒ general
    start_line: int | None = None
    end_line: int | None = None
    iteration: int | None = None  # reviewed iteration ordinal; enables auto-tracking
    change_tracking_id: int | None = None  # per-file, iteration-stable ADO id
    status: int = 1  # ADO thread status: active=1, closed=4


@dataclass(frozen=True)
class PostResult:
    """Per-finding outcome of a publish attempt."""

    finding_id: str
    status: str  # 'posted' | 'skipped' | 'failed' | 'ambiguous'
    thread_id: int | None = None
    detail: str | None = None


class Transport(Protocol):
    """Minimal HTTP surface: return ``(status_code, body_text)``; raise
    :class:`TransportError` for network-level failures."""

    def request(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, str]: ...


class TransportError(RuntimeError):
    """A network-level failure (timeout, connection reset) with no HTTP status."""


class _UrllibTransport:
    """Default urllib-backed transport."""

    def request(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, str]:
        req = urllib.request.Request(url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise TransportError(str(err)) from err


def parse_watermark_hashes(text: str) -> set[str]:
    """Collect every Roundtable watermark hash present in a blob of comment text."""
    return {stable_hash for _identity, stable_hash in _WATERMARK_RE.findall(text or "")}


def _parse_watermarks(text: str) -> list[tuple[str, str]]:
    return _WATERMARK_RE.findall(text or "")


def summary_matches_session(identity: str, content: str, session_id: str) -> bool:
    """Whether a scoped or legacy summary watermark belongs to one session."""
    return identity == f"__summary__:{session_id}" or (
        identity == "__summary__"
        and re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(session_id)}(?![A-Za-z0-9_-])",
            content,
        )
        is not None
    )


def _pr_thread_context(comment: PreparedComment) -> dict | None:
    """ADO ``pullRequestThreadContext`` that pins an inline anchor to the reviewed
    iteration so the server auto-tracks the comment forward to the PR head.

    Only emitted for an inline comment (a file anchor) that carries a resolved
    iteration ordinal; ``None`` otherwise (general thread, or iteration
    unresolved ⇒ the caller has already downgraded to a general thread).

    ``firstComparingIteration == secondComparingIteration == R`` expresses "the
    reviewer's full-diff view of iteration R vs its base" — the frame the persisted
    right-file lines were computed in. (S0 spike constant; see plan.)

    ``changeTrackingId`` is the file's per-iteration-stable id resolved from the
    reviewed iteration's changes; it is omitted when unresolved (ADO then tracks on
    ``iterationContext`` alone). It is never fabricated — a wrong id would anchor
    the comment against a different file's change entry.
    """
    if comment.iteration is None or not comment.file_path or comment.start_line is None:
        return None
    context: dict = {
        "iterationContext": {
            "firstComparingIteration": comment.iteration,
            "secondComparingIteration": comment.iteration,
        },
    }
    if comment.change_tracking_id is not None:
        context["changeTrackingId"] = comment.change_tracking_id
    return context


def _thread_context(comment: PreparedComment) -> dict | None:
    """ADO ``threadContext`` for an inline anchor; ``None`` for a general thread.

    ADO wants a repo-absolute ``filePath`` (leading slash) and whole-line anchors
    (``offset: 1``) on the RIGHT (post-change) side.
    """
    if not comment.file_path or comment.start_line is None:
        return None
    end = (
        comment.end_line
        if comment.end_line and comment.end_line >= comment.start_line
        else comment.start_line
    )
    return {
        "filePath": f"/{comment.file_path.lstrip('/')}",
        "rightFileStart": {"line": comment.start_line, "offset": 1},
        "rightFileEnd": {"line": end, "offset": 1},
    }


def _thread_payload(comment: PreparedComment) -> dict:
    payload: dict = {
        "comments": [{"parentCommentId": 0, "content": comment.content, "commentType": 1}],
        "status": comment.status,
    }
    context = _thread_context(comment)
    if context is not None:
        payload["threadContext"] = context
    pr_context = _pr_thread_context(comment)
    if pr_context is not None:
        payload["pullRequestThreadContext"] = pr_context
    return payload


class PrThreadPoster:
    """Posts prepared comments to a PR's threads with dedup + safe retry."""

    def __init__(
        self,
        pr: PrReference,
        *,
        host: str | None = None,
        timeout: float = 30.0,
        max_get_retries: int = 3,
        transport: Transport | None = None,
    ) -> None:
        self._pr = pr
        self._timeout = timeout
        self._max_get_retries = max_get_retries
        self._transport = transport or _UrllibTransport()
        resolved_host = host or pr.host or "dev.azure.com"
        base = build_ado_base_url(resolved_host, pr.org)
        self._threads_endpoint = (
            f"{base}/{encode_segment(pr.project)}/_apis/git/repositories/"
            f"{encode_segment(pr.repo_name)}/pullRequests/{pr.pr_id}/threads"
        )
        self._threads_url = f"{self._threads_endpoint}?api-version=7.1"
        auth = ado_auth_header()
        if not auth:
            raise RuntimeError(
                "Publishing requires ADO credentials. Set ROUNDTABLE_ADO_PAT (or "
                "AZURE_DEVOPS_PAT) with scope Code (write), or sign in with 'az login'."
            )
        self._headers = {"Authorization": auth, "Content-Type": "application/json"}

    # ── reads ────────────────────────────────────────────────────────────────
    def existing_watermark_hashes(self) -> set[str]:
        """GET the PR threads and collect every Roundtable watermark hash present."""
        status, body = self._get_with_retry(self._threads_url)
        if status != 200:
            raise RuntimeError(f"ADO threads GET failed (HTTP {status}).")
        hashes: set[str] = set()
        for thread in json.loads(body).get("value") or []:
            for comment in thread.get("comments") or []:
                hashes |= parse_watermark_hashes(comment.get("content") or "")
        return hashes

    def _watermarked_comments(self) -> list[tuple[int, int, str, str, str]]:
        """Return thread/comment ids, semantic id, hash, and content."""
        status, body = self._get_with_retry(self._threads_url)
        if status != 200:
            raise RuntimeError(f"ADO threads GET failed (HTTP {status}).")
        out: list[tuple[int, int, str, str, str]] = []
        for thread in json.loads(body).get("value") or []:
            thread_id = thread.get("id")
            for comment in thread.get("comments") or []:
                comment_id = comment.get("id")
                content = comment.get("content") or ""
                if thread_id is None or comment_id is None:
                    continue
                for identity, stable_hash in _parse_watermarks(content):
                    out.append((thread_id, comment_id, identity, stable_hash, content))
        return out

    def find_watermarked_comments(self) -> list[tuple[int, int, str]]:
        """GET the PR threads and return ``(thread_id, comment_id, watermark_hash)``
        for every comment that carries an Roundtable watermark.

        This is the read half of ``unpublish``: it locates the *specific* comment
        that holds each watermark (not just the hash), so exactly that comment — and
        nothing a human added to the thread — can be deleted.
        """
        return [
            (thread_id, comment_id, stable_hash)
            for thread_id, comment_id, _identity, stable_hash, _content in (
                self._watermarked_comments()
            )
        ]

    def find_watermarked_comment_details(self) -> list[tuple[int, int, str, str, str]]:
        """Return ids, semantic identity, hash, and content for safe session matching."""
        return self._watermarked_comments()

    def _get_with_retry(self, url: str) -> tuple[int, str]:
        last_status = 0
        last_body = ""
        for attempt in range(self._max_get_retries):
            try:
                status, body = self._transport.request(
                    "GET", url, headers=self._headers, body=None, timeout=self._timeout
                )
            except TransportError:
                if attempt == self._max_get_retries - 1:
                    raise
                time.sleep(0)
                continue
            if status not in _RETRYABLE_STATUS:
                return status, body
            last_status, last_body = status, body
            time.sleep(0)
        return last_status, last_body

    # ── writes ───────────────────────────────────────────────────────────────
    def publish(
        self,
        comments: Sequence[PreparedComment],
        *,
        existing_hashes: set[str] | None = None,
    ) -> list[PostResult]:
        """Post new comments and refresh same-hash comments whose rendering changed."""
        if existing_hashes is not None:
            return self._publish_against_hashes(comments, existing_hashes)

        existing = self._watermarked_comments()
        seen = {item[3] for item in existing}
        results: list[PostResult] = []
        for comment in comments:
            matches = [
                item
                for item in existing
                if item[2] == comment.finding_id and item[3] == comment.stable_hash
            ]
            if len(matches) > 1:
                results.append(
                    PostResult(comment.finding_id, "failed", detail="multiple matching comments")
                )
            elif matches:
                results.append(self._refresh_finding(matches[0], comment))
            elif comment.stable_hash in seen:
                results.append(PostResult(comment.finding_id, "skipped", detail="already posted"))
            else:
                results.append(self._post_one(comment))
                seen.add(comment.stable_hash)
        return results

    def _publish_against_hashes(
        self,
        comments: Sequence[PreparedComment],
        existing_hashes: set[str],
    ) -> list[PostResult]:
        seen = set(existing_hashes)
        results: list[PostResult] = []
        for comment in comments:
            if comment.stable_hash in seen:
                results.append(PostResult(comment.finding_id, "skipped", detail="already posted"))
                continue
            results.append(self._post_one(comment))
            seen.add(comment.stable_hash)
        return results

    def _refresh_finding(
        self,
        existing: tuple[int, int, str, str, str],
        comment: PreparedComment,
    ) -> PostResult:
        thread_id, comment_id, _identity, _stable_hash, content = existing
        if content == comment.content:
            return PostResult(
                comment.finding_id,
                "skipped",
                detail="already posted",
            )
        return self._update_finding(thread_id, comment_id, comment)

    def _update_finding(
        self,
        thread_id: int,
        comment_id: int,
        comment: PreparedComment,
    ) -> PostResult:
        body = json.dumps({"content": comment.content}).encode("utf-8")
        try:
            status, _ = self._transport.request(
                "PATCH",
                self._comment_url(thread_id, comment_id),
                headers=self._headers,
                body=body,
                timeout=self._timeout,
            )
        except TransportError as err:
            return self._resolve_finding_update(comment, reason=str(err), thread_id=thread_id)
        if status in (200, 201):
            return PostResult(
                comment.finding_id,
                "posted",
                thread_id=thread_id,
                detail="updated existing comment",
            )
        if status in _RETRYABLE_STATUS:
            return self._resolve_finding_update(
                comment,
                reason=f"HTTP {status}",
                thread_id=thread_id,
            )
        return PostResult(comment.finding_id, "failed", detail=f"HTTP {status}")

    def _resolve_finding_update(
        self,
        comment: PreparedComment,
        *,
        reason: str,
        thread_id: int,
    ) -> PostResult:
        try:
            landed = any(
                identity == comment.finding_id
                and stable_hash == comment.stable_hash
                and content == comment.content
                for _tid, _cid, identity, stable_hash, content in self._watermarked_comments()
            )
        except (RuntimeError, TransportError):
            return PostResult(comment.finding_id, "ambiguous", detail=f"{reason}; re-GET failed")
        if landed:
            return PostResult(
                comment.finding_id,
                "posted",
                thread_id=thread_id,
                detail=f"comment update recovered after {reason}",
            )
        return PostResult(comment.finding_id, "failed", detail=reason)

    def publish_summary(
        self,
        comment: PreparedComment,
        *,
        summary_identity: str,
        legacy_summary_identities: Sequence[str] = (),
        force_recreate: bool = False,
    ) -> list[PostResult]:
        """Keep one closed summary and recreate it when needed to restore UI focus."""
        identities = (summary_identity, *legacy_summary_identities)
        try:
            candidates = [
                item
                for item in self._watermarked_comments()
                if any(self._is_same_summary(item[2], item[4], identity) for identity in identities)
            ]
        except (RuntimeError, TransportError) as err:
            return [PostResult(comment.finding_id, "failed", detail=str(err))]
        if not candidates:
            return [self._post_one(comment)]
        thread_id, _comment_id, _identity, stable_hash, content = candidates[0]
        current = len(candidates) == 1 and stable_hash == comment.stable_hash
        if current and content == comment.content and not force_recreate:
            if not self._set_thread_status(thread_id, comment.status):
                return [
                    PostResult(
                        comment.finding_id,
                        "failed",
                        thread_id=thread_id,
                        detail="could not close existing summary",
                    )
                ]
            return [
                PostResult(
                    comment.finding_id,
                    "skipped",
                    thread_id=thread_id,
                    detail="summary already current",
                )
            ]
        for existing_thread_id, comment_id, *_rest in candidates:
            if self.delete_comment(existing_thread_id, comment_id) not in {"removed", "absent"}:
                return [
                    PostResult(
                        comment.finding_id,
                        "failed",
                        thread_id=existing_thread_id,
                        detail="could not replace existing summary",
                    )
                ]
        return [self._post_one(comment)]

    @staticmethod
    def _is_same_summary(identity: str, content: str, summary_identity: str) -> bool:
        return summary_matches_session(identity, content, summary_identity)

    def _set_thread_status(self, thread_id: int, status_value: int) -> bool:
        body = json.dumps({"status": status_value}).encode("utf-8")
        try:
            status, _ = self._transport.request(
                "PATCH",
                f"{self._threads_endpoint}/{thread_id}?api-version=7.1",
                headers=self._headers,
                body=body,
                timeout=self._timeout,
            )
        except TransportError:
            return False
        return status in (200, 201)

    def _post_one(self, comment: PreparedComment) -> PostResult:
        body = json.dumps(_thread_payload(comment)).encode("utf-8")
        try:
            status, resp = self._transport.request(
                "POST", self._threads_url, headers=self._headers, body=body, timeout=self._timeout
            )
        except TransportError as err:
            return self._resolve_ambiguous(comment, reason=str(err))
        if status in (200, 201):
            thread_id = None
            with contextlib.suppress(ValueError, AttributeError):
                thread_id = json.loads(resp).get("id")
            return PostResult(comment.finding_id, "posted", thread_id=thread_id)
        if status in _RETRYABLE_STATUS:
            # The request may or may not have been applied — never blindly re-POST.
            return self._resolve_ambiguous(comment, reason=f"HTTP {status}")
        return PostResult(comment.finding_id, "failed", detail=f"HTTP {status}")

    def _resolve_ambiguous(self, comment: PreparedComment, *, reason: str) -> PostResult:
        """A POST failed ambiguously; re-GET and match the watermark to decide."""
        try:
            landed = comment.stable_hash in self.existing_watermark_hashes()
        except RuntimeError:
            return PostResult(comment.finding_id, "ambiguous", detail=f"{reason}; re-GET failed")
        if landed:
            return PostResult(comment.finding_id, "posted", detail=f"recovered after {reason}")
        return PostResult(comment.finding_id, "failed", detail=reason)

    # ── deletes (unpublish) ──────────────────────────────────────────────────
    def _comment_url(self, thread_id: int, comment_id: int) -> str:
        return f"{self._threads_endpoint}/{thread_id}/comments/{comment_id}?api-version=7.1"

    def delete_comment(self, thread_id: int, comment_id: int) -> str:
        """Delete one comment by id. Returns ``'removed'`` (2xx), ``'absent'``
        (HTTP 404 — already gone), or ``'failed'`` (any other status / permission
        error / network failure).

        DELETE is idempotent, so — unlike the non-idempotent thread POST — it is
        retry-safe: a re-run after a partial unpublish just sees 404s and reports
        ``'absent'``.
        """
        url = self._comment_url(thread_id, comment_id)
        try:
            status, _ = self._delete_with_retry(url)
        except TransportError:
            return "failed"
        if status in (200, 204):
            return "removed"
        if status == 404:
            return "absent"
        return "failed"

    def _delete_with_retry(self, url: str) -> tuple[int, str]:
        last_status = 0
        last_body = ""
        for attempt in range(self._max_get_retries):
            try:
                status, body = self._transport.request(
                    "DELETE", url, headers=self._headers, body=None, timeout=self._timeout
                )
            except TransportError:
                if attempt == self._max_get_retries - 1:
                    raise
                time.sleep(0)
                continue
            if status not in _RETRYABLE_STATUS:
                return status, body
            last_status, last_body = status, body
            time.sleep(0)
        return last_status, last_body
