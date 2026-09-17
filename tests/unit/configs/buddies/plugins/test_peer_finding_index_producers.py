"""Only LLM nodes may contribute to the peer finding index.

``AdjudicationInputs`` is a ``reducer`` that re-renders the reviewers' findings verbatim so
the Judge sees claims without remediation. Because that rendering embeds the original finding
JSON, ``extract_findings`` could parse it straight back out, and the index then held a phantom
``adjudication_inputs::<id>`` record for a finding the projection never originated. The Judge
cited that key alongside the real one, and the published thread read
``found by Big-O (BH-01), adjudication inputs (BH-01)``.

The corpus is derived from the graph's ``is_llm`` entries, so any future deterministic node
that echoes findings is excluded by construction rather than by name.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from roundtable.bundle.paths import set_config_root
from roundtable.configs import buddies as buddies_pkg
from roundtable.configs.buddies.plugins.configuration import buddies_configuration
from roundtable.configs.buddies.plugins.peer_finding_index import (
    _finding_producing_agents,
    build_peer_finding_index,
)
from roundtable.runtime import try_resolve_canonical_id

BUNDLE = Path(buddies_pkg.__file__).parent
_BUDDIES_ROOT = "roundtable/configs/buddies"


@pytest.fixture(autouse=True)
def _use_buddies_config():
    set_config_root(_BUDDIES_ROOT)
    try:
        yield
    finally:
        set_config_root(None)


def _example(name: str) -> dict[str, Any]:
    schema = yaml.safe_load(
        (BUNDLE / "schemas" / f"{name}.schema.yaml").read_text(encoding="utf-8")
    )
    return copy.deepcopy(schema["examples"][0])


def _results(**agents: Any) -> dict[str, dict[str, object]]:
    return {
        name: {"response": payload if isinstance(payload, str) else json.dumps(payload)}
        for name, payload in agents.items()
    }


def test_llm_reviewer_findings_are_indexed() -> None:
    index = build_peer_finding_index(_results(bigoh=_example("bigoh")))

    assert index.all_records, "the reviewer's own findings must still be indexed"
    assert {r.source_agent_canonical for r in index.all_records} == {"bigoh"}


def test_reducer_echo_of_a_reviewer_finding_is_not_indexed() -> None:
    """The exact defect: the projection re-renders bigoh's payload, so it parses back out."""
    payload = _example("bigoh")
    echo = f"### Big-O [AVAILABLE]\n\n```json\n{json.dumps(payload)}\n```"

    index = build_peer_finding_index(_results(AdjudicationInputs=echo))

    assert index.all_records == ()
    assert not [key for key in index.by_key if key.startswith("adjudication_inputs::")]


def test_the_reducer_echo_would_otherwise_have_been_indexed() -> None:
    """Proves the case above is not vacuous — the echo really is parseable as findings."""
    from roundtable.extraction import extract_findings

    payload = _example("bigoh")
    echo = f"### Big-O [AVAILABLE]\n\n```json\n{json.dumps(payload)}\n```"

    recovered = extract_findings("AdjudicationInputs", echo, buddies_configuration())

    assert recovered, "if this stops parsing, the regression above no longer guards anything"


def test_a_reviewer_finding_is_attributed_once_when_the_projection_is_present() -> None:
    payload = _example("bigoh")
    echo = f"### Big-O [AVAILABLE]\n\n```json\n{json.dumps(payload)}\n```"

    index = build_peer_finding_index(_results(bigoh=payload, AdjudicationInputs=echo))

    finding_ids = [r.finding_id for r in index.all_records]
    assert len(finding_ids) == len(set(finding_ids))
    assert {r.source_agent_canonical for r in index.all_records} == {"bigoh"}


def test_judge_is_excluded_even_though_it_is_an_llm_node() -> None:
    index = build_peer_finding_index(_results(Judge=_example("judge")))

    assert index.all_records == ()


def test_producer_set_is_derived_from_the_graph_not_a_hand_list() -> None:
    configuration = buddies_configuration()
    producers = _finding_producing_agents(configuration)

    expected = {
        try_resolve_canonical_id(entry.key, configuration) or entry.key.lower()
        for entry in configuration.entries
        if getattr(entry, "is_llm", False)
    }
    assert producers == expected
    assert "adjudication_inputs" not in producers
    assert "consolidation" not in producers
