"""remote_url: normalize a git remote URL into a stable discovery key.

Repo discovery must match a reviewed repo to a local clone by *remote identity*,
never by folder name (two unrelated folders can share a name; one repo can live
under many names). The same remote is written many ways — HTTPS vs SSH, with or
without a ``.git`` suffix, mixed-case host, embedded credentials, a trailing
slash. Azure DevOps additionally serves every repo under two hosts —
``dev.azure.com`` and the older ``{org}.visualstudio.com``.
:func:`normalize_remote_url` collapses all of those to one canonical string so
``https://user@Dev.Azure.com/org/proj/_git/Repo.git``,
``git@ssh.dev.azure.com:v3/org/proj/Repo`` and
``https://org.visualstudio.com/proj/_git/Repo`` compare equal.

The key is intentionally opaque (used only for equality/hashing), not a URL to
fetch from — the original remote is preserved separately for cloning.
"""

from __future__ import annotations

import re

from roundtable.providers import canonicalize_remote

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_SCP_LIKE = re.compile(r"^([^/@]+@)?([^/:]+):(.+)$")  # git@host:path (no scheme)

#: ADO identity keys always name this host, whichever host the remote was written
#: against, so the two spellings of one repo cannot produce two identities.
_ADO_IDENTITY_HOST = "dev.azure.com"
_ADO_LEGACY_KEY = re.compile(r"^[^/.]+\.visualstudio\.com/([^/]+/[^/]+/[^/]+)$")


def _strip_credentials(authority: str) -> str:
    """Drop any ``user[:pass]@`` prefix from a URL authority component."""
    return authority.rsplit("@", 1)[-1]


def _strip_port(host: str) -> str:
    """Drop a ``:port`` suffix, leaving the bare host."""
    return host.rsplit(":", 1)[0] if ":" in host else host


def normalize_remote_url(url: str) -> str | None:
    """Return a canonical ``host/path`` key for ``url``, or ``None`` if unusable.

    ADO remotes are canonicalized via :func:`parse_ado_remote_url` so every spelling
    of the *same* repo — HTTPS or SSH, ``dev.azure.com`` or ``{org}.visualstudio.com``
    — collapses to one key; the whole ADO key is lowercased because ADO treats
    org/project/repo case-insensitively.

    Non-ADO remotes fall back to generic normalization: strip scheme, embedded
    credentials, and port; lowercase the host; strip a trailing ``.git`` and
    surrounding slashes. The path case is preserved for hosts that are
    path-case-sensitive.
    """
    if not url or not url.strip():
        return None
    raw = url.strip()

    provider_identity = canonicalize_remote(raw)
    if provider_identity is not None:
        return provider_identity

    host = ""
    path = ""

    scheme_match = _SCHEME.match(raw)
    if scheme_match:
        rest = raw[scheme_match.end() :]
        authority, _, tail = rest.partition("/")
        host = _strip_port(_strip_credentials(authority))
        path = tail
    else:
        scp = _SCP_LIKE.match(raw)
        # Require a dotted host so a Windows drive path (``C:/repo``) isn't mistaken
        # for a ``host:path`` SCP remote.
        if scp and "." in _strip_credentials(f"{scp.group(1) or ''}{scp.group(2)}"):
            host = _strip_port(_strip_credentials(f"{scp.group(1) or ''}{scp.group(2)}"))
            path = scp.group(3)
        else:
            # A bare local path or unrecognized form — not a networked remote.
            return None

    host = host.lower().strip("/")
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    path = path.strip("/")

    if not host or not path:
        return None
    return f"{host}/{path}"


def canonical_ado_identity(key: str) -> str:
    """Upgrade an identity key recorded under a legacy ADO host, else return it as-is.

    Keys persisted before ADO hosts were collapsed name the same repo under
    ``{org}.visualstudio.com``; rewriting the host keeps those records comparable
    with keys :func:`normalize_remote_url` produces today.
    """
    match = _ADO_LEGACY_KEY.match(key.strip().lower())
    return f"{_ADO_IDENTITY_HOST}/{match.group(1)}" if match else key
