"""Context: always-focused Git Context rendering with a UNIFORM focusing policy.

Renders the Git Context with a uniform focusing policy — the per-file diff cap,
low-signal-path suppression, and the three ``git_context_mode`` renderings
(full / changed-files-only / omit).

The context builder applies a UNIFORM focusing policy (per-file byte cap +
low-signal-path suppression) to EVERY agent; there is no per-agent diff
slicing. Dispatch is on ``git_context_mode`` (full / changed-files-only / omit)
only.

FIDELITY NOTE: rather than depend on a globbing library, this ships a
small glob->regex translator (``_glob_to_regex``) that supports the exact
construct set used by ``LOW_SIGNAL_DIFF_PATH_PATTERNS`` (``**``, ``*``, ``?``,
brace alternation ``{a,b}``). It is NOT a general picomatch reimplementation; the
``test_low_signal`` suite pins the patterns we depend on.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from roundtable.graph import GraphEntry

# Per-file diff size cap (bytes). drift-sync 98cc85e: raised 50 KB -> 512 KB so
# realistic hand-written files (large stored procedures, migrations) reach agents
# WHOLE — the 50 KB value omitted an 83 KB primary stored proc from ~every agent
# in a live review. Generated bloat is removed earlier by the low-signal denylist,
# so this cap is only a pathology backstop. The global priority-aware truncate()
# still bounds total context, so raising it does not uncap the payload.
MAX_PER_FILE_DIFF_BYTES = 512 * 1024

# Patterns whose diffs are suppressed from agent context as low-signal.
LOW_SIGNAL_DIFF_PATH_PATTERNS: tuple[str, ...] = (
    "**/appsettings*.json",
    "**/{i18n,locale,locales,lang,translation,translations}/**",
    "**/*.{resx,locstring}",
    "**/bin/{Debug,Release,net*}/**",
    "**/obj/{Debug,Release,net*}/**",
    "**/*.min.{js,css}",
    "**/*.{js,css}.map",
    "**/*.{g,designer,generated}.{cs,ts,js}",
    "**/checkpoints/**",
    "**/trace/**/*.md",
    "**/CHANGELOG*.md",
    "**/dist/**",
    "**/node_modules/**",
    "**/__snapshots__/**",
)

_DIFF_HEADER_PREFIX = "diff --git "


# ─── glob matching (picomatch subset) ───────────────────────────────────────
def _expand_braces(pattern: str) -> list[str]:
    """Expand a single, non-nested ``{a,b,c}`` group into alternatives.

    Only one level of bracing appears in LOW_SIGNAL_DIFF_PATH_PATTERNS, but a
    pattern can contain two sequential groups (e.g. ``*.{g,designer}.{cs,ts}``),
    so we expand left-to-right recursively.
    """
    start = pattern.find("{")
    if start < 0:
        return [pattern]
    end = pattern.find("}", start)
    if end < 0:
        return [pattern]
    pre, options, post = pattern[:start], pattern[start + 1 : end], pattern[end + 1 :]
    results: list[str] = []
    for opt in options.split(","):
        for tail in _expand_braces(post):
            results.append(pre + opt + tail)
    return results


def _glob_to_regex(glob: str) -> str:
    """Translate one (brace-free) glob into a regex source string.

    Semantics (picomatch-compatible for our pattern set):
      ``**/`` -> optional leading dir segments; ``**`` -> any chars incl ``/``;
      ``*`` -> any chars except ``/``; ``?`` -> one char except ``/``.
    """
    out: list[str] = ["^"]
    i = 0
    n = len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":
                # globstar
                if i + 2 < n and glob[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return "".join(out)


def _compile_low_signal() -> re.Pattern[str]:
    alts: list[str] = []
    for pat in LOW_SIGNAL_DIFF_PATH_PATTERNS:
        for expanded in _expand_braces(pat):
            alts.append(_glob_to_regex(expanded))
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    return re.compile("|".join(alts), flags)


_LOW_SIGNAL_RE = _compile_low_signal()


def is_low_signal_path(path: str) -> bool:
    return _LOW_SIGNAL_RE.match(normalize_path(path)) is not None


# ─── diff header parsing ────────────────────────────
def normalize_path(p: str) -> str:
    return p.replace("\\", "/")


def _unquote(s: str) -> str:
    return s.replace("\\\\", "\\").replace('\\"', '"').replace("\\t", "\t").replace("\\n", "\n")


def parse_diff_header_paths(header: str) -> tuple[str, str] | None:
    """Return (a_path, b_path) from a ``diff --git`` header, or None."""
    if not header.startswith(_DIFF_HEADER_PREFIX):
        return None
    after = header[len(_DIFF_HEADER_PREFIX) :].strip()
    if not after:
        return None
    quoted = re.match(r'^"a/(.+?)"\s+"b/(.+?)"$', after)
    if quoted:
        return _unquote(quoted.group(1)), _unquote(quoted.group(2))
    if not after.startswith("a/"):
        return None
    sep = after.rfind(" b/")
    if sep < 0:
        return None
    a, b = after[2:sep], after[sep + 3 :]
    if not a or not b:
        return None
    return a, b


# ─── git-context header helpers ─────────────────────────────────────────────
def extract_base_ref(git_context: str) -> str:
    m = re.search(r"Base:\s+(\S+)\s+\|", git_context)
    return m.group(1) if m else "HEAD~1"


def find_diff_body_start(git_context: str) -> int:
    marker = "\n-- Diff --\n"
    idx = git_context.find(marker)
    return idx + len(marker) if idx >= 0 else -1


def _split_file_blocks(body: str) -> list[str]:
    """Split a diff body before each ``diff --git`` line (keeps the line)."""
    return re.split(r"(?m)^(?=diff --git )", body)


def _classify_diff_block(part: str) -> str:
    """Full-mode per-file cap decision for one block: ``keep`` | ``low_signal``
    | ``oversized``. Single source of truth shared by the renderer
    (:func:`_cap_diff_blocks`) and the counter (:func:`diff_cap_stats`) so the
    two can never drift on what counts as suppressed."""
    if not part.startswith(_DIFF_HEADER_PREFIX):
        return "keep"
    parsed = parse_diff_header_paths(part.split("\n", 1)[0])
    if parsed and is_low_signal_path(parsed[1]):
        return "low_signal"
    if len(part.encode("utf-8")) > MAX_PER_FILE_DIFF_BYTES:
        return "oversized"
    return "keep"


@dataclass(frozen=True)
class DiffCapStats:
    """How many files the full-mode per-file cap suppresses, by reason."""

    low_signal: int
    oversized: int

    @property
    def truncated_files(self) -> int:
        return self.low_signal + self.oversized


def diff_cap_stats(git_context: str) -> DiffCapStats:
    """Count files whose patch bodies the full-mode cap drops (low-signal +
    oversized).

    This is a **session-constant** property of the raw diff — the same cap runs
    over the same input for every full-mode agent — so it can be computed once
    and surfaced in the session header without breaking the byte-equality
    invariant across agents.
    """
    diff_start = find_diff_body_start(git_context)
    body = git_context[diff_start:] if diff_start >= 0 else git_context
    low_signal = oversized = 0
    for part in _split_file_blocks(body):
        kind = _classify_diff_block(part)
        if kind == "low_signal":
            low_signal += 1
        elif kind == "oversized":
            oversized += 1
    return DiffCapStats(low_signal=low_signal, oversized=oversized)


# ─── per-file diff cap (full mode) ──────────────────────────────────────────
def _cap_diff_blocks(body: str, head: str, base_ref: str) -> str:
    parts = _split_file_blocks(body)
    truncated_any = False
    suppressed_by_pattern = 0
    suppressed_by_size = 0
    capped: list[str] = []

    for part in parts:
        kind = _classify_diff_block(part)
        if kind == "keep":
            capped.append(part)
            continue
        first_line = part.split("\n", 1)[0]
        parsed = parse_diff_header_paths(first_line)
        file = (
            parsed[1]
            if parsed
            else first_line.replace(_DIFF_HEADER_PREFIX, "").strip() or "unknown"
        )
        part_bytes = len(part.encode("utf-8"))
        truncated_any = True

        if kind == "low_signal":
            suppressed_by_pattern += 1
            capped.append(
                f"{first_line}\n"
                f"[Roundtable: file diff omitted (low-signal path: {file}; "
                f"{part_bytes} bytes). "
                f"Use `git diff -M {base_ref}...HEAD -- {file}` to fetch full content.]\n"
            )
            continue

        suppressed_by_size += 1
        # drift-sync 98cc85e: preserve change SHAPE for an over-cap (but
        # high-signal) file — emit the file header + every `@@` hunk header
        # (line ranges), drop only the patch bodies. Agents still see WHICH
        # ranges changed (no blind spot, no body-slice that could hide a later
        # critical hunk), with a fetch hint for full detail.
        hunk_headers = [ln for ln in part.split("\n") if ln.startswith("@@ ")]
        if hunk_headers:
            shape = (
                "Hunk headers (patch bodies dropped):\n"
                + "\n".join(f"  {h}" for h in hunk_headers)
                + "\n"
            )
        else:
            shape = "(no hunk headers — likely binary, rename, or mode-only change)\n"
        capped.append(
            f"{first_line}\n"
            f"[Roundtable: patch bodies dropped (over {MAX_PER_FILE_DIFF_BYTES}-byte cap). "
            f"path: {file}; original: {part_bytes} bytes. "
            f"Use `git diff -M {base_ref}...HEAD -- {file}` to fetch full content.]\n"
            f"{shape}"
        )

    if truncated_any:
        print(
            f"[Roundtable][context] Suppressed file diffs: "
            f"{suppressed_by_pattern} by low-signal path, "
            f"{suppressed_by_size} by {MAX_PER_FILE_DIFF_BYTES}-byte cap.",
            file=sys.stderr,
        )
        return head + "".join(capped)
    # No change → preserve byte-exact input (oracle parity for typical PRs).
    return head + body


def apply_per_file_diff_cap(git_context: str, base_ref_override: str | None = None) -> str:
    base_ref = base_ref_override or extract_base_ref(git_context)
    diff_start = find_diff_body_start(git_context)
    if diff_start < 0:
        return _cap_diff_blocks(git_context, "", base_ref)
    head = git_context[:diff_start]
    body = git_context[diff_start:]
    return _cap_diff_blocks(body, head, base_ref)


# ─── changed-files-only summary ─────────────────────────────────────────────
def summarize_git_context_changed_files_only(git_context: str) -> str:
    base_ref = extract_base_ref(git_context)
    diff_start = find_diff_body_start(git_context)
    if diff_start < 0:
        return (
            git_context
            + "\n\n[Roundtable: Git Context lacks the standard `-- Diff --` marker; "
            + "emitted as-is for changed-files-only mode.]"
        )
    head = git_context[:diff_start]
    body = git_context[diff_start:]

    file_blocks = [b for b in _split_file_blocks(body) if b.strip()]
    summaries: list[str] = []
    for block in file_blocks:
        first_line = block.split("\n", 1)[0]
        m = re.match(r"^diff --git a/(.+?) b/(.+?)$", first_line)
        file = m.group(2) if m else first_line.replace(_DIFF_HEADER_PREFIX, "").strip() or "unknown"
        hunks = [ln for ln in block.split("\n") if ln.startswith("@@ ")]
        if not hunks:
            summaries.append(
                f"### {file}\n  (no hunks — likely binary, rename, or mode-only change)"
            )
        else:
            summaries.append("### " + file + "\n" + "\n".join(f"  {h}" for h in hunks))

    summary_body = (
        "-- Hunk Summary (per file) --\n" + "\n\n".join(summaries)
        if summaries
        else "-- Hunk Summary (per file) --\n(no per-file diff blocks present)"
    )
    return (
        f"{head}{summary_body}\n\n"
        f"[Roundtable: full diff bodies omitted (gitContextMode='changed-files-only'). "
        f"Hunk headers above carry file:line ranges for overlap checks. "
        f"Use `git diff -M {base_ref}...HEAD -- <path>` to fetch a per-file patch when needed.]"
    )


# ─── public rendering surface ───────────────────────────────────────────────
def render_git_context_section(entry: GraphEntry, git_context: str) -> str:
    """Render the ``## Git Context`` section for one agent under the UNIFORM
    focusing policy. Dispatches on ``git_context_mode`` only."""
    safe = git_context or ""
    mode = entry.git_context_mode or "full"

    if mode == "omit":
        return (
            "## Git Context [OMITTED]\n"
            "[Roundtable: Git Context intentionally omitted for this agent "
            "(GraphEntry.gitContextMode='omit').]"
        )
    if mode == "changed-files-only":
        return (
            "## Git Context [SUMMARY: changed-files-only — per GraphEntry.gitContextMode]\n"
            + summarize_git_context_changed_files_only(safe)
        )
    return "## Git Context\n" + apply_per_file_diff_cap(safe)
