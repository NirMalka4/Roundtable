"""Static contract for the post-Judge Remedy Scout."""

from __future__ import annotations

from pathlib import Path

import yaml

from roundtable.validation.gates import compile_validator

ROOT = Path("roundtable/configs/buddies")


def _graph() -> dict:
    return yaml.safe_load((ROOT / "agent_graph.yaml").read_text(encoding="utf-8"))


def _agent(key: str) -> dict:
    return next(agent for agent in _graph()["agents"] if agent["key"] == key)


def test_schema_examples_satisfy_the_decision_contract() -> None:
    schema = yaml.safe_load((ROOT / "schemas/remedy_scout.schema.yaml").read_text(encoding="utf-8"))
    validator = compile_validator(schema, ROOT / "schemas")
    for example in schema["examples"]:
        assert list(validator.iter_errors(example)) == []
    assert "maxItems" not in schema["properties"]["decisions"]["items"]["properties"]["evidence"]
    text = (ROOT / "schemas/remedy_scout.schema.yaml").read_text(encoding="utf-8")
    assert not any(term in text for term in ("confidence:", "quality:", "rank:", "score:"))


def test_remedy_scout_runs_after_judge_with_only_read_operations() -> None:
    reducer = _agent("RemediationInputs")
    reviewer = _agent("RemedyScout")
    verdict = _agent("Verdict")
    assert {edge["from"] for edge in reducer["edges"]} >= {"Judge", "taintcheck", "bigoh"}
    assert {edge["from"] for edge in reviewer["edges"]} == {
        "RemediationInputs",
        "ReviewDiff",
    }
    assert {edge["from"] for edge in verdict["edges"]} == {
        "Judge",
        "RemedyScout",
    }
    assert all(edge["required"] is False for edge in verdict["edges"])
    assert set(reviewer["tools"]) >= {"view", "rg", "glob"}
    assert not set(reviewer["tools"]) & {
        "apply_patch",
        "powershell",
        "execute",
        "web_fetch",
        "ado-code-write",
    }


def test_prompt_keeps_the_role_narrow_and_uses_an_ordered_final_gate() -> None:
    prompt = (ROOT / "prompts/Reviewer/Agents/remedy_scout.agent.md").read_text(encoding="utf-8")
    assert prompt.count("**Non-negotiable") == 2
    assert all(f"{index}." in prompt for index in range(1, 7))
    assert "Never rewrite it" in prompt
    assert "must never be executed" in prompt
    assert "reviewer checks are leads, not accepted proof" in " ".join(prompt.lower().split())
    assert "alter the Judge claim" in prompt
