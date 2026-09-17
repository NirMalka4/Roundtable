"""Unit tests for the DeterministicPreScan base anti-pattern scan.

The expected strings are pinned verbatim so the scan output stays byte-stable
against the reference implementation.
"""

from __future__ import annotations

from roundtable.configs.inspectorx.plugins.prescan import extract_added_lines, run_prescan


def _diff(*added: str) -> str:
    """Build a minimal unified diff whose added lines are ``added``."""
    body = "\n".join(f"+{line}" for line in added)
    return f"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n{body}"


def test_clean_diff_reports_no_issues():
    assert run_prescan(_diff("x = 1", "y = 2")) == (
        "Deterministic Pre-Scan: No obvious issues found."
    )


def test_debug_print_console_log_flagged():
    assert run_prescan(_diff("console.log('hi')")) == (
        "Deterministic Pre-Scan Findings:\n- Found debug print statements in added code"
    )


def test_debug_print_python_print_flagged():
    out = run_prescan(_diff("print('debug')"))
    assert "- Found debug print statements in added code" in out


def test_todo_fixme_flagged():
    out = run_prescan(_diff("# TODO: refactor"))
    assert out == ("Deterministic Pre-Scan Findings:\n- Found TODO/FIXME comments in added code")
    out2 = run_prescan(_diff("# FIXME later"))
    assert "- Found TODO/FIXME comments in added code" in out2


def test_hardcoded_secret_patterns_flagged():
    for line in ("password = 'p'", "secret: abc", "API_KEY=xyz"):
        out = run_prescan(_diff(line))
        assert "- Found potential hardcoded secrets in added code" in out, line


def test_all_three_findings_in_source_order():
    out = run_prescan(_diff("console.log(x)", "# TODO", "password = 1"))
    assert out == (
        "Deterministic Pre-Scan Findings:\n"
        "- Found debug print statements in added code\n"
        "- Found TODO/FIXME comments in added code\n"
        "- Found potential hardcoded secrets in added code"
    )


def test_removed_and_context_lines_ignored():
    # A secret only on a removed/context line must NOT be flagged (added-only scan).
    diff = (
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,1 @@\n"
        "-password = 'old'\n"
        " print('context')\n"
        "+clean = 1"
    )
    assert run_prescan(diff) == "Deterministic Pre-Scan: No obvious issues found."


def test_extract_added_lines_excludes_plusplusplus_header():
    diff = "+++ b/new.py\n+real_added = 1"
    assert extract_added_lines(diff) == "real_added = 1"


def test_prescan_always_non_empty():
    """The base scan always satisfies a consumer's hard dep (never empty)."""
    assert run_prescan("") != ""
    assert run_prescan("no diff markers here") != ""
