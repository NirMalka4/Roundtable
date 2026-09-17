"""ado.rest: dependency-neutral ADO REST primitives (auth, URL building, host guard).

Lifted from :mod:`roundtable.inputs.pr_diff` so both the PR-diff fetch (review
time) and the publish poster share ONE auth + URL surface. This module depends
only on :mod:`roundtable.settings.workspace` and the stdlib — never on ``inputs``
or ``ado.publish`` — so any consumer can build an authenticated ADO REST call
without importing a review-side module (avoids an import cycle, E9).

Beyond lifting the auth, this adds the safety envelope the publisher needs (E3):
a host allow-list (only genuine Azure DevOps hosts may receive a credential) and
a path-segment encoder so org/project/repo can never inject into the URL path.
"""

from __future__ import annotations

import base64
import os
import subprocess
import urllib.parse

from roundtable.settings import get_settings, resolve_ado_auth

# Azure DevOps resource GUID — used to mint an AAD access token via the az CLI
# when no static PAT is configured.
ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"


def is_allowed_ado_host(host: str) -> bool:
    """True only for genuine Azure DevOps hosts.

    Guards against a tampered ``pr`` provenance block or a ``--pr`` override
    directing a PAT/bearer at an arbitrary host (SSRF / credential leak, E3).
    """
    normalized = (host or "").strip().lower()
    return normalized == "dev.azure.com" or normalized.endswith(".visualstudio.com")


def ensure_allowed_ado_host(host: str) -> str:
    """Return ``host`` if it is an allowed ADO host, else raise ``RuntimeError``."""
    if not is_allowed_ado_host(host):
        raise RuntimeError(
            f"Refusing to attach ADO credentials to untrusted host {host!r}. "
            "Only dev.azure.com and *.visualstudio.com are permitted."
        )
    return host


def encode_segment(value: str) -> str:
    """URL-encode a single path segment (org/project/repo), escaping ``/`` too."""
    return urllib.parse.quote(value or "", safe="")


def build_ado_base_url(host: str, org: str) -> str:
    """Build the ``https`` base URL for an org, honoring the legacy VSTS host form.

    The host is allow-list validated first so a credential is never attached to a
    URL pointing at an attacker-chosen host.
    """
    ensure_allowed_ado_host(host or "dev.azure.com")
    if host and "visualstudio.com" in host:
        return f"https://{encode_segment(org)}.visualstudio.com"
    return f"https://dev.azure.com/{encode_segment(org)}"


def ado_bearer_token(*, timeout: float = 30.0) -> str | None:
    """Mint an AAD access token for Azure DevOps via the ``az`` CLI.

    Returns ``None`` when az is unavailable or not logged in, so the caller can
    surface the no-credentials error path. Used only as a fallback when no
    static PAT is present (``ROUNDTABLE_ADO_PAT`` / ``AZURE_DEVOPS_PAT``).
    """
    try:
        proc = subprocess.run(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                ADO_RESOURCE_ID,
                "--query",
                "accessToken",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=(os.name == "nt"),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    token = (proc.stdout or "").strip()
    return token or None


def ado_auth_header() -> str | None:
    """Resolve the ADO REST Authorization header per the configured auth mode.

    Routing is decided by :func:`resolve_ado_auth` (auto/az-login/pat, from
    ``roundtable.yaml``/env); this function only performs the side effects
    (base64 for a PAT, ``az`` bearer minting).
    """
    plan = resolve_ado_auth(get_settings(), os.environ)
    if plan.error:
        raise RuntimeError(plan.error)
    if plan.pat:
        token = base64.b64encode(f":{plan.pat}".encode()).decode()
        return f"Basic {token}"
    if plan.allow_bearer:
        bearer = ado_bearer_token()
        if bearer:
            return f"Bearer {bearer}"
    return None
