"""Unit gate for the uniform-focusing context builder.

Covers the low-signal glob matcher, the per-file diff cap (pattern-before-size),
the byte-exact passthrough invariant, and the three gitContextMode renderings.
"""

from __future__ import annotations

from roundtable.bundle import resolve_bundle
from roundtable.context import builder as cb
from roundtable.graph import model as wc

_CONFIG = wc.get_configuration(resolve_bundle("inspectorx"))


def _entry(mode=None):
    return wc.GraphEntry(
        key="X",
        prompt_path="x.md",
        edges=(),
        emoji="x",
        git_context_mode=mode,
    )


def _git_context(diff_body: str, base="origin/main") -> str:
    return (
        f"=== Repository: demo | Base: {base} | HEAD: feature ===\n"
        "-- Changed Files --\n"
        "calc.py\n"
        "-- Diff --\n" + diff_body
    )


_SMALL_DIFF = (
    "diff --git a/calc.py b/calc.py\n"
    "@@ -1,3 +1,4 @@\n"
    " def divide(a, b):\n"
    "+    # guard\n"
    "     return a / b\n"
)


# ─── low-signal glob matcher ────────────────────────────────────────────────
def test_low_signal_positive_matches():
    for p in [
        "src/appsettings.Production.json",
        "appsettings.json",
        "web/i18n/en.json",
        "app/locales/fr/messages.po",
        "Resources/Strings.resx",
        "ui/Strings.locstring",
        "service/bin/Debug/net8.0/app.dll",
        "service/obj/Release/net6.0/x.cs",
        "web/app.min.js",
        "web/styles.min.css",
        "web/bundle.js.map",
        "gen/Model.designer.cs",
        "gen/Thing.generated.ts",
        "a/b/checkpoints/001.md",
        "logs/trace/run/summary.md",
        "CHANGELOG.md",
        "packages/CHANGELOG-2025.md",
        "pkg/dist/index.js",
        "node_modules/lib/index.js",
        "tests/__snapshots__/Comp.test.tsx.snap",
    ]:
        assert cb.is_low_signal_path(p), f"expected low-signal: {p}"


def test_low_signal_negative_matches():
    for p in [
        "src/calc.py",
        "src/service.ts",
        "config.json",  # not appsettings*
        "src/Model.cs",  # not *.designer/generated
        "docs/guide.md",  # CHANGELOG only, not any .md
        "src/distribute.py",  # 'dist' must be a path segment
    ]:
        assert not cb.is_low_signal_path(p), f"expected NOT low-signal: {p}"


def test_windows_backslash_paths_normalized():
    assert cb.is_low_signal_path(r"pkg\dist\index.js")


# ─── diff header parsing ────────────────────────────────────────────────────
def test_parse_diff_header_paths():
    assert cb.parse_diff_header_paths("diff --git a/src/x.py b/src/x.py") == (
        "src/x.py",
        "src/x.py",
    )
    assert cb.parse_diff_header_paths('diff --git "a/has space.py" "b/has space.py"') == (
        "has space.py",
        "has space.py",
    )
    assert cb.parse_diff_header_paths("not a header") is None


def test_extract_base_ref_and_marker():
    gc = _git_context(_SMALL_DIFF, base="abc123")
    assert cb.extract_base_ref(gc) == "abc123"
    assert cb.extract_base_ref("no header") == "HEAD~1"
    assert cb.find_diff_body_start(gc) > 0
    assert cb.find_diff_body_start("no marker") == -1


# ─── per-file diff cap ──────────────────────────────────────────────────────
def test_small_diff_passes_through_byte_exact():
    gc = _git_context(_SMALL_DIFF)
    out = cb.apply_per_file_diff_cap(gc)
    # No suppression → output identical to input (oracle-parity invariant).
    assert out == gc


