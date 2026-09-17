"""Release-helper scripts — the coherence gate and local release preparation.

``scripts/`` is not an importable package, so each script is loaded by path. The
tests target pure parsing/planning functions and use throwaway files for the
public commands.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load("check_release_version")
release = _load("release")


def test_distribution_declares_pep_639_mit_license():
    metadata = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["build-system"]["requires"] == ["setuptools>=77.0.3"]
    assert metadata["project"]["license"] == "MIT"
    assert metadata["project"]["license-files"] == ["LICENSE"]
    assert (_REPO / "LICENSE").is_file()


# ── check_release_version.authored_version ──────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('__version__ = "1.2.3"\n', "1.2.3"),
        ("__version__ = '0.10.0'\n", "0.10.0"),
        ('__version__ = "1.2.3+dev"\n', "1.2.3"),  # local suffix dropped
    ],
)
def test_authored_version_extracts_bare(text, expected):
    assert check.authored_version(text) == expected


def test_authored_version_missing_raises():
    with pytest.raises(ValueError, match="no __version__ assignment"):
        check.authored_version("# nothing here\n")


# ── check_release_version.top_release_version ───────────────────────────────
def test_top_release_version_skips_unreleased_and_takes_newest():
    text = (
        "# Changelog\n\n"
        "## [Unreleased]\n- wip\n\n"
        "## [v1.2.0] - 2026-07-16\n- a\n\n"
        "## [v1.1.0] - 2026-07-01\n- b\n"
    )
    assert check.top_release_version(text) == "1.2.0"


def test_top_release_version_accepts_no_v_prefix():
    assert check.top_release_version("## [1.0.0] - 2026-07-16\n") == "1.0.0"


def test_top_release_version_none_raises():
    with pytest.raises(ValueError, match=r"no '## \[vX.Y.Z\]' release heading"):
        check.top_release_version("## [Unreleased]\n- only wip\n")


# ── check_release_version.unreleased_body ───────────────────────────────────
def test_unreleased_body_is_empty_right_after_a_release():
    text = "# Changelog\n\n## [Unreleased]\n\n## [v1.2.0] - 2026-07-16\n- shipped\n"
    assert check.unreleased_body(text) == ""


def test_unreleased_body_stops_at_the_next_release_heading():
    text = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n### Added\n- a pending thing\n\n"
        "## [v1.2.0] - 2026-07-16\n- already shipped\n"
    )
    body = check.unreleased_body(text)
    assert "- a pending thing" in body
    assert "already shipped" not in body


def test_unreleased_body_missing_heading_raises():
    with pytest.raises(ValueError, match=r"no '## \[Unreleased\]' heading"):
        check.unreleased_body("## [v1.0.0] - 2026-07-01\n- x\n")


# ── check_release_version.main (--require-empty-unreleased) ─────────────────
_PROMOTED = "# Changelog\n\n## [Unreleased]\n\n## [v1.2.0] - 2026-07-16\n- shipped\n"
_UNPROMOTED = (
    "# Changelog\n\n"
    "## [Unreleased]\n\n### Added\n- forgot to promote this\n\n"
    "## [v1.2.0] - 2026-07-16\n- shipped\n"
)


def _repo_files(tmp_path, monkeypatch, version: str, changelog: str, trunk: str | None = None):
    """Point the gate at a throwaway version/changelog pair and a chosen trunk version."""
    version_file = tmp_path / "_version.py"
    version_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(changelog, encoding="utf-8")
    monkeypatch.setattr(check, "_VERSION_FILE", version_file)
    monkeypatch.setattr(check, "_CHANGELOG", changelog_file)
    monkeypatch.setattr(
        check, "trunk_version", lambda: None if trunk is None else (trunk, "origin/main")
    )


def test_main_publishing_accepts_a_promoted_changelog(tmp_path, monkeypatch):
    _repo_files(tmp_path, monkeypatch, "1.2.0", _PROMOTED)
    assert check.main(["--require-empty-unreleased"]) == 0


def test_main_publishing_rejects_unpromoted_notes(tmp_path, monkeypatch, capsys):
    _repo_files(tmp_path, monkeypatch, "1.2.0", _UNPROMOTED)
    assert check.main(["--require-empty-unreleased"]) == 1
    assert "UNPROMOTED" in capsys.readouterr().err


def test_main_without_the_flag_tolerates_pending_notes(tmp_path, monkeypatch):
    """Between releases [Unreleased] is *supposed* to fill up — everyday CI must pass."""
    _repo_files(tmp_path, monkeypatch, "1.2.0", _UNPROMOTED, trunk="1.2.0")
    assert check.main([]) == 0


# ── check_release_version: deriving "this branch publishes" without the feed ──
def test_unpromoted_notes_fail_a_release_branch_without_the_flag(tmp_path, monkeypatch, capsys):
    """The everyday pre-flight must catch this, not the publish job after the merge."""
    _repo_files(tmp_path, monkeypatch, "1.2.0", _UNPROMOTED, trunk="1.1.0")
    assert check.main([]) == 1
    assert "UNPROMOTED" in capsys.readouterr().err


def test_a_branch_trailing_trunk_is_not_treated_as_a_release(tmp_path, monkeypatch):
    """A feature branch that has not rebased is behind trunk — it publishes nothing."""
    _repo_files(tmp_path, monkeypatch, "1.2.0", _UNPROMOTED, trunk="1.3.0")
    assert check.main([]) == 0


def test_an_unreadable_trunk_abstains_instead_of_guessing(tmp_path, monkeypatch, capsys):
    """A shallow CI clone has nothing to compare against; the gate must not invent one."""
    _repo_files(tmp_path, monkeypatch, "1.2.0", _UNPROMOTED, trunk=None)
    assert check.main([]) == 0
    assert "no trunk ref" in capsys.readouterr().out


def test_the_unpromoted_remedy_folds_into_the_pending_release(tmp_path, monkeypatch, capsys):
    """`bump` would cut a further version; the notes belong to the version being published."""
    _repo_files(tmp_path, monkeypatch, "1.2.0", _UNPROMOTED, trunk="1.1.0")
    assert check.main([]) == 1
    err = capsys.readouterr().err
    assert "release.py promote" in err
    assert "bump" not in err


def test_trunk_version_falls_back_to_the_next_ref(monkeypatch):
    asked = []

    def run(command, *, cwd, capture_output, text):
        asked.append(command[2])
        missing = command[2].startswith("origin/main")
        return type(
            "Completed",
            (),
            {"returncode": 1 if missing else 0, "stdout": '__version__ = "2.0.0"\n', "stderr": ""},
        )()

    monkeypatch.setattr(check.subprocess, "run", run)
    assert check.trunk_version() == ("2.0.0", "main")
    assert asked == ["origin/main:roundtable/_version.py", "main:roundtable/_version.py"]


# ── release.bump_version_file / current_version_of ──────────────────────────
def test_bump_version_file_replaces_only_the_assignment():
    text = '"""doc"""\n__version__ = "1.0.0"\n'
    assert release.bump_version_file(text, "1.1.0") == '"""doc"""\n__version__ = "1.1.0"\n'


def test_bump_version_file_missing_raises():
    with pytest.raises(release.ReleaseError, match="no __version__ assignment"):
        release.bump_version_file("x = 1\n", "1.1.0")


def test_current_version_of_reads_assignment():
    assert release.current_version_of('__version__ = "2.3.4"\n') == "2.3.4"


# ── release.promote_changelog ───────────────────────────────────────────────
def test_promote_changelog_moves_body_and_keeps_unreleased():
    text = "# Changelog\n\n## [Unreleased]\n\n- added a thing\n\n## [v1.0.0] - 2026-07-01\n- old\n"
    out = release.promote_changelog(text, "1.1.0", "2026-07-16")
    assert "## [v1.1.0] - 2026-07-16" in out
    assert "- added a thing" in out.split("## [v1.1.0]", 1)[1]  # body landed under new heading
    assert "## [Unreleased]" in out  # accumulator kept for next cycle
    assert out.index("## [Unreleased]") < out.index("## [v1.1.0]")  # order preserved
    assert "## [v1.0.0] - 2026-07-01" in out  # prior release untouched


def test_promote_changelog_missing_unreleased_raises():
    with pytest.raises(release.ReleaseError, match=r"no '## \[Unreleased\]' heading"):
        release.promote_changelog("## [v1.0.0] - 2026-07-01\n- x\n", "1.1.0", "2026-07-16")


# ── release.plan_bump (atomic; reads real repo files, writes nothing) ───────
def test_plan_bump_non_increasing_is_rejected():
    with pytest.raises(release.ReleaseError, match="strictly increase"):
        release.plan_bump("0.0.1", today="2026-07-16")


def test_plan_bump_computes_both_without_writing():
    new_version_text, new_changelog = release.plan_bump("9.9.9", today="2026-07-16")
    assert '__version__ = "9.9.9"' in new_version_text
    assert "## [v9.9.9] - 2026-07-16" in new_changelog
    # real files are untouched — the helper only computes.
    assert "9.9.9" not in check._VERSION_FILE.read_text(encoding="utf-8")


_PENDING_CHANGELOG = (
    "# Changelog\n\n"
    "## [Unreleased]\n\n"
    "### Fixed\n"
    "- repaired behavior\n\n"
    "## [v4.2.0] - 2026-08-15\n\n"
    "- previous release\n"
)
_PROMOTED_CHANGELOG = (
    "# Changelog\n\n"
    "## [Unreleased]\n\n\n"
    "## [v4.2.1] - 2026-08-16\n\n"
    "### Fixed\n"
    "- repaired behavior\n\n"
    "## [v4.2.0] - 2026-08-15\n\n"
    "- previous release\n"
)


def _manual_release_files(tmp_path, monkeypatch, changelog=_PENDING_CHANGELOG):
    version_file = tmp_path / "_version.py"
    version_file.write_text('__version__ = "4.2.0"\n', encoding="utf-8")
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(changelog, encoding="utf-8")
    monkeypatch.setattr(release, "_VERSION_FILE", version_file)
    monkeypatch.setattr(release, "_CHANGELOG", changelog_file)
    return version_file, changelog_file


def test_manual_release_promotes_release_and_runs_the_authoritative_gates(
    tmp_path, monkeypatch, capsys
):
    version_file, changelog_file = _manual_release_files(tmp_path, monkeypatch)
    calls = []

    def run(command, *, cwd):
        calls.append((command, cwd))
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(release.subprocess, "run", run)

    assert release.main(["manual-release", "4.2.1", "--date", "2026-08-16"]) == 0
    assert version_file.read_text(encoding="utf-8") == '__version__ = "4.2.1"\n'
    assert changelog_file.read_text(encoding="utf-8") == _PROMOTED_CHANGELOG
    assert calls == [([release.sys.executable, "scripts/gates.py"], release._REPO)]
    output = capsys.readouterr().out
    assert f"local preparation passed on {release.sys.platform}" in output
    assert "does not validate other CI platforms or publish" in output
    assert "GitHub CI checks for Python 3.12 and 3.13" in output
    assert "publishing 4.2.1 is a separate maintainer action" in output


def test_manual_release_propagates_a_gate_failure_and_keeps_release_edits(
    tmp_path, monkeypatch, capsys
):
    version_file, changelog_file = _manual_release_files(tmp_path, monkeypatch)

    def fail_gates(command, *, cwd):
        return type("Completed", (), {"returncode": 7})()

    monkeypatch.setattr(release.subprocess, "run", fail_gates)

    assert release.main(["manual-release", "4.2.1", "--date", "2026-08-16"]) == 7
    assert version_file.read_text(encoding="utf-8") == '__version__ = "4.2.1"\n'
    assert changelog_file.read_text(encoding="utf-8") == _PROMOTED_CHANGELOG
    assert "preflight failed with exit code 7" in capsys.readouterr().err


def test_manual_release_rejects_an_empty_unreleased_section_without_side_effects(
    tmp_path, monkeypatch
):
    empty = "# Changelog\n\n## [Unreleased]\n\n## [v4.2.0] - 2026-08-15\n- previous\n"
    version_file, changelog_file = _manual_release_files(tmp_path, monkeypatch, empty)

    def unexpected(*args, **kwargs):
        raise AssertionError("gates reached")

    monkeypatch.setattr(release.subprocess, "run", unexpected)

    assert release.main(["manual-release", "4.2.1", "--date", "2026-08-16"]) == 1
    assert version_file.read_text(encoding="utf-8") == '__version__ = "4.2.0"\n'
    assert changelog_file.read_text(encoding="utf-8") == empty


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["manual-release", "4.2.1"], "the following arguments are required: --date"),
        (
            ["manual-release", "4.2.1", "--date", "August 16, 2026"],
            "date must be an ISO date (YYYY-MM-DD)",
        ),
    ],
)
def test_manual_release_requires_an_explicit_iso_release_date(
    argv, message, tmp_path, monkeypatch, capsys
):
    version_file, changelog_file = _manual_release_files(tmp_path, monkeypatch)
    original_changelog = changelog_file.read_text(encoding="utf-8")

    def unexpected(*args, **kwargs):
        raise AssertionError("gates reached")

    monkeypatch.setattr(release.subprocess, "run", unexpected)

    with pytest.raises(SystemExit) as exit_info:
        release.main(argv)
    assert exit_info.value.code != 0
    assert message in capsys.readouterr().err
    assert version_file.read_text(encoding="utf-8") == '__version__ = "4.2.0"\n'
    assert changelog_file.read_text(encoding="utf-8") == original_changelog


# -- release.promote (fold notes into a release already bumped) --------------
_LATE_NOTES = (
    "# Changelog\n\n"
    "## [Unreleased]\n\n"
    "### Fixed\n- arrived after the bump\n\n"
    "### Changed\n- a group that belongs mid-order\n\n"
    "### Security\n- a group the release lacks\n\n"
    "## [v4.2.0] - 2026-08-16\n\n"
    "### Added\n- a feature\n\n"
    "### Fixed\n- an earlier fix\n\n"
    "## [v4.1.0] - 2026-08-01\n\n"
    "### Added\n- older\n"
)


def test_fold_unreleased_appends_to_the_group_the_release_already_has():
    out = release.fold_unreleased(_LATE_NOTES)
    assert "### Fixed\n- an earlier fix\n- arrived after the bump\n" in out
    assert out.count("### Fixed") == 1


def test_fold_unreleased_inserts_a_missing_group_in_keep_a_changelog_order():
    out = release.fold_unreleased(_LATE_NOTES)
    assert out.index("### Added") < out.index("### Changed") < out.index("### Fixed")
    assert out.index("### Fixed") < out.index("### Security") < out.index("## [v4.1.0]")


def test_fold_unreleased_empties_the_pending_section_and_spares_older_releases():
    out = release.fold_unreleased(_LATE_NOTES)
    assert check.unreleased_body(out) == ""
    assert "## [v4.1.0] - 2026-08-01\n\n### Added\n- older\n" in out


def test_fold_unreleased_is_a_no_op_when_nothing_is_pending():
    promoted = release.fold_unreleased(_LATE_NOTES)
    assert release.fold_unreleased(promoted) == promoted


def test_promote_writes_the_folded_changelog(tmp_path, monkeypatch, capsys):
    _, changelog_file = _manual_release_files(tmp_path, monkeypatch, _LATE_NOTES)
    assert release.main(["promote"]) == 0
    assert check.unreleased_body(changelog_file.read_text(encoding="utf-8")) == ""
    assert "folded [Unreleased]" in capsys.readouterr().out


def test_promote_dry_run_reports_without_writing(tmp_path, monkeypatch, capsys):
    _, changelog_file = _manual_release_files(tmp_path, monkeypatch, _LATE_NOTES)
    assert release.main(["promote", "--dry-run"]) == 0
    assert changelog_file.read_text(encoding="utf-8") == _LATE_NOTES
    assert "dry run" in capsys.readouterr().out


def test_promote_refuses_a_heading_that_is_not_the_authored_version(tmp_path, monkeypatch, capsys):
    """Folding into someone else's version would mislabel what the artifact ships."""
    stale = _LATE_NOTES.replace("## [v4.2.0]", "## [v4.1.5]")
    _, changelog_file = _manual_release_files(tmp_path, monkeypatch, stale)
    assert release.main(["promote"]) == 1
    assert changelog_file.read_text(encoding="utf-8") == stale
    assert "bump <version>" in capsys.readouterr().err


def test_promote_reports_an_already_empty_pending_section(tmp_path, monkeypatch, capsys):
    folded = release.fold_unreleased(_LATE_NOTES)
    _, changelog_file = _manual_release_files(tmp_path, monkeypatch, folded)
    assert release.main(["promote"]) == 0
    assert changelog_file.read_text(encoding="utf-8") == folded
    assert "nothing to promote" in capsys.readouterr().out


def test_release_writes_lf_endings_on_every_platform(tmp_path, monkeypatch):
    """Git normalises on commit, but a CRLF rewrite buries the real edit in the diff."""
    _, changelog_file = _manual_release_files(tmp_path, monkeypatch, _LATE_NOTES)
    assert release.main(["promote"]) == 0
    assert b"\r\n" not in changelog_file.read_bytes()
