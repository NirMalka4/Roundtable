"""Graph SSOT structural invariants: the serialize/deserialize pair round-trips
exactly, and the loaded graph keeps its declared size and boundary order.

These are stance-independent properties (they hold regardless of graph content).
Serialized-graph golden tests are intentionally absent: pinning the graph only
told you it *changed*, which git already shows, and forced a regen-and-review
ritual on every legitimate graph edit. ``doctor`` validates graph *correctness*;
these tests validate the serializer round-trip and declaration order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from roundtable.graph import Configuration
from roundtable.graph.loader import (
    _bundle_fingerprint_inputs,
    _fingerprint,
    entry_to_dict,
    graph_config_sha,
    load_agent_graph,
    load_executor_name,
    load_step_budget,
    record_to_entry,
)


def test_entry_dict_roundtrip_is_identity():
    """record_to_entry(entry_to_dict(e)) == e for every loaded entry."""
    for e in load_agent_graph():
        assert record_to_entry(entry_to_dict(e)) == e


def test_graph_config_sha_is_stable_12_hex():
    """The provenance fingerprint is a deterministic 12-char hex digest."""
    sha = graph_config_sha()
    assert sha == graph_config_sha()  # deterministic across calls
    assert len(sha) == 12
    assert all(c in "0123456789abcdef" for c in sha)


def test_fingerprint_covers_whole_effective_bundle():
    """The digest spans every configuration-controlled behavior source."""
    labels = [label for label, _ in _bundle_fingerprint_inputs()]
    assert "graph" in labels
    assert "gates" in labels
    assert any(label.startswith("schema:") for label in labels)
    assert any(label.startswith("prompt:") for label in labels)
    assert any(label.startswith("plugin:") for label in labels)
    assert "mcp-servers" in labels
    # schema tree alone is 30+ files — a graph-only hash would have a single input.
    assert len(labels) > 30


def test_fingerprint_is_sensitive_to_content_and_labels():
    """Any changed byte OR a rename (label change) moves the digest."""
    base = [("graph", b"1"), ("schema:x", b"2")]
    assert _fingerprint(base) != _fingerprint([("graph", b"1"), ("schema:x", b"9")])
    assert _fingerprint(base) != _fingerprint([("graph", b"1"), ("schema:y", b"2")])
    assert _fingerprint(base) == _fingerprint(list(base))


@pytest.mark.parametrize(
    ("relative", "changes_fingerprint"),
    [
        ("plugins/main.py", True),
        ("plugins/helper.py", True),
        ("plugins/undeclared.py", False),
        ("plugins/__pycache__/main.cpython-312.pyc", False),
        ("plugins/main.py.tmp", False),
    ],
)
def test_fingerprint_includes_only_declared_plugins_and_required_sources(
    tmp_path: Path, relative: str, changes_fingerprint: bool
) -> None:
    root = tmp_path / "bundle"
    plugins = root / "plugins"
    plugins.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (plugins / "__init__.py").write_text("", encoding="utf-8")
    (plugins / "main.py").write_text("from .helper import VALUE\n", encoding="utf-8")
    (plugins / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (plugins / "undeclared.py").write_text("VALUE = 1\n", encoding="utf-8")
    document = {
        "name": "bundle",
        "plugins": ["bundle.plugins.main"],
        "agents": [{"key": "Source", "kind": "source", "emoji": "S"}],
    }
    before = Configuration.from_document(document, root=root).fingerprint
    changed = root / relative
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_bytes(changed.read_bytes() + b"# changed\n" if changed.exists() else b"changed")
    after = Configuration.from_document(document, root=root).fingerprint

    assert (after != before) is changes_fingerprint


def test_graph_preserves_declaration_order_anchors():
    entries = load_agent_graph()
    assert entries[0].key == "ReviewDiff"
    assert entries[-1].key == "Verdict"


def test_executor_name_is_dag_for_the_canonical_graph():
    assert load_executor_name() == "dag"


def test_executor_name_reads_explicit_key(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("executor: iterative\nagents: []\n", encoding="utf-8")
    assert load_executor_name(p) == "iterative"


def test_executor_name_defaults_when_absent(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("agents: []\n", encoding="utf-8")
    assert load_executor_name(p) == "dag"


def test_step_budget_is_unbounded_for_the_canonical_graph():
    # The real graph declares no max_steps — a validated DAG needs no budget.
    assert load_step_budget() is None


def test_step_budget_reads_positive_int(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("max_steps: 5\nagents: []\n", encoding="utf-8")
    assert load_step_budget(p) == 5


def test_step_budget_none_when_absent_or_non_positive(tmp_path):
    absent = tmp_path / "a.yaml"
    absent.write_text("agents: []\n", encoding="utf-8")
    assert load_step_budget(absent) is None
    zero = tmp_path / "z.yaml"
    zero.write_text("max_steps: 0\nagents: []\n", encoding="utf-8")
    assert load_step_budget(zero) is None


def test_conditional_edge_when_roundtrips():
    """An Edge carrying a ``when:`` predicate survives serialize/deserialize."""
    from roundtable.graph.model import Edge, GraphEntry
    from roundtable.graph.predicates import parse_predicate

    e = GraphEntry(
        key="Deep",
        prompt_path="Deep.agent.md",
        edges=(
            Edge(
                source="Triage",
                required=True,
                when=parse_predicate({"all": [{"field": "risk", "in": ["high", "critical"]}]}),
            ),
        ),
        emoji="x",
    )
    assert record_to_entry(entry_to_dict(e)) == e
    # the when: block is emitted under the edge record
    rec = entry_to_dict(e)
    assert rec["edges"][0]["when"] == {"all": [{"field": "risk", "in": ["high", "critical"]}]}


def test_fan_out_roundtrips():
    """A ``kind: map`` node's ``fan_out`` spec survives serialize/deserialize."""
    from roundtable.graph.model import Edge, FanOut, GraphEntry

    e = GraphEntry(
        key="Fanner",
        prompt_path="",
        edges=(Edge(source="Producer", required=True),),
        emoji="x",
        kind="map",
        fan_out=FanOut(over="Producer.items"),
    )
    assert record_to_entry(entry_to_dict(e)) == e
    rec = entry_to_dict(e)
    assert rec["fan_out"] == {"over": "Producer.items"}
    assert rec["kind"] == "map"


