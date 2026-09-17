"""Release helper — bump files or prepare an explicit manual release.

The low-level ``bump`` command makes the two coordinated release edits (bump
``roundtable/_version.py`` and promote ``CHANGELOG.md``'s ``[Unreleased]``
section to a versioned heading). ``promote`` handles the other case: a release
branch that has already bumped and then took more notes, folding ``[Unreleased]``
into the heading that exists rather than cutting a further version.
``manual-release`` is the explicit maintainer fallback:
it requires the target version and release date, makes the same edits, then runs
the repository gate set. It does not stage, commit, push, create a PR, or use
credentials.

Both commands compute *both* new file contents before writing *either*, so a
validation failure never leaves a half-bumped tree.

Usage:
    python scripts/release.py manual-release 1.1.0 --date 2026-07-16
    python scripts/release.py bump 1.1.0            # write both files
    python scripts/release.py bump 1.1.0 --dry-run  # print the plan, write nothing
    python scripts/release.py promote               # fold notes into the pending release

Exit 0 on success, non-zero on a bad/duplicate/non-increasing version.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from check_release_version import (  # noqa: E402
    top_release_span,
    top_release_version,
    unreleased_body,
    unreleased_span,
)

from roundtable.updater import normalize_version, parse_version  # noqa: E402

_VERSION_FILE = _REPO / "roundtable" / "_version.py"
_CHANGELOG = _REPO / "CHANGELOG.md"

_VERSION_ASSIGN_RE = re.compile(r"""(__version__\s*=\s*["'])([^"']+)(["'])""")
_GROUP_RE = re.compile(r"^###[ \t]+(?P<name>\S.*?)[ \t]*$", re.MULTILINE)
# Keep a Changelog's group order, mirroring the authoring guide at the top of
# CHANGELOG.md. Only used to place a group the release does not have yet.
_GROUP_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")


class ReleaseError(Exception):
    """A release bump could not proceed (bad version, non-increasing, bad file)."""


def bump_version_file(text: str, new_version: str) -> str:
    """Return ``_version.py`` text with ``__version__`` set to ``new_version``."""
    if _VERSION_ASSIGN_RE.search(text) is None:
        raise ReleaseError(f"no __version__ assignment found in {_VERSION_FILE}")
    return _VERSION_ASSIGN_RE.sub(rf"\g<1>{new_version}\g<3>", text, count=1)


def current_version_of(text: str) -> str:
    match = _VERSION_ASSIGN_RE.search(text)
    if match is None:
        raise ReleaseError(f"no __version__ assignment found in {_VERSION_FILE}")
    return match.group(2)


def promote_changelog(text: str, new_version: str, today: str) -> str:
    """Move the ``[Unreleased]`` body under a new ``## [vX.Y.Z] - <date>`` heading.

    ``[Unreleased]`` is kept (now empty) for the next cycle; whatever was under it
    becomes the body of the released version.
    """
    try:
        body_start, body_end = unreleased_span(text)
    except ValueError as err:
        raise ReleaseError(str(err)) from err
    unreleased = text[body_start:body_end].strip("\n")

    released = f"## [v{new_version}] - {today}"
    block = f"\n\n{released}\n"
    if unreleased:
        block += f"\n{unreleased}\n"
    return text[:body_start] + block + "\n" + text[body_end:].lstrip("\n")


def _group_sections(body: str) -> list[tuple[str, str]]:
    """Split a changelog section body into ordered ``(group, entries)`` pairs.

    Entries stay opaque text, so a wrapped or multi-line note survives untouched.
    Anything before the first ``###`` heading is returned under the empty name.
    """
    matches = list(_GROUP_RE.finditer(body))
    if not matches:
        return [("", body)] if body.strip() else []
    lead = body[: matches[0].start()]
    sections = [("", lead)] if lead.strip() else []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append((match.group("name"), body[match.end() : end]))
    return sections


def _insertion_point(sections: list[tuple[str, str]], name: str) -> int:
    """Index that keeps ``_GROUP_ORDER``; an unrecognised group goes last."""
    if name not in _GROUP_ORDER:
        return len(sections)
    rank = _GROUP_ORDER.index(name)
    for index, (existing, _) in enumerate(sections):
        if existing in _GROUP_ORDER and _GROUP_ORDER.index(existing) > rank:
            return index
    return len(sections)


def _merge_groups(
    release: list[tuple[str, str]], pending: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Append each pending group's entries to the release's group of the same name."""
    merged = list(release)
    for name, entries in pending:
        for index, (existing, body) in enumerate(merged):
            if existing == name:
                merged[index] = (existing, f"{body.rstrip()}\n{entries.strip()}\n")
                break
        else:
            merged.insert(_insertion_point(merged, name), (name, entries))
    return merged


def _render_groups(sections: list[tuple[str, str]]) -> str:
    """Render ``(group, entries)`` pairs as the body under a ``##`` heading."""
    blocks = [
        f"### {name}\n{entries.strip()}\n" if name else f"{entries.strip()}\n"
        for name, entries in sections
        if entries.strip()
    ]
    return "\n\n" + "\n".join(blocks) + "\n" if blocks else "\n\n"


def fold_unreleased(text: str) -> str:
    """Move the ``[Unreleased]`` notes into the newest release, group by group."""
    pending_start, pending_end = unreleased_span(text)
    if not text[pending_start:pending_end].strip():
        return text
    release_start, release_end = top_release_span(text)
    if pending_end > release_start:
        raise ReleaseError(
            "CHANGELOG.md has [Unreleased] below the newest release heading; "
            "promote expects the pending section first."
        )
    merged = _render_groups(
        _merge_groups(
            _group_sections(text[release_start:release_end]),
            _group_sections(text[pending_start:pending_end]),
        )
    )
    # Rewrite the later span first so the pending offsets stay valid.
    text = text[:release_start] + merged + text[release_end:]
    return text[:pending_start] + "\n\n" + text[pending_end:]


