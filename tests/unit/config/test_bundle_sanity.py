"""Bundle sanity: no orphan prompt files, no CRLF, and every LLM agent composes.

These are stance-independent structural checks (they answer "is the bundle
well-formed?", not "did an assembled prompt change?"). The regeneratable
Composed-prompt and bundle-SHA goldens are intentionally absent: a
byte-snapshot of an assembled prompt only tells you it *changed*, not that it is
*correct*, and git already records the change — so it was pure ceremony.

What remains here is cheap and load-bearing:

- Completeness: every on-disk ``*.agent.md`` is referenced by an is_llm graph
  entry and vice-versa (catches orphaned/renamed prompt files) + required
  ``Shared/*`` files and subdirs exist.
- Composition contract: every is_llm agent's ``compose_system_prompt`` output
  equals an INDEPENDENT spec recompose (body -> ``shared_context`` -> ``mcp_usage``,
  LF-normalized, trimmed, ``\\n\\n``-joined). This pins the exact part order / no
  hidden injection for ALL installed agents — including ``SuggestionPublisher``,
  which runs observe-only and so never appears in a scheduled live-run dump.
- No CRLF leaked into the LF-normalized bundle.
"""

from __future__ import annotations

from roundtable.bundle import resolve_bundle, shipped_bundles
from roundtable.graph.model import get_configuration
from roundtable.runtime.agent_setup import parse_agent_file, system_prompts_by_key

_REQUIRED_SHARED = (
    "AgentPreamble.md",
    "InputAwareness.md",
    "ContextAwareness.md",
    "PragmaticReview.md",
    "AntiDriftProtocol.md",
    "TemporalContext.md",
    "ValidationChecklists.md",
)
_REQUIRED_SUBDIRS = ("Agents", "Agents/Specialists", "Shared", "Shared/SectionalAnalysis")
_CONFIG = get_configuration(resolve_bundle("inspectorx"))
_ROOT = _CONFIG.root / "prompts" / "Reviewer"


def test_bundle_completeness() -> None:
    root = _ROOT
    agents = list((root / "Agents").rglob("*.agent.md"))
    # Post-consolidation the bundle carries NO orphans: every on-disk agent file is
    # referenced by an is_llm graph entry, and every referenced file exists.
    on_disk = {p.relative_to(root).as_posix() for p in agents}
    referenced = {e.prompt_path for e in _CONFIG.entries if e.is_llm and e.prompt_path}
    assert on_disk == referenced, (
        f"orphan_files={sorted(on_disk - referenced)} missing_files={sorted(referenced - on_disk)}"
    )
    for rel in _REQUIRED_SHARED:
        assert (root / "Shared" / rel).is_file(), f"missing Shared/{rel}"
    for sub in _REQUIRED_SUBDIRS:
        assert (root / sub).is_dir(), f"missing subdir {sub}"


def test_shipped_llm_routes_are_declared() -> None:
    for bundle in shipped_bundles():
        config = get_configuration(resolve_bundle(bundle))
        llm_models = [entry.model for entry in config.entries if entry.is_llm]
        assert llm_models and all(llm_models), f"{bundle}: every LLM node must declare a model"


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _spec_recompose(entry, root) -> str:
    """Re-derive an agent's system prompt straight from the documented SSOT rule.

    This is an INDEPENDENT oracle — it does not call ``full_system_prompt``. It encodes
    the contract verbatim (body -> ``shared_context`` -> ``mcp_usage``, each LF-normalized
    + trimmed, blanks dropped, ``\\n\\n``-joined) then appends the agent's ``## Output
    contract`` (schema-derived), so the assertion catches any drift where the composer
    reorders parts, wraps them in envelopes, or injects hidden content.
    """
    from roundtable.engine.agent_runner import build_output_contract

    _fm, body = parse_agent_file((root / entry.prompt_path).read_text(encoding="utf-8"))
    raw = [body]
    raw += [(root / ref).read_text(encoding="utf-8") for ref in entry.shared_context]
    raw += [(root / ref).read_text(encoding="utf-8") for ref in entry.mcp_usage_files]
    composed = "\n\n".join(t for t in (_lf(r).strip() for r in raw) if t)
    contract = build_output_contract(entry, _CONFIG.root / "schemas")
    return f"{composed}\n\n{contract}" if contract else composed


def test_every_llm_agent_recomposes_exactly() -> None:
    """Every is_llm agent's composed prompt equals an independent spec recompose.

    Stronger than a non-empty smoke: it pins the *composition contract* (exact part
    order, no envelopes, no hidden injection) for all installed agents — including
    ``SuggestionPublisher``, which runs observe-only and so never appears in a
    scheduled live-run dump (its prompt can regress unnoticed without this guard).
    """
    root = _ROOT
    prompts = system_prompts_by_key(root, entries=_CONFIG.entries)
    llm_keys = [e.key for e in _CONFIG.entries if e.is_llm]
    assert set(prompts) == set(llm_keys), (
        f"composed set != is_llm set: extra={sorted(set(prompts) - set(llm_keys))} "
        f"missing={sorted(set(llm_keys) - set(prompts))}"
    )
    for entry in _CONFIG.entries:
        if not entry.is_llm:
            continue
        composed = prompts[entry.key]
        assert composed.strip(), f"empty composed prompt: {entry.key}"
        assert composed == _spec_recompose(entry, root), f"composition drift: {entry.key}"


def test_non_graph_infra_llm_agents_are_statically_covered() -> None:
    """Guard that every is_llm infra agent (never scheduled) is still recompose-checked.

    ``SuggestionPublisher`` is ``non_graph_infra`` — ``_runnable_graph_entries()``
    excludes it, so no live-run ever dumps its ``system.md``. This is the ONLY place
    its prompt is composed + validated; the assertion below stops it from silently
    slipping out of coverage.
    """
    cfg = _CONFIG
    prompts = system_prompts_by_key(_ROOT, entries=cfg.entries)
    infra_llm = [e.key for e in cfg.entries if e.is_llm and e.key in cfg.non_graph_infra_agents]
    assert "SuggestionPublisher" in infra_llm, "SuggestionPublisher missing from is_llm infra set"
    for key in infra_llm:
        assert prompts.get(key, "").strip(), f"infra agent not composed: {key}"


def test_no_crlf_in_vendored_bundle() -> None:
    for p in _ROOT.rglob("*.md"):
        assert b"\r" not in p.read_bytes(), f"CRLF leaked into vendored {p.name}"
