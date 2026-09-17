"""Unit tests for the opt-in per-agent prompt dump (T0 parity) and the system-prompt
resolver that feeds it."""

import json

from roundtable.bundle import resolve_bundle
from roundtable.engine.agent_runner import AgentRunOutcome
from roundtable.graph import get_configuration
from roundtable.persistence.prompt_dump import dump_session_prompts
from roundtable.runtime.agent_setup import system_prompts_by_key

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def test_system_prompts_by_key_resolves_composed_bodies():
    prompts = system_prompts_by_key(
        _CONFIG.root / "prompts" / "Reviewer",
        entries=_CONFIG.entries,
    )
    # Keyed by the GraphEntry.key (filename stem), with the composed body (no frontmatter).
    assert "Analyst_Logic" in prompts
    body = prompts["Analyst_Logic"]
    assert body.lstrip().startswith("# Analyst Logic")
    assert not body.lstrip().startswith("---")  # frontmatter stripped


def test_dump_session_prompts_writes_ts_like_layout(tmp_path):
    outcome = AgentRunOutcome(
        agent="Analyst_Logic",
        response='{"pass":"ok","findings":[]}',
        valid=True,
        gate="semantic",
        attempts=1,
        model="claude-opus-4.6",
        tools_used=["view", "grep"],
        first_prompt="## Change Under Review\n...\n## Git Context\nDIFF\n",
    )
    session_dir = tmp_path / "session_x"
    session_dir.mkdir()
    agents_dir = dump_session_prompts(
        session_dir,
        agent_outcomes={"Analyst_Logic": outcome},
        system_prompts={"Analyst_Logic": "# Analyst Logic\nSYS"},
    )
    d = agents_dir / "Analyst_Logic"
    assert (d / "system.md").read_text(encoding="utf-8") == "# Analyst Logic\nSYS"
    assert (d / "context.md").read_text(encoding="utf-8") == outcome.first_prompt
    assert (d / "response.md").read_text(encoding="utf-8") == outcome.response
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["agent"] == "Analyst_Logic"
    assert manifest["attempts"] == 1
    assert manifest["valid"] is True
    assert manifest["model"] == "claude-opus-4.6"
    assert manifest["toolsUsed"] == ["view", "grep"]


def test_dump_missing_system_prompt_writes_empty_not_crash(tmp_path):
    outcome = AgentRunOutcome(
        agent="Unknown",
        response="x",
        valid=False,
        gate=None,
        first_prompt="CTX",
    )
    session_dir = tmp_path / "s"
    session_dir.mkdir()
    agents_dir = dump_session_prompts(
        session_dir,
        agent_outcomes={"Unknown": outcome},
        system_prompts={},
    )
    assert (agents_dir / "Unknown" / "system.md").read_text(encoding="utf-8") == ""
    assert (agents_dir / "Unknown" / "context.md").read_text(encoding="utf-8") == "CTX"
