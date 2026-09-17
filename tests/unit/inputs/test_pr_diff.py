"""Tests for pr_diff: ADO REST PR-metadata fetch + auth-header routing.

Diff construction was retired (both modes now flow through the git_context
gatherer over a workspace), so this module is metadata-only.
"""

from __future__ import annotations

import pytest

from roundtable.inputs.pr_diff import (
    _ado_auth_header,
    fetch_pr_by_merge_commit,
    fetch_pr_metadata,
)
from roundtable.inputs.pr_reference import PrReference


# ── fetch_pr_metadata guard ─────────────────────────────────────────────────
def test_fetch_pr_metadata_without_credentials_raises(monkeypatch):
    monkeypatch.delenv("ROUNDTABLE_ADO_PAT", raising=False)
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    # Neutralize the az-CLI ****** so we exercise the no-credentials path.
    monkeypatch.setattr("roundtable.ado_client.client.ado_bearer_token", lambda **_: None)
    pr = PrReference(org="o", project="p", repo_name="r", pr_id=1, host="dev.azure.com")
    with pytest.raises(RuntimeError, match="requires ADO credentials"):
        fetch_pr_metadata(pr)


# ── auth-header routing (via the historical pr_diff aliases) ─────────────────
def test_ado_auth_header_prefers_pat(monkeypatch):
    monkeypatch.setenv("ROUNDTABLE_ADO_PAT", "secret-pat")
    # az fallback must not be consulted when a PAT is present.
    monkeypatch.setattr(
        "roundtable.ado_client.client.ado_bearer_token",
        lambda **_: (_ for _ in ()).throw(AssertionError("az must not be called")),
    )
    header = _ado_auth_header()
    assert header is not None and header.startswith("Basic ")


def test_ado_auth_header_falls_back_to_bearer(monkeypatch):
    monkeypatch.delenv("ROUNDTABLE_ADO_PAT", raising=False)
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    monkeypatch.setattr(
        "roundtable.ado_client.client.ado_bearer_token", lambda **_: "aad-token-xyz"
    )
    assert _ado_auth_header() == "Bearer aad-token-xyz"


# -- description extraction --
class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def _mock_pr_body(monkeypatch, body: dict) -> None:
    import json

    monkeypatch.setenv("ROUNDTABLE_ADO_PAT", "secret-pat")
    monkeypatch.setattr(
        "roundtable.inputs.pr_diff.urllib.request.urlopen",
        lambda *_a, **_k: _FakeResp(json.dumps(body).encode("utf-8")),
    )


_MIN_BODY = {
    "title": "T",
    "sourceRefName": "refs/heads/feature/x",
    "targetRefName": "refs/heads/main",
    "lastMergeSourceCommit": {"commitId": "src-sha"},
    "lastMergeTargetCommit": {"commitId": "tgt-sha"},
}


def test_fetch_pr_metadata_extracts_description(monkeypatch):
    _mock_pr_body(monkeypatch, {**_MIN_BODY, "description": "  Consolidate enums.  "})
    pr = PrReference(org="o", project="p", repo_name="r", pr_id=1, host="dev.azure.com")
    assert fetch_pr_metadata(pr).description == "Consolidate enums."


def test_fetch_pr_metadata_description_absent_is_none(monkeypatch):
    _mock_pr_body(monkeypatch, dict(_MIN_BODY))
    pr = PrReference(org="o", project="p", repo_name="r", pr_id=1, host="dev.azure.com")
    assert fetch_pr_metadata(pr).description is None


def test_fetch_pr_metadata_blank_description_is_none(monkeypatch):
    _mock_pr_body(monkeypatch, {**_MIN_BODY, "description": "   "})
    pr = PrReference(org="o", project="p", repo_name="r", pr_id=1, host="dev.azure.com")
    assert fetch_pr_metadata(pr).description is None


def test_fetch_pr_by_merge_commit_uses_ado_query(monkeypatch):
    import json

    captured = {}
    merge_sha = "m" * 40
    body = {
        "results": [
            {
                merge_sha: [
                    {
                        **_MIN_BODY,
                        "pullRequestId": 42,
                        "status": "completed",
                        "lastMergeCommit": {"commitId": merge_sha},
                    }
                ]
            }
        ]
    }

    def open_request(request, **_kwargs):
        captured["request"] = request
        return _FakeResp(json.dumps(body).encode("utf-8"))

    monkeypatch.setenv("ROUNDTABLE_ADO_PAT", "secret-pat")
    monkeypatch.setattr(
        "roundtable.inputs.pr_diff.urllib.request.urlopen",
        open_request,
    )
    repository = PrReference(
        org="o",
        project="p",
        repo_name="r",
        pr_id=0,
        host="dev.azure.com",
    )

    reference, metadata = fetch_pr_by_merge_commit(repository, merge_sha)

    request = captured["request"]
    assert request.method == "POST"
    assert json.loads(request.data) == {
        "queries": [{"type": "lastMergeCommit", "items": [merge_sha]}]
    }
    assert reference.pr_id == 42
    assert metadata.source_commit_sha == "src-sha"
    assert metadata.target_commit_sha == "tgt-sha"


@pytest.mark.parametrize("matches", [[], [{}, {}]])
def test_fetch_pr_by_merge_commit_requires_exactly_one_match(monkeypatch, matches):
    merge_sha = "m" * 40
    candidates = [
        {
            **_MIN_BODY,
            "pullRequestId": index + 1,
            "status": "completed",
            "lastMergeCommit": {"commitId": merge_sha},
        }
        for index, _ in enumerate(matches)
    ]
    _mock_pr_body(monkeypatch, {"results": [{merge_sha: candidates}]})
    repository = PrReference(org="o", project="p", repo_name="r", pr_id=0)

    with pytest.raises(RuntimeError, match="exactly one completed PR"):
        fetch_pr_by_merge_commit(repository, merge_sha)
