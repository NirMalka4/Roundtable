"""Real-git-repo test helpers for the inputs (Phase 1) suite.

Bare-importable (the ``tests/unit/inputs`` dir is on ``sys.path`` under pytest's
prepend import mode) so test modules can ``from _gitrepo import ...`` without a
package ``__init__``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout.decode("utf-8", "replace")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "core.autocrlf", "false")
    _git(path, "config", "commit.gpgsign", "false")


def _write(path: Path, rel: str, content: str) -> None:
    f = path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8", newline="\n")


@dataclass
class GitRepo:
    path: Path
    base_sha: str
    head_sha: str

    def git(self, *args: str) -> str:
        return _git(self.path, *args)


def make_feature_repo(tmp_path: Path) -> GitRepo:
    """A repo with a ``main`` base commit and a ``feature`` HEAD that modifies
    ``calc.py`` and adds ``util/new_helper.py``."""
    repo = tmp_path / "TestRepo"
    _init_repo(repo)

    _write(repo, "calc.py", "def divide(a, b):\n    return a / b\n")
    _write(repo, "README.md", "# Test\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").strip()

    _git(repo, "checkout", "-b", "feature")
    _write(
        repo,
        "calc.py",
        "def divide(a, b):\n    if b == 0:\n        raise ValueError\n    return a / b\n",
    )
    _write(repo, "util/new_helper.py", "def helper():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feature change")
    head_sha = _git(repo, "rev-parse", "HEAD").strip()

    return GitRepo(path=repo, base_sha=base_sha, head_sha=head_sha)
