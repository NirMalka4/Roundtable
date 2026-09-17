"""No surface may still describe the contract the Judge no longer has.

A stale reference in a prompt, gate manifest, or hint is worse than a missing one: it
tells the model to produce something the schema rejects, and the cost lands on a retry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

BUNDLE = Path("roundtable/configs/buddies")
_GATES = yaml.safe_load((BUNDLE / "gates.yaml").read_text(encoding="utf-8"))["gates"]
_GRAPH = yaml.safe_load((BUNDLE / "agent_graph.yaml").read_text(encoding="utf-8"))
_JUDGE = next(entry for entry in _GRAPH["agents"] if entry["key"] == "Judge")


def test_the_free_text_evidence_gate_is_gone_everywhere() -> None:
    """Its regex only imposed structure the schema now enforces structurally."""
    assert "evidence_shape" not in _GATES
    assert not (BUNDLE / "hints" / "evidence_shape.md").exists()
    assert "evidence_shape" not in (BUNDLE / "plugins" / "gates.py").read_text(encoding="utf-8")
    assert all(g["gate"] != "evidence_shape" for g in _JUDGE["ovg_gates"])


def test_every_judge_gate_is_declared_and_hinted() -> None:
    core = {"json_schema"}
    for wiring in _JUDGE["ovg_gates"]:
        name = wiring["gate"]
        if name in core:
            continue
        assert name in _GATES, f"{name} is wired but not declared"
        hint = _GATES[name].get("default_hint")
        assert hint and (BUNDLE / "hints" / f"{hint}.md").exists(), f"{name} has no hint file"


def _resolve(schema: dict, path: str) -> dict:
    """Walk a gate `requires` path into the schema it claims to read."""
    node = schema
    for segment in path.split("."):
        node = node["properties"][segment.removesuffix("[]")]
        if segment.endswith("[]"):
            node = node["items"]
    return node


def test_gate_requires_paths_exist_in_the_judge_schema() -> None:
    schema = yaml.safe_load((BUNDLE / "schemas" / "judge.schema.yaml").read_text(encoding="utf-8"))
    for wiring in _JUDGE["ovg_gates"]:
        for path in _GATES.get(wiring["gate"], {}).get("requires", ()):
            assert _resolve(schema, path), f"{path} resolves to nothing"


def test_the_path_walk_would_notice_a_stale_requires() -> None:
    schema = yaml.safe_load((BUNDLE / "schemas" / "judge.schema.yaml").read_text(encoding="utf-8"))
    with pytest.raises(KeyError):
        _resolve(schema, "claims[].evidence[].filePath")


def test_judge_prompt_does_not_restate_the_evidence_field_shape() -> None:
    """The runtime appends the schema's own contract; a copy in the body can drift."""
    prompt = (BUNDLE / "prompts" / "Reviewer" / "Agents" / "judge.agent.md").read_text(
        encoding="utf-8"
    )
    for field in ("start_line", "end_line", "counter_evidence", "missing_guard"):
        assert field not in prompt


def test_judge_prompt_gives_stated_understandings_a_use_not_only_a_prohibition() -> None:
    prompt = (BUNDLE / "prompts" / "Reviewer" / "Agents" / "judge.agent.md").read_text(
        encoding="utf-8"
    )
    assert "### Using stated understandings" in prompt
    assert "Do not invent product intent." in prompt
    assert "never produce a new one" in prompt
