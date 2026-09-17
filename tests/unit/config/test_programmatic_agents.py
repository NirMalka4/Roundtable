"""Guards for the SDK backend's programmatic-agent registry.

:func:`agent_setup.graph_custom_agents` hands the SDK each ``is_llm`` agent's
identity + composed prompt in memory (as ``custom_agents``). This test pins the
invariant that the registry covers exactly the ``is_llm`` graph keys, each with a
non-empty prompt + description.
"""

from __future__ import annotations

from dataclasses import replace

from roundtable.bundle import resolve_bundle
from roundtable.graph import PowershellToolPolicy, ToolPolicy
from roundtable.graph.model import get_configuration
from roundtable.runtime.agent_setup import graph_custom_agents

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
_ROOT = _CONFIG.root / "prompts" / "Reviewer"


def test_registry_covers_every_llm_agent_with_content() -> None:
    agents = graph_custom_agents(_ROOT, entries=_CONFIG.entries)

    llm_keys = {e.key for e in _CONFIG.entries if e.is_llm}
    assert set(agents) == llm_keys, (
        f"registry != is_llm set: extra={sorted(set(agents) - llm_keys)} "
        f"missing={sorted(llm_keys - set(agents))}"
    )
    for key, fields in agents.items():
        assert fields.name == key
        assert fields.prompt.strip(), f"empty prompt: {key}"
        assert fields.description.strip(), f"empty description: {key}"


def test_registry_preserves_declared_tool_policy() -> None:
    entry = next(e for e in _CONFIG.entries if e.is_llm)
    policy = ToolPolicy(powershell=PowershellToolPolicy(120, False))

    fields = graph_custom_agents(_ROOT, entries=(replace(entry, tool_policy=policy),))[entry.key]

    assert fields.tool_policy == policy
