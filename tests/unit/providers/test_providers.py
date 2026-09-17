from __future__ import annotations

import pytest

from roundtable.providers import ProviderId, RepositoryIdentity, get_provider, register_provider


def test_ado_provider_canonicalizes_all_supported_host_forms() -> None:
    provider = get_provider("azure_devops")
    assert (
        provider.canonicalize_remote("git@ssh.dev.azure.com:v3/Org/Project/Repo.git")
        == "dev.azure.com/org/project/repo"
    )
    assert (
        provider.canonicalize_remote("https://Org.visualstudio.com/Project/_git/Repo")
        == "dev.azure.com/org/project/repo"
    )


def test_provider_registration_is_duplicate_safe_and_unknown_is_loud() -> None:
    class Fake:
        id = "test_provider_contract"

        def canonicalize_remote(self, remote_url):
            return None

        def parse_repository(self, remote_url):
            return RepositoryIdentity(ProviderId(self.id), "host", "repo", "repo")

        def parse_change_request(self, value):
            return None

    register_provider(Fake.id, Fake())
    with pytest.raises(ValueError, match="already registered"):
        register_provider(Fake.id, Fake())
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("absent_provider_contract")