def test_no_fan_out_key_emitted_for_non_map_entries():
    """Entries without fan_out omit the key entirely (defaults-omitted schema)."""
    from roundtable.graph.model import GraphEntry

    e = GraphEntry(key="C", prompt_path="", edges=(), emoji="x", kind="code", code_fn="run_prescan")
    assert "fan_out" not in entry_to_dict(e)


def test_tool_policy_roundtrips_with_concrete_sdk_tool_names():
    from roundtable.graph import (
        GraphEntry,
        PowershellToolPolicy,
        ReadPowershellToolPolicy,
        ToolPolicy,
    )

    policy = ToolPolicy(
        powershell=PowershellToolPolicy(120, False),
        read_powershell=ReadPowershellToolPolicy(30),
    )
    entry = GraphEntry(
        key="Tester",
        prompt_path="Tester.agent.md",
        edges=(),
        emoji="x",
        tools=("powershell", "read_powershell"),
        tool_policy=policy,
    )
    record = entry_to_dict(entry)

    assert record["tool_policy"] == {
        "powershell": {
            "invocation_cap_seconds": 120,
            "detached_allowed": False,
        },
        "read_powershell": {"poll_cap_seconds": 30},
    }
    assert record_to_entry(record) == entry


def test_timeout_seconds_roundtrips_without_unit_conversion():
    from roundtable.graph import GraphEntry

    entry = GraphEntry(
        key="Slow",
        prompt_path="Slow.agent.md",
        edges=(),
        emoji="x",
        timeout_seconds=1800,
    )
    record = entry_to_dict(entry)

    assert record["timeout_seconds"] == 1800
    assert "timeout_ms" not in record
    assert record_to_entry(record) == entry


