"""Release-coherence gate — `_version.py` must match the top CHANGELOG heading.

A release bumps ``roundtable/_version.py`` and promotes the ``[Unreleased]``
section of ``CHANGELOG.md`` to a matching ``## [vX.Y.Z]`` heading in the *same*
PR. This script proves the two agree, so a release cannot omit its changelog
entry (or vice-versa).

It reads the **authored** version string straight out of ``_version.py`` with a
regex (no import). In a source/editable tree the imported runtime value carries a
``+dev`` suffix — exactly where this gate runs — so it needs the raw authored
``X.Y.Z``. The comparison is on the bare ``X.Y.Z`` (a leading ``v`` on the changelog
heading is optional; a ``+local`` build suffix on the version is ignored).

The release-notes half only makes sense while a version is actually being
published: it demands that the release promoted its notes, so the published
heading describes everything in the artifact. Between releases ``[Unreleased]``
is *supposed* to accumulate, so that half must not fire on an ordinary branch.

This script decides that for itself. Because the check above pins
``_version.py`` to the top heading, a branch is a pending release exactly when
its authored version is **ahead of trunk's** — a local, offline question that
needs no feed. So the everyday pre-flight catches unpromoted notes at author
time. ``--require-empty-unreleased`` forces the same strictness when a caller
already knows it is preparing a release.

Usage:  python scripts/check_release_version.py [--require-empty-unreleased]
Exit 0 when they match, 1 on drift (with an actionable message).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from roundtable.updater import parse_version  # noqa: E402

_VERSION_FILE = _REPO / "roundtable" / "_version.py"
_CHANGELOG = _REPO / "CHANGELOG.md"
# Trunk refs to read the comparison version from, most authoritative first.
# `git show` needs a repo-relative path, so it cannot reuse _VERSION_FILE above.
_TRUNK_REFS = ("origin/main", "main")
_TRUNK_VERSION_PATH = "roundtable/_version.py"

_VERSION_RE = re.compile(r"""^__version__\s*=\s*["'](?P<v>[^"']+)["']""", re.MULTILINE)
# A released heading: `## [vX.Y.Z] - DATE`. `[Unreleased]` is deliberately skipped.
_HEADING_RE = re.compile(r"^##\s*\[v?(?P<v>\d+\.\d+\.\d+)\]", re.MULTILINE)
_UNRELEASED_RE = re.compile(r"^##\s*\[Unreleased\]\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s*\[", re.MULTILINE)


def authored_version(text: str) -> str:
    """Return the bare ``X.Y.Z`` authored in ``_version.py`` (drops any ``+local``)."""
    match = _VERSION_RE.search(text)
    if match is None:
        raise ValueError(f"no __version__ assignment found in {_VERSION_FILE}")
    return match.group("v").split("+", 1)[0]


def top_release_version(text: str) -> str:
    """Return the newest released ``X.Y.Z`` heading in the changelog (skips Unreleased)."""
    match = _HEADING_RE.search(text)
    if match is None:
        raise ValueError(
            f"no '## [vX.Y.Z]' release heading found in {_CHANGELOG} "
            "(is the release still under [Unreleased]?)"
        )
    return match.group("v")


def unreleased_span(text: str) -> tuple[int, int]:
    """Character span of the body under ``## [Unreleased]``, excluding its heading.

    Shared with ``release.py``, which moves exactly this span under the new
    version's heading — so "the [Unreleased] section" is defined once.
    """
    match = _UNRELEASED_RE.search(text)
    if match is None:
        raise ValueError(f"no '## [Unreleased]' heading found in {_CHANGELOG}")
    start = match.end()
    following = _SECTION_RE.search(text[start:])
    return start, (start + following.start()) if following else len(text)


def unreleased_body(text: str) -> str:
    """The notes sitting under ``[Unreleased]`` — empty right after a release."""
    start, end = unreleased_span(text)
    return text[start:end].strip()


def top_release_span(text: str) -> tuple[int, int]:
    """Character span of the body under the newest ``## [vX.Y.Z]`` heading.

    The mirror of :func:`unreleased_span`, so both sections are located by one
    definition; ``release.py promote`` moves notes from the first into the second.
    """
    match = _HEADING_RE.search(text)
    if match is None:
        raise ValueError(f"no '## [vX.Y.Z]' release heading found in {_CHANGELOG}")
    heading_end = text.find("\n", match.end())
    start = len(text) if heading_end == -1 else heading_end
    following = _SECTION_RE.search(text[start + 1 :])
    return start, (start + 1 + following.start()) if following else len(text)


def trunk_version() -> tuple[str, str] | None:
    """Trunk's authored version and the ref it came from, or ``None`` if unreadable.

    A shallow clone or a repository without the remote has nothing to compare
    against, so the caller abstains rather than guessing.
    """
    for ref in _TRUNK_REFS:
        proc = subprocess.run(
            ["git", "show", f"{ref}:{_TRUNK_VERSION_PATH}"],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            continue
        try:
            return authored_version(proc.stdout), ref
        except ValueError:
            return None
    return None


def publishes_a_new_version(version: str) -> tuple[bool, str]:
    """Whether merging this branch would publish ``version``, and why.

    ``_version.py`` is pinned to the top changelog heading, so a version ahead of
    trunk's is a release that has not shipped yet.
    """
    found = trunk_version()
    if found is None:
        return False, "no trunk ref to compare against (shallow clone or no remote)"
    trunk, ref = found
    here, there = parse_version(version), parse_version(trunk)
    if here is None or there is None:
        return False, f"{ref} version {trunk!r} is not comparable SemVer"
    if here > there:
        return True, f"{version} is ahead of {ref} ({trunk}) — merging it publishes"
    return False, f"{version} does not lead {ref} ({trunk}) — this branch publishes nothing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release-coherence gate.")
    parser.add_argument(
        "--require-empty-unreleased",
        action="store_true",
        help="also fail when [Unreleased] still holds notes (only valid while publishing)",
    )
    args = parser.parse_args(argv)

    version = authored_version(_VERSION_FILE.read_text(encoding="utf-8"))
    changelog_text = _CHANGELOG.read_text(encoding="utf-8")
    changelog = top_release_version(changelog_text)
    if version != changelog:
        print(
            f"DRIFT  _version.py={version}  !=  CHANGELOG top heading=v{changelog}\n"
            "  A release must bump both in the same PR. Fix whichever is stale:\n"
            f'    - roundtable/_version.py  __version__ = "{changelog}"\n'
            f"    - CHANGELOG.md               ## [v{version}] - <date>",
            file=sys.stderr,
        )
        return 1

    if args.require_empty_unreleased:
        require_empty, why = True, "--require-empty-unreleased was passed"
    else:
        require_empty, why = publishes_a_new_version(version)

    if require_empty:
        leftovers = unreleased_body(changelog_text)
        if leftovers:
            entries = [ln.strip() for ln in leftovers.splitlines() if ln.lstrip().startswith("- ")]
            sample = (entries[0] if entries else leftovers.splitlines()[0].strip())[:72]
            print(
                f"UNPROMOTED  publishing {version}, but CHANGELOG.md [Unreleased] still holds\n"
                f"  {len(entries)} note(s) — first: {sample!r}\n"
                f"  Why this is strict: {why}.\n"
                "  They ship inside the artifact while its own heading says nothing about them.\n"
                f"  Promote them into the v{version} heading they belong to:\n"
                "    python scripts/release.py promote",
                file=sys.stderr,
            )
            return 1

    print(f"OK  _version.py == CHANGELOG top heading == {version}")
    if require_empty:
        print(f"OK  CHANGELOG [Unreleased] is empty — the release promoted its notes ({why}).")
    else:
        print(f"OK  CHANGELOG [Unreleased] may accumulate — {why}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
