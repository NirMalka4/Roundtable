from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap

SOURCE = "https://packages.example/simple/"


def _project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_user_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_user_update_path",
        lambda: tmp_path / "user-settings" / "update.yaml",
    )


def _tool_bin(tmp_path: Path, platform: str) -> Path:
    tool_bin = tmp_path / "uv tools"
    tool_bin.mkdir()
    suffix = ".exe" if platform == "win32" else ""
    for name in ("roundtable", "rt"):
        (tool_bin / f"{name}{suffix}").write_text("", encoding="utf-8")
    return tool_bin


def _recording_run(monkeypatch, tool_bin: Path, *, uv_available: bool = True):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        command = list(command)
        calls.append((command, kwargs))
        if command[-2:] == ["uv", "--version"]:
            return SimpleNamespace(returncode=0 if uv_available else 1, stdout="")
        if command[-3:] == ["tool", "dir", "--bin"]:
            return SimpleNamespace(returncode=0, stdout=f"{tool_bin}\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("platform", "suffix"),
    (("win32", ".exe"), ("linux", "")),
)
def test_default_installs_checkout_as_uv_tool_and_verifies_both_scripts(
    tmp_path: Path,
    monkeypatch,
    platform: str,
    suffix: str,
) -> None:
    root = tmp_path / "repo with spaces"
    root.mkdir()
    root = _project(root)
    monkeypatch.setattr(sys, "platform", platform)
    tool_bin = _tool_bin(tmp_path, platform)
    calls = _recording_run(monkeypatch, tool_bin)

    bootstrap.install(bootstrap.resolve_bootstrap(root))

    commands = [call[0] for call in calls]
    assert [
        sys.executable,
        "-m",
        "uv",
        "tool",
        "install",
        "--force",
        "--python",
        sys.executable,
        str(root.resolve()),
    ] in commands
    assert [sys.executable, "-m", "uv", "tool", "update-shell"] in commands
    assert [sys.executable, "-m", "uv", "tool", "dir", "--bin"] in commands
    assert [str(tool_bin / f"roundtable{suffix}"), "--version"] in commands
    assert [str(tool_bin / f"rt{suffix}"), "--version"] in commands
    assert all(
        "--index-url" not in command and "--default-index" not in command for command in commands
    )


def test_pip_default_index_is_propagated_to_uv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap.os, "environ", {"PIP_INDEX_URL": "https://packages.example/simple"}
    )

    env = bootstrap._uv_environment(tmp_path)

    assert env["UV_DEFAULT_INDEX"] == "https://packages.example/simple"


def test_propagated_index_reaches_uv_tool_install(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)
    monkeypatch.setattr(
        bootstrap.os,
        "environ",
        {"PIP_INDEX_URL": "https://packages.example/simple"},
    )
    calls = _recording_run(monkeypatch, tool_bin)

    bootstrap.install(bootstrap.resolve_bootstrap(root))

    _, kwargs = next(call for call in calls if call[0][3:5] == ["tool", "install"])
    assert kwargs["env"]["UV_DEFAULT_INDEX"] == "https://packages.example/simple"


@pytest.mark.parametrize("override", bootstrap._UV_INDEX_OVERRIDES)
def test_uv_index_override_takes_precedence(
    tmp_path: Path,
    monkeypatch,
    override: str,
) -> None:
    monkeypatch.setattr(
        bootstrap.os,
        "environ",
        {
            "PIP_INDEX_URL": "https://pip.example/simple",
            override: "https://uv.example/simple",
        },
    )

    env = bootstrap._uv_environment(tmp_path)

    assert env[override] == "https://uv.example/simple"
    assert env.get("UV_DEFAULT_INDEX") != "https://pip.example/simple"


def test_pip_config_default_index_is_propagated_cross_platform(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(bootstrap.os, "environ", {})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="https://configured.example/simple\n",
        ),
    )

    env = bootstrap._uv_environment(tmp_path)

    assert env["UV_DEFAULT_INDEX"] == "https://configured.example/simple"


def test_no_pip_index_leaves_uv_index_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap.os, "environ", {})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    env = bootstrap._uv_environment(tmp_path)

    assert all(name not in env for name in bootstrap._UV_INDEX_OVERRIDES)


def test_missing_uv_is_installed_in_current_python_user_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)
    calls = _recording_run(monkeypatch, tool_bin, uv_available=False)

    bootstrap.install(bootstrap.resolve_bootstrap(root))

    commands = [call[0] for call in calls]
    assert [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--user",
        "--upgrade",
        "uv",
    ] in commands
    assert commands.count([sys.executable, "-m", "uv", "--version"]) == 2


def test_default_rerun_always_replaces_existing_tool(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)
    calls = _recording_run(monkeypatch, tool_bin)

    bootstrap.install(bootstrap.resolve_bootstrap(root))
    bootstrap.install(bootstrap.resolve_bootstrap(root))

    installs = [command for command, _ in calls if command[3:5] == ["tool", "install"]]
    assert len(installs) == 2
    assert all("--force" in command for command in installs)


