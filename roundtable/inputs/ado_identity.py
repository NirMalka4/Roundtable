"""ado_identity: resolve ADO repository identities for the ADO context sections.

Resolves ADO repository identities. An :class:`AdoIdentity` carries the org/project/repo
coordinates plus — best-effort — the project/repository **GUIDs** (the values the
ADO MCP ``repo_*`` tools want, since names are ambiguous across collections).

Identity resolution is **best-effort and never fails a review**: GUID enrichment
hits the ADO REST API (reusing the PAT-first/az-Bearer auth from ``pr_diff``), and
any failure (no creds, network, 404) degrades gracefully to a name-only identity.
The caller (PR mode) enforces the "≥1 identity or hard-fail" policy, not this
module.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

from roundtable.providers import ProviderId, RepositoryIdentity

from ._gitexec import run_git
from .pr_diff import _ado_auth_header, _build_ado_base_url
from .pr_reference import PrReference, parse_ado_remote_url


@dataclass(frozen=True)
class AdoIdentity:
    """One ADO repository identity."""

    org: str
    project: str
    repo_name: str
    remote_url: str
    host: str
    repository_id: str | None = None
    project_id: str | None = None

    @property
    def repository_identity(self) -> RepositoryIdentity:
        extensions = {
            "organization": self.org,
            "project": self.project,
            "remoteUrl": self.remote_url,
        }
        if self.repository_id is not None:
            extensions["repositoryId"] = self.repository_id
        if self.project_id is not None:
            extensions["projectId"] = self.project_id
        return RepositoryIdentity(
            provider=ProviderId("azure_devops"),
            host=self.host,
            locator=f"{self.org}/{self.project}/{self.repo_name}",
            display_name=self.repo_name,
            extensions=extensions,
        )


def _origin_remote_url(repo_path: str) -> str | None:
    """Best-effort ``git remote get-url origin`` (None on any failure)."""
    try:
        res = run_git(["remote", "get-url", "origin"], repo_path, timeout=5.0, check=False)
    except Exception:
        return None
    url = (res.stdout or "").strip()
    return url or None


def _resolve_repo_guid(
    org: str, project: str, repo_name: str, host: str, *, timeout: float = 10.0
) -> tuple[str, str] | None:
    """Best-effort (repository_id, project_id) via ADO REST; None on any failure.

    Resolves the repo GUID: ``GET {base}/{project}/_apis/git/
    repositories/{repo}?api-version=7.1`` → ``.id`` (repo GUID) + ``.project.id``
    (project GUID).
    """
    auth = _ado_auth_header()
    if not auth:
        return None
    base = _build_ado_base_url(host or "dev.azure.com", org)
    url = (
        f"{base}/{urllib.parse.quote(project)}/_apis/git/repositories/"
        f"{urllib.parse.quote(repo_name)}?api-version=7.1"
    )
    try:
        req = urllib.request.Request(url, headers={"Authorization": auth})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    repo_id = body.get("id")
    proj_id = (body.get("project") or {}).get("id")
    if repo_id and proj_id:
        return (str(repo_id), str(proj_id))
    return None


def build_identity(
    *,
    org: str,
    project: str,
    repo_name: str,
    host: str = "dev.azure.com",
    remote_url: str = "",
    enrich: bool = True,
    timeout: float = 10.0,
) -> AdoIdentity:
    """Build one identity, optionally enriching with GUIDs (best-effort)."""
    ident = AdoIdentity(
        org=org,
        project=project,
        repo_name=repo_name,
        remote_url=remote_url,
        host=host or "dev.azure.com",
    )
    if enrich:
        guids = _resolve_repo_guid(org, project, repo_name, ident.host, timeout=timeout)
        if guids:
            ident = replace(ident, repository_id=guids[0], project_id=guids[1])
    return ident


def resolve_ado_identities(
    repo_path: str | None = None,
    *,
    remote_url: str | None = None,
    pr: PrReference | None = None,
    enrich: bool = True,
) -> list[AdoIdentity]:
    """Resolve the ADO identities for a review (0 or 1 today; a list leaves room
    for future multi-identity reviews).

    Precedence: an explicit :class:`PrReference` (PR mode) → an explicit
    ``remote_url`` → ``git remote get-url origin`` in ``repo_path`` (local mode).
    Returns ``[]`` when no ADO remote can be derived (e.g. a GitHub repo) — the
    caller decides whether that is fatal (PR mode) or tolerated (local mode).
    """
    if pr is not None:
        return [
            build_identity(
                org=pr.org,
                project=pr.project,
                repo_name=pr.repo_name,
                host=pr.host or "dev.azure.com",
                remote_url=remote_url or "",
                enrich=enrich,
            )
        ]

    url = remote_url or (_origin_remote_url(repo_path) if repo_path else None)
    if not url:
        return []
    parsed = parse_ado_remote_url(url)
    if parsed is None:
        return []
    return [
        build_identity(
            org=parsed.org,
            project=parsed.project,
            repo_name=parsed.repo_name,
            host=parsed.host,
            remote_url=url,
            enrich=enrich,
        )
    ]
