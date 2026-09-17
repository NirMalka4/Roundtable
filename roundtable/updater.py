r"""Self-update logic for ``roundtable update`` (configured-index model).

The tool installs new versions from an operator-configured PyPI simple index by
asking pip to reinstall itself from that index:

    pip install --upgrade --index-url <feed-simple-url> roundtable==<X.Y.Z>

``<feed-simple-url>`` is resolved by :mod:`roundtable.settings.workspace`
(``--source`` / ``ROUNDTABLE_UPDATE_SOURCE`` / nearest workspace
``update.source`` / persisted user setting). A *single* index is used
deliberately: the feed's PyPI upstream resolves the public runtime deps, so
there is no ``--extra-index-url`` (which would invite dependency confusion).

Everything that touches the network or mutates the environment is isolated behind
injectable seams so the resolution logic is unit-testable offline:

* ``list_remote_versions`` — returns the feed's published ``X.Y.Z`` versions
  (default: a ``pip index versions`` call, which shares pip's keyring/PAT auth).
* ``run_pip`` — executes the built pip command and returns its exit code (default:
  ``subprocess.run``).
* ``launcher_pid`` / ``start_detached_update`` — the Windows launcher-replacement
  seams (see below).

**Windows launcher replacement.** When the CLI is invoked through the
``roundtable.exe`` console-script *launcher*, that launcher process keeps its own
``.exe`` image mapped for the entire run, so pip's in-place upgrade cannot delete
``Scripts\roundtable.exe`` to recreate it → ``WinError 32`` (and a corrupted,
half-uninstalled install). Renaming the exe aside in place is *also* blocked here:
importing ``roundtable`` calls ``importlib.metadata.version`` at import time,
which opens a no-``FILE_SHARE_DELETE`` handle on the launcher exe for the process
lifetime. So the only robust fix is to run pip **after this process exits** — the
canonical self-update handoff used by npm/choco/rustup.

:func:`launcher_pid` finds the live launcher-exe ancestor pid, and
:func:`start_detached_update` copies the standalone, stdlib-only
:mod:`roundtable._update_helper` to a throwaway directory *outside* the install
tree and spawns it **detached, by file path** (never ``-m roundtable._update_helper``,
which would re-import the package and re-lock the exe). The helper waits for the
launcher pid to exit, takes a per-venv mutex, runs pip non-interactively, and writes
a status file. The parent CLI reports "update started" and exits immediately — so
success means *launched*, not *installed*; the helper's status/log carry the pip
result. ``python -m roundtable update`` and every non-Windows install have no
launcher lock and use the plain synchronous :func:`run_pip` path.

The pure core (:func:`parse_version`, :func:`parse_available_versions`,
:func:`select_version`, :func:`build_pip_command`, :func:`resolve_plan`,
:func:`is_editable`, :func:`find_launcher_pid`, :func:`build_helper_command`) has no
I/O.
"""

from __future__ import annotations

import json
import os
import re
import site
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError
from pathlib import Path

PACKAGE_NAME = "roundtable"
LATEST = "latest"

# A published feed version is strictly ``MAJOR.MINOR.PATCH`` (PEP 440, no ``v``); a
# *requested* version may add a leading ``v`` for convenience and is normalised
# back to the canonical bare form.
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_REQUESTED_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# The line `pip index versions` prints, e.g. `Available versions: 1.2.0, 1.1.0`.
_AVAILABLE_RE = re.compile(r"^\s*Available versions:\s*(?P<list>.+)$", re.MULTILINE)


class UpdateError(Exception):
    """A self-update could not proceed (bad version, no releases, pip failure)."""


def parse_version(version: str) -> tuple[int, int, int] | None:
    """Return the sortable ``(major, minor, patch)`` of an ``X.Y.Z`` string, else None."""
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def normalize_version(requested: str) -> str:
    """Canonicalise a user-supplied version to bare ``X.Y.Z`` (accepts a leading ``v``)."""
    match = _REQUESTED_RE.match(requested.strip())
    if match is None:
        raise UpdateError(
            f"invalid version {requested!r}: expected 'X.Y.Z' (e.g. 1.0.0) or 'latest'"
        )
    return "{}.{}.{}".format(*match.groups())


