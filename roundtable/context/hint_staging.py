"""Stage an author-supplied hint into a dedicated, agent-readable directory.

The ``--hint-path`` target lives on the operator's real filesystem, outside the
detached review worktree agents run in. Rather than widen the agents' ``--add-dir``
grant to the hint's *real* parent — which would expose every unrelated sibling — we
copy the hint into a session-scoped staging dir that holds ONLY the hint, and grant
that. A directory hint is copied whole (VCS metadata and symlinks skipped) under a
size/count guard so it cannot smuggle a large or secret-laden tree into the
agent-readable space.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Guards apply to *directory* hints only — a single-file hint is an explicit,
# bounded choice by the author, so it is copied as-is. A directory could smuggle a
# large or secret-laden tree into the agent-readable staging dir, so cap both the
# file count and the total bytes and drop the hint (non-fatal) when either is blown.
MAX_HINT_FILES = 200
MAX_HINT_BYTES = 5 * 1024 * 1024  # 5 MiB
_SKIP_DIRS = frozenset({".git", ".hg", ".svn"})


class HintTooLarge(ValueError):
    """A directory hint exceeds the file-count / byte-size guard."""


@dataclass(frozen=True)
class StagedHint:
    grant_dir: Path  # the ONLY dir handed to agents' --add-dir (holds just the hint)
    pointer: Path  # the staged path agents are told to read (file or dir root)


def _is_excluded(name: str, parent: Path) -> bool:
    # VCS metadata leaks history/config; a symlink can point back out of the tree
    # and defeat the whole staging boundary — exclude both from the copy and count.
    return name in _SKIP_DIRS or (parent / name).is_symlink()


def _measure_tree(src: Path) -> None:
    files = 0
    total = 0
    for root, dirs, names in os.walk(src, followlinks=False):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not _is_excluded(d, root_path)]
        for name in names:
            path = root_path / name
            if path.is_symlink():
                continue
            files += 1
            if files > MAX_HINT_FILES:
                raise HintTooLarge(f"{src} has more than {MAX_HINT_FILES} files")
            try:
                total += path.stat().st_size
            except OSError:
                continue
            if total > MAX_HINT_BYTES:
                raise HintTooLarge(f"{src} exceeds {MAX_HINT_BYTES // (1024 * 1024)} MiB")


def _ignore_excluded(dir_path: str, names: list[str]) -> set[str]:
    parent = Path(dir_path)
    return {n for n in names if _is_excluded(n, parent)}


def stage_hint(src: Path, dest_root: Path) -> StagedHint:
    """Copy ``src`` (an existing file or directory) into a staging dir under
    ``dest_root`` and return the grant dir + the pointer agents should read.

    Raises :class:`HintTooLarge` when a directory hint blows the guard; the caller
    treats that as non-fatal and drops the hint.
    """
    grant_dir = dest_root / "hint"
    dest = grant_dir / src.name
    grant_dir.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        _measure_tree(src)
        shutil.copytree(src, dest, symlinks=False, ignore=_ignore_excluded)
    else:
        shutil.copy2(src, dest)
    return StagedHint(grant_dir=grant_dir, pointer=dest)
