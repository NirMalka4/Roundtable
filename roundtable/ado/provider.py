"""Azure DevOps repository-provider adapter."""

from __future__ import annotations

import re
from urllib.parse import unquote

from roundtable.providers import (
    ChangeRequestIdentity,
    ProviderId,
    RepositoryIdentity,
)

_REMOTE_PATTERNS = (
    (re.compile(r"dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/\s]+)", re.I), "dev.azure.com"),
    (
        re.compile(
            r"([^/.]+)\.visualstudio\.com(?:/DefaultCollection)?/([^/]+)/_git/([^/\s]+)",
            re.I,
        ),
        None,
    ),
    (re.compile(r"ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/\s]+)", re.I), "dev.azure.com"),
    (re.compile(r"vs-ssh\.visualstudio\.com:v3/([^/]+)/([^/]+)/([^/\s]+)", re.I), None),
)
_PR_PATTERNS = (
    re.compile(
        r"https?://dev\.azure\.com/([^/]+)(?:/DefaultCollection)?/([^/]+)"
        r"/_git/([^/]+)/pullrequest/(\d+)",
        re.I,
    ),
    re.compile(
        r"https?://([^.]+)\.visualstudio\.com(?:/DefaultCollection)?/([^/]+)"
        r"/_git/([^/]+)/pullrequest/(\d+)",
        re.I,
    ),
)


class AzureDevOpsProvider:
    id = "azure_devops"

    def parse_repository(self, remote_url: str) -> RepositoryIdentity | None:
        for pattern, fixed_host in _REMOTE_PATTERNS:
            match = pattern.search(remote_url)
            if match is None:
                continue
            org, project, repository = (unquote(part) for part in match.groups())
            repository = repository.removesuffix(".git")
            host = fixed_host or f"{org}.visualstudio.com"
            canonical = f"dev.azure.com/{org}/{project}/{repository}".lower()
            return RepositoryIdentity(
                provider=ProviderId(self.id),
                host=host,
                locator=f"{org}/{project}/{repository}",
                display_name=repository,
                canonical_url=canonical,
                extensions={"organization": org, "project": project},
            )
        return None

    def canonicalize_remote(self, remote_url: str) -> str | None:
        identity = self.parse_repository(remote_url)
        return identity.canonical_url if identity is not None else None

    def parse_change_request(self, value: str) -> ChangeRequestIdentity | None:
        for pattern in _PR_PATTERNS:
            match = pattern.search(value.split("?")[0].rstrip("/"))
            if match is None:
                continue
            org, project, repository, request_id = (unquote(part) for part in match.groups())
            host = (
                "dev.azure.com" if "dev.azure.com" in value.lower() else f"{org}.visualstudio.com"
            )
            repo = RepositoryIdentity(
                provider=ProviderId(self.id),
                host=host,
                locator=f"{org}/{project}/{repository}",
                display_name=repository,
                extensions={"organization": org, "project": project},
            )
            return ChangeRequestIdentity(
                repository=repo,
                locator=request_id,
                display_id=request_id,
                canonical_url=value,
            )
        return None


__all__ = ["AzureDevOpsProvider"]