def parse_available_versions(pip_index_output: str) -> list[str]:
    """Extract the ``X.Y.Z`` versions from ``pip index versions`` stdout."""
    match = _AVAILABLE_RE.search(pip_index_output)
    if match is None:
        return []
    return [
        candidate
        for raw in match.group("list").split(",")
        if parse_version(candidate := raw.strip()) is not None
    ]


def sort_versions_desc(versions: Sequence[str]) -> list[str]:
    """Return the unique valid ``X.Y.Z`` versions, newest first."""
    valid = {version for version in versions if parse_version(version) is not None}
    return sorted(valid, key=lambda version: parse_version(version), reverse=True)  # type: ignore[arg-type]


def is_outdated(current: str, available: Sequence[str]) -> str | None:
    """Return the newest available version iff it is strictly newer than ``current``.

    Returns ``None`` when up to date, when nothing is published, or when ``current``
    is not a clean ``X.Y.Z`` (a ``+dev`` editable/source build) — such a build has
    no meaningful place in the release order, so it is never reported as outdated.
    """
    current_parsed = parse_version(current)
    if current_parsed is None:
        return None
    ordered = sort_versions_desc(available)
    if not ordered:
        return None
    latest = ordered[0]
    return latest if parse_version(latest) > current_parsed else None  # type: ignore[operator]


def select_version(requested: str | None, available: Sequence[str]) -> str:
    """Pick the target version from ``available`` (newest-first) for ``requested``.

    ``None``/``latest`` picks the highest version; an explicit version must exist
    as a published feed version. Raises :class:`UpdateError` with an actionable
    message otherwise.
    """
    ordered = sort_versions_desc(available)
    if requested is None or requested.strip().lower() == LATEST:
        if not ordered:
            raise UpdateError(
                "no released versions found on the feed yet. A release publishes on "
                "merge to main once _version.py + CHANGELOG.md are bumped."
            )
        return ordered[0]
    target = normalize_version(requested)
    if target not in ordered:
        available_note = ", ".join(ordered) if ordered else "(none published)"
        raise UpdateError(
            f"version {target} is not published to the feed. Available: {available_note}"
        )
    return target


def build_pip_command(source: str, version: str, *, python: str = sys.executable) -> list[str]:
    """Build the ``pip install --upgrade`` argv that installs ``version`` from the feed.

    A single ``--index-url`` points at the feed's simple index; the feed's PyPI
    upstream resolves the public runtime dependencies.
    """
    spec = f"{PACKAGE_NAME}=={version}"
    return [python, "-m", "pip", "install", "--upgrade", "--index-url", source, spec]


def is_editable(direct_url_json: str | None) -> bool:
    """Pure test of a dist's ``direct_url.json`` for a modern editable ('-e') install."""
    if not direct_url_json:
        return False
    try:
        data = json.loads(direct_url_json)
    except (ValueError, TypeError):
        return False
    return bool(isinstance(data, dict) and data.get("dir_info", {}).get("editable"))


def is_under_any(path: Path, roots: Sequence[str]) -> bool:
    """Pure: is ``path`` inside any of ``roots`` (a site-packages-style directory)?"""
    resolved = Path(path).resolve()
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def _site_roots() -> list[str]:
    roots = list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    user = site.getusersitepackages()
    if user:
        roots.append(user)
    return roots


def current_install_is_editable() -> bool:
    """True when the running ``roundtable`` is an editable/source install.

    Such an install *is* a working tree, so reinstalling from the feed would
    clobber local work — the CLI refuses and points the user at git instead. Also
    the honesty seam for provenance: when this is true, :mod:`roundtable`
    suffixes the version with ``+dev`` so unreleased code never stamps a clean
    release label. Detected via the PEP 610 ``direct_url.json`` marker (modern
    ``pip install -e``) OR the package importing from outside site-packages (legacy
    editable / source checkout, which record no marker). A missing installed dist
    also counts.
    """
    try:
        dist = Distribution.from_name(PACKAGE_NAME)
    except PackageNotFoundError:
        return True
    if is_editable(dist.read_text("direct_url.json")):
        return True
    return not is_under_any(Path(__file__).resolve().parent, _site_roots())