def test_no_update_shell_skips_only_shell_modification(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)
    calls = _recording_run(monkeypatch, tool_bin)

    bootstrap.install(bootstrap.resolve_bootstrap(root), update_shell=False)

    commands = [call[0] for call in calls]
    assert [sys.executable, "-m", "uv", "tool", "update-shell"] not in commands
    assert [
        str(tool_bin / ("roundtable.exe" if sys.platform == "win32" else "roundtable")),
        "--version",
    ] in commands


def test_missing_installed_console_script_is_an_error(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    tool_bin = tmp_path / "empty tool bin"
    tool_bin.mkdir()
    _recording_run(monkeypatch, tool_bin)

    with pytest.raises(RuntimeError, match="did not create"):
        bootstrap.install(bootstrap.resolve_bootstrap(root))


def test_empty_or_relative_uv_tool_bin_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)

    def run(command, **_kwargs):
        if list(command)[-3:] == ["tool", "dir", "--bin"]:
            return SimpleNamespace(returncode=0, stdout="relative-bin\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(RuntimeError, match="non-absolute"):
        bootstrap.install(bootstrap.resolve_bootstrap(root))


def test_subprocess_failure_is_not_suppressed(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)

    def run(command, **kwargs):
        if list(command)[-2:] == ["uv", "--version"]:
            return SimpleNamespace(returncode=0)
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(9, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.install(bootstrap.resolve_bootstrap(root))


def test_default_dry_run_prints_cross_platform_uv_flow_without_changes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "repo with spaces"
    root.mkdir()
    root = _project(root)
    monkeypatch.setattr(bootstrap, "_uv_available", lambda _root, _env: False)

    bootstrap.install(bootstrap.resolve_bootstrap(root), dry_run=True)

    output = capsys.readouterr().out
    assert "pip install --user --upgrade uv" in output
    assert "uv tool install --force" in output
    assert "uv tool update-shell" in output
    assert "uv tool dir --bin" in output
    assert "roundtable" in output and "rt" in output
    assert not (root / ".venv").exists()


def test_default_install_prints_minimal_stages(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)
    _recording_run(monkeypatch, tool_bin)

    bootstrap.install(bootstrap.resolve_bootstrap(root))

    output = capsys.readouterr().out
    assert "==> Checking uv" in output
    assert "==> Installing Roundtable from this checkout" in output
    assert "==> Updating shell PATH" in output
    assert "==> Verifying roundtable and rt" in output


def test_ado_updates_adds_updater_dependencies_and_verifies_feed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)
    calls = _recording_run(monkeypatch, tool_bin)

    bootstrap.install(
        bootstrap.resolve_bootstrap(root),
        ado_updates=True,
        update_source=SOURCE,
    )

    commands = [call[0] for call in calls]
    install_command = next(command for command in commands if command[3:5] == ["tool", "install"])
    for requirement in ("pip", "keyring", "artifacts-keyring"):
        position = install_command.index(requirement)
        assert install_command[position - 1] == "--with"
    rt = tool_bin / ("rt.exe" if sys.platform == "win32" else "rt")
    assert [str(rt), "update", "--list", "--source", SOURCE] in commands
    output = capsys.readouterr().out
    assert "==> Adding ADO update support" in output
    assert "==> Verifying Azure Artifacts update access" in output


def test_ado_updates_dry_run_prints_feed_verification(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(bootstrap, "_uv_available", lambda _root, _env: True)

    bootstrap.install(
        bootstrap.resolve_bootstrap(root),
        ado_updates=True,
        update_source=SOURCE,
        dry_run=True,
    )

    output = capsys.readouterr().out
    assert "--with pip --with keyring --with artifacts-keyring" in output
    assert "update --list --source https://packages.example/simple/" in output


def test_ado_updates_reports_feed_verification_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    tool_bin = _tool_bin(tmp_path, sys.platform)

    def run(command, **kwargs):
        command = list(command)
        if command[-2:] == ["uv", "--version"]:
            return SimpleNamespace(returncode=0, stdout="")
        if command[-3:] == ["tool", "dir", "--bin"]:
            return SimpleNamespace(returncode=0, stdout=f"{tool_bin}\n")
        if "update" in command and "--list" in command:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(RuntimeError, match="Azure Artifacts update access"):
        bootstrap.install(
            bootstrap.resolve_bootstrap(root),
            ado_updates=True,
            update_source=SOURCE,
        )


def test_ado_updates_cannot_be_combined_with_contributor(tmp_path: Path) -> None:
    root = _project(tmp_path)

    with pytest.raises(RuntimeError, match="cannot be combined"):
        bootstrap.install(
            bootstrap.resolve_bootstrap(root),
            contributor=True,
            ado_updates=True,
            update_source=SOURCE,
        )


def test_ado_updates_requires_source_before_any_install_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(bootstrap.os, "environ", {})
    monkeypatch.setattr(bootstrap, "_user_update_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must fail before contacting package services"),
    )

    with pytest.raises(RuntimeError, match="requires an update source"):
        bootstrap.install(bootstrap.resolve_bootstrap(root), ado_updates=True)


def test_ado_updates_persists_source_for_repeated_installs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path / "repo")
    user_path = tmp_path / "config" / "update.yaml"
    tool_bin = _tool_bin(tmp_path, sys.platform)
    calls = _recording_run(monkeypatch, tool_bin)
    monkeypatch.setattr(bootstrap, "_user_update_path", lambda: user_path)

    bootstrap.install(
        bootstrap.resolve_bootstrap(root),
        ado_updates=True,
        update_source=SOURCE,
    )
    bootstrap.install(bootstrap.resolve_bootstrap(root), ado_updates=True)

    assert bootstrap._read_owned_update_source(user_path) == SOURCE
    verifications = [command for command, _ in calls if command[-4:-2] == ["update", "--list"]]
    assert len(verifications) == 2
    assert all(command[-1] == SOURCE for command in verifications)


