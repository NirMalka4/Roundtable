"""Unit tests for ``_upstream_finding_ids`` — the generic OVG coverage universe.

The node handler injects, into each agent's OVG context, the canonical
``agent::finding_id`` set of every LLM producer in the run snapshot, so a coverage gate
can verify the adjudicating agent left no finding unaddressed. This guards the
projection: LLM producers only, judge-like excluded, the node's own key excluded, and
deterministic nodes (source diffs, reducers) contributing nothing regardless of whether
their output happens to parse as findings.
"""

from __future__ import annotations

import json
from functools import partial

from roundtable.bundle import resolve_bundle
from roundtable.engine.agent_runner.model import AgentRunOutcome
from roundtable.graph.model import get_configuration
from roundtable.graph.model import get_entry as _get_entry
from roundtable.review.agent_inputs import _upstream_finding_ids

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
get_entry = partial(_get_entry, config=_CONFIG)


def _ids(entry, snapshot):
    return _upstream_finding_ids(entry, snapshot, _CONFIG)


def _outcome(name: str, response: str) -> AgentRunOutcome:
    return AgentRunOutcome(agent=name, response=response, valid=True, gate="all")


def _finding(fid: str) -> dict:
    return {
        "id": fid,
        "title": "t",
        "severity": "low",
        "description": "d",
        "locations": [{"file": "a.py", "line": 1}],
        "impact": "i",
        "fix": "do x",
    }


def test_universe_is_producers_only_keyed_by_canonical_id():
    judge = get_entry("Judge")
    snapshot = {
        "Security": _outcome("Security", json.dumps({"findings": [_finding("SEC-1")]})),
        "DocsKeeper": _outcome("DocsKeeper", json.dumps({"findings": [{"id": "DOC-9"}]})),
        "ReviewDiff": _outcome("ReviewDiff", "diff --git a/a.py b/a.py"),  # not JSON -> skipped
        "Judge": _outcome("Judge", "{}"),  # judge-like -> excluded
    }
    ids = _ids(judge, snapshot)
    assert "security::SEC-1" in ids
    assert "docskeeper::DOC-9" in ids
    assert not any(i.startswith("judge::") for i in ids)
    # A non-JSON source payload yields no ids.
    assert not any("reviewdiff" in i.lower() for i in ids)


def test_universe_excludes_the_nodes_own_key():
    security = get_entry("Security")
    snapshot = {
        "Security": _outcome("Security", json.dumps({"findings": [_finding("SEC-1")]})),
        "DocsKeeper": _outcome("DocsKeeper", json.dumps({"findings": [{"id": "DOC-9"}]})),
    }
    ids = _ids(security, snapshot)
    assert "security::SEC-1" not in ids  # a node never accounts for its own findings
    assert "docskeeper::DOC-9" in ids


def test_a_reducer_echoing_valid_finding_json_contributes_nothing():
    """A projection is not an independent producer, however parseable its output is.

    ``Dossier_Judge`` re-renders the reviewers' findings for the Judge. Admitting its
    echo would enter the same finding a second time under a second producer, and since
    the coverage gate is error-level the Judge would then be *forced* to cite a reducer
    as though it were a reviewer in order to pass. Exclusion is structural, so it cannot
    depend on whether the rendering happens to parse back out.
    """
    judge = get_entry("Judge")
    echo = json.dumps({"findings": [_finding("SEC-1"), _finding("SEC-2")]})
    snapshot = {
        "Security": _outcome("Security", json.dumps({"findings": [_finding("SEC-1")]})),
        "Dossier_Judge": _outcome("Dossier_Judge", echo),
    }
    ids = _ids(judge, snapshot)

    assert ids == ["security::SEC-1"]


def test_the_exclusion_is_keyed_on_node_kind_not_on_parseability():
    """Non-vacuity guard: the same payload from an LLM reviewer IS indexed."""
    judge = get_entry("Judge")
    payload = json.dumps({"findings": [_finding("X-1")]})

    from_reducer = _ids(judge, {"Dossier_Judge": _outcome("Dossier_Judge", payload)})
    from_reviewer = _ids(judge, {"Security": _outcome("Security", payload)})

    assert from_reducer == []
    assert from_reviewer == ["security::X-1"]


def test_universe_dedupes_and_is_stable_order():
    judge = get_entry("Judge")
    snapshot = {
        "Security": _outcome(
            "Security", json.dumps({"findings": [_finding("SEC-1"), _finding("SEC-1")]})
        ),
    }
    ids = _ids(judge, snapshot)
    assert ids.count("security::SEC-1") == 1
