"""graph_config: typed access to the agent execution graph.

The GRAPH DATA (deps, roles, scheduling params) is the py-owned SSOT in
``agent_graph.yaml``. This module loads it **lazily** into a cached
:class:`Configuration` — one **named instance** among (eventually) many — via
:func:`get_configuration` (the runtime SSOT); the derived lookups (``by_key``, the
role sets) are **computed** fields on that object, not hand-maintained here.
Consumers read :func:`get_configuration` and its fields directly — there are no
ambient module globals, so nothing loads at import and there is no global state.

To change the graph (add an agent, flip a role, edit deps), edit
``agent_graph.yaml`` and run ``roundtable doctor`` — never this file.

INVARIANT: the ``edges`` dependency graph is acyclic (the selected executor
enforces this — ``DagExecutor.validate`` via ``doctor`` offline, and the scheduler
at run time; the DAG executor derives the schedule purely from these edges — there
are no waves). A required edge is an input contract; an optional edge gates
ordering only.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from roundtable.bundle import CONFIGS_DIR, config_root
from roundtable.runtime import APP_NAME

from ..consolidation import RenderSpec
from .domain_values import DomainValues, load_domain_values
from .predicates import Predicate


@dataclass(frozen=True)
class McpBinding:
    """One agent→MCP wiring declared under ``mcp:`` in ``agent_graph.yaml``.

    ``server`` is a registry name (``mcp/mcp_servers.yaml``) the agent opts
    into for SDK custom-agent provisioning. ``usage`` is an
    optional ``Shared/MCP/*.md`` file appended to the agent's system prompt (role
    policy for the server — never a tool inventory; doctor lints that). Both are
    statically validated by ``validate_agents``.
    """

    server: str
    usage: str | None = None


@dataclass(frozen=True)
class Edge:
    """A producer→consumer **data dependency** (what data flows into a node).

    ``required`` is the only tag: ``True`` = the old hard-dep (delivered as
    ``[REQUIRED]``, emits a ``[REQUIRED — UNAVAILABLE]`` stub when the producer is
    missing, adjudicated by the delivery check); ``False`` = the old soft-dep
    (``[OPTIONAL]``, silent when missing). Ordering (DAG depth, wait-for-terminal)
    is the **executor's** concern, NOT the edge's — so an edge stays
    executor-agnostic.

    ``when`` is an optional declarative predicate over the ``source`` producer's
    schema-validated output (see :mod:`config.predicates`). It makes the edge
    **conditional**: the consumer activates only when the predicate holds; a false
    predicate SKIPS the consumer (reusing the ``skipped``/``na`` tri-state). Only
    valid on a required edge (doctor enforces) — a false ``when`` is a branch/skip
    decision, not an absent optional input.

    ``backward`` reserves the future *revise* grammar: a ``backward: true`` edge names a
    dependency in the REVERSE direction (a downstream node asking an upstream node
    to re-run with feedback), optionally under ``budget`` re-activations. It is
    parsed and doctor-validated but **inert under the DAG executor** — excluded from
    forward dependency, depth, cycle-detection and scheduling (see
    :attr:`GraphEntry.forward_edges`), so a config may declare a revise loop today
    without the DAG rejecting it as a cycle. Executing it awaits an iterative
    executor; the grammar exists now so config #N needs no engine change."""

    source: str
    required: bool = True
    when: Predicate | None = None
    backward: bool = False
    budget: int | None = None


@dataclass(frozen=True)
class FanOut:
    """Declarative fan-out spec for a ``kind: map`` node (Q4).

    ``over`` names the runtime list the node fans out over as
    ``<producer>.<dotted.field>`` — the producer is one of the node's declared edge
    sources, and the field is an array in that producer's ``output_schema`` (doctor
    validates both, offline). The map node is activated once per element of that
    list (cardinality decided at runtime from the producer's output), each element
    rendered as its own entry in the ordered activation log. Frozen + hashable so a
    :class:`GraphEntry` stays frozen and round-trips by value equality."""

    over: str

    @property
    def producer(self) -> str:
        """The producer key — the segment of ``over`` before the first dot."""
        return self.over.split(".", 1)[0]

    @property
    def field_path(self) -> str:
        """The dotted list-field path within the producer's output (after the dot)."""
        parts = self.over.split(".", 1)
        return parts[1] if len(parts) == 2 else ""


@dataclass(frozen=True)
class ConsolidationSpec:
    """Declarative wiring for a generic ``consolidate`` reducer node.

    The whole domain surface of a fan-in consolidation, declared inline on the node
    (no per-node Python): ``extract`` names a registered extract seam
    (:mod:`roundtable.context.extractors`) that maps the node's valid deps onto
    neutral records; ``adjacency_gap`` is the single positional gap-single-linkage
    threshold; ``render`` is the :class:`~roundtable.consolidation.core.RenderSpec`
    vocabulary (preamble, group/item labels) of the delivered body. ``zero_drop``
    marks this as a **complete-corpus** consolidation — a reconciler whose dossier
    must aggregate EVERY finding-producer, so doctor's Layer-8 completeness check
    fires on it (see ``validation.coherence.validate_dossier_completeness``); a
    scoped/additive consolidation leaves it ``False``. Frozen + hashable
    (``RenderSpec`` is frozen) so a :class:`GraphEntry` stays frozen and round-trips
    by value equality."""

    extract: str
    adjacency_gap: int
    render: RenderSpec
    zero_drop: bool = False


@dataclass(frozen=True)
class PowershellToolPolicy:
    """Execution controls for the concrete SDK ``powershell`` tool."""

    invocation_cap_seconds: int
    detached_allowed: bool


@dataclass(frozen=True)
class ReadPowershellToolPolicy:
    """Execution controls for the concrete SDK ``read_powershell`` tool."""

    poll_cap_seconds: int


@dataclass(frozen=True)
class ToolPolicy:
    """Optional per-tool execution controls declared by one graph entry."""

    powershell: PowershellToolPolicy | None = None
    read_powershell: ReadPowershellToolPolicy | None = None

    @property
    def tool_names(self) -> tuple[str, ...]:
        names: list[str] = []
        if self.powershell is not None:
            names.append("powershell")
        if self.read_powershell is not None:
            names.append("read_powershell")
        return tuple(names)


@dataclass(frozen=True)
class GraphEntry:
    """Single entry in the agent configuration graph."""

    key: str
    prompt_path: str
    # ── Data-dependency edges (SSOT: agent_graph.yaml `edges`). ONE tagged list —
    # each ``Edge`` names a producer ``source`` this node consumes plus a
    # ``required`` flag (required = old hard-dep, optional = old soft-dep). Read
    # ergonomically via the ``dep_keys`` / ``required_dep_keys`` /
    # ``optional_dep_keys`` computed views below (single ``edges`` source of truth).
    edges: tuple[Edge, ...]
    emoji: str
    # ── Node kind (SSOT: agent_graph.yaml `kind`; default 'llm'). The ONE declared
    # field that says how a node executes: 'llm' spawns a Copilot session; 'code'
    # invokes a registered pure Python enricher fn. The legacy ``is_llm`` boolean is
    # a DERIVED read-only property (== kind == 'llm') so callers keep reading
    # ``entry.is_llm`` unchanged. Orthogonal to ``non_graph_infra`` (which says
    # whether the node is DAG-scheduled at all).
    kind: str = "llm"
    # Registry name of the pure enricher fn invoked when ``kind == 'code'``
    # (resolved via context.enrichers.get_enricher). ``None`` for LLM nodes. Doctor
    # enforces: code ⇒ set + resolvable; llm ⇒ unset.
    code_fn: str | None = None
    # Fan-out spec for a ``kind: map`` node (see :class:`FanOut`). ``None`` for every
    # other kind. Doctor enforces: map ⇒ set + its ``over`` names a declared-edge
    # producer whose ``output_schema`` has that array field; non-map ⇒ unset.
    fan_out: FanOut | None = None
    # Declarative consolidation wiring for a generic ``consolidate`` reducer node
    # (see :class:`ConsolidationSpec`). ``None`` unless ``code_fn == 'consolidate'``.
    # Doctor enforces: code_fn==consolidate ⇒ set + its ``extract`` resolves in the
    # extractor registry; consolidation set ⇒ code_fn==consolidate.
    consolidation: ConsolidationSpec | None = None
    # Heading under which this node's output is delivered into a consumer's context
    # by the generic injection path (context.injection). ``None`` ⇒ the default
    # ``## Context from <key>``. A custom label lets a producer own the prompt
    # vocabulary consumers expect (e.g. ``## security_intent_pack``) with NO
    # name-matching special-case in injection.py. The ``[REQUIRED]`` /
    # ``[REQUIRED — UNAVAILABLE]`` state suffix is appended by the delivery path.
    delivery_label: str | None = None
    # ── Static system-prompt composition (SSOT: agent_graph.yaml `system_prompt`).
    # ``prompt_path`` is the agent's own body file (``system_prompt.instructions`` in
    # the YAML) — ALWAYS composed first. ``shared_context`` is the ordered list of
    # ``Shared/*`` files appended after it, verbatim, in exactly this order (no hidden
    # reordering). ``model`` (one SDK model) and the optional ``tools`` allowlist
    # live here (moved out of the ``.agent.md`` frontmatter so the graph, not a
    # prompt file, is where an operator reads what an agent was granted). ``mcp``
    # is the OPT-IN MCP
    # wiring (see ``McpBinding``): each entry names a registry server the agent
    # receives PLUS an optional usage-policy file appended to its system prompt.
    shared_context: tuple[str, ...] = ()
    model: str | None = None
    # None = unrestricted SDK tool surface; () = no tools; non-empty = strict
    # custom-agent allowlist that the runtime ENFORCES (proved: ``tools: []`` yields
    # only the always-on set). Entries are CONCRETE runtime tool names validated
    # against the static ``capabilities/runtime_inventory.yaml``, or
    # ``<server>/<tool>`` MCP tools. The runtime silently ignores a name it does not
    # serve, so doctor rejects unknown names before a review starts.
    tools: tuple[str, ...] | None = None
    # Optional execution controls keyed by concrete SDK tool names. The graph owns
    # the declaration; the SDK backend only compiles and enforces it.
    tool_policy: ToolPolicy | None = None
    mcp: tuple[McpBinding, ...] = ()
    timeout_seconds: int | None = None
    # ``agent_id`` is the canonical snake_case id (the OVG schema / finding-index key)
    # and ``display_name`` the report label — the TWO identity forms the graph owns as
    # SSOT (config/agent_identity derives the whole resolver from them; no hardcoded
    # roster). Both are authored per non-source entry; doctor Layer 4 enforces presence
    # + agent_id uniqueness.
    agent_id: str | None = None
    display_name: str | None = None
    git_context_mode: str | None = None  # 'full' | 'changed-files-only' | 'omit'
    # ── OVG consolidation (P-ovg): the declarative output-contract surface.
    # ``output_schema`` names a JSON Schema file under ``configs/inspectorx/schemas/`` (the
    # single source of truth for the agent's output shape, driving BOTH the OVG
    # schema gate AND the LLM output-format hint). ``ovg_gates`` is the ordered
    # list of gates to run (each a mapping with a ``gate`` name + optional
    # ``level``/``hint`` overrides). Both required for is_llm agents (doctor Layer 5).
    output_schema: str | None = None
    ovg_gates: tuple[dict, ...] | None = None
    # ``output_example`` optionally overrides the canonical shape the LLM output
    # hint imitates. It names an example document under ``configs/inspectorx/schemas/`` whose
    # root IS the example object. Agents that share a minimal ``output_schema``
    # (e.g. finding_min) use this to advertise their own richer per-agent shape
    # without de-consolidating the shared validation schema. When absent, the
    # hint falls back to the schema's own ``examples[0]``. Never validated more
    # strictly than the schema — the doctor asserts it conforms to output_schema.
    output_example: str | None = None
    inject_repo_instructions: bool = False
    # ── Role flags (authored per-entry in agent_graph.yaml; the module
    # frozensets below are COMPUTED from these, replacing 5 scattered hardcoded
    # sets — one vocabulary, one home per agent). All default False.
    terminal: bool = False  # produces a verdict-stage output (gated last)
    non_graph_infra: bool = False  # runs outside the scheduled graph

    @property
    def is_llm(self) -> bool:
        """Derived: an LLM agent (spawns a Copilot session) iff ``kind == 'llm'``.

        SSOT is ``kind``; this property preserves the historical ``entry.is_llm``
        read surface (scheduler, doctor, archetype/model/mcp maps) with no churn.
        """
        return self.kind == "llm"

    @property
    def forward_edges(self) -> tuple[Edge, ...]:
        """Edges that impose a FORWARD dependency (everything but ``backward``).

        The DAG executor's dependency, depth, cycle-detection and scheduling all read
        the graph through this — so a reserved ``backward: true`` revise edge stays
        inert (declared, validated, but never a forward dep or a cycle) until an
        iterative executor consumes it.
        """
        return tuple(e for e in self.edges if not e.backward)

    @property
    def dep_keys(self) -> tuple[str, ...]:
        """All producer keys this node consumes, in declared edge order."""
        return tuple(e.source for e in self.forward_edges)

    @property
    def required_dep_keys(self) -> tuple[str, ...]:
        """Producer keys on ``required`` edges (the old ``hard_deps``)."""
        return tuple(e.source for e in self.forward_edges if e.required)

    @property
    def optional_dep_keys(self) -> tuple[str, ...]:
        """Producer keys on optional edges (the old ``soft_deps``)."""
        return tuple(e.source for e in self.forward_edges if not e.required)

    @property
    def mcp_server_names(self) -> tuple[str, ...]:
        """The registry server names this agent opts into (``mcp[].server``)."""
        return tuple(b.server for b in self.mcp)

    @property
    def mcp_usage_files(self) -> tuple[str, ...]:
        """The declared ``mcp[].usage`` policy files (skipping bindings with none)."""
        return tuple(b.usage for b in self.mcp if b.usage)


# The graph SSOT (agent_graph.yaml) is loaded LAZILY via get_configuration() — no
# import-time side-effect. The agent_graph import is deferred to here (not module
# top) so GraphEntry is already defined when agent_graph imports it back — avoids a
# circular import.
from .loader import (  # noqa: E402
    _bundle_fingerprint_inputs,
    _fingerprint,
    record_to_entry,
)

_DEFAULT_CONFIGURATION = "inspectorx"


@dataclass(frozen=True)
class PublishingPolicy:
    """Configuration-owned defaults for publishing review findings."""

    default_min_severity: str | None = None


_DEFAULT_PUBLISHING_POLICY = PublishingPolicy()


@dataclass(frozen=True)
class Configuration:
    """A single named agent configuration — one instance among (eventually) many.

    Bundles the loaded graph (``entries``, declaration order preserved) with the
    strategies it selects (``executor``/``sink``/``max_steps``/``sink_cardinality``)
    and every lookup derived from the graph (``by_key`` and the role sets). This is
    the explicit object the engine reads instead of ambient module globals;
    :func:`get_configuration` is the lazily-cached SSOT that produces it.
    """

    name: str
    root: Path
    entries: tuple[GraphEntry, ...]
    executor: str
    sink: str | None
    projector: str | None
    report: str | None
    max_steps: int | None
    sink_cardinality: str
    plugins: tuple[str, ...]
    product_name: str
    product_emoji: str
    report_title: str
    publishing: PublishingPolicy
    by_key: dict[str, GraphEntry]
    terminal_agents: frozenset[str]
    non_graph_infra_agents: frozenset[str]
    domain_values: DomainValues | None
    fingerprint: str

    @property
    def publisher(self) -> str | None:
        """Canonical publisher name; ``sink`` remains the stored compatibility field."""
        return self.sink

    @classmethod
    def _build_unchecked(
        cls,
        *,
        name: str,
        root: Path | None = None,
        entries: tuple[GraphEntry, ...],
        executor: str,
        sink: str | None,
        max_steps: int | None,
        projector: str | None = None,
        report: str | None = None,
        sink_cardinality: str = "one",
        plugins: tuple[str, ...] = (),
        product_name: str = APP_NAME,
        product_emoji: str = "",
        report_title: str = "Review",
        publishing: PublishingPolicy = _DEFAULT_PUBLISHING_POLICY,
        domain_values: DomainValues | None = None,
        fingerprint: str = "",
    ) -> Configuration:
        """Assemble a Configuration, computing every derived lookup from ``entries``.

        The role sets (terminal / non-graph-infra) are COMPUTED from per-entry
        boolean flags — membership lives as a flag on the agent's YAML
        entry (one home, one vocabulary), never hand-maintained here.
        """
        return cls(
            name=name,
            root=(root or config_root()).resolve(),
            entries=entries,
            executor=executor,
            sink=sink,
            projector=projector,
            report=report,
            max_steps=max_steps,
            sink_cardinality=sink_cardinality,
            plugins=plugins,
            product_name=product_name,
            product_emoji=product_emoji,
            report_title=report_title,
            publishing=publishing,
            by_key={e.key: e for e in entries},
            terminal_agents=frozenset(e.key for e in entries if e.terminal),
            non_graph_infra_agents=frozenset(e.key for e in entries if e.non_graph_infra),
            domain_values=domain_values,
            fingerprint=fingerprint,
        )

    @classmethod
    def from_document(cls, document: object, *, root: str | Path) -> Configuration:
        """Validate and construct the canonical configuration model from one document."""
        from .meta import validate_config_document

        errors = validate_config_document(document)
        if errors:
            raise ValueError("\n".join(errors))
        assert isinstance(document, dict)
        resolved_root = Path(root).expanduser().resolve()
        entries = tuple(record_to_entry(record) for record in document["agents"])
        branding = document.get("branding") or {}
        publishing = document.get("publishing") or {}
        sink = document.get("publisher", document.get("sink", "azure_devops"))
        max_steps = document.get("max_steps")
        domain_values = load_domain_values(document, resolved_root)
        fingerprint_inputs = _bundle_fingerprint_inputs(resolved_root, document)
        fingerprint_inputs.append(
            (
                "domain-values",
                domain_values.fingerprint_bytes() if domain_values is not None else b"{}",
            )
        )
        return cls._build_unchecked(
            name=str(document.get("name") or "inspectorx"),
            root=resolved_root,
            entries=entries,
            executor=str(document.get("executor") or "dag"),
            sink=str(sink) if sink is not None else None,
            projector=(
                str(document["projector"]) if document.get("projector") is not None else None
            ),
            report=str(document["report"]) if document.get("report") is not None else None,
            max_steps=max_steps if isinstance(max_steps, int) and max_steps > 0 else None,
            sink_cardinality=str(document.get("sink_cardinality") or "one"),
            plugins=tuple(str(module) for module in document.get("plugins") or ()),
            product_name=str(branding.get("product_name") or APP_NAME),
            product_emoji=str(branding.get("product_emoji") or ""),
            report_title=str(branding.get("report_title") or "Review"),
            publishing=PublishingPolicy(
                default_min_severity=(
                    str(publishing["default_min_severity"])
                    if publishing.get("default_min_severity") is not None
                    else None
                )
            ),
            domain_values=domain_values,
            fingerprint=_fingerprint(fingerprint_inputs),
        )

    @classmethod
    def from_file(cls, source: str | Path) -> Configuration:
        path = Path(source).expanduser().resolve()
        if path.is_dir():
            path /= "agent_graph.yaml"
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as err:
            raise ValueError(f"cannot load configuration document {path}: {err}") from err
        return cls.from_document(document, root=path.parent)

    @property
    def identity(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": 1,
            "name": self.name,
            "graphConfigSha": self.fingerprint,
            "domainValues": self.domain_values.to_dict() if self.domain_values is not None else {},
        }
        try:
            relative = self.root.relative_to(CONFIGS_DIR.resolve())
        except ValueError:
            payload.update(kind="external", path=str(self.root))
        else:
            payload.update(kind="shipped", bundle=relative.as_posix())
        return payload


@cache
def _configuration_for_root(root: Path) -> Configuration:
    path = root / "agent_graph.yaml"
    if not path.is_file():
        raise ValueError(f"configuration root has no agent_graph.yaml: {root}")
    return Configuration.from_file(path)


def get_configuration(root: str | Path | None = None) -> Configuration:
    """The named agent configuration — lazily loaded on first call, then cached.

    This is the SSOT the engine reads. Loading happens on first access (no
    import-time side-effect) from the py-owned yaml, then memoized. ``name`` is a
    named-instance selector: only the canonical ``inspectorx`` config exists today,
    so any other name is a loud config error (more register here when they land —
    the file rename to ``configurations/<name>.yaml`` is deferred until then). The
    derived role sets and maps live as fields on the returned object.
    """
    resolved = config_root() if root is None else Path(root).expanduser().resolve()
    return _configuration_for_root(resolved)


def get_product_name(root: str | Path | None = None) -> str:
    """The human PRODUCT name the output sink stamps on user-facing surfaces —
    sourced from the active config's ``branding.product_name`` (default: engine name)."""
    return get_configuration(root).product_name


def get_product_emoji(root: str | Path | None = None) -> str:
    """The glyph shown before the product name on user-facing surfaces — sourced from
    the active config's ``branding.product_emoji`` (default: none)."""
    return get_configuration(root).product_emoji


def get_report_title(root: str | Path | None = None) -> str:
    """The report headline noun phrase (e.g. 'Deep Compute Review') — sourced from
    the active config's ``branding.report_title`` (default: 'Review')."""
    return get_configuration(root).report_title


_REGISTERED_PLUGIN_MODULES: set[str] = set()


def register_config_plugins(cfg: Configuration) -> None:
    """Import a configuration's bundle plugin modules so its DOMAIN seams register.

    The generic engine registries (``context.enrichers`` / ``context.extractors`` /
    ``output.projector`` / ``output.report``) ship no built-in seam; a config declares
    its domain built-ins in ``plugins:`` and each module registers them at import. Call
    this once before any code that resolves such a seam by name — i.e. before
    ``validate_graph_config`` (doctor/offline), before a run
    (:mod:`roundtable.review.flow`), and before publishing (``cli._configured_sink``,
    whose sink resolves the config's ``projector:``). Idempotent: each
    module is imported at most once per process (a re-import would re-run the
    ``register_*`` calls, which raise on a duplicate name). Kept OUT of
    :func:`get_configuration` to preserve its no-import-time-side-effect contract.
    """
    import importlib

    for module in cfg.plugins:
        if module in _REGISTERED_PLUGIN_MODULES:
            continue
        importlib.import_module(module)
        _REGISTERED_PLUGIN_MODULES.add(module)


@cache
def _schema_is_finding_bearing(schema_name: str, root: Path) -> bool:
    """True iff ``schema_name`` declares an ``x-finding-array`` property.

    Mirrors what ``finding_extractor._finding_arrays`` indexes — a schema is
    finding-bearing iff one of its top-level properties is marked ``x-finding-array``.
    """
    path = root / "schemas" / schema_name
    try:
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    props = (schema or {}).get("properties") or {}
    return any(isinstance(s, dict) and s.get("x-finding-array") for s in props.values())


def finding_producing_agent_keys(
    *,
    include_terminal: bool = True,
    entries: tuple[GraphEntry, ...] | None = None,
    config: Configuration | None = None,
) -> frozenset[str]:
    """Agent keys whose ``output_schema`` declares an ``x-finding-array`` property.

    The SSOT for "who contributes findings to a dossier": derived from the very
    schemas the finding extractor indexes, so a newly added finding agent joins the
    corpus the moment its schema marks its finding array — nothing to hand-maintain.
    Pass ``include_terminal=False`` to drop terminal reconcilers (SeverityInflator,
    Judge) from the set. ``entries`` defaults to the ambient configuration; pass an
    explicit graph to compute the corpus of a specific (e.g. draft) configuration.
    """
    cfg = config or get_configuration()
    if entries is None:
        entries = cfg.entries
    return frozenset(
        e.key
        for e in entries
        if e.output_schema
        and _schema_is_finding_bearing(e.output_schema, cfg.root)
        and (include_terminal or not e.terminal)
    )


def get_entry(key: str, config: Configuration | None = None) -> GraphEntry | None:
    return (config or get_configuration()).by_key.get(key)


def find_dep_cycle(by_key: dict[str, GraphEntry], key_set: set[str]) -> list[str] | None:
    """Return a cycle (as a key path ``a -> b -> … -> a``) in the edge dependency
    graph, or ``None`` if acyclic. Deps outside ``key_set`` are ignored (they are
    always-satisfied roots — e.g. non-graph infra). DFS with a recursion stack.

    Acyclicity is an **executor-specific** requirement (the DAG executor needs it to
    terminate; an iterative executor would not), so this algorithm is owned by
    :meth:`orchestration.executor.DagExecutor.validate`, not by the executor-agnostic
    :func:`validate_graph_config`."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(by_key, WHITE)
    stack: list[str] = []

    def visit(k: str) -> list[str] | None:
        color[k] = GREY
        stack.append(k)
        entry = by_key.get(k)
        deps = entry.dep_keys if entry else ()
        for d in deps:
            if d not in key_set:
                continue
            if color[d] == GREY:  # back-edge → cycle
                return [*stack[stack.index(d) :], d]
            if color[d] == WHITE:
                found = visit(d)
                if found:
                    return found
        stack.pop()
        color[k] = BLACK
        return None

    for k in by_key:
        if color[k] == WHITE:
            found = visit(k)
            if found:
                return found
    return None


def validate_graph_config(
    entries: tuple[GraphEntry, ...] | None = None,
    config: Configuration | None = None,
) -> None:
    """Validate the agent graph: dep existence, kind/label integrity.

    Executor-**agnostic** structural checks only: every declared dep exists, the
    ``kind`` contract holds (code/reducer ⇒ a resolvable ``code_fn`` and no LLM-only
    fields; llm ⇒ a ``prompt_path`` and no ``code_fn``), and every node's effective
    ``delivery_label`` is globally unique (so no two injected sections can collide
    under one heading). **Acyclicity is NOT checked here** — it is the selected
    executor's concern (:meth:`orchestration.executor.DagExecutor.validate`, run by
    ``doctor`` alongside this). Raises ``ValueError`` listing all violations.
    """
    from roundtable.context import (
        enricher_names,  # local: avoid import cycle
        extractor_names,  # local: avoid import cycle
    )

    cfg = config or get_configuration()
    if entries is None:
        entries = cfg.entries
    register_config_plugins(cfg)
    known_enrichers = enricher_names()
    known_extractors = extractor_names()
    key_set = {e.key for e in entries}
    errors: list[str] = []
    label_owner: dict[str, str] = {}

    from roundtable.engine import validate_node_entry

    default_floor = cfg.publishing.default_min_severity
    severity_values = cfg.domain_values.get("severity") if cfg.domain_values is not None else None
    if default_floor is not None:
        if severity_values is None:
            errors.append(
                f"{cfg.name}: publishing.default_min_severity {default_floor!r} requires "
                "domain_values.severity"
            )
        elif default_floor not in severity_values:
            errors.append(
                f"{cfg.name}: publishing.default_min_severity {default_floor!r} is not in "
                f"domain_values.severity {list(severity_values)}"
            )

    for entry in entries:
        for edge in entry.edges:
            if edge.source not in key_set:
                kind = "required" if edge.required else "optional"
                errors.append(f'{entry.key}: {kind} edge "{edge.source}" not found')
            if edge.when is not None and not edge.required and not edge.backward:
                errors.append(
                    f'{entry.key}: conditional edge "{edge.source}" has a when: '
                    f"predicate but is optional (when: requires required: true — a "
                    f"false predicate skips the consumer, not an absent optional input)"
                )
            if edge.budget is not None and not edge.backward:
                errors.append(
                    f'{entry.key}: edge "{edge.source}" declares budget: without '
                    f"backward: true (budget bounds a backward revise loop)"
                )
            if edge.budget is not None and edge.budget < 1:
                errors.append(f'{entry.key}: backward edge "{edge.source}" budget must be >= 1')
        errors.extend(validate_node_entry(entry, known_enrichers))

        if entry.fan_out is not None and entry.kind != "map":
            errors.append(f"{entry.key}: fan_out is only valid on kind=map (got kind={entry.kind})")
        if entry.tools is not None and entry.kind != "llm":
            errors.append(f"{entry.key}: tools is only valid on kind=llm (got kind={entry.kind})")
        if entry.tool_policy is not None and entry.kind != "llm":
            errors.append(
                f"{entry.key}: tool_policy is only valid on kind=llm (got kind={entry.kind})"
            )

        # A ``consolidation`` block is the declarative surface of the generic
        # ``consolidate`` reducer: the two are mutually implied, and the named extract
        # seam must resolve (like ``code_fn`` against the enricher registry above).
        if entry.consolidation is not None and entry.code_fn != "consolidate":
            errors.append(
                f"{entry.key}: consolidation is only valid with code_fn=consolidate "
                f"(got code_fn={entry.code_fn!r})"
            )
        if entry.code_fn == "consolidate":
            if entry.consolidation is None:
                errors.append(f"{entry.key}: code_fn=consolidate but no consolidation block")
            elif entry.consolidation.extract not in known_extractors:
                errors.append(
                    f"{entry.key}: consolidation.extract {entry.consolidation.extract!r} not in "
                    f"the extractor registry (known: {sorted(known_extractors)})"
                )

        label = entry.delivery_label or f"## Context from {entry.key}"
        if label in label_owner:
            errors.append(
                f"{entry.key}: delivery_label {label!r} collides with {label_owner[label]!r} "
                f"(headings must be globally unique so injected sections never merge)"
            )
        else:
            label_owner[label] = entry.key

    if errors:
        raise ValueError("agent graph validation failed:\n  " + "\n  ".join(errors))
