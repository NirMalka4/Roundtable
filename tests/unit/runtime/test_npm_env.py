"""Unit tests for runtime.npm_env — the npm registry pin that makes `npx` MCP
package resolution immune to the review target's project `.npmrc`."""

from __future__ import annotations

from types import SimpleNamespace

from roundtable.mcp import npm_env


def _clear_cache() -> None:
    npm_env._user_registry.cache_clear()


def test_configured_registry_wins(monkeypatch) -> None:
    _clear_cache()
    monkeypatch.setattr(
        npm_env, "get_settings", lambda: SimpleNamespace(mcp_npm_registry="  https://cfg/  ")
    )
    # The configured value short-circuits the probe (which must NOT run).
    monkeypatch.setattr(
        npm_env, "_user_registry", lambda: (_ for _ in ()).throw(AssertionError("probed"))
    )
    assert npm_env.registry_pin_env() == {"npm_config_registry": "https://cfg/"}


def test_falls_back_to_probed_user_registry(monkeypatch) -> None:
    _clear_cache()
    monkeypatch.setattr(npm_env, "get_settings", lambda: SimpleNamespace(mcp_npm_registry=None))
    monkeypatch.setattr(
        npm_env.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="https://user.reg/npm/\n", stderr=""),
    )
    assert npm_env.registry_pin_env() == {"npm_config_registry": "https://user.reg/npm/"}


def test_fail_open_when_probe_errors(monkeypatch) -> None:
    _clear_cache()
    monkeypatch.setattr(npm_env, "get_settings", lambda: SimpleNamespace(mcp_npm_registry=None))

    def boom(*a, **k):
        raise OSError("npm not found")

    monkeypatch.setattr(npm_env.subprocess, "run", boom)
    assert npm_env.registry_pin_env() == {}  # no pin ⇒ behaviour unchanged


def test_fail_open_on_undefined_output(monkeypatch) -> None:
    _clear_cache()
    monkeypatch.setattr(npm_env, "get_settings", lambda: SimpleNamespace(mcp_npm_registry=None))
    monkeypatch.setattr(
        npm_env.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="undefined\n", stderr=""),
    )
    assert npm_env.registry_pin_env() == {}


def test_child_env_merges_parent_and_pin(monkeypatch) -> None:
    _clear_cache()
    monkeypatch.setattr(npm_env.os, "environ", {"PATH": "x", "FOO": "bar"})
    monkeypatch.setattr(npm_env, "registry_pin_env", lambda: {"npm_config_registry": "https://r/"})
    env = npm_env.child_env_with_registry_pin()
    assert env["PATH"] == "x"
    assert env["FOO"] == "bar"
    assert env["npm_config_registry"] == "https://r/"
