"""Unit tests for the learned repo registry."""

from __future__ import annotations

from pathlib import Path

from roundtable.inputs.workspace import RepoRegistry


def _fake_clone(tmp_path: Path, name: str) -> Path:
    clone = tmp_path / name
    (clone / ".git").mkdir(parents=True)
    return clone


def test_record_then_lookup_roundtrips(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    clone = _fake_clone(tmp_path, "Repo")
    reg = RepoRegistry.load(root)
    reg.record("host/org/repo", clone)

    reloaded = RepoRegistry.load(root)
    assert reloaded.lookup("host/org/repo") == clone


def test_lookup_prunes_deleted_path(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    clone = _fake_clone(tmp_path, "Repo")
    reg = RepoRegistry.load(root)
    reg.record("host/org/repo", clone)

    # Path vanished (repo deleted) — lookup must miss and forget it.
    import shutil

    shutil.rmtree(clone)
    assert reg.lookup("host/org/repo") is None
    assert reg.lookup("host/org/repo") is None  # idempotent


def test_missing_or_corrupt_file_yields_empty_registry(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    assert RepoRegistry.load(root).lookup("anything") is None

    root.mkdir(parents=True)
    (root / "registry.json").write_text("{ not json", encoding="utf-8")
    assert RepoRegistry.load(root).lookup("anything") is None
