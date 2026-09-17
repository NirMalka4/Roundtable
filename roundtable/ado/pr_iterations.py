"""ado.pr_iterations: list a PR's iterations to resolve the reviewed one.

An ADO pull request accumulates numbered **iterations** as its source branch is
pushed. Each iteration has a 1-based ordinal ``id`` and the source commit SHA it
captured. Inline-comment anchoring (see :mod:`roundtable.ado.anchor`) needs the
ordinal of the iteration whose source SHA matches the one this session reviewed,
so ADO can auto-track the comment forward to the PR head.

One read-only GET ``.../pullRequests/{id}/iterations`` yields everything needed.
Auth + URL building + the host allow-list are shared with the rest of the ADO
REST surface via :mod:`roundtable.ado.rest`. The network seam is injectable so
the resolver + fetch can be unit-tested with no network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from roundtable.ado_client import ado_auth_header, build_ado_base_url, encode_segment
from roundtable.inputs import PrReference


@dataclass(frozen=True)
class IterationRef:
    """One PR iteration: its 1-based ordinal and the source commit it captured."""

    ordinal: int
    source_commit_sha: str


class _IterationTransport(Protocol):
    """Return the raw response body for a GET; raise for a network failure."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> str: ...


class _UrllibTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> str:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")


def _iterations_url(pr: PrReference) -> str:
    host = pr.host or "dev.azure.com"
    base = build_ado_base_url(host, pr.org)
    return (
        f"{base}/{encode_segment(pr.project)}/_apis/git/repositories/"
        f"{encode_segment(pr.repo_name)}/pullRequests/{pr.pr_id}/iterations"
        "?api-version=7.1"
    )


def _iteration_changes_url(pr: PrReference, iteration: int) -> str:
    host = pr.host or "dev.azure.com"
    base = build_ado_base_url(host, pr.org)
    return (
        f"{base}/{encode_segment(pr.project)}/_apis/git/repositories/"
        f"{encode_segment(pr.repo_name)}/pullRequests/{pr.pr_id}/iterations/"
        f"{iteration}/changes?api-version=7.1"
    )


def _pr_url(pr: PrReference) -> str:
    host = pr.host or "dev.azure.com"
    base = build_ado_base_url(host, pr.org)
    return (
        f"{base}/{encode_segment(pr.project)}/_apis/git/repositories/"
        f"{encode_segment(pr.repo_name)}/pullRequests/{pr.pr_id}?api-version=7.1"
    )


def parse_iterations(body: str) -> list[IterationRef]:
    """Parse the iterations list response into :class:`IterationRef` records.

    Skips any entry missing an ordinal ``id`` or a ``sourceRefCommit.commitId``.
    """
    refs: list[IterationRef] = []
    for entry in json.loads(body).get("value") or []:
        ordinal = entry.get("id")
        sha = (entry.get("sourceRefCommit") or {}).get("commitId") or ""
        if isinstance(ordinal, int) and sha:
            refs.append(IterationRef(ordinal=ordinal, source_commit_sha=sha))
    return refs


def parse_iteration_changes(body: str) -> dict[str, int]:
    """Map each changed file's normalized repo path to its ``changeTrackingId``.

    The ``changeTrackingId`` is a per-file identifier that is **stable across a
    PR's iterations**. Pairing it with ``iterationContext`` on an inline thread is
    ADO's own convention (an untagged comment is auto-stamped with both), so an
    anchored comment tracks its file forward — including across renames. The path
    is normalized (leading slash stripped) to match the persisted, normalized
    finding path. Skips any entry missing an integer id or a path.
    """
    changes: dict[str, int] = {}
    for entry in json.loads(body).get("changeEntries") or []:
        ctid = entry.get("changeTrackingId")
        path = ((entry.get("item") or {}).get("path") or "").strip().lstrip("/")
        if isinstance(ctid, int) and path:
            changes[path] = ctid
    return changes


def parse_pr_source_ref(body: str) -> str | None:
    """Extract a PR's ``sourceRefName`` (e.g. ``refs/heads/user/x/foo``).

    Returns the trimmed ref, or ``None`` when absent/blank. The caller normalizes
    it (strips ``refs/heads/``) before comparing to a session's persisted branch.
    """
    ref = (json.loads(body).get("sourceRefName") or "").strip()
    return ref or None


