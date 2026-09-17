"""Self-update resolution logic (``roundtable.updater``) — feed model.

Contract under test (all pure / offline; the two I/O seams are monkeypatched):

* ``parse_version`` accepts only bare ``X.Y.Z``; ``normalize_version`` also accepts
  a leading ``v`` and rejects everything else.
* ``parse_available_versions`` extracts the ``Available versions:`` line from
  ``pip index versions`` output and filters to valid ``X.Y.Z``.
* ``sort_versions_desc`` orders by SemVer (numeric, not lexicographic).
* ``is_outdated`` reports the newest version only when strictly newer than current,
  and never flags a ``+dev`` build.
* ``select_version`` picks the highest for ``latest``/``None`` and requires an
  explicit version to be published, with actionable errors otherwise.
* ``build_pip_command`` emits a single ``--index-url`` pinned-version upgrade argv.
* ``resolve_plan`` composes the above into a runnable plan without side effects.
* ``list_remote_versions`` parses ``pip index`` output, treats "not found" as empty,
  and raises on a genuine failure.
"""

from __future__ import annotations

import json

import pytest

from roundtable import updater
from roundtable.updater import (
    UpdateError,
    build_helper_command,
    build_pip_command,
    find_launcher_pid,
    is_editable,
    is_outdated,
    normalize_version,
    parse_available_versions,
    parse_version,
    resolve_plan,
    select_version,
    sort_versions_desc,
)

SOURCE = "https://packages.example/simple/"


# ── parse_version ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", (0, 1, 0)), ("1.2.3", (1, 2, 3)), ("0.10.0", (0, 10, 0)), (" 2.0.0 ", (2, 0, 0))],
)
def test_parse_version_accepts_bare_semver(version, expected):
    assert parse_version(version) == expected


@pytest.mark.parametrize("bad", ["v1.2.3", "1.2", "1.2.3.4", "release-1", "X.Y.Z", "", "1.2.3a"])
def test_parse_version_rejects_non_bare_semver(bad):
    assert parse_version(bad) is None


# ── normalize_version ───────────────────────────────────────────────────────
@pytest.mark.parametrize(("given", "canonical"), [("v0.1.0", "0.1.0"), ("0.1.0", "0.1.0")])
def test_normalize_version_canonicalises_to_bare(given, canonical):
    assert normalize_version(given) == canonical


@pytest.mark.parametrize("bad", ["latest", "v1", "1.2", "abc", ""])
def test_normalize_version_rejects_garbage(bad):
    with pytest.raises(UpdateError, match="invalid version"):
        normalize_version(bad)


# ── parse_available_versions ────────────────────────────────────────────────
def test_parse_available_versions_extracts_and_filters():
    output = (
        "roundtable (1.2.0)\n"
        "Available versions: 1.2.0, 1.1.0, 1.0.0, not-a-version\n"
        "  INSTALLED: 1.0.0\n"
        "  LATEST:    1.2.0\n"
    )
    assert parse_available_versions(output) == ["1.2.0", "1.1.0", "1.0.0"]


@pytest.mark.parametrize("output", ["", "no available-versions line here", "Available versions:"])
def test_parse_available_versions_empty_when_absent_or_blank(output):
    assert parse_available_versions(output) == []


# ── sort_versions_desc ──────────────────────────────────────────────────────
def test_sort_is_semver_not_lexicographic_and_dedupes_and_filters():
    versions = ["0.2.0", "0.10.0", "0.2.1", "not-a-version", "0.2.0"]
    assert sort_versions_desc(versions) == ["0.10.0", "0.2.1", "0.2.0"]


def test_sort_empty_is_empty():
    assert sort_versions_desc([]) == []


# ── is_outdated ─────────────────────────────────────────────────────────────
def test_is_outdated_returns_newer_when_behind():
    assert is_outdated("1.0.0", ["1.0.0", "1.2.0", "1.1.0"]) == "1.2.0"


@pytest.mark.parametrize(
    ("current", "available"),
    [
        ("1.2.0", ["1.0.0", "1.2.0"]),  # up to date
        ("2.0.0", ["1.9.9"]),  # ahead (local dev ahead of feed)
        ("1.0.0", []),  # nothing published
        ("1.0.0+dev", ["1.2.0"]),  # dev build is never "outdated"
        ("bogus", ["1.2.0"]),  # unparseable current
    ],
)
def test_is_outdated_returns_none_when_not_strictly_behind(current, available):
    assert is_outdated(current, available) is None


