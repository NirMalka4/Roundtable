"""Layer 8 — dossier corpus completeness for the doctor.

Two guarantees:
  * the REAL graph is complete (zero errors) — a producer dropped from a zero-drop
    dossier node turns the doctor red; and
  * the rule fires on a synthetic drift (proving it is not a silent no-op).

This is the explicit, checked replacement for the deleted ``collects_all`` implicit
auto-include: a newly added finding-producer can never be silently dropped from the
reconciled corpus.
"""

from __future__ import annotations

import dataclasses

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import finding_producing_agent_keys, get_configuration
from roundtable.validation.coherence import validate_dossier_completeness

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def test_real_graph_dossier_corpus_is_complete():
    report = validate_dossier_completeness(_CONFIG.entries, configuration=_CONFIG)
    assert report.errors == []


def test_missing_producer_is_flagged():
    producers = finding_producing_agent_keys(include_terminal=False, config=_CONFIG)
    victim = sorted(producers)[0]
    mutated = []
    for e in _CONFIG.entries:
        if e.key == "Dossier_Judge":
            e = dataclasses.replace(e, edges=tuple(ed for ed in e.edges if ed.source != victim))
        mutated.append(e)
    report = validate_dossier_completeness(mutated, configuration=_CONFIG)
    assert any(victim in err and "Dossier_Judge" in err for err in report.errors)


def test_zero_drop_flag_drives_the_check_not_a_hardcoded_name():
    # The completeness rule fires on a node BECAUSE it declares consolidation.zero_drop,
    # not because its key is hard-coded. Drop a producer from Dossier_SeverityInflator:
    # with the flag it errors; clear the flag and the very same drift is accepted.
    producers = finding_producing_agent_keys(include_terminal=False, config=_CONFIG)
    victim = sorted(producers)[0]

    def _drop_victim(entries, *, keep_flag):
        out = []
        for e in entries:
            if e.key == "Dossier_SeverityInflator":
                edges = tuple(ed for ed in e.edges if ed.source != victim)
                spec = (
                    e.consolidation
                    if keep_flag
                    else dataclasses.replace(e.consolidation, zero_drop=False)
                )
                e = dataclasses.replace(e, edges=edges, consolidation=spec)
            out.append(e)
        return out

    base = _CONFIG.entries
    flagged = validate_dossier_completeness(
        _drop_victim(base, keep_flag=True),
        configuration=_CONFIG,
    )
    assert any(victim in err and "Dossier_SeverityInflator" in err for err in flagged.errors)

    unflagged = validate_dossier_completeness(
        _drop_victim(base, keep_flag=False),
        configuration=_CONFIG,
    )
    assert not any("Dossier_SeverityInflator" in err for err in unflagged.errors)


def test_severity_inflator_excluded_from_corpus():
    # Adding SeverityInflator (a terminal producer) is NOT required — the rule only
    # demands the non-terminal producer set, so a graph without it is still complete.
    report = validate_dossier_completeness(_CONFIG.entries, configuration=_CONFIG)
    assert report.errors == []
    for node_key in ("Dossier_Judge", "Dossier_SeverityInflator"):
        node = next(e for e in _CONFIG.entries if e.key == node_key)
        scope = set(node.dep_keys)
        assert "SeverityInflator" not in scope
