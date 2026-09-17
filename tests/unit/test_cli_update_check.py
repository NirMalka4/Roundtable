"""``roundtable update --check`` — the passive "is a newer version out?" probe.

Exercises the five branches of :func:`roundtable.cli._check_for_update` against a
fake ``updater`` module (the real one's I/O seams stay untouched): opt-out env,
editable install, a fail-soft discovery error, up-to-date, and behind.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable import cli
from roundtable.decision.verdict import EXIT_CLEAN, EXIT_ERROR
from roundtable.updater import UpdateError

SOURCE = "https://packages.example/simple/"


def _fake_updater(*, editable=False, available=None, raises=None):
    """A stand-in ``updater`` module exposing only what ``_check_for_update`` calls."""
    from roundtable import updater as real

    def list_remote_versions(source):
        if raises is not None:
            raise raises
        return available or []

    return SimpleNamespace(
        UpdateError=UpdateError,
        current_install_is_editable=lambda: editable,
        list_remote_versions=list_remote_versions,
        is_outdated=real.is_outdated,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ROUNDTABLE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(cli, "__version__", "1.0.0")


def test_check_opt_out_env_is_clean(monkeypatch, capsys):
    monkeypatch.setenv("ROUNDTABLE_NO_UPDATE_CHECK", "1")
    assert cli._check_for_update(_fake_updater(available=["2.0.0"]), SOURCE) == EXIT_CLEAN
    assert "disabled" in capsys.readouterr().out


def test_check_editable_install_is_clean(capsys):
    assert cli._check_for_update(_fake_updater(editable=True, available=["2.0.0"]), SOURCE) == (
        EXIT_CLEAN
    )
    assert "editable/source install" in capsys.readouterr().out


def test_check_discovery_error_is_fail_soft(capsys):
    fake = _fake_updater(raises=UpdateError("401 Unauthorized"))
    assert cli._check_for_update(fake, SOURCE) == EXIT_CLEAN
    assert "could not check for updates" in capsys.readouterr().err


def test_check_up_to_date_is_clean(capsys):
    assert cli._check_for_update(_fake_updater(available=["1.0.0", "0.9.0"]), SOURCE) == EXIT_CLEAN
    assert "up to date" in capsys.readouterr().out


def test_check_behind_reports_and_exits_nonzero(capsys):
    assert cli._check_for_update(_fake_updater(available=["1.2.0", "1.0.0"]), SOURCE) == EXIT_ERROR
    out = capsys.readouterr().out
    assert "newer version is available: 1.2.0" in out
    assert "you have 1.0.0" in out


# ── _cmd_update: Windows launcher detached-handoff wiring ───────────────────
def _update_args():
    return SimpleNamespace(
        source=None, check=False, list=False, dry_run=False, yes=True, version="latest"
    )


@pytest.fixture
def _stub_update_env(monkeypatch):
    """Point ``_cmd_update`` at a resolvable feed with a real plan, no real I/O."""
    from roundtable import updater

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(
            update=SimpleNamespace(source=SOURCE), sources={"update.source": "default"}
        ),
    )
    monkeypatch.setattr(updater, "current_install_is_editable", lambda: False)
    monkeypatch.setattr(updater, "list_remote_versions", lambda source: ["2.0.2", "2.0.1"])
    return updater


def test_update_requires_explicit_source(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(update=SimpleNamespace(source=None), sources={}),
    )

    assert cli._cmd_update(_update_args()) == EXIT_ERROR
    assert "no source is configured" in capsys.readouterr().err


def test_update_cli_source_overrides_persisted_source(monkeypatch, _stub_update_env, capsys):
    updater = _stub_update_env
    args = _update_args()
    args.source = "https://cli.example/simple/"
    args.list = True
    monkeypatch.setattr(updater, "list_remote_versions", lambda source: ["2.0.2"])

    assert cli._cmd_update(args) == EXIT_CLEAN
    assert "https://cli.example/simple/" in capsys.readouterr().err


def test_update_cli_source_rejects_local_path(monkeypatch, capsys):
    args = _update_args()
    args.source = "C:\\packages\\simple"

    assert cli._cmd_update(args) != EXIT_CLEAN
    assert "HTTP(S) PyPI simple-index URL" in capsys.readouterr().err


def test_update_windows_launcher_detaches_not_plain_pip(monkeypatch, capsys, _stub_update_env):
    updater = _stub_update_env
    launched = {}
    monkeypatch.setattr(updater, "launcher_pid", lambda: 4321)
    monkeypatch.setattr(
        updater,
        "start_detached_update",
        lambda plan, pid: (
            launched.update(cmd=plan.command, pid=pid)
            or updater.DetachedUpdate(
                log_path=Path(r"C:\state\update.log"), status_path=Path(r"C:\state\status.json")
            )
        ),
    )
    monkeypatch.setattr(
        updater,
        "run_pip",
        lambda cmd: pytest.fail("run_pip must not run on the launcher path"),
    )

    assert cli._cmd_update(_update_args()) == EXIT_CLEAN
    assert launched["cmd"][-1] == "roundtable==2.0.2"
    assert launched["pid"] == 4321
    out = capsys.readouterr().out
    assert "in the background" in out
    assert "status.json" in out


def test_update_non_launcher_uses_plain_pip(monkeypatch, _stub_update_env):
    updater = _stub_update_env
    ran = {}
    monkeypatch.setattr(updater, "launcher_pid", lambda: None)
    monkeypatch.setattr(
        updater,
        "start_detached_update",
        lambda plan, pid: pytest.fail("must not detach off the launcher path"),
    )
    monkeypatch.setattr(updater, "run_pip", lambda cmd: (ran.setdefault("cmd", cmd), 0)[1])

    assert cli._cmd_update(_update_args()) == EXIT_CLEAN
    assert ran["cmd"][-1] == "roundtable==2.0.2"


def test_update_launcher_spawn_failure_reports_manual_fallback(
    monkeypatch, capsys, _stub_update_env
):
    updater = _stub_update_env
    monkeypatch.setattr(updater, "launcher_pid", lambda: 4321)

    def boom(plan, pid):
        raise OSError("cannot spawn")

    monkeypatch.setattr(updater, "start_detached_update", boom)

    assert cli._cmd_update(_update_args()) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "could not launch the background updater" in err
    assert "roundtable==2.0.2" in err  # manual command shown
