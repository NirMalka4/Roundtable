"""The detached, stdlib-only updater helper (``roundtable._update_helper``).

The helper never imports the package it may be replacing; it waits for the launcher
process to exit, guards against concurrent updates with a per-venv mutex, runs pip
non-interactively, and records a final status file. These tests exercise the pure /
seam-injected logic offline (the pid-wait and mutex are patched).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from roundtable import _update_helper as helper


def test_write_status_is_atomic_and_records_result(tmp_path):
    status = tmp_path / "status.json"
    helper.write_status(str(status), "installed", 0, "2.0.2")

    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["state"] == "installed"
    assert data["returncode"] == 0
    assert data["version"] == "2.0.2"
    assert data["time"].endswith("Z")
    assert not (tmp_path / "status.json.tmp").exists()  # temp swapped, not left behind


def test_main_waits_then_runs_pip_and_writes_installed(monkeypatch, tmp_path):
    log = tmp_path / "update.log"
    status = tmp_path / "status.json"
    waited = {}
    monkeypatch.setattr(helper, "wait_for_pid_exit", lambda pid, **_: waited.setdefault("pid", pid))
    monkeypatch.setattr(helper, "acquire_update_lock", lambda: object())

    ran = {}

    def fake_run(cmd, **kwargs):
        ran["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    code = helper.main(
        [
            "--parent-pid",
            "999",
            "--log",
            str(log),
            "--status",
            str(status),
            "--version",
            "2.0.2",
            "--",
            "py",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "roundtable==2.0.2",
        ]
    )

    assert code == 0
    assert waited["pid"] == 999
    assert ran["cmd"][:4] == ["py", "-m", "pip", "install"]
    assert ran["cmd"][-1] == "roundtable==2.0.2"
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "installed"


def test_main_records_failed_on_nonzero_pip(monkeypatch, tmp_path):
    status = tmp_path / "status.json"
    monkeypatch.setattr(helper, "wait_for_pid_exit", lambda pid, **_: None)
    monkeypatch.setattr(helper, "acquire_update_lock", lambda: object())
    monkeypatch.setattr(
        helper.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 7)
    )

    code = helper.main(
        [
            "--parent-pid",
            "1",
            "--log",
            str(tmp_path / "l"),
            "--status",
            str(status),
            "--version",
            "2.0.2",
            "--",
            "pip",
            "install",
        ]
    )

    assert code == 7
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "failed"


def test_main_skips_when_lock_held(monkeypatch, tmp_path):
    status = tmp_path / "status.json"
    monkeypatch.setattr(helper, "wait_for_pid_exit", lambda pid, **_: None)
    monkeypatch.setattr(helper, "acquire_update_lock", lambda: None)  # already held
    monkeypatch.setattr(
        helper.subprocess, "run", lambda *a, **k: pytest.fail("pip must not run when lock is held")
    )

    code = helper.main(
        [
            "--parent-pid",
            "1",
            "--log",
            str(tmp_path / "l"),
            "--status",
            str(status),
            "--",
            "pip",
            "install",
        ]
    )

    assert code == 0
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "skipped"