# ── select_version ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("requested", [None, "latest", "LATEST", " latest "])
def test_select_latest_picks_highest(requested):
    assert select_version(requested, ["0.1.0", "0.3.0", "0.2.0"]) == "0.3.0"


def test_select_latest_with_nothing_published_errors_with_guidance():
    with pytest.raises(UpdateError, match="no released versions found on the feed"):
        select_version("latest", [])


def test_select_explicit_present_returns_bare():
    assert select_version("v0.2.0", ["0.1.0", "0.2.0"]) == "0.2.0"


def test_select_explicit_absent_lists_available():
    with pytest.raises(UpdateError, match=r"not published to the feed.*0\.1\.0"):
        select_version("9.9.9", ["0.1.0"])


def test_select_explicit_invalid_format_is_rejected():
    with pytest.raises(UpdateError, match="invalid version"):
        select_version("garbage", ["0.1.0"])


# ── build_pip_command ───────────────────────────────────────────────────────
def test_build_pip_command_uses_single_index_and_pins_version():
    cmd = build_pip_command(SOURCE, "0.1.0", python="/usr/bin/python3")
    assert cmd == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--index-url",
        SOURCE,
        "roundtable==0.1.0",
    ]


# ── is_editable ─────────────────────────────────────────────────────────────
def test_is_editable_true_marker():
    assert is_editable(json.dumps({"url": "file:///x", "dir_info": {"editable": True}})) is True


def test_is_editable_false_when_not_marked():
    assert is_editable(json.dumps({"url": "file:///x", "dir_info": {}})) is False


@pytest.mark.parametrize("text", [None, "", "not json", "[1,2,3]"])
def test_is_editable_handles_missing_or_bad(text):
    assert is_editable(text) is False


# ── is_under_any (site-packages containment) ────────────────────────────────
def test_is_under_any_true_when_nested(tmp_path):
    from roundtable.updater import is_under_any

    pkg = tmp_path / "site-packages" / "roundtable"
    pkg.mkdir(parents=True)
    assert is_under_any(pkg, [str(tmp_path / "site-packages")]) is True


def test_is_under_any_false_when_outside(tmp_path):
    from roundtable.updater import is_under_any

    src = tmp_path / "checkout" / "roundtable"
    src.mkdir(parents=True)
    assert is_under_any(src, [str(tmp_path / "site-packages")]) is False


def test_is_under_any_empty_roots_is_false(tmp_path):
    from roundtable.updater import is_under_any

    assert is_under_any(tmp_path, []) is False


# ── resolve_plan (composition, no I/O) ──────────────────────────────────────
def test_resolve_plan_latest():
    plan = resolve_plan("latest", SOURCE, ["0.1.0", "0.2.0"], python="py")
    assert plan.version == "0.2.0"
    assert plan.is_latest is True
    assert plan.source == SOURCE
    assert plan.command[-1] == "roundtable==0.2.0"
    assert "--index-url" in plan.command


def test_resolve_plan_explicit_is_not_latest():
    plan = resolve_plan("0.1.0", SOURCE, ["0.1.0", "0.2.0"], python="py")
    assert plan.version == "0.1.0"
    assert plan.is_latest is False


def test_resolve_plan_unknown_version_raises():
    with pytest.raises(UpdateError):
        resolve_plan("5.0.0", SOURCE, ["0.1.0"])


# ── seams: list_remote_versions parsing (pip output faked) ──────────────────
def _fake_proc(returncode: int, stdout: str = "", stderr: str = ""):
    class _Proc:
        pass

    proc = _Proc()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_list_remote_versions_parses_pip_index(monkeypatch):
    out = "roundtable (0.2.0)\nAvailable versions: 0.2.0, 0.1.0\n"
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _fake_proc(0, stdout=out))
    assert updater.list_remote_versions(SOURCE) == ["0.2.0", "0.1.0"]


def test_list_remote_versions_not_found_is_empty_not_error(monkeypatch):
    err = "ERROR: No matching distribution found for roundtable"
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _fake_proc(1, stderr=err))
    assert updater.list_remote_versions(SOURCE) == []


def test_list_remote_versions_real_failure_raises(monkeypatch):
    err = "ERROR: 401 Client Error: Unauthorized"
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _fake_proc(1, stderr=err))
    with pytest.raises(UpdateError, match="pip index versions failed"):
        updater.list_remote_versions(SOURCE)


