"""Enforce the one-facade rule for every top-level Roundtable package."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "roundtable"


def _source_package(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root)
    return relative.parts[0] if len(relative.parts) > 1 else None


def _containing_module(path: Path, root: Path) -> list[str]:
    return ["roundtable", *path.relative_to(root).parent.parts]


def _targets(node: ast.AST, containing: list[str]) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if not node.level:
        return [node.module] if node.module else []
    base = containing[: len(containing) - (node.level - 1)]
    suffix = node.module.split(".") if node.module else []
    return [".".join([*base, *suffix])]


def _cross_package_reach_ins(path: Path, root: Path) -> list[str]:
    source = _source_package(path, root)
    containing = _containing_module(path, root)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        for target in _targets(node, containing):
            parts = target.split(".")
            if len(parts) < 3 or parts[0] != "roundtable" or parts[1] == source:
                continue
            relative = path.relative_to(root.parent).as_posix()
            violations.append(
                f"{relative}:{getattr(node, 'lineno', 0)} reaches through {target!r}; "
                f"import 'roundtable.{parts[1]}' instead"
            )
    return violations


def _all_exports(path: Path) -> list[str] | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ]
    if len(assignments) != 1:
        return None
    value = assignments[0].value
    if not isinstance(value, ast.List):
        return None
    exports: list[str] = []
    for item in value.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        exports.append(item.value)
    return exports


def check(package_root: str | Path | None = None) -> list[str]:
    root = Path(package_root).resolve() if package_root is not None else _ROOT
    violations: list[str] = []
    for package in sorted(path for path in root.iterdir() if (path / "__init__.py").is_file()):
        facade = package / "__init__.py"
        exports = _all_exports(facade)
        if exports is None or (not exports and package.name != "configs"):
            violations.append(
                f"{facade.relative_to(root.parent).as_posix()}: "
                "facade must define one non-empty literal __all__"
            )
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            violations.extend(_cross_package_reach_ins(path, root))
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("\n".join(violations))
        return 1
    print("package boundaries: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
