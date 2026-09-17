"""pr_diff: ADO REST PR metadata for ``--pr`` mode.

Fetches PR metadata (title, source/target branches, permanent merge-commit SHAs)
from the **ADO REST API directly** rather than through an MCP agent + LLM
round-trip. The diff itself is no longer built here: both local and ``--pr`` /
URL reviews now materialize a workspace at the reviewed revision and flow through
the single three-dot gatherer in :mod:`roundtable.inputs.git_context`. This
module is the metadata seam that resolves the SHAs that workspace is built from.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from roundtable.ado_client import (
    ADO_RESOURCE_ID,
    ado_auth_header,
    ado_bearer_token,
    build_ado_base_url,
)

from .pr_reference import PrReference


@dataclass
class PrMetadata:
    title: str
    source_branch: str
    target_branch: str
    source_commit_sha: str
    target_commit_sha: str
    description: str | None = None


def _metadata_from_body(body: dict, *, fallback_title: str) -> PrMetadata:
    source_sha = (body.get("lastMergeSourceCommit") or {}).get("commitId", "")
    target_sha = (body.get("lastMergeTargetCommit") or {}).get("commitId", "")
    if not source_sha or not target_sha:
        raise RuntimeError(
            "PR metadata missing commit SHAs (lastMergeSourceCommit/lastMergeTargetCommit)."
        )

    def strip_ref(ref: str) -> str:
        return re.sub(r"^refs/heads/", "", ref or "")

    description = body.get("description")
    description = (description.strip() or None) if isinstance(description, str) else None
    return PrMetadata(
        title=body.get("title") or fallback_title,
        source_branch=strip_ref(body.get("sourceRefName", "")),
        target_branch=strip_ref(body.get("targetRefName", "")),
        source_commit_sha=source_sha,
        target_commit_sha=target_sha,
        description=description,
    )


# ── ADO REST metadata (replaces the MCP agent) ──────────────────────────────
# Auth + URL building now live in the dependency-neutral ``ado.rest`` module so
# the publish poster shares one credential/URL surface (E9). The private aliases
# below preserve pr_diff's historical call sites unchanged.
_build_ado_base_url = build_ado_base_url
_ADO_RESOURCE_ID = ADO_RESOURCE_ID
_ado_bearer_token = ado_bearer_token
_ado_auth_header = ado_auth_header


def fetch_pr_metadata(pr: PrReference, *, timeout: float = 20.0) -> PrMetadata:
    """Fetch PR metadata from the ADO REST API.

    Auth resolves PAT-first (``ROUNDTABLE_ADO_PAT`` / ``AZURE_DEVOPS_PAT``), then
    falls back to an AAD access token minted via the ``az`` CLI. Extracts title,
    source/target branches, and the permanent merge commit SHAs
    (``lastMergeSourceCommit`` / ``lastMergeTargetCommit``) that survive branch
    deletion.
    """
    auth_header = ado_auth_header()
    if not auth_header:
        raise RuntimeError(
            "PR metadata fetch requires ADO credentials. Set ROUNDTABLE_ADO_PAT "
            "(or AZURE_DEVOPS_PAT) with scope Code Read, or sign in with "
            "'az login' so an Azure DevOps access token can be minted, then retry."
        )

    host = pr.host or "dev.azure.com"
    base_url = build_ado_base_url(host, pr.org)
    url = (
        f"{base_url}/{urllib.parse.quote(pr.project)}/_apis/git/repositories/"
        f"{urllib.parse.quote(pr.repo_name)}/pullrequests/{pr.pr_id}?api-version=7.1"
    )
    req = urllib.request.Request(url, headers={"Authorization": auth_header})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:  # pragma: no cover - network
        raise RuntimeError(
            f"ADO REST PR fetch failed (HTTP {err.code}) for PR #{pr.pr_id}: {err.reason}"
        ) from err

    return _metadata_from_body(body, fallback_title=f"PR #{pr.pr_id}")


def fetch_pr_by_merge_commit(
    repository: PrReference,
    merge_commit_sha: str,
    *,
    timeout: float = 20.0,
) -> tuple[PrReference, PrMetadata]:
    """Resolve one completed PR by its Azure DevOps ``lastMergeCommit``."""
    auth_header = ado_auth_header()
    if not auth_header:
        raise RuntimeError(
            "merge-commit lookup requires ADO credentials. Set ROUNDTABLE_ADO_PAT "
            "(or AZURE_DEVOPS_PAT), or sign in with 'az login'."
        )
    host = repository.host or "dev.azure.com"
    base_url = build_ado_base_url(host, repository.org)
    project = urllib.parse.quote(repository.project)
    repo = urllib.parse.quote(repository.repo_name)
    url = f"{base_url}/{project}/_apis/git/repositories/{repo}/pullrequestquery?api-version=7.1"
    payload = json.dumps(
        {"queries": [{"type": "lastMergeCommit", "items": [merge_commit_sha]}]}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:  # pragma: no cover - network
        raise RuntimeError(
            f"ADO merge-commit lookup failed (HTTP {err.code}): {err.reason}"
        ) from err

    results = body.get("results", [])
    candidates = results[0].get(merge_commit_sha, []) if len(results) == 1 else []
    matches = [
        candidate
        for candidate in candidates
        if candidate.get("status") == "completed"
        and (candidate.get("lastMergeCommit") or {}).get("commitId") == merge_commit_sha
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"could not identify exactly one completed PR for merge commit "
            f"{merge_commit_sha}; found {len(matches)}"
        )
    match = matches[0]
    pr_id = int(match.get("pullRequestId") or 0)
    if pr_id <= 0:
        raise RuntimeError("ADO merge-commit lookup returned a PR without pullRequestId")
    reference = PrReference(
        org=repository.org,
        project=repository.project,
        repo_name=repository.repo_name,
        pr_id=pr_id,
        host=repository.host,
    )
    return reference, _metadata_from_body(match, fallback_title=f"PR #{pr_id}")