# ── find_launcher_pid (pure ancestor walk) ──────────────────────────────────
def test_find_launcher_pid_finds_grandparent_launcher():
    # CLI(100) -> python redirector(90) -> roundtable.exe launcher(80)
    processes = {
        100: (90, "python.exe"),
        90: (80, "python.exe"),
        80: (10, "roundtable.exe"),
        10: (0, "pwsh.exe"),
    }
    assert find_launcher_pid(processes, 100) == 80


def test_find_launcher_pid_none_when_no_launcher_ancestor():
    # `python -m roundtable` — no console-script launcher in the chain
    processes = {100: (90, "python.exe"), 90: (10, "pwsh.exe"), 10: (0, "explorer.exe")}
    assert find_launcher_pid(processes, 100) is None


def test_find_launcher_pid_is_cycle_and_depth_guarded():
    processes = {1: (2, "python.exe"), 2: (1, "python.exe")}  # parent cycle
    assert find_launcher_pid(processes, 1) is None


def test_find_launcher_pid_matches_stem_case_insensitively():
    processes = {5: (4, "python.exe"), 4: (0, "ROUNDTABLE.EXE")}
    assert find_launcher_pid(processes, 5) == 4


# ── build_helper_command (pure argv) ────────────────────────────────────────
def test_build_helper_command_runs_helper_by_path_with_pip_after_guard():
    got = build_helper_command(
        r"C:\state\update_helper.py",
        1234,
        r"C:\state\update.log",
        r"C:\state\status.json",
        "2.0.2",
        ["py", "-m", "pip", "install", "--upgrade", "roundtable==2.0.2"],
        python=r"C:\venv\python.exe",
    )
    assert got[0] == r"C:\venv\python.exe"
    assert got[1] == r"C:\state\update_helper.py"  # by path, never `-m`
    assert "--parent-pid" in got and got[got.index("--parent-pid") + 1] == "1234"
    assert got[got.index("--version") + 1] == "2.0.2"
    # everything after `--` is the verbatim pip argv (its flags never parsed as ours)
    sep = got.index("--")
    assert got[sep + 1 :] == ["py", "-m", "pip", "install", "--upgrade", "roundtable==2.0.2"]


# ── launcher_pid (seam) ─────────────────────────────────────────────────────
def test_launcher_pid_none_off_windows(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert updater.launcher_pid() is None


def test_launcher_pid_delegates_to_snapshot_walk(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_snapshot_processes", lambda: {7: (0, "roundtable.exe")})
    monkeypatch.setattr(updater.os, "getpid", lambda: 7)
    assert updater.launcher_pid() == 7


def test_launcher_pid_none_on_snapshot_failure(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")

    def boom():
        raise RuntimeError("snapshot broke")

    monkeypatch.setattr(updater, "_snapshot_processes", boom)
    assert updater.launcher_pid() is None


# ── start_detached_update (orchestration seam) ──────────────────────────────
def test_start_detached_update_deploys_helper_and_spawns_detached(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_update_state_dir", lambda: tmp_path)
    spawned = {}
    monkeypatch.setattr(updater, "spawn_detached", lambda cmd: spawned.setdefault("cmd", cmd))

    plan = resolve_plan("2.0.2", SOURCE, ["2.0.2"])
    handoff = updater.start_detached_update(plan, 4321)

    # a real, out-of-tree helper copy exists and is the stdlib-only script
    helper = handoff.log_path.parent / "update_helper.py"
    assert helper.exists()
    # stdlib-only: it must never import the package it is replacing (it runs
    # by-path with a fresh interpreter, not as ``-m roundtable._update_helper``).
    helper_src = helper.read_text(encoding="utf-8")
    assert "import roundtable" not in helper_src
    assert "from roundtable" not in helper_src
    # it was spawned by path, waiting on the launcher pid, carrying the pip argv
    cmd = spawned["cmd"]
    assert cmd[1] == str(helper)
    assert cmd[cmd.index("--parent-pid") + 1] == "4321"
    assert cmd[cmd.index("--") + 1 :] == plan.command
    assert handoff.status_path.name == "status.json"


def test_start_detached_update_propagates_spawn_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_update_state_dir", lambda: tmp_path)

    def boom(cmd):
        raise OSError("cannot spawn")

    monkeypatch.setattr(updater, "spawn_detached", boom)
    plan = resolve_plan("2.0.2", SOURCE, ["2.0.2"])
    with pytest.raises(OSError):
        updater.start_detached_update(plan, 1)
