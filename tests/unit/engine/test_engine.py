from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from pathlib import Path

from roundtable.backend import MockBackend
from roundtable.bundle import resolve_bundle, set_config_root
from roundtable.engine import Engine, RunOptions, _runnable_graph_entries
from roundtable.graph import get_configuration


def test_one_engine_runs_two_configurations_with_distinct_agent_keys() -> None:
    runs = {}
    opened_for: list[str] = []

    @contextmanager
    def bind_backend(options):
        opened_for.append(options.configuration.name)
        delegate = MockBackend(options.configuration, changed_files=("x.py",))

        class StrictConfigurationBackend:
            def run(self, request):
                if request.agent not in options.configuration.by_key:
                    raise KeyError(request.agent)
                return delegate.run(request)

        yield StrictConfigurationBackend()

    engine = Engine(bind_backend, options=RunOptions(max_attempts=1, changed_files=("x.py",)))
    failure: Exception | None = None
    try:
        try:
            for name, ambient in (("inspectorx", "buddies"), ("buddies", "inspectorx")):
                config = get_configuration(resolve_bundle(name))
                set_config_root(resolve_bundle(ambient))
                result = engine.run(config, {"ReviewDiff": "diff --git a/x.py b/x.py\n+pass"})
                runs[name] = set(result.results)
                assert set(result.results) == {
                    entry.key for entry in _runnable_graph_entries(config)
                }
                assert result.results["Verdict"].valid is True
                assert set(json.loads(result.results["Verdict"].response)) == {
                    "counts",
                    "reason",
                    "verdict",
                    "verdictIcon",
                    "verdictOverridden",
                }
        except Exception as err:
            failure = err
    finally:
        set_config_root(None)

    assert failure is None, repr(failure)
    assert "Profiler_CodeMap" in runs["inspectorx"]
    assert "Profiler_CodeMap" not in runs["buddies"]
    assert "bigoh" in runs["buddies"]
    assert "bigoh" not in runs["inspectorx"]
    assert opened_for == ["inspectorx", "buddies"]


def test_engine_has_no_domain_imports_or_ambient_configuration_reads() -> None:
    engine_root = Path(__file__).parents[3] / "roundtable" / "engine"
    forbidden = {"ado", "configs", "inputs", "review"}
    violations: list[str] = []
    for path in engine_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if len(parts) > 1 and parts[0] == "roundtable" and parts[1] in forbidden:
                    violations.append(f"{path.name}:{node.lineno}:{node.module}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "get_configuration"
            ):
                violations.append(f"{path.name}:{node.lineno}:get_configuration")
    assert violations == []
