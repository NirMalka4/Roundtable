"""Tests for the config-root seam (``config/paths.py``).

Proves the precedence override → ``ROUNDTABLE_CONFIG_ROOT`` → package default, the
derived path helpers, and that a loader honors the resolved root at call time (the
seam that makes relocating / pointing at another bundle possible).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from roundtable.bundle import paths
from roundtable.bundle.paths import (
    CONFIGS_DIR,
    ENV_VAR,
    ConfigRootError,
    resolve_bundle,
    resolve_config_root,
    set_config_root,
)
from roundtable.graph.loader import load_config_name


@pytest.fixture(autouse=True)
def _clear_override():
    """Ensure no override leaks between tests."""
    paths.set_config_root(None)
    yield
    paths.set_config_root(resolve_bundle("inspectorx"))


def test_default_root_is_package_config_dir():
    root = paths.config_root()
    assert root == Path(paths.__file__).resolve().parent.parent / "configs" / "buddies"
    assert (root / "agent_graph.yaml").is_file()


def test_derived_helpers_hang_off_root():
    root = paths.config_root()
    assert paths.graph_path() == root / "agent_graph.yaml"
    assert paths.schema_dir() == root / "schemas"
    assert paths.hint_dir() == root / "hints"
    assert paths.gates_manifest() == root / "gates.yaml"


def test_env_var_overrides_default(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    assert paths.config_root() == tmp_path.resolve()
    assert paths.graph_path() == tmp_path.resolve() / "agent_graph.yaml"


def test_explicit_override_wins_over_env(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    paths.set_config_root(other)
    assert paths.config_root() == other.resolve()


def test_loader_honors_resolved_root_at_call_time(tmp_path):
    (tmp_path / "agent_graph.yaml").write_text("name: elsewhere\nagents: []\n", encoding="utf-8")
    paths.set_config_root(tmp_path)
    assert load_config_name() == "elsewhere"


def test_nothing_reads_a_bundle_while_the_cli_is_imported():
    """The invariant that makes ``--config`` possible.

    ``main`` binds the flag from raw argv before anything else, but bundle-derived
    values are cached — so a single read during import would freeze the default
    bundle and the flag would relabel a run it never switched. Guard the whole CLI
    import graph, not just the two constants that used to do this.
    """
    probe = textwrap.dedent(
        """
        import sys
        import roundtable.bundle.paths as paths

        reads = []
        paths.config_root = lambda *_a, **_k: (reads.append(1), paths.resolve_config_root()[0])[1]
        import roundtable.cli  # noqa: F401
        print(len(reads))
        """
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "0"


def test_a_shipped_bundle_resolves_by_name_and_an_unknown_one_fails_loud():
    assert resolve_bundle("buddies") == (CONFIGS_DIR / "buddies").resolve()

    with pytest.raises(ConfigRootError) as err:
        resolve_bundle("no-such-bundle")
    assert "buddies" in str(err.value)  # names what IS available


def test_the_layer_that_chose_the_root_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    set_config_root(None)
    assert resolve_config_root()[1] == "default"

    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    assert resolve_config_root() == (tmp_path.resolve(), f"env:{ENV_VAR}")

    set_config_root(tmp_path)
    try:
        assert resolve_config_root() == (tmp_path.resolve(), "flag:--config")
    finally:
        set_config_root(None)
