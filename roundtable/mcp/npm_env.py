"""runtime.npm_env: pin the npm registry for Roundtable-spawned ``npx`` MCP servers
so package resolution is immune to the *review target's* project ``.npmrc``.

WHY (live-run finding): Roundtable spawns ``npx -y @azure-devops/mcp ...`` for the
ADO MCP servers. ``npx`` resolves that package against the nearest ``.npmrc``
walking up from its working directory — and both the prewarm probe
(``mcp_prewarm._spawn``) and the copilot agent subprocess inherit Roundtable's cwd,
which is the review *target's* checkout. A repo that pins ``registry=`` to a private
feed (e.g. an Azure Artifacts feed guarded by a short-lived token) then governs
where Roundtable fetches an unrelated tool from; an expired feed token makes the
fetch 401, so the server is pruned as ``unreachable`` — even though the operator's
OWN global npm registry serves the package fine. In short: MCP warm-up depended on
the directory Roundtable was invoked from.

FIX: export ``npm_config_registry`` in the child env. npm's config precedence is
``cli > ENV > project .npmrc > user .npmrc > global``, so an env pin overrides the
target repo's project ``.npmrc`` regardless of cwd. The pinned value is the
operator's OWN user/global registry (probed from a neutral cwd, so the target repo
cannot taint the probe) — we do not hardcode a registry, we merely stop the review
target from hijacking the operator's. An explicit ``mcp_npm_registry`` setting
(``ROUNDTABLE_MCP_NPM_REGISTRY`` env or ``roundtable.yaml``) overrides the probe.

Fail-open: if no registry can be determined, no pin is added and behaviour is
exactly as before. Only the *default* (unscoped) registry is pinned; a repo using a
scoped ``@scope:registry=`` pin for the MCP package would need a scoped env key —
out of scope until observed (the live failure was on the default registry).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from functools import lru_cache

from roundtable.settings import get_settings

_NPM_REGISTRY_ENV = "npm_config_registry"


def registry_pin_env() -> dict[str, str]:
    """The ``{npm_config_registry: <url>}`` overlay to merge into an ``npx`` child
    env, or ``{}`` when no registry can be resolved (fail-open — behaviour unchanged).
    """
    registry = _resolved_registry()
    return {_NPM_REGISTRY_ENV: registry} if registry else {}


def child_env_with_registry_pin() -> dict[str, str]:
    """A full child environment (``os.environ`` + the registry pin) for a spawn."""
    return {**os.environ, **registry_pin_env()}


def _resolved_registry() -> str | None:
    """The configured registry if set, else the operator's probed user/global one."""
    configured = get_settings().mcp_npm_registry
    if configured:
        return configured.strip()
    return _user_registry()


@lru_cache(maxsize=1)
def _user_registry() -> str | None:
    """Probe the operator's user/global npm ``registry`` from a neutral cwd.

    Run from the OS temp dir so npm never walks into the review target's project
    ``.npmrc`` — the returned value is the operator's own registry, exactly what we
    want to pin back so the target repo cannot override it. Cached (stable per run)
    and fail-open: any error / empty output ⇒ ``None`` (no pin).
    """
    try:
        proc = subprocess.run(
            ["npm", "config", "get", "registry"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=tempfile.gettempdir(),
            shell=(os.name == "nt"),  # npm→npm.cmd on Windows
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (proc.stdout or "").strip().splitlines()
    registry = value[-1].strip() if value else ""
    if not registry or registry.lower() in {"undefined", "null"}:
        return None
    return registry
