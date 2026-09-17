"""Tests for hint staging — copying an author hint into an isolated grant dir."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from roundtable.context import hint_staging
from roundtable.context.hint_staging import HintTooLarge, stage_hint


def test_stage_file_isolates_it_from_siblings(tmp_path: Path):
    src_dir = tmp_path / "notes"
    src_dir.mkdir()
    hint = src_dir / "why.md"
    hint.write_text("rationale", encoding="utf-8")
    (src_dir / "secret.env").write_text("TOKEN=xyz", encoding="utf-8")  # sibling

    staged = stage_hint(hint, tmp_path / "session")

    assert staged.pointer.read_text(encoding="utf-8") == "rationale"
    # The grant dir holds ONLY the hint — the sibling secret never comes along.
    assert {p.name for p in staged.grant_dir.iterdir()} == {"why.md"}
    assert staged.pointer.parent == staged.grant_dir


def test_stage_directory_copies_tree_and_skips_vcs(tmp_path: Path):
    src = tmp_path / "design"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "config").write_text("[core]", encoding="utf-8")
    (src / "a.md").write_text("a", encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "b.md").write_text("b", encoding="utf-8")

    staged = stage_hint(src, tmp_path / "session")

    assert staged.pointer.name == "design"
    assert (staged.pointer / "a.md").read_text(encoding="utf-8") == "a"
    assert (staged.pointer / "sub" / "b.md").read_text(encoding="utf-8") == "b"
    # VCS metadata is never staged into agent-readable space.
    assert not (staged.pointer / ".git").exists()


def test_stage_directory_over_file_cap_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(hint_staging, "MAX_HINT_FILES", 2)
    src = tmp_path / "big"
    src.mkdir()
    for i in range(3):
        (src / f"f{i}.txt").write_text("x", encoding="utf-8")
    with pytest.raises(HintTooLarge):
        stage_hint(src, tmp_path / "session")


def test_stage_directory_over_byte_cap_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(hint_staging, "MAX_HINT_BYTES", 10)
    src = tmp_path / "big"
    src.mkdir()
    (src / "f.txt").write_text("x" * 100, encoding="utf-8")
    with pytest.raises(HintTooLarge):
        stage_hint(src, tmp_path / "session")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privilege on Windows")
def test_stage_directory_skips_symlinks(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET", encoding="utf-8")
    src = tmp_path / "design"
    src.mkdir()
    (src / "a.md").write_text("a", encoding="utf-8")
    (src / "link.txt").symlink_to(outside)  # points out of the tree

    staged = stage_hint(src, tmp_path / "session")

    assert (staged.pointer / "a.md").exists()
    # A symlink escaping the tree is never followed into the grant dir.
    assert not (staged.pointer / "link.txt").exists()
