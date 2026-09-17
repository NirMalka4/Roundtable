from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from scripts.check_package_boundaries import check


def test_every_package_is_consumed_only_through_its_facade() -> None:
    assert check() == []


def _write_package(root: Path, name: str, facade: str, internal: str = "") -> None:
    package = root / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(facade, encoding="utf-8")
    (package / "internal.py").write_text(internal, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("from roundtable.beta.internal import VALUE\n__all__ = ['VALUE']\n", "reaches through"),
        ("VALUE = 1\n", "facade must define one non-empty literal __all__"),
        ("VALUE = 1\n__all__ = ('VALUE',)\n", "facade must define one non-empty literal __all__"),
        ("from roundtable.beta import VALUE\n__all__ = ['VALUE']\n", None),
    ),
)
def test_boundary_check_detects_mutations(
    tmp_path: Path, source: str, expected: str | None
) -> None:
    root = tmp_path / "roundtable"
    _write_package(root, "alpha", source)
    _write_package(root, "beta", "VALUE = 1\n__all__ = ['VALUE']\n", "VALUE = 1\n")

    violations = check(root)

    if expected is None:
        assert violations == []
    else:
        assert any(expected in violation for violation in violations)


def test_every_declared_facade_export_is_available_at_runtime() -> None:
    root = Path(__file__).parents[2] / "roundtable"
    missing: list[str] = []
    for package in sorted(path for path in root.iterdir() if (path / "__init__.py").is_file()):
        module = importlib.import_module(f"roundtable.{package.name}")
        missing.extend(
            f"{module.__name__}.{name}" for name in module.__all__ if not hasattr(module, name)
        )
    assert missing == []


def test_bundle_registration_uses_only_plugins_facade() -> None:
    root = Path(__file__).parents[2] / "roundtable" / "configs"
    violations = []
    registration_names = {
        "register_enricher",
        "register_extractor",
        "register_gate_function",
        "register_projector",
        "register_report",
    }
    for path in root.glob("*/plugins/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported = {alias.name for alias in node.names} & registration_names
            if imported and node.module != "roundtable.plugins":
                violations.append(f"{path}:{node.lineno}:{sorted(imported)}")
    assert violations == []


def test_ado_and_configs_facades_do_not_expose_inspectorx_internals() -> None:
    root = Path(__file__).parents[2] / "roundtable"
    ado_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "ado").glob("*.py")
    )
    configs_facade = (root / "configs" / "__init__.py").read_text(encoding="utf-8")

    assert "roundtable.configs" not in ado_sources
    assert "VerdictOverlay" not in ado_sources
    assert "SpecialistFinding" not in configs_facade


def test_agent_facing_documentation_is_minimal() -> None:
    root = Path(__file__).parents[2]
    assert len((root / "AGENTS.md").read_text(encoding="utf-8").splitlines()) <= 130
    assert len(list((root / "roundtable").rglob("README.md"))) == 0
    assert {
        "architecture.md",
        "azure-devops.md",
        "backends.md",
        "cli-reference.md",
        "compatibility.md",
        "installation.md",
        "providers.md",
    } <= {path.name for path in (root / "docs").glob("*.md")}
    assert sorted(path.name for path in (root / ".github" / "skills").iterdir()) == [
        "roundtable-add-agent",
        "roundtable-add-config",
        "roundtable-adoption",
        "roundtable-agent-forge",
        "roundtable-evaluation-pr",
        "roundtable-manage-mcp",
        "roundtable-review",
    ]
