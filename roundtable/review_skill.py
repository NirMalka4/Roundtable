"""Install and inspect the packaged Roundtable review skill."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from roundtable import __version__

SKILL_NAME = "roundtable-review"
METADATA_FILE = ".roundtable-install.json"


def default_destination() -> Path:
    return Path.home() / ".copilot" / "skills" / SKILL_NAME


def packaged_skill() -> Any:
    return files("roundtable").joinpath("skills", SKILL_NAME)


def _hash_tree(root: Any, *, metadata: bool = False) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and (metadata or item.name != METADATA_FILE)
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def status(destination: Path) -> dict[str, str]:
    destination = destination.expanduser().resolve()
    if not destination.exists():
        return {"status": "absent", "destination": str(destination)}
    metadata_path = destination / METADATA_FILE
    if not metadata_path.is_file():
        return {"status": "diverged", "destination": str(destination)}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "diverged", "destination": str(destination)}
    actual_hash = _hash_tree(destination)
    if actual_hash != metadata.get("contentHash"):
        state = "diverged"
    else:
        with as_file(packaged_skill()) as source:
            current_hash = _hash_tree(source)
        state = (
            "current"
            if actual_hash == current_hash and metadata.get("packageVersion") == __version__
            else "stale"
        )
    return {
        "status": state,
        "destination": str(destination),
        "contentHash": actual_hash,
        "packageVersion": str(metadata.get("packageVersion", "")),
    }


def install(destination: Path, *, force: bool = False) -> dict[str, str]:
    destination = destination.expanduser().resolve()
    before = status(destination)
    if before["status"] == "diverged" and not force:
        raise ValueError("installed review skill has diverged; use --force to overwrite")
    if before["status"] == "current":
        return before
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=destination.parent))
    try:
        with as_file(packaged_skill()) as source:
            shutil.copytree(source, temporary, dirs_exist_ok=True)
        content_hash = _hash_tree(temporary)
        metadata = {"contentHash": content_hash, "packageVersion": __version__}
        (temporary / METADATA_FILE).write_text(
            json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        backup = destination.with_name(f".{destination.name}-backup")
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(temporary, destination)
        except BaseException:
            if backup.exists():
                _restore_backup(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return status(destination)


def _restore_backup(backup: Path, destination: Path) -> None:
    """Retry transient Windows directory-handle contention during rollback."""
    for attempt in range(20):
        try:
            os.replace(backup, destination)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == 19:
                raise
            time.sleep(0.05)


def uninstall(destination: Path, *, force: bool = False) -> dict[str, str]:
    destination = destination.expanduser().resolve()
    before = status(destination)
    if before["status"] == "diverged" and not force:
        raise ValueError("installed review skill has diverged; use --force to remove")
    if destination.exists():
        shutil.rmtree(destination)
    return status(destination)
