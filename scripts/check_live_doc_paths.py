"""Verify source paths cited by live documentation and ignore historical records."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PATH = re.compile(r"`(?P<path>roundtable/[A-Za-z0-9_./\\-]+\.(?:py|yaml|md))(?:[:#][^`]*)?`")


def _live_documents(root: Path) -> list[Path]:
    documents = [root / "README.md", root / "AGENTS.md", root / "CONTRIBUTING.md"]
    documents.extend((root / ".github" / "skills").rglob("*.md"))
    return [path for path in documents if path.is_file()]


def check(root: Path = _ROOT) -> list[str]:
    violations: list[str] = []
    for document in _live_documents(root):
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for match in _PATH.finditer(line):
                cited = match.group("path").replace("\\", "/")
                if not (root / cited).is_file():
                    relative = document.relative_to(root).as_posix()
                    violations.append(f"{relative}:{line_number}: missing cited path {cited}")
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("\n".join(violations))
        return 1
    print("live documentation paths: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