@dataclass(frozen=True)
class UpdatePlan:
    """A fully resolved, ready-to-run update (no side effects performed yet)."""

    version: str
    source: str
    command: list[str]
    is_latest: bool


def resolve_plan(
    requested: str | None,
    source: str,
    available: Sequence[str],
    *,
    python: str = sys.executable,
) -> UpdatePlan:
    """Resolve ``requested`` against ``available`` into a runnable :class:`UpdatePlan`."""
    version = select_version(requested, available)
    is_latest = requested is None or requested.strip().lower() == LATEST
    return UpdatePlan(
        version=version,
        source=source,
        command=build_pip_command(source, version, python=python),
        is_latest=is_latest,
    )


# ── Windows launcher replacement (pure decision + helper argv) ───────────────
def find_launcher_pid(
    processes: dict[int, tuple[int, str]],
    start_pid: int,
    *,
    launcher_stem: str = PACKAGE_NAME,
    max_depth: int = 16,
) -> int | None:
    """Pure: the pid of the ``roundtable.exe`` console-script launcher ancestor, or None.

    ``processes`` maps ``pid -> (parent_pid, image_basename)`` (a process snapshot).
    Walks the parent chain up from ``start_pid`` looking for a process whose image
    starts with ``launcher_stem`` (e.g. ``roundtable.exe``) — that process holds
    the live launcher ``.exe`` open, so pip must wait for *it* to exit. The launcher
    is usually a *grand*parent (a venv ``python.exe`` redirector sits between it and
    the CLI), so the immediate parent alone is insufficient. A ``python -m
    roundtable`` run has no such ancestor ⇒ None ⇒ the synchronous path. Cycle-
    and depth-guarded.
    """
    stem = launcher_stem.lower()
    pid = start_pid
    seen: set[int] = set()
    for _ in range(max_depth):
        entry = processes.get(pid)
        if entry is None or pid in seen:
            return None
        seen.add(pid)
        parent_pid, image = entry
        if image.strip().lower().startswith(stem):
            return pid
        pid = parent_pid
    return None


def build_helper_command(
    helper_path: str,
    parent_pid: int,
    log_path: str,
    status_path: str,
    version: str,
    pip_command: Sequence[str],
    *,
    python: str = sys.executable,
) -> list[str]:
    """Build the detached-helper argv (pure).

    Runs the helper **by file path** (never ``-m``) so a fresh interpreter executes
    it without importing :mod:`roundtable`. The pip argv follows a ``--`` guard so
    its own leading flags are never mistaken for helper options.
    """
    return [
        python,
        helper_path,
        "--parent-pid",
        str(parent_pid),
        "--log",
        log_path,
        "--status",
        status_path,
        "--version",
        version,
        "--",
        *pip_command,
    ]


# ── I/O seams for the detached launcher-safe update ──────────────────────────
def _snapshot_processes() -> dict[int, tuple[int, str]]:
    """Windows-only: snapshot all processes as ``pid -> (parent_pid, image_basename)``.

    Uses the Toolhelp process snapshot (which exposes each process' *parent* pid —
    ``os.getppid`` only gives the immediate parent, but the launcher is a
    grandparent). Any failure yields an empty map ⇒ the safe synchronous path.
    """
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (0, -1, None):
        return {}
    processes: dict[int, tuple[int, str]] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            processes[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                entry.szExeFile.decode(errors="replace"),
            )
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return processes