def plan_promote() -> str:
    """Compute the changelog with ``[Unreleased]`` folded into the pending release."""
    version = current_version_of(_VERSION_FILE.read_text(encoding="utf-8")).split("+", 1)[0]
    text = _CHANGELOG.read_text(encoding="utf-8")
    try:
        heading = top_release_version(text)
    except ValueError as err:
        raise ReleaseError(str(err)) from err
    if heading != version:
        raise ReleaseError(
            f"_version.py is {version} but the newest CHANGELOG heading is v{heading}. "
            "promote folds notes into a heading that already exists; run "
            "'python scripts/release.py bump <version>' to cut a new one."
        )
    return fold_unreleased(text)


def plan_bump(
    new_version: str, *, today: str, require_release_notes: bool = False
) -> tuple[str, str]:
    """Compute both new file contents (no writes). Validates version + monotonicity."""
    target = normalize_version(new_version)  # bare X.Y.Z (accepts a leading v)

    version_text = _VERSION_FILE.read_text(encoding="utf-8")
    changelog_text = _CHANGELOG.read_text(encoding="utf-8")
    current = current_version_of(version_text)
    current_parsed = parse_version(current.split("+", 1)[0])
    target_parsed = parse_version(target)
    if current_parsed is not None and target_parsed is not None and target_parsed <= current_parsed:
        raise ReleaseError(
            f"target {target} is not greater than current {current}. "
            "A release must strictly increase the version."
        )
    new_version_text = bump_version_file(version_text, target)
    new_changelog = promote_changelog(changelog_text, target, today)
    if require_release_notes and not unreleased_body(changelog_text):
        raise ReleaseError("CHANGELOG.md [Unreleased] has no release notes to promote")
    return new_version_text, new_changelog


def _release_date(value: str) -> str:
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError as err:
        raise argparse.ArgumentTypeError("date must be an ISO date (YYYY-MM-DD)") from err


def _run_promote(*, dry_run: bool) -> int:
    """Fold pending notes into the release already bumped in ``_version.py``."""
    try:
        new_changelog = plan_promote()
    except ReleaseError as err:
        print(f"release: {err}", file=sys.stderr)
        return 1
    if new_changelog == _CHANGELOG.read_text(encoding="utf-8"):
        print("release: CHANGELOG.md [Unreleased] is already empty — nothing to promote.")
        return 0
    if dry_run:
        print("release: dry run — would fold [Unreleased] into the newest release heading.")
        return 0
    _CHANGELOG.write_text(new_changelog, encoding="utf-8", newline="\n")
    print("release: folded [Unreleased] into the newest release heading. Review the diff.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release helper for roundtable.")
    sub = parser.add_subparsers(dest="command", required=True)
    bump = sub.add_parser("bump", help="bump _version.py + promote CHANGELOG.md together")
    bump.add_argument("version", help="target version, e.g. 1.1.0 (a leading 'v' is accepted)")
    bump.add_argument(
        "--dry-run", action="store_true", help="print the plan without writing any file"
    )
    promote = sub.add_parser(
        "promote", help="fold [Unreleased] into the release already bumped in _version.py"
    )
    promote.add_argument(
        "--dry-run", action="store_true", help="print the plan without writing any file"
    )
    manual = sub.add_parser(
        "manual-release", help="prepare a local release and run the repository gates"
    )
    manual.add_argument("version", help="exact target version, e.g. 1.1.0")
    manual.add_argument(
        "--date",
        required=True,
        type=_release_date,
        help="release date in deterministic YYYY-MM-DD form",
    )
    args = parser.parse_args(argv)

    if args.command == "promote":
        return _run_promote(dry_run=args.dry_run)

    today = args.date if args.command == "manual-release" else datetime.date.today().isoformat()
    try:
        new_version_text, new_changelog = plan_bump(
            args.version,
            today=today,
            require_release_notes=args.command == "manual-release",
        )
    except ReleaseError as err:
        print(f"release: {err}", file=sys.stderr)
        return 1

    target = normalize_version(args.version)
    if args.command == "bump" and args.dry_run:
        print(f"release: dry run — would set _version.py to {target} and add CHANGELOG heading:")
        print(f'  roundtable/_version.py  __version__ = "{target}"')
        print(f"  CHANGELOG.md               ## [v{target}] - {today}")
        return 0

    _VERSION_FILE.write_text(new_version_text, encoding="utf-8", newline="\n")
    _CHANGELOG.write_text(new_changelog, encoding="utf-8", newline="\n")
    if args.command == "bump":
        print(
            f"release: bumped to {target} (both files). Review the diff, then open the release PR."
        )
        return 0

    print(f"release: prepared {target}; running python scripts/gates.py", flush=True)
    gate_result = subprocess.run([sys.executable, "scripts/gates.py"], cwd=_REPO)
    if gate_result.returncode != 0:
        print(
            f"release: preflight failed with exit code {gate_result.returncode}; "
            "the release edits remain in the worktree for inspection.",
            file=sys.stderr,
        )
        return gate_result.returncode
    print(
        f"release: local preparation passed on {sys.platform}; this does not validate "
        "other CI platforms or publish the package.\n"
        "release: next, review the diff and open a release PR; require the GitHub CI "
        "checks for Python 3.12 and 3.13 to pass before merge.\n"
        f"release: publishing {target} is a separate maintainer action."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
