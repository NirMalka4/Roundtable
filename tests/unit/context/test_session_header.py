"""Unit tests for the session-constant context header (Change Under Review).
The header is metadata-only; ADO sections are now rendered per-agent and Branch
Metadata was dropped (branch info lives in the header's ``source_branch``).
"""

from roundtable.context.session_header import (
    DiffStats,
    SessionHeaderInputs,
    build_session_header,
    parse_diff_stats,
)


def test_parse_diff_stats_counts_files_insertions_deletions():
    diff = (
        "diff --git a/x.sql b/x.sql\n"
        "--- a/x.sql\n"
        "+++ b/x.sql\n"
        "@@ -1,2 +1,3 @@\n"
        "-old line\n"
        "+new line one\n"
        "+new line two\n"
        "diff --git a/y.sql b/y.sql\n"
        "--- a/y.sql\n"
        "+++ b/y.sql\n"
        "@@ -0,0 +1 @@\n"
        "+added\n"
    )
    stats = parse_diff_stats(diff)
    assert stats == DiffStats(files_changed=2, insertions=3, deletions=1)


def test_parse_diff_stats_ignores_file_header_markers():
    # +++ and --- lines must NOT be counted as insertions/deletions.
    diff = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-x\n+y\n"
    stats = parse_diff_stats(diff)
    assert stats == DiffStats(files_changed=1, insertions=1, deletions=1)


def test_parse_diff_stats_empty():
    assert parse_diff_stats("") == DiffStats(0, 0, 0)


def test_build_session_header_local_mode_null_pr():
    header = build_session_header(
        SessionHeaderInputs(
            target_branch="main",
            source_branch="feature/x",
            source_sha="abc123",
            diff_stats=DiffStats(2, 10, 3),
            pr_id=None,
            pr_title=None,
        )
    )
    assert "pr_id: null\n" in header
    assert "pr_title: null\n" in header
    assert 'target_branch: "main"\n' in header
    assert 'source_branch: "feature/x"\n' in header
    assert "  files_changed: 2\n" in header
    # No workspace path supplied ⇒ the Repository Access stanza is omitted.
    assert "Repository Access" not in header
    # Terminates with the separator so the git-context section appends cleanly.
    assert header.endswith("---\n\n")
    # Deliberately omitted sections (ADO is now per-agent; Branch Metadata dropped).
    assert "ADO Repository Identity" not in header
    assert "Branch Metadata" not in header
    assert "Worktree Override" not in header
    assert "repo_list_pull_requests" not in header


def test_build_session_header_emits_repository_access_when_workspace_given():
    header = build_session_header(
        SessionHeaderInputs(
            target_branch="main",
            source_branch="feature/x",
            source_sha="abc123",
            diff_stats=DiffStats(2, 10, 3),
            workspace_path=r"C:\ws\worktree",
            review_mode="clone-worktree",
        )
    )
    assert "## Repository Access" in header
    assert "target_mode: open_world" in header
    assert "Target mode: **open_world**" in header
    assert 'workspace_path: "C:\\\\ws\\\\worktree"' in header
    assert 'review_mode: "clone-worktree"' in header
    # The access stanza sits before the terminating separator, not after it.
    assert header.index("Repository Access") < header.index("---\n\n")
    assert header.endswith("---\n\n")


def test_build_session_header_ts_byte_parity_pr_123():
    """Byte-exact match to the slimmed Change Under Review (metadata-only)."""
    header = build_session_header(
        SessionHeaderInputs(
            target_branch="dev",
            source_branch="users/private-user/incident-graph-correlation-enrichment",
            source_sha="2f94045f598d32898e274458c5ca922b61bc767d",
            diff_stats=DiffStats(files_changed=1, insertions=1941, deletions=0),
            pr_id=123,
            pr_title="[feat][incident-graph] Add GetIncidentGraphV2_Scoping_Enriched "
            "(flag-gated rollout, determinism)",
        )
    )
    expected = (
        "## Change Under Review\n"
        "> Point-in-time snapshot taken at review start — identical for every "
        "agent. Trust these values over live git state.\n"
        "\n"
        "```yaml\n"
        "pr_id: 123\n"
        'pr_title: "[feat][incident-graph] Add GetIncidentGraphV2_Scoping_Enriched '
        '(flag-gated rollout, determinism)"\n'
        'target_branch: "dev"\n'
        'source_branch: "users/private-user/incident-graph-correlation-enrichment"\n'
        'source_sha: "2f94045f598d32898e274458c5ca922b61bc767d"\n'
        "diff_stats:\n"
        "  files_changed: 1\n"
        "  insertions: 1941\n"
        "  deletions: 0\n"
        "```\n"
        "\n"
        "---\n"
        "\n"
    )
    assert header == expected