def test_ado_updates_dry_run_shows_write_without_mutating(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = _project(tmp_path / "repo")
    user_path = tmp_path / "config" / "update.yaml"
    monkeypatch.setattr(bootstrap, "_user_update_path", lambda: user_path)
    monkeypatch.setattr(bootstrap, "_uv_available", lambda _root, _env: True)

    bootstrap.install(
        bootstrap.resolve_bootstrap(root),
        ado_updates=True,
        update_source=SOURCE,
        dry_run=True,
    )

    output = capsys.readouterr().out
    assert f"Would atomically write {user_path}" in output
    assert SOURCE in output
    assert not user_path.exists()


@pytest.mark.parametrize(
    "source",
    ("", "C:\\feed\\simple", "file:///feed/simple/", "https://user:pat@example/simple/"),
)
def test_ado_updates_rejects_invalid_source(tmp_path: Path, source: str) -> None:
    root = _project(tmp_path)

    with pytest.raises(RuntimeError):
        bootstrap.install(
            bootstrap.resolve_bootstrap(root),
            ado_updates=True,
            update_source=source,
        )


def test_persisting_update_source_does_not_modify_workspace_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path / "repo")
    workspace = root / "roundtable.yaml"
    workspace.write_text("concurrency: 4\n", encoding="utf-8")
    before = workspace.read_bytes()
    user_path = tmp_path / "config" / "update.yaml"
    monkeypatch.setattr(bootstrap, "_user_update_path", lambda: user_path)

    bootstrap._resolve_ado_update_source(root, SOURCE, dry_run=False)

    assert workspace.read_bytes() == before
    assert bootstrap._read_owned_update_source(user_path) == SOURCE


def test_contributor_creates_local_environment_with_all_extras(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    resolved = bootstrap.resolve_bootstrap(root)
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        command = list(command)
        calls.append(command)
        if command[1:3] == ["-m", "venv"]:
            resolved.python.parent.mkdir(parents=True)
            resolved.python.write_text("", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)

    bootstrap.install(resolved, contributor=True)

    assert [sys.executable, "-m", "venv", str(resolved.environment)] in calls
    assert [*resolved.pip, "install", f"{root.resolve()}[dev,lint,types]"] in calls
    assert (resolved.environment / bootstrap._MARKER).is_file()


def test_contributor_reuses_environment_and_force_requires_ownership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _project(tmp_path)
    resolved = bootstrap.resolve_bootstrap(root)
    resolved.python.parent.mkdir(parents=True)
    resolved.python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: calls.append(list(command)) or SimpleNamespace(returncode=0),
    )

    bootstrap.install(resolved, contributor=True)
    assert all(command[1:3] != ["-m", "venv"] for command in calls)

    with pytest.raises(RuntimeError, match="unowned"):
        bootstrap.install(resolved, contributor=True, force=True)


def test_contributor_force_recreates_owned_environment_dry_run(
    tmp_path: Path,
    capsys,
) -> None:
    root = _project(tmp_path)
    resolved = bootstrap.resolve_bootstrap(root)
    resolved.environment.mkdir()
    (resolved.environment / bootstrap._MARKER).write_text("owned\n", encoding="utf-8")

    bootstrap.install(resolved, contributor=True, force=True, dry_run=True)

    output = capsys.readouterr().out
    assert "-m venv" in output
    assert "dev,lint,types" in output
    assert resolved.environment.exists()


def test_validate_python_rejects_unsupported_interpreter(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "version_info", (3, 11, 9))

    with pytest.raises(RuntimeError, match=r"requires Python 3\.12"):
        bootstrap.validate_python(root)
