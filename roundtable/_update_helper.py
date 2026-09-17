r"""Detached, stdlib-only updater helper for ``roundtable update`` on Windows.

This module ships inside the package but is **never imported** as part of it. The
updater copies this file to a throwaway directory *outside* the venv/package tree
and launches it **by path** with a fresh interpreter::

    python <copy>\update_helper.py --parent-pid <PID> --log <L> --status <S> \
        --version <X.Y.Z> -- <pip argv...>

Why detached + out-of-tree + by-path (not ``-m roundtable._update_helper``):

* A running ``roundtable.exe`` console-script launcher keeps its own image file
  mapped for its whole lifetime, so pip cannot delete/recreate it in place
  (``WinError 32``). The helper therefore **waits for the launcher process to exit**
  before running pip — only then is the ``.exe`` free.
* Importing ``roundtable`` calls ``importlib.metadata.version(...)`` at import
  time, which itself opens a handle on the launcher exe. Running by module path
  (``-m``) would re-trigger that and re-lock the file we are trying to replace, so
  this helper is a standalone script that imports **nothing** from the package.
* Running from a copy outside the install tree means the very files pip rewrites are
  not the ones executing.

The helper is intentionally dependency-free (stdlib only) and self-contained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime

_WAIT_FOR_PARENT_SECONDS = 120.0
_GRACE_AFTER_EXIT_SECONDS = 0.5


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def wait_for_pid_exit(pid: int, timeout: float = _WAIT_FOR_PARENT_SECONDS) -> None:
    """Block until process ``pid`` exits (or ``timeout`` elapses).

    On Windows, waits on a real process handle (``SYNCHRONIZE``); an already-gone or
    unopenable pid returns immediately. Elsewhere, polls ``os.kill(pid, 0)``.
    """
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        try:
            kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        finally:
            kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _mutex_name() -> str:
    """A per-venv mutex name, keyed from ``sys.prefix`` (backslash-free for Win32)."""
    digest = hashlib.sha256(sys.prefix.encode("utf-8", "replace")).hexdigest()[:16]
    return f"roundtable-update-{digest}"


class _MutexHeld:
    """Sentinel returned by :func:`acquire_update_lock` on non-Windows (no-op lock)."""


def acquire_update_lock() -> object | None:
    """Acquire the per-venv update mutex; return a handle, or ``None`` if already held.

    Prevents two detached updaters running pip against the same venv concurrently
    (which can corrupt it). Non-Windows returns a sentinel (single-writer isn't a
    concern for the in-process path there).
    """
    if sys.platform != "win32":
        return _MutexHeld()
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, _mutex_name())
    error_already_exists = 183
    if (
        ctypes.get_last_error() == error_already_exists
        or kernel32.GetLastError() == error_already_exists
    ):
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def write_status(path: str, state: str, returncode: int | None, version: str) -> None:
    """Atomically write the final ``{state, returncode, version, time}`` status record."""
    payload = {
        "state": state,
        "returncode": returncode,
        "version": version,
        "time": _now(),
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, path)


def _run_pip(pip_command: list[str], log) -> int:
    """Run pip with stdin detached, streaming combined output into ``log``.

    ``stdin`` is ``DEVNULL`` so a stray interactive prompt fails fast (EOF) instead
    of hanging this invisible background process. ``--no-input`` is deliberately
    *not* added: on the Azure Artifacts feed it suppresses the artifacts-keyring
    credential provider and turns an authenticated 200 into a silent "no versions"
    401. The parent always runs ``list_remote_versions`` before handing off, so the
    keyring token cache is already warm — pip reuses it here without prompting.
    """
    command = list(pip_command)
    log.write(f"[{_now()}] running: {' '.join(command)}\n")
    log.flush()
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--version", default="")
    parser.add_argument("pip", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    pip_command = list(args.pip)
    if pip_command and pip_command[0] == "--":
        pip_command = pip_command[1:]

    with open(args.log, "w", encoding="utf-8") as log:
        log.write(f"[{_now()}] update helper started; waiting for pid {args.parent_pid} to exit\n")
        log.flush()
        wait_for_pid_exit(args.parent_pid)
        time.sleep(_GRACE_AFTER_EXIT_SECONDS)

        lock = acquire_update_lock()
        if lock is None:
            log.write(f"[{_now()}] another update is already in progress; exiting\n")
            write_status(args.status, "skipped", None, args.version)
            return 0

        if not pip_command:
            log.write(f"[{_now()}] no pip command supplied; nothing to do\n")
            write_status(args.status, "failed", None, args.version)
            return 2

        try:
            code = _run_pip(pip_command, log)
        except OSError as err:
            log.write(f"[{_now()}] failed to run pip: {err}\n")
            write_status(args.status, "failed", None, args.version)
            return 1
        state = "installed" if code == 0 else "failed"
        log.write(f"[{_now()}] pip exited {code} ({state})\n")
        write_status(args.status, state, code, args.version)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