def test_consolidation_roundtrips_with_default_render_fields_omitted():
    """A ``consolidate`` node's consolidation block survives serialize/deserialize,
    and defaulted render fields are omitted from the record."""
    from roundtable.consolidation import RenderSpec
    from roundtable.graph.model import ConsolidationSpec, Edge, GraphEntry

    e = GraphEntry(
        key="Cons",
        prompt_path="",
        edges=(Edge(source="P", required=False),),
        emoji="x",
        kind="reducer",
        code_fn="consolidate",
        consolidation=ConsolidationSpec(
            extract="specialist_findings",
            adjacency_gap=10,
            render=RenderSpec(
                preamble="p",
                empty_note="none",
                group_prefix="C",
                item_singular="finding",
                group_plural_label="Concerns",
                item_plural_label="Findings",
            ),
        ),
    )
    assert record_to_entry(entry_to_dict(e)) == e
    rec = entry_to_dict(e)
    assert rec["consolidation"]["extract"] == "specialist_findings"
    assert rec["consolidation"]["adjacency_gap"] == 10
    # defaulted render fields are omitted, required ones present
    assert "checksum_header" not in rec["consolidation"]["render"]
    assert "no_group_label" not in rec["consolidation"]["render"]
    assert rec["consolidation"]["render"]["group_plural_label"] == "Concerns"


def test_consolidation_roundtrips_with_overridden_render_defaults():
    """Non-default checksum_header / no_group_label survive the round-trip."""
    from roundtable.consolidation import RenderSpec
    from roundtable.graph.model import ConsolidationSpec, GraphEntry

    e = GraphEntry(
        key="Cons",
        prompt_path="",
        edges=(),
        emoji="x",
        kind="reducer",
        code_fn="consolidate",
        consolidation=ConsolidationSpec(
            extract="specialist_findings",
            adjacency_gap=5,
            render=RenderSpec(
                preamble="p",
                empty_note="none",
                group_prefix="G",
                item_singular="item",
                group_plural_label="Groups",
                item_plural_label="Items",
                checksum_header="Tally",
                no_group_label="(unplaced)",
            ),
        ),
    )
    assert record_to_entry(entry_to_dict(e)) == e
    rec = entry_to_dict(e)
    assert rec["consolidation"]["render"]["checksum_header"] == "Tally"
    assert rec["consolidation"]["render"]["no_group_label"] == "(unplaced)"


def test_consolidation_roundtrips_with_appendix_depth():
    """A drill-down render (depth + appendix_header) survives the round-trip and
    omits those keys again when left at their defaults."""
    from roundtable.consolidation import RenderSpec
    from roundtable.graph.model import ConsolidationSpec, GraphEntry

    def _entry(**render_over) -> GraphEntry:
        return GraphEntry(
            key="Cons",
            prompt_path="",
            edges=(),
            emoji="x",
            kind="reducer",
            code_fn="consolidate",
            consolidation=ConsolidationSpec(
                extract="specialist_findings",
                adjacency_gap=5,
                render=RenderSpec(
                    preamble="p",
                    empty_note="none",
                    group_prefix="G",
                    item_singular="item",
                    group_plural_label="Groups",
                    item_plural_label="Items",
                    **render_over,
                ),
            ),
        )

    drill = _entry(depth="index+appendix", appendix_header="Full Records")
    assert record_to_entry(entry_to_dict(drill)) == drill
    rec = entry_to_dict(drill)
    assert rec["consolidation"]["render"]["depth"] == "index+appendix"
    assert rec["consolidation"]["render"]["appendix_header"] == "Full Records"

    default = _entry()
    assert "depth" not in entry_to_dict(default)["consolidation"]["render"]
    assert "appendix_header" not in entry_to_dict(default)["consolidation"]["render"]
