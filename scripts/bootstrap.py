"""Install Roundtable from this checkout, or prepare a contributor environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_MARKER = ".roundtable-bootstrap"
_MINIMUM = (3, 12)
_ADO_UPDATE_REQUIREMENTS = ("pip", "keyring", "artifacts-keyring")
_UV_INDEX_OVERRIDES = ("UV_DEFAULT_INDEX", "UV_INDEX", "UV_INDEX_URL")
_UPDATE_FILENAME = "update.yaml"


@dataclass(frozen=True)
class Bootstrap:
    root: Path
    environment: Path
    python: Path
    pip: tuple[str, ...]


def repository_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError(f"repository root has no pyproject.toml: {root}")
    return root


def resolve_bootstrap(root: Path) -> Bootstrap:
    resolved_root = root.resolve()
    environment = resolved_root / ".venv"
    python = (
        environment / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else environment / "bin" / "python"
    )
    return Bootstrap(
        root=resolved_root,
        environment=environment,
        python=python,
        pip=(str(python), "-m", "pip"),
    )


def validate_python(root: Path) -> None:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = metadata["project"]["requires-python"]
    if requirement != ">=3.12":
        raise RuntimeError(f"unsupported project Python requirement: {requirement!r}")
    if sys.version_info < _MINIMUM:
        found = ".".join(str(part) for part in sys.version_info[:3])
        raise RuntimeError(f"Roundtable requires Python 3.12+; found {found}")


def _user_update_path(
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    values = os.environ if env is None else env
    user_home = Path.home() if home is None else home
    current_platform = sys.platform if platform is None else platform
    if current_platform == "win32":
        base = Path(values.get("APPDATA") or user_home / "AppData" / "Roaming")
        return base / "Roundtable" / _UPDATE_FILENAME
    if current_platform == "darwin":
        return user_home / "Library" / "Application Support" / "Roundtable" / _UPDATE_FILENAME
    base = Path(values.get("XDG_CONFIG_HOME") or user_home / ".config")
    return base / "roundtable" / _UPDATE_FILENAME


def _validate_update_source(source: str, *, source_name: str) -> str:
    value = source.strip()
    parsed = urlsplit(value)
    if not value:
        raise RuntimeError(f"{source_name} must not be blank")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{source_name} must be an HTTP(S) PyPI simple-index URL")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f"{source_name} must not contain credentials")
    if parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/simple"):
        raise RuntimeError(
            f"{source_name} must end in /simple or /simple/ with no query or fragment"
        )
    return value


def _read_owned_update_source(path: Path) -> str | None:
    if not path.is_file():
        return None
    match = re.fullmatch(
        r"\s*# Roundtable updater settings; this file contains no credentials\.\s*"
        r"source:\s*(?P<source>\"(?:[^\"\\]|\\.)*\")\s*",
        path.read_text(encoding="utf-8"),
    )
    if match is None:
        raise RuntimeError(f"Roundtable user update settings are malformed: {path}")
    return _validate_update_source(json.loads(match.group("source")), source_name=str(path))


def _workspace_update_source(root: Path) -> str | None:
    path = root / "roundtable.yaml"
    if not path.is_file():
        return None
    in_update = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_update = line.split("#", 1)[0].strip() == "update:"
            continue
        if in_update and indent > 0:
            match = re.match(r"\s*source:\s*(?P<source>[^#]+)", line)
            if match:
                raw = match.group("source").strip().strip("'\"")
                return _validate_update_source(raw, source_name=str(path))
    return None


def _write_user_update_source(path: Path, source: str, *, dry_run: bool) -> None:
    rendered = (
        "# Roundtable updater settings; this file contains no credentials.\n"
        f"source: {json.dumps(source)}\n"
    )
    _stage("Configuring future updates")
    if dry_run:
        print(f"Would atomically write {path}:\n{rendered}", end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)
    print(f"Saved update source in {path}")


def _resolve_ado_update_source(
    root: Path,
    supplied: str | None,
    *,
    dry_run: bool,
) -> str:
    if supplied is not None:
        source = _validate_update_source(supplied, source_name="--update-source")
        _write_user_update_source(_user_update_path(), source, dry_run=dry_run)
        return source
    source = (
        os.environ.get("ROUNDTABLE_UPDATE_SOURCE")
        or _workspace_update_source(root)
        or _read_owned_update_source(_user_update_path())
    )
    if source:
        return _validate_update_source(source, source_name="resolved update source")
    raise RuntimeError(
        "--ado-updates requires an update source. Pass --update-source "
        "https://<host>/<feed>/pypi/simple/; no feed is guessed or contacted."
    )


def _print(command: list[str]) -> None:
    print(subprocess.list2cmdline(command))


def _stage(message: str) -> None:
    print(f"==> {message}")


def _run(command: list[str], root: Path, *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=root, check=True, env=env)


def _run_or_print(
    command: list[str],
    root: Path,
    dry_run: bool,
    *,
    env: dict[str, str] | None = None,
) -> None:
    if dry_run:
        _print(command)
    else:
        _run(command, root, env=env)


def _uv_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "uv", *args]


def _configured_pip_index(root: Path, env: dict[str, str]) -> str | None:
    if index := env.get("PIP_INDEX_URL"):
        return index
    result = subprocess.run(
        [sys.executable, "-m", "pip", "config", "get", "global.index-url"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        return None
    return getattr(result, "stdout", "").strip() or None


def _uv_environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    if any(env.get(name) for name in _UV_INDEX_OVERRIDES):
        return env
    if index := _configured_pip_index(root, env):
        env["UV_DEFAULT_INDEX"] = index
    return env


def _uv_available(root: Path, env: dict[str, str]) -> bool:
    result = subprocess.run(
        _uv_command("--version"),
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    return result.returncode == 0


def _ensure_uv(root: Path, dry_run: bool, env: dict[str, str]) -> None:
    _stage("Checking uv")
    if _uv_available(root, env):
        return
    _stage("Installing uv")
    install_uv = [sys.executable, "-m", "pip", "install", "--user", "--upgrade", "uv"]
    _run_or_print(install_uv, root, dry_run)
    if not dry_run:
        _run(_uv_command("--version"), root, env=env)


def _tool_executable(tool_bin: Path, name: str) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    executable = tool_bin / f"{name}{suffix}"
    if not executable.is_file():
        raise RuntimeError(f"uv tool installation did not create {executable}")
    return executable


def _locate_tool_bin(root: Path, env: dict[str, str]) -> Path:
    result = subprocess.run(
        _uv_command("tool", "dir", "--bin"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    rendered = result.stdout.strip()
    if not rendered:
        raise RuntimeError("uv returned an empty tool executable directory")
    tool_bin = Path(rendered).expanduser()
    if not tool_bin.is_absolute():
        raise RuntimeError(f"uv returned a non-absolute tool executable directory: {rendered}")
    return tool_bin


def _verify_tools(root: Path, env: dict[str, str]) -> Path:
    tool_bin = _locate_tool_bin(root, env)
    for name in ("roundtable", "rt"):
        executable = _tool_executable(tool_bin, name)
        _run([str(executable), "--version"], root, env=env)
    print(f"Installed Roundtable executables in {tool_bin}")
    return tool_bin


def _verify_ado_updates(root: Path, tool_bin: Path, source: str) -> None:
    _stage("Verifying Azure Artifacts update access")
    command = [str(_tool_executable(tool_bin, "rt")), "update", "--list", "--source", source]
    try:
        _run(command, root)
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            "Roundtable is installed, but Azure Artifacts update access could not be "
            "verified. Configure feed authentication and rerun with --ado-updates."
        ) from err


def install_tool(
    bootstrap: Bootstrap,
    *,
    ado_updates: bool = False,
    update_source: str | None = None,
    dry_run: bool = False,
    update_shell: bool = True,
) -> None:
    resolved_update_source = (
        _resolve_ado_update_source(bootstrap.root, update_source, dry_run=dry_run)
        if ado_updates
        else None
    )
    uv_env = _uv_environment(bootstrap.root)
    _ensure_uv(bootstrap.root, dry_run, uv_env)
    if ado_updates:
        _stage("Adding ADO update support")
    _stage("Installing Roundtable from this checkout")
    install_command = _uv_command(
        "tool",
        "install",
        "--force",
        "--python",
        sys.executable,
    )
    for requirement in _ADO_UPDATE_REQUIREMENTS if ado_updates else ():
        install_command.extend(("--with", requirement))
    install_command.append(str(bootstrap.root))
    _run_or_print(install_command, bootstrap.root, dry_run, env=uv_env)
    if update_shell:
        _stage("Updating shell PATH")
        _run_or_print(_uv_command("tool", "update-shell"), bootstrap.root, dry_run, env=uv_env)
    if dry_run:
        _stage("Verifying roundtable and rt")
        _print(_uv_command("tool", "dir", "--bin"))
        suffix = ".exe" if sys.platform == "win32" else ""
        for name in ("roundtable", "rt"):
            _print([str(Path("<uv-tool-bin>") / f"{name}{suffix}"), "--version"])
        if ado_updates:
            _print(
                [
                    str(Path("<uv-tool-bin>") / f"rt{suffix}"),
                    "update",
                    "--list",
                    "--source",
                    resolved_update_source or "",
                ]
            )
        return
    _stage("Verifying roundtable and rt")
    tool_bin = _verify_tools(bootstrap.root, uv_env)
    if ado_updates:
        assert resolved_update_source is not None
        _verify_ado_updates(bootstrap.root, tool_bin, resolved_update_source)
    if update_shell:
        print("Open a new terminal if roundtable is not yet on PATH in this one.")


def install_contributor(
    bootstrap: Bootstrap,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    _stage("Preparing contributor environment")
    environment = bootstrap.environment
    if force and environment.exists():
        marker = environment / _MARKER
        if not marker.is_file():
            raise RuntimeError(f"refusing to remove unowned environment: {environment}")
        if not dry_run:
            shutil.rmtree(environment)
    if environment.exists() and not bootstrap.python.is_file() and not (force and dry_run):
        raise RuntimeError(
            f"{environment} exists but is not a compatible virtual environment; "
            "move it aside or recreate it explicitly"
        )
    create_environment = not bootstrap.python.is_file() or (force and dry_run)
    if create_environment:
        _run_or_print(
            [sys.executable, "-m", "venv", str(environment)],
            bootstrap.root,
            dry_run,
        )
    target = f"{bootstrap.root}[dev,lint,types]"
    if dry_run:
        _print([*bootstrap.pip, "install", target])
        return
    if create_environment:
        (environment / _MARKER).write_text(
            "owned by scripts/bootstrap.py\n",
            encoding="utf-8",
        )
    _run([*bootstrap.pip, "install", target], bootstrap.root)
    print(f"Prepared contributor environment in {environment}")


def install(
    bootstrap: Bootstrap,
    *,
    contributor: bool = False,
    ado_updates: bool = False,
    update_source: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    update_shell: bool = True,
) -> None:
    if contributor:
        if ado_updates or update_source is not None:
            raise RuntimeError(
                "--ado-updates/--update-source cannot be combined with --contributor"
            )
        install_contributor(bootstrap, dry_run=dry_run, force=force)
        return
    install_tool(
        bootstrap,
        ado_updates=ado_updates,
        update_source=update_source,
        dry_run=dry_run,
        update_shell=update_shell,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contributor",
        action="store_true",
        help="create or reuse checkout-local .venv with dev, lint, and type extras",
    )
    parser.add_argument(
        "--ado-updates",
        action="store_true",
        help="add Azure Artifacts updater dependencies and verify feed access",
    )
    parser.add_argument(
        "--update-source",
        default=None,
        help="with --ado-updates, persist this PyPI simple-index URL for future updates",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print changes without applying them"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --contributor, recreate only a bootstrap-owned .venv",
    )
    parser.add_argument(
        "--no-update-shell",
        action="store_true",
        help="do not add the uv tool executable directory to future shell PATH",
    )
    args = parser.parse_args(argv)
    try:
        if args.update_source is not None and not args.ado_updates:
            raise RuntimeError("--update-source requires --ado-updates")
        root = repository_root()
        _stage("Checking Python")
        validate_python(root)
        install(
            resolve_bootstrap(root),
            contributor=args.contributor,
            ado_updates=args.ado_updates,
            update_source=args.update_source,
            dry_run=args.dry_run,
            force=args.force,
            update_shell=not args.no_update_shell,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as err:
        print(f"bootstrap: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
