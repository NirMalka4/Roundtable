#!/usr/bin/env python3
"""verify_live_run.py — thin CLI over :mod:`roundtable.review.acceptance`.

Grades an already-produced ``roundtable review`` session directory. It is
**inspect-only**: it never launches the LLM or reads the reviewed repository.

    python scripts/verify_live_run.py --session <session_dir> \
        [--report <out.json>] [--exit-code <n>]

Tiers (GATE = affects exit code; advisory = report-only):
    T1  Completion / Structure        GATE
    T2  Internal Correctness          GATE
    T4  Perf / Cost                   advisory

The session must contain ``trace.json``; ``verdict.md`` is one of the graded artifacts.
Exit code: non-zero iff any T1/T2 GATE check fails; T4 advisories never change it.
The grader logic lives in :mod:`roundtable.review.acceptance`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Make the roundtable package importable when run as a loose script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from roundtable.review.acceptance import format_table, grade  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Inspect an existing Roundtable session: grade T1 completion/structure and T2 "
            "internal correctness as gates, plus advisory T4 performance/cost. No AI is run."
        )
    )
    ap.add_argument("--session", required=True, help="session dir (contains trace.json)")
    ap.add_argument("--report", default=None, help="write the machine JSON report here")
    ap.add_argument(
        "--exit-code",
        type=int,
        default=None,
        help="observed process exit code of the review run (enables T1 exit check)",
    )
    args = ap.parse_args(argv)

    session_dir = Path(args.session).resolve()
    if not (session_dir / "trace.json").exists():
        print(f"error: {session_dir}/trace.json not found", file=sys.stderr)
        return 2

    report = grade(session_dir, args.exit_code)
    print(format_table(report))
    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "session": report.session,
                    "gateFailed": report.gate_failed,
                    "checks": [asdict(c) for c in report.checks],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return 1 if report.gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
