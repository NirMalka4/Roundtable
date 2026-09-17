"""Unit tests for remote-URL normalization (the discovery key)."""

from __future__ import annotations

import pytest

from roundtable.inputs.workspace import canonical_ado_identity, normalize_remote_url


@pytest.mark.parametrize(
    "https, ssh",
    [
        (
            "https://user@Dev.Azure.com/org/proj/_git/Repo.git",
            "git@ssh.dev.azure.com:v3/org/proj/Repo",
        ),
        (
            "https://contoso.visualstudio.com/DefaultCollection/ExampleProject/_git/ExampleRepo",
            "git@vs-ssh.visualstudio.com:v3/contoso/ExampleProject/ExampleRepo",
        ),
        (
            "https://github.com/foo/Bar.git",
            "git@github.com:foo/Bar.git",
        ),
    ],
)
def test_https_and_ssh_forms_collapse_to_one_key(https: str, ssh: str) -> None:
    assert normalize_remote_url(https) == normalize_remote_url(ssh)
    assert normalize_remote_url(https) is not None


def test_ado_key_is_case_insensitive() -> None:
    a = normalize_remote_url("https://dev.azure.com/Org/Proj/_git/Repo")
    b = normalize_remote_url("https://dev.azure.com/org/proj/_git/repo")
    assert a == b == "dev.azure.com/org/proj/repo"


def test_trailing_git_and_slashes_stripped() -> None:
    assert normalize_remote_url("https://github.com/foo/bar/") == "github.com/foo/bar"
    assert normalize_remote_url("https://github.com/foo/bar.git") == "github.com/foo/bar"


@pytest.mark.parametrize("bad", ["", "   ", "C:/local/path", "/home/me/repo", "not a url"])
def test_non_remote_inputs_return_none(bad: str) -> None:
    assert normalize_remote_url(bad) is None


@pytest.mark.parametrize(
    "visualstudio, dev_azure",
    [
        (
            "https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo",
            "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
        ),
        (
            "git@vs-ssh.visualstudio.com:v3/contoso/ExampleProject/ExampleRepo",
            "git@ssh.dev.azure.com:v3/contoso/ExampleProject/ExampleRepo",
        ),
    ],
)
def test_both_ado_hosts_collapse_to_one_key(visualstudio: str, dev_azure: str) -> None:
    """ADO serves one repo on two hosts; two keys would hide an existing clone."""
    assert normalize_remote_url(visualstudio) == normalize_remote_url(dev_azure)
    assert normalize_remote_url(visualstudio).startswith("dev.azure.com/")


def test_legacy_identity_key_upgrades_to_the_canonical_host() -> None:
    assert (
        canonical_ado_identity("contoso.visualstudio.com/contoso/exampleproject/examplerepo")
        == "dev.azure.com/contoso/exampleproject/examplerepo"
    )


@pytest.mark.parametrize(
    "key",
    [
        "dev.azure.com/org/proj/repo",
        "github.com/foo/bar",
        "foo.visualstudio.com/not/ado",
        "file:///c:/tmp/remote.git",
    ],
)
def test_canonical_ado_identity_leaves_other_keys_untouched(key: str) -> None:
    assert canonical_ado_identity(key) == key


def test_canonical_ado_identity_is_a_no_op_on_current_keys() -> None:
    key = normalize_remote_url("https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo")
    assert canonical_ado_identity(key) == key
