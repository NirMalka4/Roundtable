"""Plumbing guard for the Author Context flags (``--hint`` / ``--hint-path``).

``build_session_header`` already renders the ``## Author Context`` block from its
inputs (see ``tests/unit/context/test_session_header.py``); this file pins the CLI
seam that resolves the operator's flags — parser defaults, inline-hint passthrough,
and the non-fatal missing-path warning — before they reach the header. Hint staging
itself is covered by ``tests/unit/context/test_hint_staging.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from roundtable import cli


# ── parser defaults ─────────────────────────────────────────────────────────
def test_hint_flags_default_to_none():
    args = cli.build_parser().parse_args(["review", "some/repo"])
    assert args.hint is None
    assert args.hint_path is None


def test_hint_flags_parse_values():
    args = cli.build_parser().parse_args(
        ["review", "some/repo", "--hint", "intentional", "--hint-path", "notes.md"]
    )
    assert args.hint == "intentional"
    assert args.hint_path == "notes.md"


# ── _resolve_hint_args ──────────────────────────────────────────────────────
def test_resolve_hint_args_passes_inline_hint_through():
    args = SimpleNamespace(hint="  weigh this  ", hint_path=None)
    assert cli._resolve_hint_args(args) == ("weigh this", None)


def test_resolve_hint_args_empty_normalizes_to_none():
    args = SimpleNamespace(hint="   ", hint_path="")
    assert cli._resolve_hint_args(args) == (None, None)


def test_resolve_hint_args_returns_absolute_source_for_file(tmp_path: Path):
    f = tmp_path / "rationale.md"
    f.write_text("why", encoding="utf-8")
    args = SimpleNamespace(hint=None, hint_path=str(f))
    hint, hint_src = cli._resolve_hint_args(args)
    assert hint is None
    assert hint_src == str(f.resolve())


def test_resolve_hint_args_accepts_a_directory(tmp_path: Path):
    d = tmp_path / "design"
    d.mkdir()
    args = SimpleNamespace(hint=None, hint_path=str(d))
    _, hint_src = cli._resolve_hint_args(args)
    assert hint_src == str(d.resolve())


def test_resolve_hint_args_absolutizes_relative_path(tmp_path: Path, monkeypatch):
    # Agents run with cwd=workspace.path, so a relative hint path must be resolved
    # to absolute (against the CLI cwd) or it would point somewhere else for them.
    f = tmp_path / "notes.md"
    f.write_text("ctx", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(hint=None, hint_path="notes.md")
    _, hint_src = cli._resolve_hint_args(args)
    assert hint_src == str(f.resolve())
    assert Path(hint_src).is_absolute()


def test_resolve_hint_args_missing_path_warns_and_drops(tmp_path: Path, capsys):
    missing = str(tmp_path / "nope.md")
    args = SimpleNamespace(hint=None, hint_path=missing)
    hint, hint_src = cli._resolve_hint_args(args)
    assert (hint, hint_src) == (None, None)
    assert "--hint-path not found" in capsys.readouterr().err
