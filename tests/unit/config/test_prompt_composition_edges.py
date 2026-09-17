"""Edge-case fixtures for the graph-driven prompt composer
(:func:`agent_setup.compose_system_prompt`).

Post-consolidation, an agent's system prompt is a plain, explicit concatenation of
its ``instructions`` body (frontmatter stripped, always first) followed by each
``shared_context`` file in listed order — no ``<shared_reference>`` envelopes, no
hidden reordering. These tests exercise that contract with a synthetic bundle +
``GraphEntry``-like objects: order preservation, verbatim content pass-through
(no tool-name rewriting), LF normalization, blank-part drop, and no-frontmatter
guard.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable.runtime import agent_setup as A


def _entry(prompt_path: str, shared_context=(), mcp_usage_files=()):
    return SimpleNamespace(
        prompt_path=prompt_path,
        shared_context=tuple(shared_context),
        mcp_usage_files=tuple(mcp_usage_files),
    )


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    (tmp_path / "Agents").mkdir()
    (tmp_path / "Shared").mkdir()
    (tmp_path / "Shared" / "Preamble.md").write_text("PREAMBLE BODY", encoding="utf-8")
    (tmp_path / "Shared" / "Standards.md").write_text("STANDARDS BODY", encoding="utf-8")
    return tmp_path


def _write_agent(bundle: Path, stem: str, body: str) -> str:
    rel = f"Agents/{stem}.agent.md"
    (bundle / rel).write_text(f"---\ndescription: {stem}\n---\n{body}", encoding="utf-8")
    return rel


def test_instructions_first_then_shared_in_listed_order(bundle: Path):
    rel = _write_agent(bundle, "A", "# A Body")
    entry = _entry(rel, ["Shared/Preamble.md", "Shared/Standards.md"])
    out = A.compose_system_prompt(entry, bundle)
    assert out == "# A Body\n\nPREAMBLE BODY\n\nSTANDARDS BODY"


def test_instructions_frontmatter_is_stripped(bundle: Path):
    rel = _write_agent(bundle, "A", "# Only Body Here")
    out = A.compose_system_prompt(_entry(rel), bundle)
    assert out == "# Only Body Here"
    assert "description:" not in out  # the instructions frontmatter never leaks in


def test_tool_names_pass_through_verbatim(bundle: Path):
    """The composer no longer rewrites tool names: prompt sources author the
    CLI-native ``ado-<tool>`` form directly, and it must survive composition
    unchanged (no VS Code ``#mcp_ado_`` coupling remains)."""
    rel = _write_agent(bundle, "A", "use ado-repo_get")
    (bundle / "Shared" / "Std2.md").write_text("call ado-wit_create", encoding="utf-8")
    out = A.compose_system_prompt(_entry(rel, ["Shared/Std2.md"]), bundle)
    assert "ado-repo_get" in out and "ado-wit_create" in out


def test_crlf_normalized_to_lf(bundle: Path):
    rel = _write_agent(bundle, "A", "L1\r\nL2")
    out = A.compose_system_prompt(_entry(rel), bundle)
    assert "\r" not in out


def test_blank_shared_part_dropped_no_triple_newline(bundle: Path):
    rel = _write_agent(bundle, "A", "# Body")
    (bundle / "Shared" / "Blank.md").write_text("   \n\n  ", encoding="utf-8")
    out = A.compose_system_prompt(_entry(rel, ["Shared/Blank.md", "Shared/Standards.md"]), bundle)
    assert out == "# Body\n\nSTANDARDS BODY"  # blank part dropped
    assert "\n\n\n" not in out


def test_missing_instructions_frontmatter_raises(bundle: Path):
    (bundle / "Agents" / "Bad.agent.md").write_text("no frontmatter", encoding="utf-8")
    with pytest.raises(ValueError):
        A.compose_system_prompt(_entry("Agents/Bad.agent.md"), bundle)
