"""The gate set - the single source of truth for "is this branch shippable".

GitHub Actions runs this script and ``CONTRIBUTING.md`` names it, so the
gate list — and every flag it carries: the coverage floor, the ruff targets, the
pytest plugin pins — is authored exactly once. Adding a gate here adds it to CI
and to the documented pre-flight in the same edit; there is no second list.

Gates run in order and stop at the first red one, mirroring how a pipeline job
aborts on a failed step. Because CI runs them as one step rather than seven, the
failed gate is named on stderr — that name is the actionable part of the log.

Tools are invoked as ``python -m <tool>`` so the run does not depend on console
scripts being on PATH.

Usage:  python scripts/gates.py
Exit 0 when every gate is green, 1 on the first red one.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

GATES: tuple[tuple[str, list[str]], ...] = (
    ("release coherence", [sys.executable, "scripts/check_release_version.py"]),
    ("package boundaries", [sys.executable, "scripts/check_package_boundaries.py"]),
    ("live documentation paths", [sys.executable, "scripts/check_live_doc_paths.py"]),
    ("ruff check", [sys.executable, "-m", "ruff", "check", "roundtable", "scripts", "tests"]),
    (
        "ruff format",
        [sys.executable, "-m", "ruff", "format", "--check", "roundtable", "scripts", "tests"],
    ),
    ("pyright", [sys.executable, "-m", "pyright", "roundtable"]),
    (
        "pytest (+ branch-coverage floor 80%)",
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:randomly",
            "-q",
            "--cov=roundtable",
            "--cov-branch",
            "--cov-fail-under=80",
            "--junitxml=junit.xml",
        ],
    ),
    (
        "static doctor (buddies)",
        [sys.executable, "-m", "roundtable.cli", "doctor", "--static", "--config", "buddies"],
    ),
    (
        "static doctor (inspectorx)",
        [sys.executable, "-m", "roundtable.cli", "doctor", "--static", "--config", "inspectorx"],
    ),
)


def _run(name: str, command: list[str]) -> bool:
    print(f"\n=== gate: {name} ===", flush=True)
    started = time.perf_counter()
    code = subprocess.run(command, cwd=_REPO).returncode
    verdict = "ok" if code == 0 else "FAILED"
    print(f"--- gate: {name} -> {verdict} in {time.perf_counter() - started:.1f}s", flush=True)
    return code == 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Run the gate set; stop at the first red gate and name it."
    ).parse_args(argv)
    for name, command in GATES:
        if not _run(name, command):
            print(
                f"\ngate failed: {name}\n  fix it, then re-run: python scripts/gates.py",
                file=sys.stderr,
            )
            return 1
    print(f"\nall {len(GATES)} gates green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
