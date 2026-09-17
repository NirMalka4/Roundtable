"""Unit tests for ADO identity resolution.

GUID enrichment is mocked (no live ADO); the resolver's identity-derivation and
graceful-degradation behavior is what we assert.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gitrepo import _git, _init_repo

from roundtable.inputs import ado_identity
from roundtable.inputs.ado_identity import (
    AdoIdentity,
    build_identity,
    resolve_ado_identities,
)
from roundtable.inputs.pr_reference import PrReference


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Default: GUID resolution returns None (no creds / offline)."""
    monkeypatch.setattr(ado_identity, "_resolve_repo_guid", lambda *a, **k: None)


def test_pr_mode_yields_single_identity_from_pr_reference():
    pr = PrReference(
        org="contoso",
        project="ExampleProject",
        repo_name="ExampleRepo",
        pr_id=42,
        host="dev.azure.com",
    )
    ids = resolve_ado_identities(pr=pr)
    assert len(ids) == 1
    ident = ids[0]
    assert (ident.org, ident.project, ident.repo_name) == (
        "contoso",
        "ExampleProject",
        "ExampleRepo",
    )
    assert ident.host == "dev.azure.com"
    assert ident.repository_id is None and ident.project_id is None


def test_remote_url_mode_parses_dev_azure():
    url = "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo"
    ids = resolve_ado_identities(remote_url=url)
    assert len(ids) == 1
    assert ids[0].repo_name == "ExampleRepo"
    assert ids[0].remote_url == url


def test_non_ado_remote_yields_empty():
    assert resolve_ado_identities(remote_url="https://github.com/foo/bar.git") == []


def test_no_remote_no_path_yields_empty():
    assert resolve_ado_identities() == []


def test_local_mode_reads_origin_remote(tmp_path):
    repo = tmp_path / "Repo"
    _init_repo(repo)
    _git(
        repo,
        "remote",
        "add",
        "origin",
        "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
    )
    ids = resolve_ado_identities(repo_path=str(repo))
    assert len(ids) == 1
    assert ids[0].org == "contoso" and ids[0].project == "ExampleProject"


def test_local_mode_no_origin_yields_empty(tmp_path):
    repo = tmp_path / "Repo"
    _init_repo(repo)
    assert resolve_ado_identities(repo_path=str(repo)) == []


def test_enrichment_fills_guids_when_available(monkeypatch):
    monkeypatch.setattr(
        ado_identity,
        "_resolve_repo_guid",
        lambda *a, **k: ("repo-guid-123", "proj-guid-456"),
    )
    ident = build_identity(org="contoso", project="ExampleProject", repo_name="ExampleRepo")
    assert ident.repository_id == "repo-guid-123"
    assert ident.project_id == "proj-guid-456"


def test_enrich_disabled_skips_rest(monkeypatch):
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return ("x", "y")

    monkeypatch.setattr(ado_identity, "_resolve_repo_guid", _spy)
    ident = build_identity(org="o", project="p", repo_name="r", enrich=False)
    assert called["n"] == 0
    assert ident.repository_id is None


def test_identity_is_frozen():
    ident = AdoIdentity(org="o", project="p", repo_name="r", remote_url="", host="h")
    with pytest.raises(FrozenInstanceError):
        ident.org = "x"  # type: ignore[misc]
