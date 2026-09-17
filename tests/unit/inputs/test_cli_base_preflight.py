"""Tests for the CLI base-branch pre-flight gate (false-CLEAN guard).

The CLI base-branch contract: for local-mode reviews, an unresolvable diff base
hard-fails (BAD_ARGS) instead of silently degrading to an empty — and therefore
false-CLEAN — review.
"""

from __future__ import annotations

import pytest
from _gitrepo import GitRepo

from roundtable.cli import _BaseBranchUnresolved, _resolve_verify_base


def test_verify_base_autodetect_returns_resolvable(feature_repo: GitRepo):
    # --base-branch omitted -> auto-detected to the repo's mainline (origin/main).
    assert _resolve_verify_base(feature_repo.path, None, "[test]") == "origin/main"


def test_verify_base_explicit_resolvable(feature_repo: GitRepo):
    # Explicit base that resolves is returned verbatim.
    assert _resolve_verify_base(feature_repo.path, "main", "[test]") == "main"


def test_verify_base_unresolvable_raises(feature_repo: GitRepo):
    # Explicit base that does NOT resolve -> hard-fail, never a silent empty diff.
    with pytest.raises(_BaseBranchUnresolved, match="would be empty"):
        _resolve_verify_base(feature_repo.path, "no-such-branch", "[test]")
