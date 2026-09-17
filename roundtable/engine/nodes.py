"""nodes: node-as-callable seam — one handler per graph ``kind``.

Every graph node is executed through a uniform callable: a :data:`NodeHandler`
with the signature ``(entry, snapshot, degraded_deps, ctx) -> NodeResult``. The
per-``kind`` handlers are registered in :data:`NODE_HANDLERS`; :func:`dispatch_node`
selects one by ``entry.kind``. This is the seam the executor dispatches through —
the DAG scheduler owns *ordering/readiness*, this module owns *how a single node
runs*.

Five handlers ship today, all behavior-preserving:

  * ``llm``     — assemble per-agent context and run the backend/submission loop
    (:func:`run_agent_with_ovg`).
  * ``code``    — invoke the node's registered enricher fn over the run snapshot
    (no context assembly or LLM execution — R6). A degraded hard-dep invalidates it.
  * ``reducer`` — a *tolerant* typed fan-in merge: the same registered-fn machinery
    as ``code``, but a degraded input degrades the merge instead of invalidating it
    (zero-drop tolerance is intrinsic to the kind). ``code`` + ``reducer`` share
    :func:`_backend_node`.
  * ``map``     — node-internal bounded fan-out (``Send``): reads a list field from a
    producer's output and emits one sub-activation per element (identity-render in
    v1) plus one aggregated outcome, so the activation log gains N entries while the
    snapshot/injection model is unchanged (:func:`run_map_node`).
  * ``source``  — emits caller-supplied content as a graph-root outcome
    (:func:`run_source_node`).

The run-scoped state the handlers need (session header, repository grants, and
the pre-seeded corpus source payloads) is bundled once per run into a frozen
:class:`NodeContext` instead of being captured as closure variables, so a handler
is a standalone callable an executor can invoke without reaching into the
scheduler. New kinds register in :data:`NODE_HANDLERS` without touching the
scheduling loop.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from roundtable.backend import Backend
from roundtable.context import get_enricher
from roundtable.graph import Configuration, GraphEntry

from .agent_runner import DEFAULT_AGENT_TIMEOUT_S, AgentRunOutcome, run_agent_with_ovg
from .inputs import AgentInputBuilder

NodeResult = tuple[str, AgentRunOutcome, dict[str, Any]]
NodeHandler = Callable[
    [GraphEntry, dict[str, AgentRunOutcome], tuple[str, ...], "NodeContext"], NodeResult
]
NodeValidator = Callable[[GraphEntry, frozenset[str]], list[str]]


@dataclass(frozen=True)
class NodeKind:
    run: NodeHandler
    validate: NodeValidator


@dataclass(frozen=True)
class NodeContext:
    """Run-scoped inputs shared by every node handler.

    Built once per :func:`run_graph_dag` call and passed to each handler — the
    former closure environment made explicit so a handler is a standalone callable.
    """

    backend: Backend
    configuration: Configuration
    add_dirs: list[str] | None
    cwd: str | None
    # Pre-seeded corpus content by source-node key (``kind: source`` nodes emit it):
    # e.g. ``{"ReviewDiff": <diff>, "GitHistory": <history>}``. A consumer resolves
    # its corpus from the snapshot via its own ``kind: source`` edges — no global key.
    source_payloads: Mapping[str, str]
    session_reuse: bool
    max_attempts: int
    depth: Mapping[str, float]
    scheduled: frozenset[str]
    agent_input_builder: AgentInputBuilder

    def source_inputs(
        self,
        entry: GraphEntry,
        snapshot: Mapping[str, AgentRunOutcome],
    ) -> Mapping[str, str]:
        return {
            edge.source: outcome.response
            for edge in entry.forward_edges
            if (outcome := snapshot.get(edge.source)) is not None
            and outcome.valid
            and (source := self.configuration.by_key.get(edge.source)) is not None
            and source.kind == "source"
        }


def _backend_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
    *,
    tolerant: bool,
) -> NodeResult:
    """Shared core for the two deterministic fn-kinds (``code`` and ``reducer``):
    invoke the node's registered fn over the snapshot instead of spawning Copilot.
    No context assembly or LLM execution (R6). Failure modes degrade gracefully (no
    retry, R5):

      * ``tolerant=False`` (``code``) — G1: any degraded hard-dep input ⇒ node
        invalid (its output would be built on incomplete inputs); the fn is not
        even called.
      * ``tolerant=True`` (``reducer``) — degraded inputs are *informational*: the
        fn IS called on the valid survivors and the node stays valid (a merge is
        the sum of what arrived — zero-drop tolerance is intrinsic to the kind, not
        a per-edge ``required: false`` the author must remember).
      * unregistered fn ⇒ invalid (a wiring bug, surfaced).
      * the fn raises ⇒ invalid, exception captured.
    """
    fn = get_enricher(entry.code_fn or "")
    if degraded_deps and not tolerant:
        outcome = AgentRunOutcome(
            agent=entry.key,
            response="",
            valid=False,
            gate="deterministic-degraded-input",
            errors=[f"hard-dep input degraded: {', '.join(degraded_deps)}"],
            attempts=1,
        )
    elif fn is None:
        outcome = AgentRunOutcome(
            agent=entry.key,
            response="",
            valid=False,
            gate="deterministic-unregistered",
            errors=[f"no enricher registered for {entry.code_fn!r}"],
            attempts=1,
        )
    else:
        try:
            body = fn(snapshot, ctx.source_inputs(entry, snapshot), entry)
            outcome = AgentRunOutcome(
                agent=entry.key, response=body, valid=True, gate=None, attempts=1
            )
        except Exception as exc:  # graceful degradation: never crash the run
            outcome = AgentRunOutcome(
                agent=entry.key,
                response="",
                valid=False,
                gate="deterministic-error",
                errors=[f"{type(exc).__name__}: {exc}"],
                attempts=1,
            )
    return entry.key, outcome, {}


def run_code_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
) -> NodeResult:
    """Execute a ``kind: code`` node: a general enricher fn over the snapshot. A
    degraded hard-dep invalidates the node (its inputs are incomplete) — see
    :func:`_backend_node`."""
    return _backend_node(entry, snapshot, degraded_deps, ctx, tolerant=False)


def run_reducer_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
) -> NodeResult:
    """Execute a ``kind: reducer`` node: a *tolerant* typed fan-in merge. Unlike
    ``code``, a degraded input degrades the merge (fewer contributions) but never
    invalidates it — the reducer emits the merge of whatever valid inputs arrived
    (Q2: a pure typed merge, distinct from Judge's LLM reconciliation). See
    :func:`_backend_node`."""
    return _backend_node(entry, snapshot, degraded_deps, ctx, tolerant=True)


def _attempt_timeout_seconds(entry: GraphEntry) -> float:
    """Wall-clock budget for one attempt, or the engine default when absent."""
    if entry.timeout_seconds is None:
        return DEFAULT_AGENT_TIMEOUT_S
    return float(entry.timeout_seconds)


def run_llm_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
) -> NodeResult:
    """Execute an LLM node with input supplied by the caller."""
    agent_input = ctx.agent_input_builder(entry, snapshot, ctx.scheduled)
    outcome = run_agent_with_ovg(
        agent=entry.key,
        context=agent_input.context,
        model=entry.model or "",
        backend=ctx.backend,
        configuration=ctx.configuration,
        add_dirs=ctx.add_dirs,
        cwd=ctx.cwd,
        session_reuse=ctx.session_reuse,
        max_attempts=ctx.max_attempts,
        timeout_s=_attempt_timeout_seconds(entry),
        ovg_context=dict(agent_input.validation_context),
    )
    if outcome.valid:
        outcome.response = agent_input.finalize_response(outcome.response)
    return entry.key, outcome, {}


def _resolve_fan_out_list(response: str, field_path: str) -> list[Any] | None:
    """Resolve ``field_path`` (dotted) into a JSON list inside a producer's output.

    Returns the list, or ``None`` if the response is not JSON, the path does not
    resolve, or the resolved value is not a list. ``None`` ⇒ the map node has no
    valid list to fan out over (invalid, like a degraded ``code`` input)."""
    try:
        data: Any = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        return None
    node: Any = data
    for part in field_path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, list) else None


def run_map_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
) -> NodeResult:
    """Execute a ``kind: map`` node: node-internal bounded fan-out (``Send``, Q4).

    Reads the list named by ``fan_out.over`` (``<producer>.<dotted.field>``) from the
    producer's snapshot output and fans out over its elements: one **sub-activation**
    per element (identity-render in v1 — a per-item mapper fn is a later increment),
    plus one **aggregated** outcome that carries the whole list on to downstream
    nodes via ``delivery_label``. The sub-activations ride in
    ``meta['fan_out_activations']``; the executor splices them into the ordered
    activation log (the multi-activation the log was generalized for), while the
    snapshot/injection/readiness model is unchanged — a map node looks like any
    single producer to its consumers.

    Failure modes degrade gracefully (no fan-out, node invalid): the producer is
    missing/invalid, its output is not JSON, or ``fan_out.over`` does not resolve to
    a list."""
    fo = entry.fan_out
    if fo is None:  # doctor forbids this; defensive
        outcome = AgentRunOutcome(
            agent=entry.key,
            response="",
            valid=False,
            gate="map-no-fanout",
            errors=["kind=map without a fan_out spec"],
            attempts=1,
        )
        return entry.key, outcome, {"fan_out_activations": []}

    src = snapshot.get(fo.producer)
    if src is None or not src.valid:
        outcome = AgentRunOutcome(
            agent=entry.key,
            response="",
            valid=False,
            gate="map-degraded-source",
            errors=[f"fan-out source {fo.producer!r} missing or invalid"],
            attempts=1,
        )
        return entry.key, outcome, {"fan_out_activations": []}

    items = _resolve_fan_out_list(src.response, fo.field_path)
    if items is None:
        outcome = AgentRunOutcome(
            agent=entry.key,
            response="",
            valid=False,
            gate="map-invalid-source",
            errors=[f"fan_out.over {fo.over!r} did not resolve to a list"],
            attempts=1,
        )
        return entry.key, outcome, {"fan_out_activations": []}

    subs: list[AgentRunOutcome] = []
    for i, item in enumerate(items):
        body = item if isinstance(item, str) else json.dumps(item, sort_keys=True)
        subs.append(
            AgentRunOutcome(
                agent=f"{entry.key}[{i}]", response=body, valid=True, gate=None, attempts=1
            )
        )
    aggregated = json.dumps(items, sort_keys=True)
    outcome = AgentRunOutcome(
        agent=entry.key, response=aggregated, valid=True, gate=None, attempts=1
    )
    return entry.key, outcome, {"fan_out_activations": subs}


def run_source_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
) -> NodeResult:
    """Execute a ``kind: source`` node: the graph-root corpus provider.

    Emits ``ctx.source_payloads[entry.key]`` as a valid outcome. A source has no
    dependencies, so it runs before its consumers.
    """
    payload = ctx.source_payloads.get(entry.key, "")
    outcome = AgentRunOutcome(agent=entry.key, response=payload, valid=True, gate=None, attempts=1)
    return entry.key, outcome, {}


def _validate_llm(entry: GraphEntry, _enrichers: frozenset[str]) -> list[str]:
    errors: list[str] = []
    if not entry.prompt_path:
        errors.append(f"{entry.key}: kind=llm but no prompt_path")
    if entry.code_fn:
        errors.append(f"{entry.key}: kind=llm must not declare a code_fn")
    return errors


def _validate_function_node(entry: GraphEntry, enrichers: frozenset[str]) -> list[str]:
    errors: list[str] = []
    if not entry.code_fn:
        errors.append(f"{entry.key}: kind={entry.kind} but no code_fn")
    elif entry.code_fn not in enrichers:
        errors.append(
            f"{entry.key}: code_fn {entry.code_fn!r} not in the enricher "
            f"registry (known: {sorted(enrichers)})"
        )
    if entry.prompt_path:
        errors.append(f"{entry.key}: kind={entry.kind} must not declare a prompt_path")
    if entry.model:
        errors.append(f"{entry.key}: kind={entry.kind} must not declare a model")
    return errors


def _validate_map(entry: GraphEntry, _enrichers: frozenset[str]) -> list[str]:
    errors: list[str] = []
    if entry.fan_out is None:
        errors.append(f"{entry.key}: kind=map but no fan_out spec")
    elif "." not in entry.fan_out.over:
        errors.append(
            f"{entry.key}: fan_out.over {entry.fan_out.over!r} must be '<producer>.<list_field>'"
        )
    if entry.prompt_path:
        errors.append(f"{entry.key}: kind=map must not declare a prompt_path")
    if entry.model:
        errors.append(f"{entry.key}: kind=map must not declare a model")
    if entry.code_fn:
        errors.append(f"{entry.key}: kind=map must not declare a code_fn")
    return errors


def _validate_source(entry: GraphEntry, _enrichers: frozenset[str]) -> list[str]:
    errors: list[str] = []
    if entry.edges:
        errors.append(f"{entry.key}: kind=source must not declare edges (it is a root)")
    if entry.prompt_path:
        errors.append(f"{entry.key}: kind=source must not declare a prompt_path")
    if entry.model:
        errors.append(f"{entry.key}: kind=source must not declare a model")
    if entry.code_fn:
        errors.append(f"{entry.key}: kind=source must not declare a code_fn")
    return errors


NODE_HANDLERS: dict[str, NodeKind] = {
    "llm": NodeKind(run_llm_node, _validate_llm),
    "code": NodeKind(run_code_node, _validate_function_node),
    "reducer": NodeKind(run_reducer_node, _validate_function_node),
    "map": NodeKind(run_map_node, _validate_map),
    "source": NodeKind(run_source_node, _validate_source),
}


def get_node_handler(kind: str) -> NodeHandler | None:
    """Return the registered handler for a node ``kind``, or ``None`` if unknown."""
    registered = NODE_HANDLERS.get(kind)
    return registered.run if registered is not None else None


def validate_node_entry(entry: GraphEntry, enrichers: frozenset[str]) -> list[str]:
    registered = NODE_HANDLERS.get(entry.kind)
    if registered is None:
        expected = ", ".join(repr(kind) for kind in NODE_HANDLERS)
        return [f"{entry.key}: invalid kind {entry.kind!r} (expected {expected})"]
    return registered.validate(entry, enrichers)


def dispatch_node(
    entry: GraphEntry,
    snapshot: dict[str, AgentRunOutcome],
    degraded_deps: tuple[str, ...],
    ctx: NodeContext,
) -> NodeResult:
    """Run a single node through its ``kind`` handler.

    An unregistered ``kind`` is a wiring bug (doctor validates kinds offline); it is
    surfaced loudly rather than silently mis-dispatched.
    """
    handler = get_node_handler(entry.kind)
    if handler is None:
        raise ValueError(f"no node handler registered for kind {entry.kind!r} (agent {entry.key})")
    return handler(entry, snapshot, degraded_deps, ctx)