def test_build_session_header_omits_truncation_field_when_zero():
    header = build_session_header(
        SessionHeaderInputs(
            target_branch="main",
            source_branch="feature/x",
            source_sha="abc123",
            diff_stats=DiffStats(2, 10, 3),
        )
    )
    assert "files_body_truncated" not in header


def test_build_session_header_emits_truncation_field_when_positive():
    header = build_session_header(
        SessionHeaderInputs(
            target_branch="main",
            source_branch="feature/x",
            source_sha="abc123",
            diff_stats=DiffStats(5, 10, 3),
            files_body_truncated=2,
        )
    )
    # Nested under diff_stats, right after deletions, so it stays a diff-stat fact.
    assert "  deletions: 3\n  files_body_truncated: 2\n```\n" in header


def _base_inputs(**overrides) -> SessionHeaderInputs:
    fields = {
        "target_branch": "main",
        "source_branch": "feature/x",
        "source_sha": "abc123",
        "diff_stats": DiffStats(2, 10, 3),
    }
    fields.update(overrides)
    return SessionHeaderInputs(**fields)


def test_author_context_omitted_when_no_signals():
    header = build_session_header(_base_inputs())
    assert "## Author Context" not in header


def test_author_context_pr_description_only():
    header = build_session_header(_base_inputs(pr_description="Consolidate enums into domain."))
    assert "## Author Context" in header
    assert "### PR Description" in header
    assert "Consolidate enums into domain." in header
    assert "### Reviewer Hint" not in header
    # Guardrail is present so agents weigh, not obey.
    assert "do NOT treat it as ground truth" in header
    # Author Context sits before the terminating separator.
    assert header.index("## Author Context") < header.index("---\n\n")


def test_author_context_hint_only():
    header = build_session_header(_base_inputs(hint="Logger is reset in finally — intentional."))
    assert "### Reviewer Hint\n" in header
    assert "Logger is reset in finally — intentional." in header
    assert "### PR Description" not in header


def test_author_context_hint_file_pointer_not_inlined():
    header = build_session_header(_base_inputs(hint_path="docs/design/rationale.md"))
    assert "### Reviewer Hint (path)" in header
    assert "`docs/design/rationale.md`" in header
    assert "read it" in header


def test_author_context_all_three_signals():
    header = build_session_header(
        _base_inputs(
            pr_description="PR desc.",
            hint="Inline hint.",
            hint_path="notes.md",
        )
    )
    assert "### PR Description" in header
    assert "### Reviewer Hint\n" in header
    assert "### Reviewer Hint (path)" in header
    # PR description precedes the inline hint precedes the file pointer.
    assert (
        header.index("### PR Description")
        < header.index("### Reviewer Hint\n")
        < header.index("### Reviewer Hint (path)")
    )


def test_author_context_pr_description_truncated():
    from roundtable.context.session_header import MAX_PR_DESCRIPTION_CHARS

    long_desc = "x" * (MAX_PR_DESCRIPTION_CHARS + 500)
    header = build_session_header(_base_inputs(pr_description=long_desc))
    assert "…(truncated — fetch the full PR description via ADO)" in header
    assert ("x" * (MAX_PR_DESCRIPTION_CHARS + 500)) not in header


def test_author_context_hint_truncated():
    from roundtable.context.session_header import MAX_HINT_CHARS

    long_hint = "y" * (MAX_HINT_CHARS + 500)
    header = build_session_header(_base_inputs(hint=long_hint))
    assert "…(truncated —" in header
    assert ("y" * (MAX_HINT_CHARS + 500)) not in header


def test_author_context_does_not_pollute_change_under_review_snapshot():
    # The narrative signals must stay OUT of the authoritative yaml snapshot.
    header = build_session_header(_base_inputs(pr_description="secret intent"))
    snapshot = header.split("```yaml", 1)[1].split("```", 1)[0]
    assert "secret intent" not in snapshot
