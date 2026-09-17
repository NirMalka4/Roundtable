"""The Judge sees reviewer claims and proof, never reviewer remediation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from roundtable.configs.buddies.plugins.configuration import buddies_configuration
from roundtable.configs.buddies.plugins.context_plugins import (
    _render_adjudication_inputs,
    _render_remediation_inputs,
)
from roundtable.engine import AgentRunOutcome
from roundtable.review.agent_inputs import ReviewAgentInputBuilder


def _entry(*keys: str):
    return SimpleNamespace(dep_keys=keys)


def _outcome(payload, *, valid=True):
    response = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(response=response, valid=valid)


def test_projection_carries_every_reviewers_stated_intent() -> None:
    """Load-bearing: the Judge reads intents from this projection, not a separate section.

    A duplicate `## Agent Understandings` section used to deliver the same six strings
    verbatim in the Judge's final, recency-privileged context slot. Dropping it is only safe
    while the intent survives here, adjacent to the findings it must be weighed against — so
    the removal is pinned rather than trusted.
    """
    schemas = Path("roundtable/configs/buddies/schemas")
    reviewers = ("bigoh", "countercase", "north_star", "redgreen", "smellcheck", "taintcheck")
    snapshot, expected = {}, {}
    for name in reviewers:
        schema = yaml.safe_load((schemas / f"{name}.schema.yaml").read_text(encoding="utf-8"))
        assert "intent" in schema["required"], f"{name} may omit intent, so it cannot be relied on"
        example = schema["examples"][0]
        snapshot[name] = _outcome(example)
        expected[name] = example["intent"]

    rendered = _render_adjudication_inputs(snapshot, None, _entry(*reviewers))

    for name, intent in expected.items():
        assert json.dumps(intent)[1:-1] in rendered, f"{name}'s intent is missing from the Judge"


def test_projection_removes_nested_remediation_and_preserves_proof() -> None:
    payload = {
        "intent": "Protect the query boundary.",
        "findings": [
            {
                "id": "TC-01",
                "title": "Raw SQL",
                "anchors": [{"filePath": "src/a.py", "startLine": 4, "endLine": 4}],
                "chain": ["entry", "sink"],
                "remediation": {
                    "kind": "fix",
                    "rationale": "This keeps untrusted input out of the query grammar.",
                    "checks": [
                        {
                            "filePath": "src/caller.py",
                            "startLine": 8,
                            "endLine": 8,
                            "observation": "The caller passes the same value to the sink.",
                        }
                    ],
                    "limitations": ["The proposal was not executed."],
                    "prose": "Parameterize it.",
                },
            }
        ],
    }
    rendered = _render_adjudication_inputs(
        {"taintcheck": _outcome(payload)}, None, _entry("taintcheck")
    )
    assert "TC-01" in rendered
    assert "src/a.py" in rendered
    assert '"chain"' in rendered
    assert '"remediation"' not in rendered
    assert "Parameterize it" not in rendered
    assert "query grammar" not in rendered
    assert "src/caller.py" not in rendered
    assert "proposal was not executed" not in rendered


def test_projection_names_unavailable_reviewers() -> None:
    rendered = _render_adjudication_inputs({}, None, _entry("redgreen"))
    assert "redgreen [UNAVAILABLE]" in rendered
    assert "did not run" in rendered


def test_projection_names_invalid_json() -> None:
    rendered = _render_adjudication_inputs({"bigoh": _outcome("not json")}, None, _entry("bigoh"))
    assert "bigoh [UNAVAILABLE]" in rendered
    assert "not valid JSON" in rendered


def test_judge_receives_projection_instead_of_raw_reviewer_edges() -> None:
    graph = yaml.safe_load(
        Path("roundtable/configs/buddies/agent_graph.yaml").read_text(encoding="utf-8")
    )
    judge = next(entry for entry in graph["agents"] if entry["key"] == "Judge")
    dependencies = {edge["from"] for edge in judge["edges"]}
    assert "AdjudicationInputs" in dependencies
    assert not dependencies & {
        "bigoh",
        "countercase",
        "north_star",
        "redgreen",
        "smellcheck",
        "taintcheck",
    }


def test_judge_prompt_contains_no_remediation_rubric_or_raw_context_contract() -> None:
    prompt = Path("roundtable/configs/buddies/prompts/Reviewer/Agents/judge.agent.md").read_text(
        encoding="utf-8"
    )
    assert "### Remediation rubric" not in prompt
    assert "Evaluate remediation" not in prompt
    assert "## Context from <reviewer>" not in prompt
    assert "judge-originated claim is allowed" not in prompt.lower()


def test_reviewer_titles_do_not_smuggle_remediation_past_the_projection() -> None:
    """`_without_remediation` strips one key; a title naming the fix walks around it.

    Big-O used to require the title to "show the improvement", so its own canonical
    example reached the remediation-free Judge carrying the remedy.
    """
    schemas = Path("roundtable/configs/buddies/schemas")
    for name in ("bigoh", "taintcheck"):
        schema = yaml.safe_load((schemas / f"{name}.schema.yaml").read_text(encoding="utf-8"))
        contract = schema["properties"]["findings"]["items"]["properties"]["title"]["description"]
        assert "improvement" not in contract
        assert "never the fix" in contract, f"{name} title must declare its consumer"


def test_bigoh_titles_state_the_cost_without_naming_the_remedy() -> None:
    schema = yaml.safe_load(
        Path("roundtable/configs/buddies/schemas/bigoh.schema.yaml").read_text(encoding="utf-8")
    )
    for finding in schema["examples"][0]["findings"]:
        title = finding["title"].lower()
        assert not any(word in title for word in ("with a set", "by hoisting", "->"))


def test_remediation_projection_selects_only_surviving_primary_drafts() -> None:
    reviewer = {
        "findings": [
            {"id": "TC-01", "title": "selected", "remediation": {"proposal": "selected"}},
            {"id": "TC-02", "title": "unselected", "remediation": {"proposal": "unselected"}},
        ]
    }
    claims = [
        {
            "id": "J-01",
            "title": "survives",
            "criterion": "security_privacy",
            "disposition": "upheld",
            "severity": "high",
            "reason": "The sink remains reachable.",
            "evidence": [{"file": "src/a.py", "observation": "reachable"}],
            "primary_source_finding_id": "taintcheck::TC-01",
        },
        {
            "id": "J-02",
            "title": "dismissed",
            "criterion": "security_privacy",
            "disposition": "rejected",
            "severity": "none",
            "reason": "The path is guarded.",
            "evidence": [],
            "primary_source_finding_id": "taintcheck::TC-02",
        },
    ]
    rendered = _render_remediation_inputs(
        {
            "taintcheck": _outcome(reviewer),
            "Judge": _outcome({"verdict": {}, "claims": claims}),
        },
        None,
        _entry("Judge", "taintcheck"),
    )
    dossier = json.loads(rendered)["eligible_remediations"]
    assert dossier == [
        {
            "claim": {
                key: claims[0][key]
                for key in (
                    "id",
                    "title",
                    "criterion",
                    "disposition",
                    "severity",
                    "reason",
                    "evidence",
                )
            },
            "primary_source_finding_id": "taintcheck::TC-01",
            "reviewer_finding": {
                "id": "TC-01",
                "title": "selected",
                "remediation": {"proposal": "selected"},
            },
        }
    ]


def test_remediation_projection_excludes_missing_or_unresolvable_drafts() -> None:
    claims = [
        {
            "id": "J-01",
            "disposition": "upheld",
            "severity": "high",
            "primary_source_finding_id": "taintcheck::TC-99",
        }
    ]
    rendered = _render_remediation_inputs(
        {
            "taintcheck": _outcome({"findings": [{"id": "TC-01"}]}),
            "Judge": _outcome({"verdict": {}, "claims": claims}),
        },
        None,
        _entry("Judge", "taintcheck"),
    )
    assert json.loads(rendered) == {"eligible_remediations": []}


def test_remedy_scout_validation_receives_only_recorded_direct_upstream_responses() -> None:
    configuration = buddies_configuration()
    entry = configuration.by_key["RemedyScout"]
    snapshot = {
        "RemediationInputs": AgentRunOutcome(
            "RemediationInputs",
            '{"eligible_remediations":[]}',
            True,
            None,
        ),
        "ReviewDiff": AgentRunOutcome("ReviewDiff", "diff --git a/a.py b/a.py", True, None),
        "Judge": AgentRunOutcome("Judge", '{"claims":[]}', True, None),
    }
    builder = ReviewAgentInputBuilder(
        configuration,
        session_header="",
        context_by_key={},
        changed_files=("a.py",),
        workspace=None,
    )
    agent_input = builder(entry, snapshot, frozenset(snapshot))
    assert agent_input.validation_context["upstream_responses"] == {
        "RemediationInputs": '{"eligible_remediations":[]}',
        "ReviewDiff": "diff --git a/a.py b/a.py",
    }
