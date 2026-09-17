"""The generic engine packages must not import a config bundle or the review domain."""

from __future__ import annotations

import pytest

from roundtable.context_boundary import check_context_import_boundary
from roundtable.import_boundary import CONFIG_BUNDLES, check_import_boundary, guarded_package_dir
from roundtable.persistence_boundary import check_persistence_import_boundary
from roundtable.validation_boundary import check_validation_import_boundary

_GUARDS = (
    check_context_import_boundary,
    check_persistence_import_boundary,
    check_validation_import_boundary,
)


@pytest.mark.parametrize("guard", _GUARDS, ids=lambda g: g.__name__)
def test_guarded_package_is_clean(guard):
    assert guard() == []


def test_a_bundle_import_is_reported(tmp_path):
    """The denylist covers EVERY bundle — an inspectorx-only list would miss buddies."""
    (tmp_path / "leak.py").write_text(
        "from roundtable.configs.buddies.plugins import gates\n", encoding="utf-8"
    )
    violations = check_import_boundary("roundtable.validation", tmp_path)
    assert len(violations) == 1
    assert "roundtable.configs.buddies.plugins" in violations[0]
    assert "validation must stay domain-agnostic" in violations[0]


def test_a_relative_domain_import_is_reported(tmp_path):
    """Relative imports resolve to absolute targets before the denylist is applied."""
    (tmp_path / "leak.py").write_text("from ..ado import markdown\n", encoding="utf-8")
    violations = check_import_boundary("roundtable.validation", tmp_path)
    assert len(violations) == 1
    assert "roundtable.ado" in violations[0]


def test_a_sibling_engine_import_is_allowed(tmp_path):
    """A denylist, not an allowlist: generic neighbours stay importable."""
    (tmp_path / "ok.py").write_text(
        "from roundtable.bundle.paths import config_root\nfrom ..types import vocabulary\n",
        encoding="utf-8",
    )
    assert check_import_boundary("roundtable.validation", tmp_path) == []


def test_guarded_package_dir_resolves_under_roundtable():
    assert guarded_package_dir("validation").is_dir()


@pytest.mark.parametrize("package", ("engine", "decision", "records"))
def test_generic_packages_do_not_import_configuration_bundles(package):
    assert (
        check_import_boundary(
            f"roundtable.{package}",
            guarded_package_dir(package),
            forbidden=(CONFIG_BUNDLES,),
        )
        == []
    )


def test_generic_packages_do_not_encode_inspectorx_judge_fields():
    fields = {
        "judge_observations",
        "merged_with",
        "needs_human_judgment",
        "validated_safe",
        "verdict_overlay",
    }
    root = guarded_package_dir("engine").parent
    violations = []
    for package in ("engine", "decision", "records"):
        for path in (root / package).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for field in fields:
                if field in source:
                    violations.append(f"{path.relative_to(root)}:{field}")
    assert violations == []
