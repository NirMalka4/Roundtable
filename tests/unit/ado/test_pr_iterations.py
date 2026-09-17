"""Unit tests for ado.pr_iterations — iterations fetch + parsing (no network)."""

from __future__ import annotations

import json

import pytest

from roundtable.ado import pr_iterations
from roundtable.ado.pr_iterations import (
    IterationRef,
    fetch_pr_iteration_changes,
    fetch_pr_iterations,
    fetch_pr_source_ref,
    parse_iteration_changes,
    parse_iterations,
    parse_pr_source_ref,
)
from roundtable.inputs.pr_reference import PrReference


def _pr() -> PrReference:
    return PrReference(org="o", project="p", repo_name="r", pr_id=42, host="dev.azure.com")


class FakeTransport:
    def __init__(self, body: str):
        self._body = body
        self.calls: list[str] = []

    def get(self, url, *, headers, timeout):
        self.calls.append(url)
        return self._body


def _iters_body(*pairs: tuple[int, str]) -> str:
    return json.dumps(
        {"value": [{"id": ordinal, "sourceRefCommit": {"commitId": sha}} for ordinal, sha in pairs]}
    )


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_iterations_extracts_ordinal_and_sha():
    refs = parse_iterations(_iters_body((1, "a" * 40), (2, "b" * 40)))
    assert refs == [
        IterationRef(ordinal=1, source_commit_sha="a" * 40),
        IterationRef(ordinal=2, source_commit_sha="b" * 40),
    ]


def test_parse_iterations_skips_incomplete_entries():
    body = json.dumps(
        {
            "value": [
                {"id": 1, "sourceRefCommit": {"commitId": "a" * 40}},
                {"id": 2},  # no sourceRefCommit
                {"sourceRefCommit": {"commitId": "c" * 40}},  # no ordinal
            ]
        }
    )
    assert parse_iterations(body) == [IterationRef(ordinal=1, source_commit_sha="a" * 40)]


def test_parse_iterations_empty():
    assert parse_iterations(json.dumps({"value": []})) == []
    assert parse_iterations(json.dumps({})) == []


# ── fetch (URL + auth shape via fake transport) ──────────────────────────────
def test_fetch_builds_iterations_url_and_sends_auth(monkeypatch):
    monkeypatch.setattr(pr_iterations, "ado_auth_header", lambda: "Basic xxx")
    transport = FakeTransport(_iters_body((3, "d" * 40)))
    refs = fetch_pr_iterations(_pr(), transport=transport)
    assert refs == [IterationRef(ordinal=3, source_commit_sha="d" * 40)]
    url = transport.calls[0]
    assert url == (
        "https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/42/"
        "iterations?api-version=7.1"
    )


def test_fetch_raises_when_no_credentials(monkeypatch):
    monkeypatch.setattr(pr_iterations, "ado_auth_header", lambda: None)
    with pytest.raises(RuntimeError, match="credentials"):
        fetch_pr_iterations(_pr(), transport=FakeTransport(_iters_body()))


# ── iteration changes (path → changeTrackingId) ──────────────────────────────
def _changes_body(*pairs: tuple[str, int]) -> str:
    return json.dumps(
        {
            "changeEntries": [
                {"item": {"path": path}, "changeTrackingId": ctid} for path, ctid in pairs
            ]
        }
    )


def test_parse_iteration_changes_normalizes_paths():
    body = _changes_body(("/svc/x.cs", 17), ("/svc/y.cs", 4))
    assert parse_iteration_changes(body) == {"svc/x.cs": 17, "svc/y.cs": 4}


def test_parse_iteration_changes_skips_incomplete_entries():
    body = json.dumps(
        {
            "changeEntries": [
                {"item": {"path": "/svc/x.cs"}, "changeTrackingId": 17},
                {"item": {"path": "/svc/y.cs"}},  # no id
                {"changeTrackingId": 9},  # no path
            ]
        }
    )
    assert parse_iteration_changes(body) == {"svc/x.cs": 17}


def test_parse_iteration_changes_empty():
    assert parse_iteration_changes(json.dumps({"changeEntries": []})) == {}
    assert parse_iteration_changes(json.dumps({})) == {}


def test_fetch_changes_builds_url_and_sends_auth(monkeypatch):
    monkeypatch.setattr(pr_iterations, "ado_auth_header", lambda: "Basic xxx")
    transport = FakeTransport(_changes_body(("/svc/x.cs", 17)))
    changes = fetch_pr_iteration_changes(_pr(), 3, transport=transport)
    assert changes == {"svc/x.cs": 17}
    assert transport.calls[0] == (
        "https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/42/"
        "iterations/3/changes?api-version=7.1"
    )


def test_fetch_changes_raises_when_no_credentials(monkeypatch):
    monkeypatch.setattr(pr_iterations, "ado_auth_header", lambda: None)
    with pytest.raises(RuntimeError, match="credentials"):
        fetch_pr_iteration_changes(_pr(), 3, transport=FakeTransport(_changes_body()))


# ── PR source ref (branch) for the relatedness guard ─────────────────────────
def test_parse_pr_source_ref_extracts_ref():
    assert parse_pr_source_ref(json.dumps({"sourceRefName": "refs/heads/user/x/foo"})) == (
        "refs/heads/user/x/foo"
    )


def test_parse_pr_source_ref_missing_or_blank_is_none():
    assert parse_pr_source_ref(json.dumps({})) is None
    assert parse_pr_source_ref(json.dumps({"sourceRefName": "  "})) is None


def test_fetch_pr_source_ref_builds_url_and_sends_auth(monkeypatch):
    monkeypatch.setattr(pr_iterations, "ado_auth_header", lambda: "Basic xxx")
    transport = FakeTransport(json.dumps({"sourceRefName": "refs/heads/user/x/foo"}))
    ref = fetch_pr_source_ref(_pr(), transport=transport)
    assert ref == "refs/heads/user/x/foo"
    assert transport.calls[0] == (
        "https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/42?api-version=7.1"
    )


def test_fetch_pr_source_ref_raises_when_no_credentials(monkeypatch):
    monkeypatch.setattr(pr_iterations, "ado_auth_header", lambda: None)
    with pytest.raises(RuntimeError, match="credentials"):
        fetch_pr_source_ref(_pr(), transport=FakeTransport(json.dumps({})))
