"""agent_graph: load the agent execution graph from ``agent_graph.yaml``.

`agent_graph.yaml` is the **single source of truth** for the whole agent system —
per-agent dependencies, scheduling params, role flags, AND each agent's full input:
``system_prompt`` (``instructions`` + ordered ``shared_context``), ``model`` and
optional ``mcp`` (opt-in MCP servers + per-role usage policy). The ``.agent.md``
files hold only
``description:`` + prompt body; everything about *which* files compose an agent's
system prompt, *in what order*, and how it is routed lives here — statically
verifiable before any Copilot session is spawned. **One home, no duplication.**

This module is the loader: ``load_agent_graph()`` parses the YAML into the typed
``GraphEntry`` tuple exposed as ``get_configuration().entries``. The role
frozensets (``terminal_agents`` etc.) are computed from the per-entry boolean role
flags, replacing 5 scattered hardcoded sets.

``entry_to_dict`` / ``record_to_entry`` are exact inverses and define the on-disk
schema (the nested ``system_prompt`` block maps to the flat ``prompt_path`` +
``shared_context`` fields on ``GraphEntry``); a round-trip test asserts
``record_to_entry(entry_to_dict(e)) == e`` for every loaded entry.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from roundtable.bundle import config_root, graph_path
from roundtable.mcp import mcp_config_path
from roundtable.runtime import APP_NAME

from .predicates import parse_predicate, predicate_to_dict

if TYPE_CHECKING:  # avoid a circular import at module load (graph_config imports us)
    from ..consolidation import RenderSpec
    from .model import ConsolidationSpec, Edge, FanOut, GraphEntry


def _edge_to_dict(edge: Edge) -> dict[str, Any]:
    """Serialize one ``Edge`` to its YAML record (``when:`` omitted when absent)."""
    rec: dict[str, Any] = {"from": edge.source, "required": edge.required}
    if edge.when is not None:
        rec["when"] = predicate_to_dict(edge.when)
    if edge.backward:
        rec["backward"] = True
    if edge.budget is not None:
        rec["budget"] = edge.budget
    return rec


def _edge_from_dict(ed: dict[str, Any]) -> Edge:
    """Build one ``Edge`` from its YAML record. Inverse of :func:`_edge_to_dict`."""
    from .model import Edge

    when = ed.get("when")
    return Edge(
        source=ed["from"],
        required=bool(ed.get("required", True)),
        when=parse_predicate(when) if when is not None else None,
        backward=bool(ed.get("backward", False)),
        budget=ed.get("budget"),
    )


def _fanout_to_dict(fo: FanOut) -> dict[str, Any]:
    """Serialize a ``FanOut`` to its YAML record. Inverse of :func:`_fanout_from_dict`."""
    return {"over": fo.over}


def _fanout_from_dict(rec: dict[str, Any]) -> FanOut:
    """Build a ``FanOut`` from its YAML record. Inverse of :func:`_fanout_to_dict`."""
    from .model import FanOut

    return FanOut(over=rec["over"])


def _tool_policy_to_dict(policy: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {}
    if policy.powershell is not None:
        rec["powershell"] = {
            "invocation_cap_seconds": policy.powershell.invocation_cap_seconds,
            "detached_allowed": policy.powershell.detached_allowed,
        }
    if policy.read_powershell is not None:
        rec["read_powershell"] = {
            "poll_cap_seconds": policy.read_powershell.poll_cap_seconds,
        }
    return rec


def _tool_policy_from_dict(rec: dict[str, Any]) -> Any:
    from .model import PowershellToolPolicy, ReadPowershellToolPolicy, ToolPolicy

    powershell = rec.get("powershell")
    read_powershell = rec.get("read_powershell")
    return ToolPolicy(
        powershell=(
            PowershellToolPolicy(
                invocation_cap_seconds=int(powershell["invocation_cap_seconds"]),
                detached_allowed=bool(powershell["detached_allowed"]),
            )
            if powershell is not None
            else None
        ),
        read_powershell=(
            ReadPowershellToolPolicy(poll_cap_seconds=int(read_powershell["poll_cap_seconds"]))
            if read_powershell is not None
            else None
        ),
    )


# RenderSpec fields with defaults — omitted from the serialized render block when at
# their default so the YAML stays minimal (round-trip restores them from the default).
_RENDER_DEFAULTS = {
    "checksum_header": "Count Checksum",
    "no_group_label": "(no location)",
    "depth": "index",
    "appendix_header": "Details",
}


def _consolidation_to_dict(spec: ConsolidationSpec) -> dict[str, Any]:
    """Serialize a ``ConsolidationSpec`` to its YAML record. Inverse of
    :func:`_consolidation_from_dict`. Defaulted render fields are omitted."""
    render = {
        "preamble": spec.render.preamble,
        "empty_note": spec.render.empty_note,
        "group_prefix": spec.render.group_prefix,
        "item_singular": spec.render.item_singular,
        "group_plural_label": spec.render.group_plural_label,
        "item_plural_label": spec.render.item_plural_label,
    }
    for name, default in _RENDER_DEFAULTS.items():
        val = getattr(spec.render, name)
        if val != default:
            render[name] = val
    rec: dict[str, Any] = {
        "extract": spec.extract,
        "adjacency_gap": spec.adjacency_gap,
        "render": render,
    }
    if spec.zero_drop:
        rec["zero_drop"] = True
    return rec


def _consolidation_from_dict(rec: dict[str, Any]) -> ConsolidationSpec:
    """Build a ``ConsolidationSpec`` from its YAML record. Inverse of
    :func:`_consolidation_to_dict`."""
    from .model import ConsolidationSpec

    return ConsolidationSpec(
        extract=rec["extract"],
        adjacency_gap=int(rec["adjacency_gap"]),
        render=_render_spec_from_dict(rec["render"]),
        zero_drop=bool(rec.get("zero_drop", False)),
    )


def _render_spec_from_dict(rec: dict[str, Any]) -> RenderSpec:
    """Build a ``RenderSpec`` from its YAML render block (defaults fill omitted keys)."""
    from ..consolidation import RenderSpec  # lazy: leaf module, avoid import cost

    fields = {
        "preamble": rec["preamble"],
        "empty_note": rec["empty_note"],
        "group_prefix": rec["group_prefix"],
        "item_singular": rec["item_singular"],
        "group_plural_label": rec["group_plural_label"],
        "item_plural_label": rec["item_plural_label"],
    }
    for name in _RENDER_DEFAULTS:
        if name in rec:
            fields[name] = rec[name]
    return RenderSpec(**fields)


def graph_config_sha(root: Path | None = None) -> str:
    """12-hex sha256 of the EFFECTIVE config bundle — the agent-system fingerprint.

    Hashes the graph AND everything it references: the gate manifest, every schema
    document (``schemas/**``, including the shared vocabulary), and every referenced
    prompt body (each agent's instructions + ``shared_context`` + MCP usage files).
    Any edit to routing, gates, output contracts, or prompts changes the digest, so
    two reviews are directly comparable only when it matches. Inputs are hashed in a
    stable order, each tagged by its identifying label, so a rename alone still moves
    the digest.
    """
    from .model import Configuration

    return Configuration.from_file((root or config_root()).resolve()).fingerprint


def _fingerprint(inputs: list[tuple[str, bytes]]) -> str:
    """Order-sensitive, path-tagged sha256 over ``(label, bytes)`` pairs (12 hex)."""
    h = hashlib.sha256()
    for label, data in inputs:
        h.update(label.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()[:12]


def _bundle_fingerprint_inputs(
    root: Path | None = None,
    document: dict[str, Any] | None = None,
) -> list[tuple[str, bytes]]:
    """Ordered ``(label, bytes)`` inputs that define the effective-bundle fingerprint."""
    root = (root or config_root()).resolve()
    if document is None:
        loaded = yaml.safe_load((root / "agent_graph.yaml").read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("configuration document root must be a mapping")
        document = loaded
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    inputs: list[tuple[str, bytes]] = [("graph", canonical)]
    inputs += _dir_fingerprint_inputs(root / "schemas", "schema")
    inputs += _optional_file_input(root / "gates.yaml", "gates")
    inputs += _plugin_fingerprint_inputs(root, tuple(document.get("plugins") or ()))
    entries = tuple(record_to_entry(rec) for rec in document.get("agents") or ())
    inputs += _prompt_fingerprint_inputs(root / "prompts" / "Reviewer", entries)
    inputs += _optional_file_input(mcp_config_path(), "mcp-servers")
    return inputs


def _dir_fingerprint_inputs(root: Path, tag: str) -> list[tuple[str, bytes]]:
    """Every file under ``root`` (recursive), sorted by posix relpath, tagged ``tag:rel``."""
    if not root.is_dir():
        return []
    out: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() in {".json", ".yaml", ".yml"}
        ):
            out.append((f"{tag}:{path.relative_to(root).as_posix()}", path.read_bytes()))
    return out


def _bundle_package_name(root: Path) -> str:
    names = [root.name]
    parent = root.parent
    while (parent / "__init__.py").is_file():
        names.insert(0, parent.name)
        parent = parent.parent
    return ".".join(names)


def _module_source(root: Path, bundle_package: str, module: str) -> Path | None:
    if module == bundle_package:
        path = root / "__init__.py"
    elif module.startswith(bundle_package + "."):
        relative = module[len(bundle_package) + 1 :].replace(".", "/")
        source = root / f"{relative}.py"
        package = root / relative / "__init__.py"
        path = source if source.is_file() else package
    else:
        return None
    return path.resolve() if path.is_file() else None


def _local_imports(path: Path, module: str, bundle_package: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base_parts = package.split(".")
            keep = len(base_parts) - node.level + 1
            base = ".".join(base_parts[:keep])
            target = ".".join(part for part in (base, node.module or "") if part)
            found.add(target)
            if node.module is None:
                found.update(f"{target}.{alias.name}" for alias in node.names)
        elif node.module:
            found.add(node.module)
    return {
        name for name in found if name == bundle_package or name.startswith(bundle_package + ".")
    }


def _plugin_fingerprint_inputs(
    root: Path,
    declared_modules: tuple[str, ...],
) -> list[tuple[str, bytes]]:
    """Declared bundle modules plus only their bundle-local Python dependencies."""
    bundle_package = _bundle_package_name(root)
    pending = list(declared_modules)
    sources: dict[Path, str] = {}
    visited: set[str] = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        source = _module_source(root, bundle_package, module)
        if source is None:
            continue
        sources[source] = module
        pending.extend(_local_imports(source, module, bundle_package) - visited)

        relative_parent = source.parent.relative_to(root)
        cursor = root
        package_module = bundle_package
        init = cursor / "__init__.py"
        if init.is_file():
            sources[init.resolve()] = package_module
        for part in relative_parent.parts:
            cursor /= part
            package_module += f".{part}"
            init = cursor / "__init__.py"
            if init.is_file():
                sources[init.resolve()] = package_module

    return [
        (f"plugin:{path.relative_to(root).as_posix()}", path.read_bytes())
        for path in sorted(sources)
    ]


def _optional_file_input(path: Path, tag: str) -> list[tuple[str, bytes]]:
    """A single ``(tag, bytes)`` input, or nothing if the file is absent."""
    return [(tag, path.read_bytes())] if path.is_file() else []


def _prompt_fingerprint_inputs(
    root: Path,
    entries: tuple[GraphEntry, ...] | None = None,
) -> list[tuple[str, bytes]]:
    """Every prompt body the graph references (instructions + shared_context + MCP usage)."""
    refs = sorted(
        {
            ref
            for entry in entries or load_agent_graph(root.parent.parent / "agent_graph.yaml")
            for ref in (entry.prompt_path, *entry.shared_context, *entry.mcp_usage_files)
            if ref
        }
    )
    out: list[tuple[str, bytes]] = []
    for rel in refs:
        path = root / rel
        if path.is_file():
            out.append((f"prompt:{rel}", path.read_bytes()))
    return out


# Per-entry boolean role flags (default False). Order = emit order in the YAML.
_ROLE_FLAGS: tuple[str, ...] = (
    "terminal",
    "non_graph_infra",
)

# Optional scalar fields emitted only when not None.
_OPTIONAL_SCALARS: tuple[str, ...] = (
    "agent_id",
    "display_name",
    "code_fn",
    "delivery_label",
    "timeout_seconds",
    "git_context_mode",
)


def entry_to_dict(e: GraphEntry) -> dict[str, Any]:
    """Serialize a ``GraphEntry`` to a clean record (defaults omitted).

    Inverse of :func:`record_to_entry`. Defines the ``agent_graph.yaml`` schema.
    The static system prompt is emitted as a nested ``system_prompt`` block
    (``instructions`` = the agent body, always first; ``shared_context`` = the
    ordered list of appended ``Shared/*`` files). ``model`` / ``tools`` / ``mcp`` are the
    per-agent LLM config (moved out of the ``.agent.md``
    frontmatter — the graph is now the single source of truth). Non-LLM entries
    (empty ``prompt_path``) emit none of these.
    """
    rec: dict[str, Any] = {
        "key": e.key,
    }
    if e.kind != "llm":
        rec["kind"] = e.kind
    rec["emoji"] = e.emoji
    if e.prompt_path:
        sp: dict[str, Any] = {"instructions": e.prompt_path}
        if e.shared_context:
            sp["shared_context"] = list(e.shared_context)
        rec["system_prompt"] = sp
    if e.model:
        rec["model"] = e.model
    if e.tools is not None:
        rec["tools"] = list(e.tools)
    if e.tool_policy is not None:
        rec["tool_policy"] = _tool_policy_to_dict(e.tool_policy)
    if e.mcp:
        rec["mcp"] = [
            {"server": b.server, "usage": b.usage} if b.usage else {"server": b.server}
            for b in e.mcp
        ]
    if e.edges:
        rec["edges"] = [_edge_to_dict(edge) for edge in e.edges]
    if e.fan_out is not None:
        rec["fan_out"] = _fanout_to_dict(e.fan_out)
    if e.consolidation is not None:
        rec["consolidation"] = _consolidation_to_dict(e.consolidation)
    for name in _OPTIONAL_SCALARS:
        val = getattr(e, name)
        if val is not None:
            rec[name] = val
    if e.inject_repo_instructions:
        rec["inject_repo_instructions"] = True
    if e.output_schema is not None:
        rec["output_schema"] = e.output_schema
    if e.output_example is not None:
        rec["output_example"] = e.output_example
    if e.ovg_gates is not None:
        rec["ovg_gates"] = [dict(g) for g in e.ovg_gates]
    for flag in _ROLE_FLAGS:
        if getattr(e, flag):
            rec[flag] = True
    return rec


def record_to_entry(rec: dict[str, Any]) -> GraphEntry:
    """Build a ``GraphEntry`` from a YAML record. Inverse of :func:`entry_to_dict`.

    Reads the nested ``system_prompt`` block (``instructions`` + ``shared_context``);
    falls back to a bare top-level ``prompt_path`` for backward compatibility during
    the migration window (pre-``system_prompt`` yaml still loads)."""
    from .model import GraphEntry, McpBinding

    sp = rec.get("system_prompt") or {}
    instructions = sp.get("instructions") if isinstance(sp, dict) else None
    if not instructions:
        instructions = rec.get("prompt_path", "")
    shared_context = tuple(sp.get("shared_context", ())) if isinstance(sp, dict) else ()
    mcp = tuple(
        McpBinding(server=m["server"], usage=m.get("usage")) for m in (rec.get("mcp") or [])
    )

    return GraphEntry(
        key=rec["key"],
        prompt_path=instructions,
        edges=tuple(_edge_from_dict(ed) for ed in rec.get("edges", ())),
        emoji=rec["emoji"],
        kind=rec.get("kind", "llm"),
        code_fn=rec.get("code_fn"),
        fan_out=(_fanout_from_dict(rec["fan_out"]) if rec.get("fan_out") is not None else None),
        consolidation=(
            _consolidation_from_dict(rec["consolidation"])
            if rec.get("consolidation") is not None
            else None
        ),
        delivery_label=rec.get("delivery_label"),
        shared_context=shared_context,
        model=rec.get("model"),
        tools=(tuple(rec["tools"]) if rec.get("tools") is not None else None),
        tool_policy=(
            _tool_policy_from_dict(rec["tool_policy"])
            if rec.get("tool_policy") is not None
            else None
        ),
        mcp=mcp,
        timeout_seconds=rec.get("timeout_seconds"),
        agent_id=rec.get("agent_id"),
        display_name=rec.get("display_name"),
        git_context_mode=rec.get("git_context_mode"),
        output_schema=rec.get("output_schema"),
        output_example=rec.get("output_example"),
        ovg_gates=(tuple(rec["ovg_gates"]) if rec.get("ovg_gates") is not None else None),
        inject_repo_instructions=bool(rec.get("inject_repo_instructions", False)),
        terminal=bool(rec.get("terminal", False)),
        non_graph_infra=bool(rec.get("non_graph_infra", False)),
    )


_DEFAULT_EXECUTOR = "dag"
_DEFAULT_SINK = "azure_devops"
_DEFAULT_PROJECTOR: str | None = None
_DEFAULT_REPORT: str | None = None
_DEFAULT_CONFIG_NAME = "inspectorx"
_DEFAULT_SINK_CARDINALITY = "one"
_DEFAULT_REPORT_TITLE = "Review"


def load_config_name(path: Path | None = None) -> str:
    """Top-level ``name:`` key from the config yaml (default ``'inspectorx'``).

    The config's self-identifier — the name under which it is one instance among
    (eventually) many. Absent/blank ⇒ the canonical ``inspectorx`` default, so an
    unannotated config keeps its identity.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("name")
        if val:
            return str(val)
    return _DEFAULT_CONFIG_NAME


def _branding_block(path: Path | None = None) -> dict[str, Any]:
    """The top-level ``branding:`` mapping from the config yaml ({} when absent)."""
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        block = data.get("branding")
        if isinstance(block, dict):
            return block
    return {}


def load_product_name(path: Path | None = None) -> str:
    """Top-level ``branding.product_name`` — the human PRODUCT name the output sink
    stamps on user-facing surfaces (report header/footer, PR-comment attribution).

    Absent/blank ⇒ the ENGINE name (:data:`config.branding.APP_NAME`): a config with
    no branding speaks as the engine, keeping the engine/bundle identity split.
    """
    val = _branding_block(path).get("product_name")
    return str(val) if val else APP_NAME


def load_product_emoji(path: Path | None = None) -> str:
    """Top-level ``branding.product_emoji`` — the glyph the output sink shows before
    the product name on user-facing surfaces (report header, PR-comment footer).

    Absent/blank ⇒ empty, and every surface renders the name alone: a bundle's mark
    belongs to the BUNDLE, so the ENGINE ships none and hardcodes none.
    """
    val = _branding_block(path).get("product_emoji")
    return str(val) if val else ""


def load_report_title(path: Path | None = None) -> str:
    """Top-level ``branding.report_title`` — the report headline noun phrase (e.g.
    ``'Deep Compute Review'``). Absent/blank ⇒ the generic ``'Review'`` default.
    """
    val = _branding_block(path).get("report_title")
    return str(val) if val else _DEFAULT_REPORT_TITLE


def load_executor_name(path: Path | None = None) -> str:
    """Top-level ``executor:`` key from ``agent_graph.yaml`` (default ``'dag'``).

    Selects the graph-execution strategy (:func:`orchestration.executor.get_executor`
    resolves the name). Absent/blank ⇒ the DAG default, so an unannotated graph runs
    exactly as before.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("executor")
        if val:
            return str(val)
    return _DEFAULT_EXECUTOR


def load_sink_name(path: Path | None = None) -> str | None:
    """Top-level ``sink:`` key from ``agent_graph.yaml`` (default ``'azure_devops'``).

    Selects the post-graph output sink (:func:`output.sink.get_sink` resolves the
    name). Absent/blank ⇒ the Azure DevOps default, so an unannotated config publishes
    exactly as before.

    An **explicit** ``sink: null`` ⇒ ``None``: publishing is an optional step each
    config owns, and a topology that declares it does not publish must not be handed
    the ADO sink behind its back. ``config_meta.schema.yaml`` has always accepted
    null here; folding it into the default made that declaration a silent no-op.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return _DEFAULT_SINK
    if "sink" in data and "publisher" in data:
        raise ValueError("agent_graph.yaml cannot define both 'publisher' and legacy 'sink'")
    key = "publisher" if "publisher" in data else "sink"
    if key in data and data[key] is None:
        return None
    val = data.get(key)
    return str(val) if val else _DEFAULT_SINK


def load_publisher_name(path: Path | None = None) -> str | None:
    """Load canonical ``publisher:`` or the legacy ``sink:`` alias."""
    return load_sink_name(path)


def load_sink_cardinality(path: Path | None = None) -> str:
    """Top-level ``sink_cardinality:`` key from ``agent_graph.yaml`` (default ``'one'``).

    The config-level result-contract: how many out-degree-0 nodes the runnable graph
    is expected to have (doctor Layer-11 enforces it). ``'one'`` — the default — means
    a single domain result read unambiguously by topology (the Roundtable contract);
    ``'any'`` opts a multi-output topology out of the check. NOT an executor law — the
    engine's ``sinks()`` has no cardinality opinion — so it lives here as config.
    Absent/blank ⇒ the ``'one'`` default, so an unannotated config validates as before.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("sink_cardinality")
        if val:
            return str(val)
    return _DEFAULT_SINK_CARDINALITY


def load_projector_name(path: Path | None = None) -> str | None:
    """Top-level ``projector:`` key from ``agent_graph.yaml`` (default ``None``).

    Selects the post-graph publish projector — the topology-specific adapter that
    maps this graph's output onto the neutral publish contract
    (:func:`output.projector.get_projector` resolves the name). There is NO generic
    default: projection is inherently topology-specific, so ``None`` (absent/blank)
    means the config cannot publish until it declares a registered projector. The
    Roundtable config declares ``projector: verdict_overlay`` explicitly,
    so the coupling to its shape lives in the config, never in generic code.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("projector")
        if val:
            return str(val)
    return _DEFAULT_PROJECTOR


def load_report_name(path: Path | None = None) -> str | None:
    """Top-level ``report:`` key from ``agent_graph.yaml`` (default ``None``).

    Selects the session-report renderer — the topology-specific adapter that turns
    this graph's output into the human ``verdict.md``
    (:func:`output.report.get_report` resolves the name). There is NO generic
    default: what a report *says* depends on the shape a config's Judge emits, so
    ``None`` (absent/blank) means this config renders the slim verdict stub rather
    than borrowing another config's reading of its output.

    Independent of ``sink:`` — every run writes a report, publishing is optional.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("report")
        if val:
            return str(val)
    return _DEFAULT_REPORT


def load_step_budget(path: Path | None = None) -> int | None:
    """Top-level ``max_steps:`` key from ``agent_graph.yaml`` (default ``None``).

    The universal step budget: the executor caps total node activations at this
    number (a run that would exceed it terminates early — the mechanism bounded
    loops rely on). Absent/blank/non-positive ⇒ ``None`` (unbounded), so an
    unannotated graph runs with no budget, exactly as before.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("max_steps")
        if isinstance(val, int) and val > 0:
            return val
    return None


def load_plugin_modules(path: Path | None = None) -> tuple[str, ...]:
    """Top-level ``plugins:`` list from ``agent_graph.yaml`` (default empty).

    Each entry is a dotted module path (e.g.
    ``roundtable.configs.inspectorx.plugins.context_plugins``)
    that a config's bundle ships to populate the generic engine registries with its
    DOMAIN built-ins — the deterministic enrichers and consolidation extract seams that
    know this config's shape. Importing the module runs its ``register_enricher`` /
    ``register_extractor`` calls; :func:`graph_config.register_config_plugins` performs
    the (idempotent) import before doctor-validate or a run resolves any ``code_fn`` /
    ``consolidation.extract`` by name. Absent/blank ⇒ no plugins, so a config that
    wires only generic seams needs no ``plugins:`` key.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        val = data.get("plugins")
        if isinstance(val, list):
            return tuple(str(m) for m in val if m)
    return ()


def load_agent_graph(path: Path | None = None) -> tuple[GraphEntry, ...]:
    """Parse ``agent_graph.yaml`` into the ordered ``GraphEntry`` tuple.

    Declaration order is preserved (the scheduler relies on it for stable
    same-depth tie-breaking). Raises on malformed YAML or a missing required key
    (fail loud at graph-build — never silently drop an agent).
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list) or not agents:
        raise ValueError(f"agent_graph.yaml: missing or empty 'agents' list ({src})")
    return tuple(record_to_entry(rec) for rec in agents)


def get_agent_emoji(key: str) -> str:
    """The agent's glyph from the ACTIVE graph, or ``""`` when unknown.

    Read through the live Configuration rather than a process-wide cache, so that
    switching bundles changes the glyphs with it — exactly as it already changes the
    display names this sits beside.
    """
    from .model import get_entry

    entry = get_entry(key)
    return (entry.emoji if entry else "") or ""
