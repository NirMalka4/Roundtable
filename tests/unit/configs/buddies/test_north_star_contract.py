from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration


def test_north_star_has_no_machine_local_mcp_dependency() -> None:
    root = resolve_bundle("buddies")
    entry = get_configuration(root).by_key["north_star"]

    assert entry.timeout_seconds == 1200
    assert all("nexus" not in tool.lower() for tool in entry.tools)

    prompt = (root / "prompts" / "Reviewer" / entry.prompt_path).read_text(encoding="utf-8")
    assert "nexus" not in prompt.lower()
