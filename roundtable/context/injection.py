"""context.injection: inter-agent context injection.

The dependency edges in ``agent_graph.yaml`` exist so a consumer agent can *read
its producer's output*. The DAG scheduler orders producers before consumers so
that handoff is possible — but ordering alone is inert unless the data actually
flows. This module is that data flow: for each agent it renders the upstream
context its declared edges promise.

Two kinds of injected content:

* **Inter-agent edges** — for each declared ``hard_dep`` that is a scheduled graph
  agent, a ``<delivery_label> [REQUIRED]`` section carrying the producer's
  **verbatim** validated output; for each ``soft_dep``, a
  ``<delivery_label> [OPTIONAL]`` section. Both use the producer's declared
  ``delivery_label`` (default ``## Context from <Dep>``) — one uniform mechanism,
  no per-producer or per-edge-kind heading branches. Verbatim
  structured output — one generic mechanism, no per-producer distillers. The
  model reads structured JSON fine; distillation can be added later as a pure
  enhancement if context bloat ever bites.) A required (hard) dep that produced no
  valid output renders an explicit ``[REQUIRED — UNAVAILABLE]`` stub so the
  consumer knows its dossier is degraded; a missing/invalid soft dep is silent.

* **The review corpus (source edges)** — the diff and per-file git-history are
  delivered by ``kind: source`` nodes, which a consumer reads through an explicit
  edge (``ReviewDiff`` / ``GitHistory``). This module **skips** source edges: the
  node handler renders the corpus itself (the diff via ``render_git_context_section``
  focusing, history verbatim under ``## Git History``), so it is never double-injected
  here. The corpus is thus an explicit, visible dependency, not an ambient global.

``DeterministicPreScan`` (DPS) is a scheduled ``runtime: deterministic`` node
(``run_prescan``, registered by the bundle plugin
``configs/inspectorx/plugins/context_plugins.py``): the DAG runs it before its two
consumers (``CodeCorrectness`` / ``SchemaDrift``), its base anti-pattern scan
(``configs/inspectorx/plugins/prescan.py``) is wrapped as an ``AgentRunOutcome`` in the
snapshot, and the SAME generic hard-dep path below delivers it under ``## Context from
DeterministicPreScan [REQUIRED]`` — no name-matching special-case.

``SecurityIntentProfiler`` (SIP) IS a scheduled graph agent that the four security
consumers hard-dep, so it flows through the producer-snapshot path. Its node
declares ``delivery_label: ## security_intent_pack`` (the prompt vocabulary), so
the SAME generic hard-dep path below delivers its verbatim output under that label
— no name-matching special-case. The derived ``## security_focus_pack`` is a
separate ``SecurityFocusPack`` ``runtime: deterministic`` node
(``context/enrichers.py`` → ``build_security_focus_pack``) that hard-deps SIP and
is delivered to the four consumers through the SAME generic path under its
``## security_focus_pack`` label.

There are ZERO ``dep == "<AgentName>"`` branches in this module: every producer —
LLM agent, deterministic enricher, DPS, SIP, SecurityFocusPack — is delivered by
the single generic path under its declared ``delivery_label`` (default
``## Context from <Dep>``). Dispatch is entirely on declared agent_graph metadata.

The per-consumer ``Dossier_*`` deterministic nodes are ordinary producers here:
each is delivered by this same generic path under its declared ``delivery_label``
(e.g. ``## agent_dossier``). There is no special dossier branch — a consumer
receives its dossier simply by declaring the node as a hard-dep.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from roundtable.engine import AgentRunOutcome
    from roundtable.graph import Configuration, GraphEntry


@dataclass(frozen=True)
class Corpus:
    """The review corpus, resolved from a consumer's edges to ``kind: source`` nodes.

    ``diff`` is the full review diff (from the source node delivering ``## Git
    Context``); ``git_history`` is the per-file ``git log --oneline -10`` block (from
    the ``## Git History`` source — empty in the ``--pr`` path, where history is not
    gathered). The corpus is now an **explicit edge**: a consumer reads it by
    declaring a source edge, not from an ambient global. Consumed by the deterministic
    diff enrichers (``context/enrichers.py``) and — for ``git_history`` — the LLM
    reviewers that declare a ``## Git History`` source edge (Historian).
    """

    diff: str = ""
    git_history: str = ""


EMPTY_CORPUS = Corpus()

# The ``delivery_label`` a ``kind: source`` node uses to declare *what corpus* it
# provides — the discriminator that lets a consumer's edge resolve to the diff vs the
# git-history source. Reuses the per-producer label machinery (no global key), and is
# doctor-checkable via the existing label-uniqueness coherence check.
GIT_CONTEXT_LABEL = "## Git Context"
GIT_HISTORY_LABEL = "## Git History"


def resolve_corpus(
    entry: GraphEntry,
    snapshot: Mapping[str, AgentRunOutcome],
    configuration: Configuration | None = None,
) -> Corpus:
    """Read the corpus from ``entry``'s own edges to ``kind: source`` nodes.

    The diff comes from the edge whose source node delivers ``## Git Context``; the
    git-history from the ``## Git History`` source. A consumer that declares no such
    edge gets an empty field — the corpus dependency is now an **explicit, visible
    edge** (opt-in), not an ambient global every node reads. A degraded/invalid
    source yields an empty field, so the ``--pr`` path (empty history payload) is
    byte-identical to before.
    """
    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    diff = ""
    git_history = ""
    for edge in entry.edges:
        src = config.by_key.get(edge.source)
        if src is None or src.kind != "source":
            continue
        out = snapshot.get(edge.source)
        text = out.response if (out is not None and out.valid) else ""
        if src.delivery_label == GIT_CONTEXT_LABEL:
            diff = text
        elif src.delivery_label == GIT_HISTORY_LABEL:
            git_history = text
    return Corpus(diff=diff, git_history=git_history)


_SEP = "\n\n---\n"


def _delivery_label(dep: str, configuration: Configuration | None = None) -> str:
    """The heading base under which producer ``dep``'s output is delivered.

    The producer's declared ``delivery_label`` (agent_graph.yaml), or the default
    ``## Context from <dep>``. The ``[REQUIRED]`` state suffix is appended by the
    caller — this returns only the heading base, so the same label drives the
    valid, unavailable, and soft sections uniformly (G2).
    """
    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    entry = config.by_key.get(dep)
    label = entry.delivery_label if entry is not None else None
    return label or f"## Context from {dep}"


def _unavailable(dep: str, label: str) -> str:
    return (
        f"{label} [REQUIRED — UNAVAILABLE]\n"
        f"[Roundtable: {dep} produced no valid output; reviewing on a degraded "
        f"dossier. Treat its contribution as absent.]"
    )


def build_injection_sections(
    entry: GraphEntry,
    snapshot: Mapping[str, AgentRunOutcome],
    scheduled: frozenset[str],
    configuration: Configuration | None = None,
) -> str:
    """Render the inter-agent injection block for ``entry`` (text only).

    Emits, per declared graph edge, a generic ``## Context from <Dep>`` section (the
    relabeled ``## security_intent_pack`` for the SIP edge) carrying the producer's
    verbatim output, or an explicit ``[REQUIRED — UNAVAILABLE]`` stub for a required
    dep that ran without valid output.

    ``kind: source`` edges are **skipped** here: a source delivers the review corpus,
    which the node handler renders itself (the diff via ``render_git_context_section``,
    history verbatim) — injecting it again would duplicate it. So this block carries
    only inter-agent findings.

    Returns ``""`` when the agent has no injectable content (no scheduled deps),
    byte-identical to the pre-injection behaviour for leaf agents. When non-empty the
    block is prefixed with the standard ``\\n\\n---\\n`` section separator and each
    section is separated the same way.
    """
    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    sections: list[str] = []

    def _is_source(dep: str) -> bool:
        src = config.by_key.get(dep)
        return src is not None and src.kind == "source"

    # Inter-agent edges: verbatim producer output along the declared graph edges.
    for dep in entry.required_dep_keys:
        if dep not in scheduled or _is_source(dep):
            # A required dep outside the scheduled set (e.g. a ``non_graph_infra``
            # agent) has no producer snapshot to deliver; a ``kind: source`` dep is
            # the corpus, rendered by the node handler — skip both here.
            continue
        out = snapshot.get(dep)
        if out is not None and out.valid and out.response.strip():
            sections.append(f"{_delivery_label(dep, config)} [REQUIRED]\n{out.response.strip()}")
        else:
            sections.append(_unavailable(dep, _delivery_label(dep, config)))

    for dep in entry.optional_dep_keys:
        if dep not in scheduled or _is_source(dep):
            continue
        out = snapshot.get(dep)
        if out is not None and out.valid and out.response.strip():
            sections.append(f"{_delivery_label(dep, config)} [OPTIONAL]\n{out.response.strip()}")
        # A missing/invalid optional dep is silent (optional by contract).

    if not sections:
        return ""
    return _SEP + _SEP.join(sections)
