"""agent_setup: materialize the Roundtable agent graph as programmatic GitHub
Copilot SDK custom agents, handed to the SDK backend in memory (never written to
the operator's global `~/.copilot/agents`).

The agent set, per-agent system prompt and model routing all derive
SOLELY from `agent_graph.yaml` (the single source of truth) — never from a
filesystem walk of the bundle. Each `is_llm` entry declares:

    system_prompt:
      instructions: Agents/CodeCorrectness.agent.md   # the agent body (always first)
      shared_context:                                 # appended in this exact order
        - Shared/AgentPreamble.md
        - Shared/InputAwareness.md
        - Shared/ContextAwareness.md
    model: gpt-5.6-sol                                # one SDK model
    mcp:                                              # optional, opt-in MCP wiring
      - server: ado-work-items                        #   registry server + optional
        usage: Shared/MCP/ado.work-items.md           #   per-role usage policy file

Composition (:func:`compose_system_prompt`) is plain, explicit concatenation:
``instructions`` first (primacy edge), then each ``shared_context`` file in listed
order — no hidden reordering, no ``<shared_reference>`` provenance envelopes. Every
part is LF-normalized, trimmed; blank parts dropped; ``\\n\\n``-joined.

:func:`graph_custom_agents` maps each ``is_llm`` graph key to its
:class:`NativeAgentFields` (``name`` = the graph key / SDK ``--agent`` id;
``description`` from the instructions file's own frontmatter; ``prompt`` = the
composed system prompt), which the SDK backend passes as programmatic
``custom_agents``. Model and tool access are graph-owned SDK custom-agent fields.

The bundle ``.agent.md`` files hold only ``description:`` + body.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from roundtable.capabilities import RuntimeCapabilities
    from roundtable.graph import Configuration, GraphEntry, ToolPolicy

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

#: A raw runtime-namespaced ADO MCP tool name — the ``<server>-<tool>`` form a call
#: is reported under, e.g. ``ado-code-read-repo_get_file_content`` /
#: ``ado-work-items-wit_get_work_item``. (The ``<server>/<tool>`` slash form is the
#: *declaration* spelling in ``agent_graph.yaml``; this regex matches the other one.)
#: Requires the ``ado-`` prefix AND an underscore (the domain_method separator),
#: so it matches tool names but NOT server names (``ado-work-items`` — no ``_``)
#: nor context fields (``ado_org`` — no hyphen). Used by ``validate_agents`` to
#: keep MCP usage prose free of tool inventories (the anti-drift lint).
_RAW_MCP_TOOL_RE = re.compile(r"\bado-[\w-]*_\w+")


def _find_raw_mcp_tool_names(text: str) -> set[str]:
    """Raw ADO MCP tool names appearing in ``text`` (see :data:`_RAW_MCP_TOOL_RE`)."""
    return set(_RAW_MCP_TOOL_RE.findall(text))


def _lf(text: str) -> str:
    """LF-normalize: the parity contract is equality after CRLF/CR -> LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_agent_file(text: str) -> tuple[dict[str, Any], str]:
    """Split an `.agent.md` into (frontmatter dict, body)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("agent file has no YAML frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    return fm, body


def compose_system_prompt(entry: Any, bundle_root: Path) -> str:
    """Compose an agent's full system prompt from the graph SSOT (agent_graph.yaml).

    Order is EXACT and explicit: the agent body (``system_prompt.instructions``,
    always first — its YAML frontmatter stripped), then each
    ``system_prompt.shared_context`` file in listed order (raw ``Shared/*.md``, no
    frontmatter) — no hidden reordering. Every part is LF-normalized, trimmed;
    blank parts dropped; ``\\n\\n``-joined. Plain concatenation — no
    ``<shared_reference>`` envelopes.

    ``entry`` is a ``GraphEntry``; both ``prompt_path`` (the instructions file) and
    every ``shared_context`` ref are bundle-root-relative paths. Any ``mcp[].usage``
    policy files are appended LAST (after ``shared_context``), in ``mcp`` order —
    the per-role MCP usage guidance an agent gets when it opts into a server.
    """
    _fm, body = parse_agent_file((bundle_root / entry.prompt_path).read_text(encoding="utf-8"))
    raw_parts = [body]
    raw_parts += [(bundle_root / ref).read_text(encoding="utf-8") for ref in entry.shared_context]
    raw_parts += [(bundle_root / ref).read_text(encoding="utf-8") for ref in entry.mcp_usage_files]

    composed: list[str] = []
    for raw in raw_parts:
        text = _lf(raw).strip()
        if text:
            composed.append(text)
    return "\n\n".join(composed)


def full_system_prompt(entry: Any, bundle_root: Path) -> str:
    """Complete runtime prompt: composition + graph policy + output contract.

    :func:`compose_system_prompt` stays the pure body+``shared_context``+``mcp_usage``
    concatenation (its documented contract); this wrapper appends the agent's STABLE
    graph-derived tool policy and stable output contract so neither runtime fact is
    duplicated in prompt prose. Single seam for the SDK runtime path and
    ``--dump-prompts``.
    """
    from roundtable.engine import build_output_contract

    composed = compose_system_prompt(entry, bundle_root)
    policy = _tool_policy_prompt(entry)
    contract = build_output_contract(entry, bundle_root.parent.parent / "schemas")
    return "\n\n".join(part for part in (composed, policy, contract) if part)


def _tool_policy_prompt(entry: Any) -> str:
    policy = entry.tool_policy
    if policy is None:
        return ""
    lines = ["## Tool execution policy"]
    if policy.powershell is not None:
        lines.append(_powershell_policy_line(policy.powershell))
    if policy.read_powershell is not None:
        lines.append(_read_powershell_policy_line(policy.read_powershell))
    return "\n".join(lines)


def _powershell_policy_line(policy: Any) -> str:
    detached = "allowed" if policy.detached_allowed else "disabled"
    return (
        f"- `powershell` invocations are capped at {policy.invocation_cap_seconds} "
        f"seconds; detached execution is {detached}."
    )


def _read_powershell_policy_line(policy: Any) -> str:
    return f"- `read_powershell.delay` is capped at {policy.poll_cap_seconds} seconds."


@dataclass(frozen=True)
class NativeAgentFields:
    """An agent's graph-owned SDK custom-agent fields.

    MCP server specs are resolved per review and attached later; everything static
    comes from the graph plus the prompt file's authored description.
    """

    name: str
    description: str
    prompt: str
    display_name: str | None = None
    tools: tuple[str, ...] | None = None
    tool_policy: ToolPolicy | None = None
    model: str = ""
    mcp_servers: dict[str, dict] | None = None


def _native_agent_fields(entry: Any, bundle_root: Path) -> NativeAgentFields:
    """Resolve one graph entry to the static SDK custom-agent fields."""
    fm, _body = parse_agent_file((bundle_root / entry.prompt_path).read_text(encoding="utf-8"))
    description = fm.get("description") or entry.key
    return NativeAgentFields(
        name=entry.key,
        display_name=entry.display_name,
        description=description,
        prompt=full_system_prompt(entry, bundle_root),
        tools=entry.tools,
        tool_policy=entry.tool_policy,
        model=entry.model,
    )


def graph_custom_agents(
    bundle_root: Path, entries: tuple[GraphEntry, ...] | None = None
) -> dict[str, NativeAgentFields]:
    """Map ``GraphEntry.key`` → :class:`NativeAgentFields`, for every ``is_llm`` agent.

    Hands the SDK backend each agent's identity + composed prompt in memory (passed
    as ``custom_agents``), all derived from the graph SSOT (``agent_graph.yaml``).
    """
    if entries is None:
        from roundtable.graph import get_configuration

        entries = get_configuration().entries
    return {e.key: _native_agent_fields(e, bundle_root) for e in entries if e.is_llm}


def load_mcp_needs_map(
    bundle_root: Path | None = None, entries: tuple[GraphEntry, ...] | None = None
) -> dict[str, list[str]]:
    """Map agent **key** (``GraphEntry.key``) → declared opt-in ``mcp`` server names,
    read straight from the graph SSOT (``agent_graph.yaml``).

    Servers are exact and opt-in: an agent receives a server only if it lists it
    under ``mcp:``. Only agents with a non-empty block appear.

    ``bundle_root`` is accepted for call-site compatibility but unused.
    """
    if entries is None:
        from roundtable.graph import get_configuration

        entries = get_configuration().entries
    return {e.key: list(e.mcp_server_names) for e in entries if e.is_llm and e.mcp}


@dataclass
class ValidationReport:
    """Structured outcome of :func:`validate_agents` — the doctor as a *service*.

    ``errors`` fail validation (they are what the old string-raising API joined and
    raised); ``warnings`` are non-blocking coherence advisories. Returning this
    instead of raising lets a caller (a human via ``doctor``, or the future
    config-authoring skill iterating on a *draft* config) read the outcome as data.
    The production fail-loud contract is preserved via :meth:`raise_if_failed`.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def failure_text(self) -> str:
        """The historical failure message (``validate_agents failed:`` + violations)."""
        return "validate_agents failed:\n  " + "\n  ".join(self.errors)

    def raise_if_failed(self) -> None:
        """Raise the historical ``ValueError`` (same message) when validation failed."""
        if self.errors:
            raise ValueError(self.failure_text)


def validate_agents(
    bundle_root_dir: Path | None = None,
    *,
    config: Configuration | None = None,
    runtime_capabilities: RuntimeCapabilities | None = None,
) -> ValidationReport:
    """Consolidated static validation of an agent graph against the prompt bundle.

    ONE function, the single home for config-vs-bundle integrity. Run
    **lazily** — at review graph-build and via the ``doctor`` CLI subcommand —
    **never at import** (a broken bundle must not crash ``--help`` / ``view``).

    ``config`` selects WHICH configuration to validate; it defaults to the ambient
    :func:`~roundtable.graph.model.get_configuration` — so validation
    works on any named (e.g. draft) config, not just the shipped singleton. Returns
    a :class:`ValidationReport` (never raises for validation errors — call
    :meth:`ValidationReport.raise_if_failed` to keep the fail-loud contract).

    Layers, all violations collected then raised together (fail loud, never
    silently degrade):
      1. **Graph integrity** — delegates to ``graph_config.validate_graph_config``
         (dep existence, ``is_llm ⇒ prompt_path``, ``kind`` contract, tool-round
         ceiling), then runs the **selected executor's** own structural check
         (``get_executor(config.executor).validate`` — acyclicity for the DAG; an
         iterative executor would not require it).
      2. **Prompt presence** — every ``is_llm`` entry's ``instructions`` file and each
         of its ``shared_context`` files resolves to an actual bundle file (explicit
         composition ⇒ a declared ref is injected by construction; presence is the
         whole contract).
      3. **Graph coverage** — every ``is_llm`` entry declares ``model`` (model
         routing) in the SSOT. When ``runtime_capabilities`` is supplied,
         every declared model and built-in tool must be available in that
         authenticated runtime.
         Every declared ``mcp`` binding names a registry-known server, and its
         optional ``usage`` policy file exists and carries NO raw MCP tool name
         (usage prose = role intent only; the tool inventory is the server
         ``bindings:`` SSOT — the anti-drift lint).
      4. **Agent identity integrity** (:func:`_validate_agent_identity`)
         — every key resolves a unique registry canonical id matching its
         ``agent_id``; every key has a report display name.
      5. **Schema↔gate coherence** (:func:`~roundtable.validation.coherence.validate_ovg_coherence`)
         — every is_llm agent is wired with an ``output_schema`` + ``ovg_gates``; each
         schema loads/compiles and its example self-validates; every wired gate + hint
         resolves; and no wired gate is *dead* against its schema. Coverage advisories
         (a schema declaring fields a gate guards without wiring it) are collected into
         the optional ``warnings`` sink — surfaced by ``doctor``, non-blocking.
      6. **Cross-agent reference lint**
         (:func:`~roundtable.validation.coherence.validate_cross_agent_refs`) — every
         ``<delivery_label>.<field>`` reference in an agent's prompt must resolve to a
         declared ``hard_dep``/``soft_dep`` edge on the producing node AND a top-level
         field of that producer's ``output_schema``. Guards class-B cross-agent coupling
         so a renamed/removed producer field can't leave a prompt silently lying.
      9. **Conditional-edge predicate coherence**
          (:func:`~roundtable.validation.coherence.validate_conditional_predicates`) —
          every conditional edge's ``when:`` field-path must resolve into its ``source``
          producer's ``output_schema`` (an unreachable path is a silent dead branch that
          always skips the consumer).
      10. **Fan-out field coherence**
          (:func:`~roundtable.validation.coherence.validate_fan_out`) — every
          ``kind: map`` node's ``fan_out.over`` must name a required-edge producer whose
          ``output_schema`` types that field as an array (otherwise the node fans out over
          a missing or non-list value).
      11. **Output-contract single sink** — the runnable graph must have EXACTLY ONE
          sink (out-degree-0 node), so ``review``'s topology-based read of the run's
          domain result is unambiguous. A config-level result-contract (not an executor
          law — the engine's ``sinks()`` has no cardinality opinion), checked over the
          *runnable* graph via ``get_executor(config.executor).sinks``.
      12. **Output-sink resolves** — the top-level ``sink:`` key must name a registered
          adapter (``output.sink.get_sink``), so a typo fails the doctor rather than
          first surfacing at ``publish`` time (mirrors the ``executor:`` name check).

    Populates and returns a :class:`ValidationReport`. ``errors`` list every
    violation; ``warnings`` carry non-blocking coherence advisories.
    """
    import roundtable.graph as wc
    from roundtable.bundle import bundle_root as _bundle_root
    from roundtable.capabilities import (
        always_on_tool_names,
        builtin_tool_names,
        inventory_provenance,
    )
    from roundtable.mcp import (
        registered_server_names,
        server_tool_names,
        validate_mcp_registry,
    )

    cfg = config if config is not None else wc.get_configuration()
    entries = cfg.entries
    root = bundle_root_dir or (
        cfg.root / "prompts" / "Reviewer" if config is not None else _bundle_root()
    )
    errors: list[str] = []
    warnings: list[str] = []
    unavailable_models: dict[str, list[str]] = {}
    unknown_builtin_tools: dict[str, list[str]] = {}
    declared_always_on_tools: dict[str, list[str]] = {}
    if runtime_capabilities is None:
        available_models: Collection[str] | None = None
        known_builtins = builtin_tool_names()
        always_on = always_on_tool_names()
        capability_provenance = inventory_provenance()
    else:
        available_models = runtime_capabilities.model_ids
        known_builtins = runtime_capabilities.builtin_tool_ids
        always_on = runtime_capabilities.always_on_tool_ids
        capability_provenance = f"active runtime: {runtime_capabilities.provenance}"
    errors.extend(validate_mcp_registry())

    # Layer 0: structural meta-schema. The raw YAML must be a well-formed config
    # document (field set, nesting, scalar types) before any semantic layer runs —
    # catches typos and stray/dead keys the tolerant loaders would silently drop.
    from roundtable.graph import validate_config_meta

    errors.extend(validate_config_meta(cfg.root / "agent_graph.yaml"))

    try:
        wc.validate_graph_config(entries, cfg)
    except ValueError as exc:
        errors.append(str(exc))

    # Layer 1b: the SELECTED executor's own structural check (DAG ⇒ acyclicity).
    # Executor-specific, so it lives behind the executor seam, not in the agnostic
    # validate_graph_config. Collected here so doctor reports it with everything else.
    from roundtable.engine import get_executor

    errors.extend(get_executor(cfg.executor).validate(entries))

    known_servers = registered_server_names()
    for entry in entries:
        if not entry.is_llm:
            continue
        if not entry.model:
            errors.append(f"{entry.key}: declares no `model` in agent_graph.yaml")
        elif available_models is not None and entry.model not in available_models:
            unavailable_models.setdefault(entry.model, []).append(entry.key)
        if not entry.prompt_path:
            continue  # promptless handled by validate_graph_config
        # Layer 2: instructions + every shared_context file must exist (else the
        # composer would raise at install). Explicit composition means a declared
        # shared_context ref is injected BY CONSTRUCTION — presence is the contract.
        if not (root / entry.prompt_path).is_file():
            errors.append(f"{entry.key}: instructions file not found: {entry.prompt_path}")
        for ref in entry.shared_context:
            if not (root / ref).is_file():
                errors.append(f"{entry.key}: shared_context file not found: {ref}")
        if entry.tools is not None:
            if len(entry.tools) != len(set(entry.tools)):
                errors.append(f"{entry.key}: declares duplicate entries in `tools`")
            declared_servers = {binding.server for binding in entry.mcp}
            for tool in entry.tools:
                if "/" not in tool:
                    if tool not in known_builtins:
                        unknown_builtin_tools.setdefault(tool, []).append(entry.key)
                    elif tool in always_on:
                        declared_always_on_tools.setdefault(tool, []).append(entry.key)
                    continue
                server, bare_tool = tool.split("/", 1)
                if not server or not bare_tool:
                    errors.append(
                        f"{entry.key}: invalid MCP tool `{tool}` "
                        "(expected `<declared-server>/<bare-tool>`)"
                    )
                    continue
                if server not in declared_servers:
                    errors.append(
                        f"{entry.key}: tool `{tool}` references MCP server `{server}` "
                        "which is not declared by this agent"
                    )
                    continue
                available = server_tool_names(server)
                if "*" not in available and bare_tool not in available:
                    errors.append(
                        f"{entry.key}: tool `{tool}` is absent from MCP server "
                        f"`{server}` inventory {sorted(available)}"
                    )
        if entry.tool_policy is not None and entry.tools is not None:
            missing_policy_tools = set(entry.tool_policy.tool_names) - set(entry.tools)
            for tool in sorted(missing_policy_tools):
                errors.append(
                    f"{entry.key}: tool_policy configures `{tool}` but `tools` does not grant it"
                )
        # MCP wiring (opt-in): for each declared `mcp` binding — the server must be
        # registry-known (else silently dropped at resolve time, a footgun); a
        # declared `usage` policy file must exist AND must carry NO raw MCP tool
        # name (`ado-<tool>`). Usage files hold ROLE INTENT only; the concrete tool
        # inventory is the SSOT `bindings:` block in mcp_servers.yaml surfaced in
        # the ADO Tool Bindings section. This lint statically prevents prose/tool
        # drift (the class of bug where a prompt names an unwired tool).
        for binding in entry.mcp:
            if binding.server not in known_servers:
                errors.append(
                    f"{entry.key}: declares unknown MCP server `{binding.server}` "
                    f"(registered: {sorted(known_servers)})"
                )
            if binding.usage:
                usage_path = root / binding.usage
                if not usage_path.is_file():
                    errors.append(f"{entry.key}: mcp usage file not found: {binding.usage}")
                else:
                    raw = _find_raw_mcp_tool_names(usage_path.read_text(encoding="utf-8"))
                    if raw:
                        errors.append(
                            f"{entry.key}: mcp usage file {binding.usage} names raw MCP tool(s) "
                            f"{sorted(raw)} — usage prose must carry role intent only; the tool "
                            f"inventory lives in the server `bindings:` (ADO Tool Bindings)"
                        )

    if unknown_builtin_tools:
        affected = "; ".join(
            f"`{tool}` declared by {sorted(nodes)}"
            for tool, nodes in sorted(unknown_builtin_tools.items())
        )
        errors.append(
            "unknown built-in tool declarations: "
            f"{affected}. The runtime ignores these names silently "
            f"({capability_provenance}; known: {sorted(known_builtins)})"
        )

    if declared_always_on_tools:
        affected = "; ".join(
            f"`{tool}` declared by {sorted(nodes)}"
            for tool, nodes in sorted(declared_always_on_tools.items())
        )
        errors.append(
            "always-on built-in tool declarations: "
            f"{affected}. These tools are granted unconditionally, so listing them "
            f"overstates the grant ({capability_provenance})"
        )

    if unavailable_models:
        affected = "; ".join(
            f"`{model}` used by {sorted(nodes)}"
            for model, nodes in sorted(unavailable_models.items())
        )
        errors.append(
            "models unavailable to the current Copilot account: "
            f"{affected}. IDs returned by models.list: {sorted(available_models or ())}. "
            f"{capability_provenance}"
        )

    errors.extend(_validate_agent_identity(entries))

    # Layer 5: schema↔gate coherence (dead gates fail; coverage gaps advise).
    from roundtable.validation import (
        validate_conditional_predicates,
        validate_cross_agent_refs,
        validate_dossier_completeness,
        validate_extractor_vocab_coherence,
        validate_fan_out,
        validate_finding_adapter_targets,
        validate_ovg_coherence,
    )

    coherence = validate_ovg_coherence(entries, configuration=cfg)
    errors.extend(coherence.errors)
    warnings.extend(coherence.warnings)
    schema_root = cfg.root / "schemas"

    # Layer 6: cross-agent output-field reference lint (class-B coupling). A
    # prompt `<delivery_label>.<field>` reference must resolve to a declared edge on
    # the producing node AND a top-level field of that producer's output_schema.
    xref = validate_cross_agent_refs(entries, root, schema_dir=schema_root)
    errors.extend(xref.errors)
    warnings.extend(xref.warnings)

    # Layer 7: extractor↔vocabulary coherence. Every finding schema composes from the
    # canonical vocabulary $defs (one name = one type, enums only via $ref, adapter
    # transforms stay in the vocab) — so the extractor can trust field names/enums.
    vocab_coherence = validate_extractor_vocab_coherence(entries, schema_dir=schema_root)
    errors.extend(vocab_coherence.errors)
    warnings.extend(vocab_coherence.warnings)

    # Layer 7b: finding-adapter location aliases. Vocabulary-independent, so it also
    # guards bundles with no shared vocabulary: every ``x-finding-adapter.locations``
    # alias must name a real array property (and real row sub-keys) on the finding
    # item, else the extractor silently reads zero locations for that agent.
    adapter_targets = validate_finding_adapter_targets(entries, schema_dir=schema_root)
    errors.extend(adapter_targets.errors)
    warnings.extend(adapter_targets.warnings)

    # Layer 8: dossier corpus completeness. Each zero-drop ``Dossier_*`` node must dep
    # on every finding-producing agent, so a consumer's dossier can never silently omit
    # a specialist's findings.
    dossier_completeness = validate_dossier_completeness(entries, configuration=cfg)
    errors.extend(dossier_completeness.errors)
    warnings.extend(dossier_completeness.warnings)

    # Layer 9: conditional-edge predicate coherence. Every ``when:`` field-path must
    # resolve into its ``source`` producer's output_schema — an unreachable path is a
    # silent dead branch (predicate always false ⇒ consumer always skipped).
    conditional = validate_conditional_predicates(entries, schema_dir=schema_root)
    errors.extend(conditional.errors)
    warnings.extend(conditional.warnings)

    # Layer 10: fan-out field coherence. Every ``kind: map`` node's ``fan_out.over``
    # must name a required-edge producer whose ``output_schema`` types that field as
    # an array — otherwise the node fans out over a missing or non-list value.
    fan_out = validate_fan_out(entries, schema_dir=schema_root)
    errors.extend(fan_out.errors)
    warnings.extend(fan_out.warnings)

    # Layer 11: output-contract sink cardinality. The config declares how many
    # runnable sinks (out-degree-0 nodes) its result-contract expects via
    # ``sink_cardinality`` — 'one' (the default) means ``review``'s topology-based
    # read of the run's domain result is unambiguous. This is NOT an executor law —
    # a DAG may legitimately have many leaves, and the engine's ``sinks()`` has no
    # cardinality opinion — so the opinion lives in the config and is validated here,
    # over the **runnable** graph (``non_graph_infra`` agents like SuggestionPublisher
    # run outside the review run and are correctly excluded). 'any' opts a
    # multi-output topology out of the single-sink contract.
    from roundtable.engine import _runnable_graph_entries

    if cfg.sink_cardinality == "one":
        runnable_sinks = get_executor(cfg.executor).sinks(_runnable_graph_entries(cfg))
        if len(runnable_sinks) != 1:
            errors.append(
                "output-contract: expected exactly one runnable graph sink (the "
                "out-degree-0 node whose output is the run's domain result), found "
                f"{len(runnable_sinks)}: {sorted(runnable_sinks)}"
            )

    # Layer 12: output-sink resolves. A declared ``sink:`` key must name a registered
    # adapter (``output.sink.get_sink``) — a typo would otherwise only surface at
    # ``publish`` time. Config-level, like the ``executor:`` name. An explicit
    # ``sink: null`` is allowed and resolves to no sink: publishing is optional and
    # config-owned, so a topology may declare it does not publish at all.
    from roundtable.delivery import get_publisher

    try:
        get_publisher(cfg.publisher)
    except ValueError as err:
        errors.append(f"output-sink: {err}")

    # Layer 13: publish-projector resolves. When a ``projector:`` is declared it must
    # name a registered adapter (``output.projector.get_projector``) — a typo would
    # otherwise only surface at ``publish`` time. Absent (``None``) is allowed: a
    # config that declares no projector simply cannot publish (there is no generic
    # default — projection is topology-specific), surfaced loudly at publish time.
    if cfg.projector is not None:
        from roundtable.delivery import get_projector

        try:
            get_projector(cfg.projector)
        except ValueError as err:
            errors.append(f"publish-projector: {err}")

    # Layer 14: session-report renderer resolves. When a ``report:`` is declared it
    # must name a renderer the bundle's ``plugins:`` registered
    # (``output.report.get_report``) — a typo would otherwise only surface as a
    # silently slim ``verdict.md`` at the end of a paid run. Absent (``None``) is
    # allowed: that config renders the slim verdict stub rather than borrowing
    # another config's reading of its Judge output.
    if cfg.report is not None:
        from roundtable.delivery import get_report

        try:
            get_report(cfg.report)
        except ValueError as err:
            errors.append(f"session-report: {err}")

    return ValidationReport(errors=errors, warnings=warnings)


def _validate_agent_identity(graph_config: Any) -> list[str]:
    """Layer 4: agent identity consistency.

    The graph is the identity SSOT (``config.agent_identity`` derives the whole
    resolver from it). This proves every non-source entry declares the two identity
    forms the satellites depend on — ``agent_id`` (the canonical id used by OVG
    schemas + the finding index; missing ⇒ those lookups silently miss) and
    ``display_name`` (the report label) — and that no two entries share a canonical
    id. After this, ``doctor`` green ⇒ all satellites consistent.
    (Output-contract consistency is Layer 5, :func:`validate_ovg_coherence`.)
    """
    errors: list[str] = []
    seen_canonical: dict[str, str] = {}
    for e in graph_config:
        # Source nodes are pure corpus inputs — they never produce findings, have no
        # OVG/finding-index lookup, and carry no report identity, so identity does
        # not apply to them.
        if e.kind == "source":
            continue

        if not e.agent_id:
            errors.append(
                f"{e.key}: missing agent_id (OVG/finding-index lookups would silently miss)"
            )
        else:
            if e.agent_id in seen_canonical:
                errors.append(
                    f"{e.key}: duplicate canonical id {e.agent_id!r} "
                    f"(also {seen_canonical[e.agent_id]})"
                )
            seen_canonical[e.agent_id] = e.key

        if not e.display_name:
            errors.append(f"{e.key}: no report display name (display_name)")

    return errors


def system_prompts_by_key(
    bundle_root: Path, entries: tuple[GraphEntry, ...] | None = None
) -> dict[str, str]:
    """Map ``GraphEntry.key`` → fully composed system prompt, from the graph SSOT.

    Iterates the graph (``is_llm`` entries) and builds each via
    :func:`full_system_prompt` (composition + ``## Output contract``), so dump/trace
    keys match ``copilot --agent`` ids exactly (aliases like ``Simulator_inverted``
    included — each has its own entry). ``bundle_root`` supplies the ``instructions`` +
    ``shared_context`` files the composer reads. Used by the ``--dump-prompts`` T0
    parity dump (``system.md``).
    """
    if entries is None:
        from roundtable.graph import get_configuration

        entries = get_configuration().entries
    return {e.key: full_system_prompt(e, bundle_root) for e in entries if e.is_llm}
