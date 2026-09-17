"""Byte-safe git invocation helper shared by the inputs subsystem.

Runs ``git`` with arguments passed as a list (never a shell string) for injection
safety, and reads stdout as raw bytes decoded UTF-8 with no newline translation so
diff content is preserved byte-for-byte (Python's text mode would otherwise apply
universal-newline translation and could rewrite ``\\r\\n`` inside a diff body).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

#: Max git diff output we accept (16 MB). subprocess has no explicit buffer cap;
#: this constant is used to truncate defensively if a diff ever exceeds it.
MAX_BUFFER = 16 * 1024 * 1024


class GitError(RuntimeError):
    """Raised when a git command exits non-zero (and the caller requested check)."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.args_list = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(args)} failed (exit {returncode}): {stderr.strip()}")


@dataclass(frozen=True)
class GitResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_git(
    args: list[str],
    cwd: str,
    *,
    timeout: float = 30.0,
    check: bool = True,
) -> GitResult:
    """Run ``git <args>`` in ``cwd``. Returns a :class:`GitResult`.

    UTF-8 decode with ``errors='replace'`` and no newline translation — diff
    bytes are preserved.
    """
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if len(stdout) > MAX_BUFFER:
        stdout = stdout[:MAX_BUFFER]
    stderr = proc.stderr.decode("utf-8", errors="replace")
    result = GitResult(stdout=stdout, stderr=stderr, returncode=proc.returncode)
    if check and not result.ok:
        raise GitError(args, proc.returncode, stderr)
    return result
