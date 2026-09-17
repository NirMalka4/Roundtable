"""Tests for pr_reference: URL + numeric --pr parsing and ADO remote URL parsing."""

from __future__ import annotations

import pytest

from roundtable.inputs.pr_reference import (
    ado_clone_url,
    parse_ado_remote_url,
    parse_pr_reference,
    parse_pr_url,
)


# ── parse_pr_url ────────────────────────────────────────────────────────────
def test_dev_azure_url():
    ref = parse_pr_url(
        "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo/pullrequest/123"
    )
    assert ref is not None
    assert ref.org == "contoso"
    assert ref.project == "ExampleProject"
    assert ref.repo_name == "ExampleRepo"
    assert ref.pr_id == 123
    assert ref.host == "dev.azure.com"


def test_dev_azure_url_legacy_defaultcollection():
    ref = parse_pr_url(
        "https://dev.azure.com/contoso/DefaultCollection/ExampleProject/_git/ExampleRepo/pullrequest/7"
    )
    assert ref is not None
    assert ref.org == "contoso"
    assert ref.project == "ExampleProject"
    assert ref.repo_name == "ExampleRepo"
    assert ref.pr_id == 7


def test_visualstudio_url():
    ref = parse_pr_url("https://contoso.visualstudio.com/Proj/_git/MyRepo/pullrequest/99")
    assert ref is not None
    assert ref.org == "contoso"
    assert ref.host == "contoso.visualstudio.com"
    assert ref.pr_id == 99


def test_url_with_query_and_trailing_slash():
    ref = parse_pr_url("https://dev.azure.com/o/p/_git/r/pullrequest/5/?foo=bar")
    assert ref is not None
    assert ref.pr_id == 5


def test_non_pr_url_returns_none():
    assert parse_pr_url("https://example.com/not/a/pr") is None


# ── parse_pr_url: real-world edge forms (coverage) ──────────────────────────
def test_visualstudio_url_with_dotted_repo_name():
    # The canonical --pr URL form from the CLI docs (repo name contains dots).
    ref = parse_pr_url(
        "https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo/pullrequest/123"
    )
    assert ref is not None
    assert ref.repo_name == "ExampleRepo"
    assert ref.org == "contoso"
    assert ref.host == "contoso.visualstudio.com"
    assert ref.pr_id == 123


def test_url_percent_encoded_segments_are_decoded():
    ref = parse_pr_url("https://dev.azure.com/contoso/My%20Project/_git/My%20Repo/pullrequest/12")
    assert ref is not None
    assert ref.project == "My Project"
    assert ref.repo_name == "My Repo"


def test_url_scheme_and_host_are_case_insensitive():
    ref = parse_pr_url(
        "HTTPS://DEV.AZURE.COM/contoso/ExampleProject/_git/ExampleRepo/pullrequest/5"
    )
    assert ref is not None and ref.pr_id == 5


def test_plain_http_scheme_is_accepted():
    ref = parse_pr_url("http://dev.azure.com/o/p/_git/r/pullrequest/9")
    assert ref is not None and ref.pr_id == 9


def test_url_with_malformed_percent_encoding_raises():
    with pytest.raises(ValueError, match="malformed percent-encoding"):
        parse_pr_url("https://dev.azure.com/o/p/_git/Re%2po/pullrequest/1")


def test_reference_dispatch_rejects_non_pr_ado_repo_url():
    # A repo URL (no /pullrequest/<id>) routed through parse_pr_reference is a URL
    # form, so it must fail as a bad PR URL rather than be mistaken for an id.
    with pytest.raises(ValueError, match="Invalid ADO PR URL"):
        parse_pr_reference("https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo")


# ── parse_pr_reference ──────────────────────────────────────────────────────
def test_numeric_id_with_remote():
    ref = parse_pr_reference(
        "4242", "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo"
    )
    assert ref.org == "contoso"
    assert ref.project == "ExampleProject"
    assert ref.repo_name == "ExampleRepo"
    assert ref.pr_id == 4242


def test_numeric_id_without_remote_raises():
    with pytest.raises(ValueError, match="requires a git remote URL"):
        parse_pr_reference("4242")


def test_empty_arg_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_pr_reference("")


def test_zero_or_negative_id_raises():
    with pytest.raises(ValueError, match="Invalid PR reference"):
        parse_pr_reference("0", "https://dev.azure.com/o/p/_git/r")


def test_nonnumeric_nonurl_raises():
    with pytest.raises(ValueError, match="Invalid PR reference"):
        parse_pr_reference("abc", "https://dev.azure.com/o/p/_git/r")


def test_bad_url_raises():
    with pytest.raises(ValueError, match="Invalid ADO PR URL"):
        parse_pr_reference("https://example.com/nope")


# ── parse_ado_remote_url ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url,org,project,repo,host",
    [
        (
            "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
            "contoso",
            "ExampleProject",
            "ExampleRepo",
            "dev.azure.com",
        ),
        (
            "https://contoso.visualstudio.com/Proj/_git/Repo",
            "contoso",
            "Proj",
            "Repo",
            "contoso.visualstudio.com",
        ),
        (
            "git@ssh.dev.azure.com:v3/contoso/ExampleProject/ExampleRepo",
            "contoso",
            "ExampleProject",
            "ExampleRepo",
            "dev.azure.com",
        ),
        (
            "user@vs-ssh.visualstudio.com:v3/contoso/Proj/Repo",
            "contoso",
            "Proj",
            "Repo",
            "contoso.visualstudio.com",
        ),
    ],
)
def test_parse_ado_remote_url(url, org, project, repo, host):
    parsed = parse_ado_remote_url(url)
    assert parsed is not None
    assert (parsed.org, parsed.project, parsed.repo_name, parsed.host) == (
        org,
        project,
        repo,
        host,
    )


def test_parse_ado_remote_url_non_ado_returns_none():
    assert parse_ado_remote_url("https://github.com/org/repo.git") is None


# ── ado_clone_url ───────────────────────────────────────────────────────────
def test_ado_clone_url_dev_azure_host():
    pr = parse_pr_url(
        "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo/pullrequest/123"
    )
    assert pr is not None
    assert ado_clone_url(pr) == "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo"


def test_ado_clone_url_visualstudio_host():
    pr = parse_pr_url(
        "https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo/pullrequest/123"
    )
    assert pr is not None
    assert ado_clone_url(pr) == "https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo"
