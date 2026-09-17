"""roundtable.mcp.registry: MCP-server definitions and per-run config resolution.
per-invocation config resolution (Tier 0 of the MCP-modularity work).

WHY: before this seam, MCP injection was hardwired — ``cli`` called a dedicated
``ado`` config builder directly and threaded the resulting single ``ado`` server
config to *every* agent. Adding a second server (or scoping a server to a subset
of agents) had no home. This module is that home — and the server *definitions*
now live in **data** (``mcp_servers.yaml``), not code:

  * **Definitions** live in :data:`_SPEC_PATH` (``mcp_servers.yaml``). Each entry
    is a static server spec (``command``/``args``/``tools``/``timeout``) plus two
    bounded dynamic affordances: ``${field}`` placeholders interpolated from the
    runtime :class:`McpBuildContext`, and a ``requires:`` list of context fields
    that must be non-empty (else the server is dropped this run). This module
    compiles each entry into a builder ``(McpBuildContext) -> dict | None`` and
    stores them in :data:`_REGISTRY` (``logical name -> builder``).
  * **Resolution** is :func:`resolve_mcp_config` — given requested server names and
    runtime context, it builds the structured SDK ``mcp_servers`` mapping or
    ``None`` when nothing resolves.

Adding a server is now a ``mcp_servers.yaml`` edit (no Python) for any static or
``${}``-parametrised server. Each graph entry declares its exact server set.

MCP ISOLATION: :func:`registered_server_names` is the authoritative
"servers WE inject" set. The SDK backend sees only the servers we pass and never
discovers the operator's ambient MCP config, so isolation is structural — no
explicit disable pass is needed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

#: Declarative server definitions (see the file's header for the schema).
_SPEC_PATH = Path(__file__).resolve().parent / "mcp_servers.yaml"

#: ``${field}`` placeholder syntax interpolated from :class:`McpBuildContext`.
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def mcp_config_path() -> Path:
    return _SPEC_PATH


@dataclass(frozen=True)
class McpBuildContext:
    """Inputs a server builder may need to materialise its spec.

    Fields are referenced from ``mcp_servers.yaml`` as ``${field}`` placeholders
    and in ``requires:`` presence checks. Intentionally minimal — add a field
    here when a server needs a new runtime parameter. ``ado_org`` is the Azure
    DevOps organisation the ``ado`` server targets (resolved from the PR/repo
    identity); ``None`` ⇒ the ``ado`` server is unavailable this run (non-ADO
    repo / ADO MCP disabled).
    """

    ado_org: str | None = None


#: A builder returns the inner server spec dict (the value placed under
#: ``mcpServers[name]``) or ``None`` when not buildable in the given context.
McpServerBuilder = Callable[[McpBuildContext], dict | None]


def _context_value(ctx: McpBuildContext, field: str) -> Any:
    return getattr(ctx, field, None)


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _interpolate(value: Any, ctx: McpBuildContext) -> Any:
    """Substitute ``${field}`` placeholders (stripped) within strings/lists.

    Mirrors the legacy ``org.strip()`` behaviour for the ``ado`` org. Non-string
    scalars (ints, etc.) pass through untouched, preserving JSON types.
    """
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            raw = _context_value(ctx, m.group(1))
            return "" if raw is None else str(raw).strip()

        return _PLACEHOLDER_RE.sub(repl, value)
    if isinstance(value, list):
        return [_interpolate(v, ctx) for v in value]
    return value


#: Meta keys in a ``mcp_servers.yaml`` entry that are NEVER emitted into the MCP
#: JSON. ``requires`` gates buildability; ``bindings`` is the ADO Tool-Bindings
#: advertisement source (consumed by ``context/ado_context``). Everything else is
#: emitted verbatim (``command``/``args``/``tools``/``timeout``).
_META_KEYS = ("requires", "bindings")


def _placeholders(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(_PLACEHOLDER_RE.findall(value))
    if isinstance(value, list):
        return frozenset(field for item in value for field in _placeholders(item))
    if isinstance(value, dict):
        return frozenset(field for item in value.values() for field in _placeholders(item))
    return frozenset()


def validate_mcp_specs(document: object, *, source: str = "mcp_servers.yaml") -> list[str]:
    """Validate the declarative MCP registry without starting any servers."""
    if not isinstance(document, dict):
        return [f"{source}: top level must be a mapping"]
    servers = document.get("servers")
    if not isinstance(servers, dict):
        return [f"{source}: 'servers' must be a mapping"]

    errors: list[str] = []
    context_fields = {field.name for field in fields(McpBuildContext)}
    for raw_name, raw_spec in servers.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            errors.append(f"{source}: server names must be non-empty strings, got {raw_name!r}")
            continue
        name = raw_name
        prefix = f"{source}: server {name!r}"
        if not isinstance(raw_spec, dict):
            errors.append(f"{prefix} spec must be a mapping")
            continue
        spec = raw_spec

        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{prefix} `command` must be a non-empty string")
        valid_lists: dict[str, bool] = {}
        for key in ("args", "tools", "requires"):
            value = spec.get(key, [])
            valid_lists[key] = isinstance(value, list) and all(
                isinstance(item, str) for item in value
            )
            if not valid_lists[key]:
                errors.append(f"{prefix} `{key}` must be a list of strings")
        timeout = spec.get("timeout")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int)):
            errors.append(f"{prefix} `timeout` must be an integer")

        requires = spec.get("requires")
        required_fields = (
            {item for item in requires if isinstance(item, str)}
            if isinstance(requires, list)
            else set()
        )
        for field in sorted(required_fields - context_fields):
            errors.append(
                f"{prefix} requires unknown McpBuildContext field {field!r} "
                f"(known: {sorted(context_fields)})"
            )
        placeholders = _placeholders(
            {key: value for key, value in spec.items() if key not in _META_KEYS}
        )
        for field in sorted(placeholders - context_fields):
            errors.append(
                f"{prefix} uses unknown McpBuildContext placeholder ${{{field}}} "
                f"(known: {sorted(context_fields)})"
            )
        for field in sorted(placeholders - required_fields):
            errors.append(f"{prefix} placeholder ${{{field}}} must also appear in `requires`")
        bindings = spec.get("bindings", [])
        if not isinstance(bindings, list):
            errors.append(f"{prefix} `bindings` must be a list of [capability, tool] rows")
            continue
        if not valid_lists["tools"]:
            continue
        try:
            _parse_bindings(name, spec)
        except ValueError as exc:
            errors.append(str(exc).replace("mcp_servers.yaml", source, 1))
    return errors


def validate_mcp_registry(path: Path | None = None) -> list[str]:
    """Load and statically validate an MCP registry file."""
    source = path or _SPEC_PATH
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{source}: cannot load MCP registry: {exc}"]
    return validate_mcp_specs(document, source=str(source))


def _make_builder(spec: dict) -> McpServerBuilder:
    """Compile one ``mcp_servers.yaml`` entry into a builder.

    ``requires:`` / ``bindings:`` are meta-fields (:data:`_META_KEYS`, not
    emitted): every ``requires`` context field must be non-empty or the builder
    returns ``None``; ``bindings`` is advertising metadata read elsewhere. The
    emitted spec preserves the YAML key order (``command, args, tools, timeout``)
    so serialisation is byte-stable / parity-preserving.
    """
    requires = list(spec.get("requires", []) or [])
    body_keys = [k for k in spec if k not in _META_KEYS]

    def builder(ctx: McpBuildContext) -> dict | None:
        for field in requires:
            if _is_empty(_context_value(ctx, field)):
                return None
        return {k: _interpolate(spec[k], ctx) for k in body_keys}

    return builder


def _parse_bindings(name: str, spec: dict) -> tuple[tuple[str, str], ...]:
    """Parse a server's ``bindings:`` meta block into ``(capability, tool)`` pairs.

    Each row is a 2-item ``[capability, bare_tool]`` list in the YAML. Malformed
    rows (wrong arity / non-string) fail loud — a broken advertisement must
    surface at import, not render a garbled Tool Bindings section. Every advertised tool must
    also be in the server's ``tools:`` allowlist (unless it is the ``"*"`` wildcard)
    — else the ADO Tool Bindings section would advertise a capability the server
    never exposes (the prose/tool drift this design fights).
    """
    raw = spec.get("bindings") or []
    allow = spec.get("tools") or []
    wildcard = "*" in allow
    out: list[tuple[str, str]] = []
    for row in raw:
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            raise ValueError(
                f"mcp_servers.yaml: bindings row must be [capability, tool], got {row!r}"
            )
        cap, tool = row
        if not (isinstance(cap, str) and isinstance(tool, str)):
            raise ValueError(f"mcp_servers.yaml: bindings row values must be strings, got {row!r}")
        if not wildcard and tool not in allow:
            raise ValueError(
                f"mcp_servers.yaml: server {name!r} binds capability {cap!r} to tool {tool!r} "
                f"which is absent from its `tools:` allowlist {sorted(allow)}"
            )
        out.append((cap, tool))
    return tuple(out)


def _load_specs(
    path: Path = _SPEC_PATH,
) -> tuple[
    dict[str, McpServerBuilder],
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, frozenset[str]],
    dict[str, dict[str, Any]],
]:
    """Parse ``mcp_servers.yaml`` into builders, bindings and tool inventories.

    Fails loud on malformed YAML or a non-mapping ``servers`` block (a broken
    spec must surface at import, not silently inject nothing).
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = validate_mcp_specs(data, source=str(path))
    if errors:
        raise ValueError("\n".join(errors))
    assert isinstance(data, dict)
    servers = data["servers"]
    assert isinstance(servers, dict)
    registry: dict[str, McpServerBuilder] = {
        str(name): _make_builder(spec) for name, spec in servers.items()
    }
    bindings: dict[str, tuple[tuple[str, str], ...]] = {
        str(name): _parse_bindings(str(name), spec) for name, spec in servers.items()
    }
    tools: dict[str, frozenset[str]] = {
        str(name): frozenset(str(t) for t in (spec.get("tools") or ()))
        for name, spec in servers.items()
    }
    return registry, bindings, tools, servers


