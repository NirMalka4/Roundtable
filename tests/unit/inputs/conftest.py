"""Pytest fixtures for the inputs (Phase 1) tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from _gitrepo import GitRepo, make_feature_repo


@pytest.fixture
def feature_repo(tmp_path: Path) -> GitRepo:
    return make_feature_repo(tmp_path)
