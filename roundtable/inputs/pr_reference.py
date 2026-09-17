"""pr_reference: parse the ``--pr`` argument + the ADO-remote-URL parse slice.

Parses the ``--pr`` argument (full ADO URL *or* numeric id) into a structured
:class:`PrReference`, and parses an ADO git remote URL into org/project/repo/host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from roundtable.providers import ChangeRequestIdentity, ProviderId, RepositoryIdentity


@dataclass
class PrReference:
    """Structured PR reference with all ADO coordinates."""

    org: str
    project: str
    repo_name: str
    pr_id: int
    pr_title: str | None = None
    host: str | None = None

    @property
    def change_request_identity(self) -> ChangeRequestIdentity:
        host = self.host or "dev.azure.com"
        repository = RepositoryIdentity(
            provider=ProviderId("azure_devops"),
            host=host,
            locator=f"{self.org}/{self.project}/{self.repo_name}",
            display_name=self.repo_name,
            extensions={"organization": self.org, "project": self.project},
        )
        return ChangeRequestIdentity(
            repository=repository,
            locator=str(self.pr_id),
            display_id=str(self.pr_id),
        )


@dataclass(frozen=True)
class ParsedAdoUrl:
    org: str
    project: str
    repo_name: str
    host: str


# ── ADO remote URL parsing ──────────────────────────────────────────────────
_DEV_AZURE = re.compile(r"dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/\s]+)")
_VSCOM = re.compile(r"([^/.]+)\.visualstudio\.com(?:/DefaultCollection)?/([^/]+)/_git/([^/\s]+)")
_SSH_AZURE = re.compile(r"ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/\s]+)")
_SSH_VS = re.compile(r"vs-ssh\.visualstudio\.com:v3/([^/]+)/([^/]+)/([^/\s]+)")


def parse_ado_remote_url(url: str) -> ParsedAdoUrl | None:
    """Parse an ADO git remote URL into org/project/repo/host, or ``None``.

    Supports dev.azure.com (HTTPS + SSH) and {org}.visualstudio.com (HTTPS + SSH),
    matching the four supported ADO URL patterns verbatim.
    """
    m = _DEV_AZURE.search(url)
    if m:
        return ParsedAdoUrl(m.group(1), m.group(2), m.group(3), "dev.azure.com")

    m = _VSCOM.search(url)
    if m:
        return ParsedAdoUrl(m.group(1), m.group(2), m.group(3), f"{m.group(1)}.visualstudio.com")

    m = _SSH_AZURE.search(url)
    if m:
        return ParsedAdoUrl(m.group(1), m.group(2), m.group(3), "dev.azure.com")

    m = _SSH_VS.search(url)
    if m:
        return ParsedAdoUrl(m.group(1), m.group(2), m.group(3), f"{m.group(1)}.visualstudio.com")

    return None


# ── PR URL parsing ──────────────────────────────────────────────────────────
_DEV_AZURE_PR = re.compile(
    r"https?://dev\.azure\.com/([^/]+)(?:/DefaultCollection)?/([^/]+)"
    r"/_git/([^/]+)/pullrequest/(\d+)",
    re.IGNORECASE,
)
_VSTS_PR = re.compile(
    r"https?://([^.]+)\.visualstudio\.com(?:/DefaultCollection)?/([^/]+)"
    r"/_git/([^/]+)/pullrequest/(\d+)",
    re.IGNORECASE,
)


def _safe_decode_component(value: str) -> str:
    # Python's unquote never raises on malformed percent-encoding (it leaves the
    # ``%`` as-is), so we validate explicitly to fail loud on genuinely
    # malformed sequences.
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError(
            f'Invalid PR URL: path segment "{value[:100]}" contains malformed percent-encoding.'
        )
    return unquote(value)


def parse_pr_url(url: str) -> PrReference | None:
    """Parse a full ADO pull-request URL into a :class:`PrReference`, or ``None``.

    The legacy ``/DefaultCollection/`` segment (TFS-era links) is tolerated and
    stripped so coordinates stay canonical.
    """
    clean = url.split("?")[0].rstrip("/")

    m = _DEV_AZURE_PR.search(clean)
    if m:
        return PrReference(
            org=_safe_decode_component(m.group(1)),
            project=_safe_decode_component(m.group(2)),
            repo_name=_safe_decode_component(m.group(3)),
            pr_id=int(m.group(4)),
            host="dev.azure.com",
        )

    m = _VSTS_PR.search(clean)
    if m:
        org = _safe_decode_component(m.group(1))
        return PrReference(
            org=org,
            project=_safe_decode_component(m.group(2)),
            repo_name=_safe_decode_component(m.group(3)),
            pr_id=int(m.group(4)),
            host=f"{org}.visualstudio.com",
        )

    return None


def parse_pr_reference(pr_arg: str, git_remote_url: str | None = None) -> PrReference:
    """Parse a ``--pr`` argument (URL or numeric id) into a :class:`PrReference`.

    Numeric ids require ``git_remote_url`` to derive org/project/repo. Raises
    ``ValueError`` with actionable guidance on any unparseable input.
    """
    if not pr_arg or not pr_arg.strip():
        raise ValueError("--pr argument is empty. Provide a PR ID or full ADO URL.")

    trimmed = pr_arg.strip()

    if trimmed.startswith("http"):
        parsed = parse_pr_url(trimmed)
        if parsed is None:
            raise ValueError(
                f"Invalid ADO PR URL: {trimmed[:200]}\n"
                "Expected: https://dev.azure.com/{org}/{project}/_git/{repo}/"
                "pullrequest/{id}"
            )
        return parsed

    try:
        pr_id = int(trimmed, 10)
    except ValueError:
        pr_id = 0
    if pr_id <= 0:
        raise ValueError(
            f'Invalid PR reference: "{trimmed[:100]}". Provide a numeric PR ID or '
            "full ADO URL.\nExamples:\n  --pr 123\n"
            "  --pr https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo/pullrequest/123"
        )

    if not git_remote_url:
        raise ValueError(
            "Numeric PR ID requires a git remote URL to determine org/project/repo.\n"
            "Either:\n  1. Run from inside a git repo with an ADO remote\n"
            "  2. Use the full ADO PR URL instead: --pr https://dev.azure.com/..."
        )

    remote = parse_ado_remote_url(git_remote_url)
    if remote is None:
        raise ValueError(
            f"Could not parse ADO coordinates from git remote: {git_remote_url}\n"
            "Use the full ADO PR URL instead: --pr https://dev.azure.com/..."
        )

    return PrReference(
        org=remote.org,
        project=remote.project,
        repo_name=remote.repo_name,
        pr_id=pr_id,
    )


def ado_clone_url(pr: PrReference) -> str:
    """Build an HTTPS git clone URL from a PR's ADO coordinates.

    Uses the PR's own ``host`` so a ``{org}.visualstudio.com`` PR clones from that
    host and a ``dev.azure.com`` PR from there. Both forms normalize to the same
    discovery key (:func:`inputs.workspace.normalize_remote_url`), so a clone made
    from either is still matched against a user's existing checkout of the other.
    """
    host = pr.host or "dev.azure.com"
    if host == "dev.azure.com":
        return f"https://dev.azure.com/{pr.org}/{pr.project}/_git/{pr.repo_name}"
    # {org}.visualstudio.com form — the org is encoded in the host, not the path.
    return f"https://{host}/{pr.project}/_git/{pr.repo_name}"