def test_over_cap_file_replaced_with_marker():
    # drift-sync 98cc85e: cap is 512 KB and the backstop preserves change SHAPE
    # (file header + every @@ hunk header) while dropping patch bodies.
    line_count = (cb.MAX_PER_FILE_DIFF_BYTES // 3) + 1000  # "+x\n" = 3 bytes each
    big_body = (
        "diff --git a/huge.py b/huge.py\n"
        "@@ -1,1 +1,100000 @@\n" + ("+x\n" * line_count)  # > 512 KiB
    )
    gc = _git_context(big_body)
    out = cb.apply_per_file_diff_cap(gc)
    assert f"patch bodies dropped (over {cb.MAX_PER_FILE_DIFF_BYTES}-byte cap)" in out
    assert "huge.py" in out
    assert "+x\n+x" not in out  # body actually dropped
    assert "@@ -1,1 +1,100000 @@" in out  # hunk header (change shape) preserved


def test_under_512kb_file_passes_through_uncapped():
    # A 90 KB hand-written file that the OLD 50 KB cap would have omitted is now
    # delivered WHOLE (the core motivation for raising the cap).
    body = (
        "diff --git a/proc.sql b/proc.sql\n"
        "@@ -1,1 +1,30000 @@\n" + ("+x\n" * 30000)  # ~90 KB, under the 512 KB cap
    )
    gc = _git_context(body)
    out = cb.apply_per_file_diff_cap(gc)
    assert "patch bodies dropped" not in out
    assert out == gc  # byte-exact passthrough


def test_low_signal_pattern_beats_size_check():
    # Small file but low-signal path → low-signal marker, not size marker.
    body = (
        "diff --git a/src/appsettings.json b/src/appsettings.json\n"
        "@@ -1,1 +1,2 @@\n"
        '+  "Flag": true\n'
    )
    gc = _git_context(body)
    out = cb.apply_per_file_diff_cap(gc)
    assert "low-signal path: src/appsettings.json" in out
    assert "patch bodies dropped" not in out


def test_cap_preserves_header_and_recovery_ref():
    body = "diff --git a/web/app.min.js b/web/app.min.js\n@@ -1 +1 @@\n+minified\n"
    gc = _git_context(body, base="origin/release")
    out = cb.apply_per_file_diff_cap(gc)
    assert "git diff -M origin/release...HEAD -- web/app.min.js" in out
    assert "=== Repository: demo" in out  # header retained


def test_diff_cap_stats_counts_low_signal_and_oversized():
    line_count = (cb.MAX_PER_FILE_DIFF_BYTES // 3) + 1000
    body = (
        _SMALL_DIFF  # kept
        + "diff --git a/src/appsettings.json b/src/appsettings.json\n"
        '@@ -1,1 +1,2 @@\n+  "Flag": true\n'  # low-signal
         + "diff --git a/huge.py b/huge.py\n@@ -1,1 +1,1 @@\n" + ("+x\n" * line_count)  # oversized
    )
    stats = cb.diff_cap_stats(_git_context(body))
    assert stats.low_signal == 1
    assert stats.oversized == 1
    assert stats.truncated_files == 2


def test_diff_cap_stats_zero_when_nothing_capped():
    stats = cb.diff_cap_stats(_git_context(_SMALL_DIFF))
    assert stats.truncated_files == 0


# ─── gitContextMode rendering ───────────────────────────────────────────────
def test_render_full_mode():
    gc = _git_context(_SMALL_DIFF)
    out = cb.render_git_context_section(_entry("full"), gc)
    assert out.startswith("## Git Context\n")
    assert "return a / b" in out


def test_render_omit_mode():
    out = cb.render_git_context_section(_entry("omit"), _git_context(_SMALL_DIFF))
    assert out.startswith("## Git Context [OMITTED]")
    assert "return a / b" not in out


def test_render_changed_files_only_mode():
    out = cb.render_git_context_section(_entry("changed-files-only"), _git_context(_SMALL_DIFF))
    assert out.startswith("## Git Context [SUMMARY: changed-files-only")
    assert "### calc.py" in out
    assert "@@ -1,3 +1,4 @@" in out  # hunk header retained
    assert "+    # guard" not in out  # patch body omitted
    assert "full diff bodies omitted" in out


def test_render_none_mode_defaults_to_full():
    out = cb.render_git_context_section(_entry(None), _git_context(_SMALL_DIFF))
    assert out.startswith("## Git Context\n")


def test_real_historian_entry_uses_changed_files_only():
    historian = wc.get_entry("Historian", _CONFIG)
    out = cb.render_git_context_section(historian, _git_context(_SMALL_DIFF))
    assert out.startswith("## Git Context [SUMMARY: changed-files-only")