def fetch_pr_iterations(
    pr: PrReference,
    *,
    timeout: float = 20.0,
    transport: _IterationTransport | None = None,
) -> list[IterationRef]:
    """GET the PR's iterations, newest-ordinal last.

    Requires ADO Code (read) credentials — same resolution as
    :func:`roundtable.inputs.pr_diff.fetch_pr_metadata`. Raises ``RuntimeError``
    on missing credentials or an HTTP error; the caller treats any failure as
    "iteration unresolved" and downgrades affected comments to general threads.
    """
    auth_header = ado_auth_header()
    if not auth_header:
        raise RuntimeError(
            "PR iteration fetch requires ADO credentials. Set ROUNDTABLE_ADO_PAT "
            "(or AZURE_DEVOPS_PAT) with scope Code Read, or sign in with "
            "'az login', then retry."
        )
    http = transport or _UrllibTransport()
    url = _iterations_url(pr)
    try:
        body = http.get(url, headers={"Authorization": auth_header}, timeout=timeout)
    except urllib.error.HTTPError as err:  # pragma: no cover - network
        raise RuntimeError(
            f"ADO REST iterations fetch failed (HTTP {err.code}) for PR #{pr.pr_id}: {err.reason}"
        ) from err
    return parse_iterations(body)


def fetch_pr_iteration_changes(
    pr: PrReference,
    iteration: int,
    *,
    timeout: float = 20.0,
    transport: _IterationTransport | None = None,
) -> dict[str, int]:
    """GET a PR iteration's file changes as ``{normalized_path: changeTrackingId}``.

    Used to attach the correct, per-file ``changeTrackingId`` to each inline
    thread (see :func:`parse_iteration_changes`). Same credential/host resolution
    as :func:`fetch_pr_iterations`. Raises ``RuntimeError`` on missing credentials
    or an HTTP error; the caller treats any failure as "no tracking ids" and posts
    inline anchors on ``iterationContext`` alone.
    """
    auth_header = ado_auth_header()
    if not auth_header:
        raise RuntimeError(
            "PR iteration changes fetch requires ADO credentials. Set "
            "ROUNDTABLE_ADO_PAT (or AZURE_DEVOPS_PAT) with scope Code Read, or "
            "sign in with 'az login', then retry."
        )
    http = transport or _UrllibTransport()
    url = _iteration_changes_url(pr, iteration)
    try:
        body = http.get(url, headers={"Authorization": auth_header}, timeout=timeout)
    except urllib.error.HTTPError as err:  # pragma: no cover - network
        raise RuntimeError(
            f"ADO REST iteration changes fetch failed (HTTP {err.code}) for "
            f"PR #{pr.pr_id} iteration {iteration}: {err.reason}"
        ) from err
    return parse_iteration_changes(body)


def fetch_pr_source_ref(
    pr: PrReference,
    *,
    timeout: float = 20.0,
    transport: _IterationTransport | None = None,
) -> str | None:
    """GET the PR and return its ``sourceRefName`` (branch), or ``None``.

    Used by the relatedness guard to disambiguate a reviewed commit that rolled
    off the PR's iterations: same source branch ⇒ force-push (downgrade), a
    different branch ⇒ the session belongs to another PR (block). Same
    credential/host resolution as :func:`fetch_pr_iterations`. Raises
    ``RuntimeError`` on missing credentials or an HTTP error; the caller treats any
    failure as "branch unknown" ⇒ unconfirmable, never a block.
    """
    auth_header = ado_auth_header()
    if not auth_header:
        raise RuntimeError(
            "PR source-ref fetch requires ADO credentials. Set ROUNDTABLE_ADO_PAT "
            "(or AZURE_DEVOPS_PAT) with scope Code Read, or sign in with "
            "'az login', then retry."
        )
    http = transport or _UrllibTransport()
    url = _pr_url(pr)
    try:
        body = http.get(url, headers={"Authorization": auth_header}, timeout=timeout)
    except urllib.error.HTTPError as err:  # pragma: no cover - network
        raise RuntimeError(
            f"ADO REST PR fetch failed (HTTP {err.code}) for PR #{pr.pr_id}: {err.reason}"
        ) from err
    return parse_pr_source_ref(body)