#: The authoritative MCP-server registry, compiled from ``mcp_servers.yaml``.
#: ``logical name -> builder``. The key is also the copilot tool-name prefix
#: (``<name>-<tool>``).
#:
_REGISTRY, _SERVER_BINDINGS, _SERVER_TOOLS, _SERVER_SPECS = _load_specs()


def mcp_server_specs() -> dict[str, dict[str, Any]]:
    """Return a detached copy of the validated declarative server specs."""
    return deepcopy(_SERVER_SPECS)


def mcp_server_placeholders(name: str) -> frozenset[str]:
    """Return the runtime context placeholders used by one registered server."""
    spec = _SERVER_SPECS.get(name)
    if spec is None:
        return frozenset()
    return _placeholders({key: value for key, value in spec.items() if key not in _META_KEYS})


def registered_server_names() -> frozenset[str]:
    """The set of server names Roundtable may inject (the registry keys).

    Consumed by ``validate_agents`` to reject a per-agent ``mcp`` declaration that
    names an unregistered server.
    """
    return frozenset(_REGISTRY)


def server_bindings(name: str) -> tuple[tuple[str, str], ...]:
    """The ``(capability, bare_tool)`` advertising rows declared for ``name``.

    SSOT is the server's ``bindings:`` block in ``mcp_servers.yaml`` (meta, never
    emitted into the MCP JSON). ``context/ado_context`` renders these into the
    per-agent ``## ADO Tool Bindings`` section. Empty tuple for a server with
    no ``bindings`` (or an unknown name).
    """
    return _SERVER_BINDINGS.get(name, ())


