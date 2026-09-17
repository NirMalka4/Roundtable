#!/usr/bin/env python3
"""Validate an explicit review request and emit its exact Roundtable argv as JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from roundtable.inputs import ReplayError, load_replay_session  # noqa: E402


def _repo(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not (path / ".git").exists():
        raise ValueError(f"not a Git repository: {path}")
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git status failed")
    if result.stdout:
        raise ValueError(
            "repository is dirty; commit the intended change first because the review uses "
            "base...HEAD"
        )
    return path


def _is_ado_pr_url(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    ado_host = host == "dev.azure.com" or host.endswith(".visualstudio.com")
    return parsed.scheme == "https" and ado_host and "/_git/" in path and "/pullrequest/" in path


def plan(args: argparse.Namespace) -> list[str]:
    static = [label for label in ("file", "module", "function") if getattr(args, label) is not None]
    if static:
        raise ValueError(
            f"static {', '.join(static)} scope is unsupported; review a PR or committed branch diff"
        )

    selected = sum(value is not None for value in (args.pr, args.input_from)) + bool(
        args.repo and args.pr is None
    )
    if selected != 1:
        raise ValueError("choose exactly one input: --pr, local --repo/--base, or --input-from")

    command = ["roundtable", "review"]
    if args.input_from:
        if args.repo or args.base:
            raise ValueError("--input-from cannot be combined with --repo or --base")
        session = Path(args.input_from).expanduser().resolve()
        try:
            load_replay_session(session)
        except ReplayError as error:
            raise ValueError(f"incomplete replay session: {error}") from error
        if args.publish:
            raise ValueError("--publish is supported only for PR reviews")
        command.extend(["--input-from", str(session)])
    elif args.pr:
        numeric = args.pr.isdecimal()
        if numeric and not args.repo:
            raise ValueError("a numeric PR ID requires --repo")
        if not numeric and args.repo:
            raise ValueError("an ADO PR URL must not be combined with --repo")
        if args.base:
            raise ValueError("--base cannot be combined with --pr")
        if numeric:
            command.append(str(_repo(args.repo)))
        elif not _is_ado_pr_url(args.pr):
            raise ValueError("--pr must be a numeric ID or an Azure DevOps PR URL")
        command.extend(["--pr", args.pr])
        if args.publish:
            command.append("--publish")
    else:
        if args.publish:
            raise ValueError("--publish is supported only for PR reviews")
        command.append(str(_repo(args.repo)))
        if args.base:
            command.extend(["--base-branch", args.base])

    command.extend(["--config", "buddies"])
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr")
    parser.add_argument("--repo")
    parser.add_argument("--base")
    parser.add_argument("--input-from")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--file")
    parser.add_argument("--module")
    parser.add_argument("--function")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        command = plan(build_parser().parse_args(argv))
    except ValueError as error:
        print(f"plan_review: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"argv": command}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