def launcher_pid() -> int | None:
    """The pid of the live ``roundtable.exe`` launcher ancestor, or None.

    None ⇒ not launched via the Windows console-script exe (``python -m`` or any
    non-Windows install) ⇒ pip can upgrade in-place synchronously. A pid ⇒ the caller
    must hand the upgrade to :func:`start_detached_update`, which waits for this pid
    to exit before running pip.
    """
    if sys.platform != "win32":
        return None
    try:
        return find_launcher_pid(_snapshot_processes(), os.getpid())
    except Exception:
        return None


@dataclass(frozen=True)
class DetachedUpdate:
    """Where a launched background update writes its progress and final result."""

    log_path: Path
    status_path: Path


def _update_state_dir() -> Path:
    """The base dir for throwaway updater workspaces, *outside* the install tree.

    ``%LOCALAPPDATA%\\Roundtable\\updater`` on Windows, else ``~/.roundtable/updater``.
    Out-of-tree matters: the helper must not run from files pip is rewriting.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "Roundtable" / "updater"
    return Path(os.path.expanduser("~")) / ".roundtable" / "updater"


def _deploy_update_helper(run_dir: Path) -> Path:
    """Copy the stdlib-only helper into ``run_dir`` and return its path.

    Copied (not referenced in place) so it survives the package being reinstalled and
    executes from outside the install tree. Uses a plain byte copy — no ``shutil`` —
    to keep the dependency surface trivial.
    """
    source = Path(__file__).with_name("_update_helper.py")
    dest = run_dir / "update_helper.py"
    dest.write_bytes(source.read_bytes())
    return dest


def spawn_detached(command: Sequence[str]) -> None:
    """Spawn ``command`` fully detached from this process/console.

    ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` so the helper outlives this CLI
    and its console closing; stdio is detached (the helper logs to a file). Raises
    ``OSError`` if the spawn itself fails.
    """
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(
        list(command),
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def start_detached_update(plan: UpdatePlan, wait_pid: int) -> DetachedUpdate:
    """Launch the detached updater that runs ``plan`` after ``wait_pid`` exits.

    Provisions a unique out-of-tree workspace, deploys the helper, and spawns it
    detached. Returns the log/status paths the user can inspect; the actual pip
    result lands in the status file once this process exits and the helper runs.
    Raises ``OSError`` if provisioning or spawning fails (caller surfaces a manual
    fallback).
    """
    run_dir = _update_state_dir() / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=True)
    helper = _deploy_update_helper(run_dir)
    log_path = run_dir / "update.log"
    status_path = run_dir / "status.json"
    command = build_helper_command(
        str(helper),
        wait_pid,
        str(log_path),
        str(status_path),
        plan.version,
        plan.command,
    )
    spawn_detached(command)
    return DetachedUpdate(log_path=log_path, status_path=status_path)


# ── I/O seams (the two functions the CLI calls; monkeypatched in tests) ──────
def list_remote_versions(source: str) -> list[str]:
    """Return the feed's published ``X.Y.Z`` versions via ``pip index versions``.

    Shelling to pip for discovery means the call shares pip's keyring/artifacts-keyring
    auth — so it succeeds in exactly the environments where the subsequent install would.
    """
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "index",
                "versions",
                PACKAGE_NAME,
                "--index-url",
                source,
            ],
            capture_output=True,
            text=True,
        )
    except OSError as err:  # pip / python missing
        raise UpdateError(f"could not run pip: {err}") from err
    if proc.returncode != 0:
        combined = f"{proc.stdout}\n{proc.stderr}".lower()
        # The package simply isn't published yet (empty feed / unknown name) — that
        # is an empty result, not a failure, so `--list` and `latest` can report
        # "no versions" cleanly. A real auth/network error carries a different
        # signal (401/403/connection…) and must still surface loudly.
        if "no matching distribution" in combined:
            return []
        raise UpdateError(
            f"pip index versions failed for {source} (exit {proc.returncode}). "
            f"Check the feed URL and your credentials (artifacts-keyring / PAT).\n"
            f"{proc.stderr.strip()}"
        )
    return parse_available_versions(proc.stdout)


def run_pip(command: Sequence[str]) -> int:
    """Execute the pip command, streaming its output; return its exit code."""
    return subprocess.run(list(command)).returncode