def server_tool_names(name: str) -> frozenset[str]:
    """The bare tools the named server exposes (empty for an unknown server)."""
    return _SERVER_TOOLS.get(name, frozenset())


def cli_tool_name(server: str, tool: str) -> str:
    """The name a tool is reported under once the runtime namespaces it.

    Confirmed against the runtime (``session.tools.getCurrentMetadata``): declaring
    ``ado-code-read/repo_get_file_content`` yields a tool whose ``name`` is
    ``ado-code-read-repo_get_file_content`` and whose ``namespaced_name`` keeps the
    slash. So the server's logical name is the prefix, and the two spellings are
    the *call* form and the *declaration* form of one tool — not a guess.
    """
    return f"{server}-{tool}"


def cli_tool_inventory() -> frozenset[str]:
    """Every MCP tool the registry can provision, under its CLI-visible name.

    The universe against which an observed tool name is *attributable*. A name
    outside it is not "ungranted" — it is simply not something the registry could
    have served.
    """
    return frozenset(
        cli_tool_name(server, tool) for server, tools in _SERVER_TOOLS.items() for tool in tools
    )


def resolve_mcp_config(
    names: Sequence[str],
    ctx: McpBuildContext,
) -> dict[str, dict] | None:
    """Resolve requested registered servers into an SDK ``mcp_servers`` mapping.

    Iterates ``names`` in **sorted, de-duplicated** order (deterministic argv).
    Unknown names are skipped (``validate_agents`` rejects them statically, so a
    skip here only ever happens for a name dropped after validation). A builder
    returning ``None`` (not buildable in ``ctx`` — e.g. ``ado`` with no org) is
    likewise skipped. Returns ``None`` when no server resolves.
    """
    servers: dict[str, dict] = {}
    for name in sorted(set(names)):
        builder = _REGISTRY.get(name)
        if builder is None:
            continue
        spec = builder(ctx)
        if spec is None:
            continue
        servers[name] = spec
    if not servers:
        return None
    return servers
