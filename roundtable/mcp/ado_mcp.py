"""ado_mcp: the Azure DevOps MCP server **family** identity.

The ADO servers' launch specs (command/args/domains/tools/timeout/bindings) live
in ``mcp/mcp_servers.yaml`` and are compiled into the registry by
``runtime/mcp_registry`` (see that module for the schema). Azure DevOps is split
into ROLE-scoped servers — ``ado-work-items`` (Profiler read + Work Item
traversal) and ``ado-publish`` (SuggestionPublisher write) — for least privilege.
This file retains only the ADO-family predicate other modules genuinely need.

IMPORTANT — tool namespace: the copilot CLI exposes a server's tools as
``<serverName>-<toolName>`` (e.g. ``ado-work-items-repo_get_pull_request_by_id``).
Every ADO server name starts with :data:`ADO_SERVER_PREFIX`, so that prefix
doubles as (a) the "is this an ADO server" predicate (identity gating) and
(b) the invoked-tool prefix that namespaces its tools in ``toolStats``. It MUST
stay in sync with the ``ado-*`` server names declared in ``mcp_servers.yaml``.

Auth: ``@azure-devops/mcp`` authenticates via ambient Azure CLI creds
(``az login``); no PAT is wired into the MCP config. REST publish (``ado/`` +
``inputs/pr_diff``) still uses a PAT — a separate path.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Server-name / tool-name family prefix for Azure DevOps MCP servers. Both the
#: server names (``ado-work-items``, ``ado-publish``) and their CLI-namespaced
#: tool names (``ado-work-items-repo_*``) start with this, so it is the single
#: authority for recognising the ADO family. MUST match the ``ado-*`` keys in
#: ``mcp_servers.yaml``.
ADO_SERVER_PREFIX = "ado-"


def is_ado_server(name: str) -> bool:
    """True iff ``name`` is an Azure DevOps MCP server (the ``ado-*`` family)."""
    return name.startswith(ADO_SERVER_PREFIX)


def ado_servers(names: Sequence[str]) -> list[str]:
    """The ADO-family server names within ``names`` (order preserved)."""
    return [n for n in names if is_ado_server(n)]


def is_publish_server(name: str) -> bool:
    """True iff ``name`` is an MCP server the publish path runs over.

    The acceptance publish MCP-connectivity gate asserts the publish server is
    connected whenever publishing was requested. Publishing currently runs over the ADO
    family — ``ado-publish`` writes suggestions, and the shared ``ado-`` prefix
    also covers the read servers agents use to resolve the PR — so this delegates
    to :func:`is_ado_server`. Callers use this predicate rather than
    :func:`is_ado_server` directly so the gate expresses *publish-server* identity;
    only this definition changes if publishing ever moves off the ADO family.
    """
    return is_ado_server(name)
