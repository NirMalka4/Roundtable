"""Settings loader (roundtable.yaml) — the single, predictable resolver for
non-secret configuration.

Contract under test:

* When no file and no env are present, every setting is ``None``/default so the
  three call sites reproduce today's behaviour byte-for-byte.
* Discovery walks up from ``start_dir`` (nearest ``roundtable.yaml`` wins);
  ``ROUNDTABLE_CONFIG`` overrides discovery.
* Precedence is env > file > default, centralised in the loader.
* Guardrails reject inline secrets, invalid ``ado.auth``, malformed YAML, and a
  non-mapping document; unknown keys warn (forward-compatible) and are ignored.
* ``resolve_ado_auth`` is a pure decision (no network/az) covering auto,
  az-login, and pat modes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from roundtable.settings import user as user_settings
from roundtable.settings.user import user_update_settings_path
from roundtable.settings.workspace import (
    DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT,
    ConfigError,
    Settings,
    discover_config,
    get_settings,
    load_settings,
    reset_settings_cache,
    resolve_ado_auth,
)


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path, monkeypatch):
    """Keep the process-wide settings cache from leaking across tests."""
    monkeypatch.setattr(
        user_settings,
        "user_update_settings_path",
        lambda **_kwargs: tmp_path / "user-settings" / "update.yaml",
    )
    reset_settings_cache()
    yield
    reset_settings_cache()


# ── defaults: absent file + absent env reproduces today ─────────────────────
def test_absent_file_and_env_yields_all_defaults(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    assert isinstance(s, Settings)
    assert s.prompt_dir is None
    assert s.mcp_npm_registry is None
    assert s.max_attempts is None
    assert s.ado.auth == "auto"
    assert s.ado.pat_env == "ROUNDTABLE_ADO_PAT"
    assert s.update.source is None
    assert s.workspace.checkout_timeout_seconds == DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT
    assert s.source_path is None


# ── sources: which layer won each setting (env > file > default) ─────────────
def test_sources_all_default_when_absent(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    for key in (
        "mcp_npm_registry",
        "max_attempts",
        "artifacts_dir",
        "ado.auth",
        "ado.pat_env",
        "update.source",
    ):
        assert s.sources[key] == "default", key


def test_sources_file_and_env_layers(tmp_path):
    _write(
        tmp_path / "roundtable.yaml",
        "mcp_npm_registry: from-file\nmax_attempts: 4\nado:\n  auth: pat\n  pat_env: MY_PAT\n"
        "update:\n  source: https://file/simple/\n",
    )
    s = load_settings(
        start_dir=tmp_path,
        env={"ROUNDTABLE_MCP_NPM_REGISTRY": "from-env"},
    )
    assert s.sources["mcp_npm_registry"] == "env:ROUNDTABLE_MCP_NPM_REGISTRY"
    assert s.sources["max_attempts"] == "roundtable.yaml"
    assert s.sources["ado.auth"] == "roundtable.yaml"
    assert s.sources["ado.pat_env"] == "roundtable.yaml"
    assert s.sources["update.source"] == "roundtable.yaml"
    assert s.sources["artifacts_dir"] == "default"


def test_empty_string_file_value_is_unset(tmp_path):
    """A falsy scalar (e.g. ``artifacts_dir: ""``) must fall through to the default,
    and ``sources`` must report ``default`` — value and provenance stay aligned."""
    _write(tmp_path / "roundtable.yaml", 'artifacts_dir: ""\nmcp_npm_registry: ""\n')
    s = load_settings(start_dir=tmp_path, env={})
    assert s.artifacts_dir is None
    assert s.mcp_npm_registry is None
    assert s.sources["artifacts_dir"] == "default"
    assert s.sources["mcp_npm_registry"] == "default"


# ── update.source: env > file > unset ───────────────────────────────────────
def test_update_source_is_unset_by_default(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    assert s.update.source is None


def test_update_source_file_value(tmp_path):
    _write(tmp_path / "roundtable.yaml", "update:\n  source: https://example/simple/\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert s.update.source == "https://example/simple/"


def test_update_source_env_overrides_file(tmp_path):
    _write(tmp_path / "roundtable.yaml", "update:\n  source: https://example/simple/\n")
    s = load_settings(
        start_dir=tmp_path,
        env={"ROUNDTABLE_UPDATE_SOURCE": "https://env.example/simple/"},
    )
    assert s.update.source == "https://env.example/simple/"


def test_update_source_precedence_is_env_then_workspace_then_user(tmp_path):
    user = _write(tmp_path / "user" / "update.yaml", 'source: "https://user.example/simple/"\n')
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write(workspace / "roundtable.yaml", "update:\n  source: https://workspace.example/simple/\n")

    from_user = load_settings(start_dir=tmp_path, env={}, user_update_path=user)
    assert from_user.update.source == "https://user.example/simple/"
    assert from_user.sources["update.source"] == f"user:{user}"

    from_workspace = load_settings(start_dir=workspace, env={}, user_update_path=user)
    assert from_workspace.update.source == "https://workspace.example/simple/"
    assert from_workspace.sources["update.source"] == "roundtable.yaml"

    from_env = load_settings(
        start_dir=workspace,
        env={"ROUNDTABLE_UPDATE_SOURCE": "https://env.example/simple/"},
        user_update_path=user,
    )
    assert from_env.update.source == "https://env.example/simple/"
    assert from_env.sources["update.source"] == "env:ROUNDTABLE_UPDATE_SOURCE"


@pytest.mark.parametrize(
    "bad",
    (
        "C:\\feed\\simple",
        "file:///feed/simple/",
        "https://example.test/not-simple/",
        "https://user:pat@example.test/simple/",
        "https://example.test/simple/?token=secret",
    ),
)
def test_update_source_rejects_invalid_or_credential_bearing_values(tmp_path, bad):
    with pytest.raises(ConfigError):
        load_settings(
            start_dir=tmp_path,
            env={"ROUNDTABLE_UPDATE_SOURCE": bad},
            user_update_path=tmp_path / "missing.yaml",
        )


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    (
        ("win32", {"APPDATA": "C:\\Profiles\\me"}, Path("C:\\Profiles\\me/Roundtable/update.yaml")),
        (
            "darwin",
            {},
            Path("/home/me/Library/Application Support/Roundtable/update.yaml"),
        ),
        ("linux", {"XDG_CONFIG_HOME": "/cfg"}, Path("/cfg/roundtable/update.yaml")),
    ),
)
def test_user_update_settings_path_is_cross_platform(platform, env, expected):
    assert user_update_settings_path(env=env, home=Path("/home/me"), platform=platform) == expected


def test_update_source_rejects_explicit_blank_env_value(tmp_path):
    with pytest.raises(ConfigError, match="must not be blank"):
        load_settings(
            start_dir=tmp_path,
            env={"ROUNDTABLE_UPDATE_SOURCE": ""},
            user_update_path=tmp_path / "missing.yaml",
        )


def test_update_unknown_key_warns_and_is_ignored(tmp_path, capsys):
    _write(tmp_path / "roundtable.yaml", "update:\n  mystery: 1\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert s.update.source is None
    assert "update.mystery" in capsys.readouterr().err


def test_workspace_checkout_timeout_env_overrides_file_and_is_bounded(tmp_path):
    _write(tmp_path / "roundtable.yaml", "workspace:\n  checkout_timeout_seconds: 700\n")
    s = load_settings(
        start_dir=tmp_path,
        env={"ROUNDTABLE_WORKSPACE_CHECKOUT_TIMEOUT": "900"},
    )
    assert s.workspace.checkout_timeout_seconds == 900
    assert s.sources["workspace.checkout_timeout_seconds"] == (
        "env:ROUNDTABLE_WORKSPACE_CHECKOUT_TIMEOUT"
    )

    with pytest.raises(ConfigError, match="must be <= 3600"):
        load_settings(
            start_dir=tmp_path,
            env={"ROUNDTABLE_WORKSPACE_CHECKOUT_TIMEOUT": "3601"},
        )


# ── discovery ───────────────────────────────────────────────────────────────
def test_discovery_finds_file_in_start_dir(tmp_path):
    cfg = _write(tmp_path / "roundtable.yaml", "mcp_npm_registry: gpt-x\n")
    assert discover_config(tmp_path) == cfg


def test_discovery_walks_up_to_ancestor(tmp_path):
    cfg = _write(tmp_path / "roundtable.yaml", "mcp_npm_registry: gpt-x\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert discover_config(nested) == cfg


def test_discovery_returns_none_when_absent(tmp_path):
    assert discover_config(tmp_path) is None


def test_env_config_path_overrides_discovery(tmp_path):
    _write(tmp_path / "roundtable.yaml", "mcp_npm_registry: from-walkup\n")
    explicit = _write(tmp_path / "other.yaml", "mcp_npm_registry: from-env\n")
    s = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_CONFIG": str(explicit)})
    assert s.mcp_npm_registry == "from-env"
    assert s.source_path == explicit


def test_env_config_path_missing_file_raises(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="ROUNDTABLE_CONFIG"):
        load_settings(start_dir=tmp_path, env={"ROUNDTABLE_CONFIG": str(missing)})


# ── file values are read ────────────────────────────────────────────────────
def test_file_values_populate_settings(tmp_path):
    _write(
        tmp_path / "roundtable.yaml",
        "prompt_dir: /prompts\nmcp_npm_registry: gpt-x\nado:\n  auth: az-login\n  pat_env: MY_PAT\n",
    )
    s = load_settings(start_dir=tmp_path, env={})
    assert s.prompt_dir == "/prompts"
    assert s.mcp_npm_registry == "gpt-x"
    assert s.ado.auth == "az-login"
    assert s.ado.pat_env == "MY_PAT"
    assert s.source_path == tmp_path / "roundtable.yaml"


# ── precedence: env overrides file ──────────────────────────────────────────
def test_env_overrides_file_for_scalars(tmp_path):
    _write(
        tmp_path / "roundtable.yaml",
        "prompt_dir: /file/prompts\nmcp_npm_registry: file-model\n",
    )
    env = {
        "ROUNDTABLE_PY_PROMPT_DIR": "/env/prompts",
        "ROUNDTABLE_MCP_NPM_REGISTRY": "env-model",
    }
    s = load_settings(start_dir=tmp_path, env=env)
    assert s.prompt_dir == "/env/prompts"
    assert s.mcp_npm_registry == "env-model"


def test_env_overrides_file_for_ado_auth(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  auth: pat\n")
    s = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_ADO_AUTH": "az-login"})
    assert s.ado.auth == "az-login"


# ── max_attempts: integer resolution + validation ──────────────────────────
def test_max_attempts_file_value_is_parsed_as_int(tmp_path):
    _write(tmp_path / "roundtable.yaml", "max_attempts: 5\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert s.max_attempts == 5


def test_max_attempts_env_overrides_file(tmp_path):
    _write(tmp_path / "roundtable.yaml", "max_attempts: 5\n")
    s = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_MAX_ATTEMPTS": "2"})
    assert s.max_attempts == 2


def test_max_attempts_absent_is_none(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    assert s.max_attempts is None


# ── artifacts_dir: env > file > None ────────────────────────────────────────
def test_artifacts_dir_file_value(tmp_path):
    _write(tmp_path / "roundtable.yaml", "artifacts_dir: /data/ix\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert s.artifacts_dir == "/data/ix"


def test_artifacts_dir_env_overrides_file(tmp_path):
    _write(tmp_path / "roundtable.yaml", "artifacts_dir: /data/ix\n")
    s = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_ARTIFACTS_DIR": "/env/ix"})
    assert s.artifacts_dir == "/env/ix"


def test_artifacts_dir_absent_is_none(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    assert s.artifacts_dir is None


@pytest.mark.parametrize("bad", ["abc", "3.5", "", "  "])
def test_max_attempts_non_integer_env_is_rejected(tmp_path, bad):
    with pytest.raises(ConfigError, match="max_attempts"):
        load_settings(start_dir=tmp_path, env={"ROUNDTABLE_MAX_ATTEMPTS": bad})


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_max_attempts_below_floor_is_rejected(tmp_path, bad):
    with pytest.raises(ConfigError, match=r"max_attempts.*>= 1"):
        load_settings(start_dir=tmp_path, env={"ROUNDTABLE_MAX_ATTEMPTS": bad})


def test_max_attempts_float_in_file_is_rejected(tmp_path):
    # YAML parses `3.5` as a float; truncating to 3 would silently change behaviour.
    _write(tmp_path / "roundtable.yaml", "max_attempts: 3.5\n")
    with pytest.raises(ConfigError, match="max_attempts"):
        load_settings(start_dir=tmp_path, env={})


def test_max_attempts_bool_in_file_is_rejected(tmp_path):
    # YAML `true` is a bool (an int subclass) — reject it rather than coerce to 1.
    _write(tmp_path / "roundtable.yaml", "max_attempts: true\n")
    with pytest.raises(ConfigError, match="max_attempts"):
        load_settings(start_dir=tmp_path, env={})


def test_max_attempts_error_names_the_source(tmp_path):
    # Env errors name the env var; file errors name the file, so a typo is findable.
    with pytest.raises(ConfigError, match="ROUNDTABLE_MAX_ATTEMPTS"):
        load_settings(start_dir=tmp_path, env={"ROUNDTABLE_MAX_ATTEMPTS": "x"})
    _write(tmp_path / "roundtable.yaml", "max_attempts: x\n")
    with pytest.raises(ConfigError, match=r"roundtable\.yaml"):
        load_settings(start_dir=tmp_path, env={})


# ── guardrails ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["pat", "token", "secret", "password", "api_key", "apikey"])
def test_inline_secret_key_is_rejected(tmp_path, key):
    _write(tmp_path / "roundtable.yaml", f"{key}: hunter2\n")
    with pytest.raises(ConfigError, match=key):
        load_settings(start_dir=tmp_path, env={})


def test_nested_inline_secret_key_is_rejected(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  token: abc123\n")
    with pytest.raises(ConfigError, match="token"):
        load_settings(start_dir=tmp_path, env={})


def test_pat_env_reference_is_allowed(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  pat_env: SOME_PAT_VAR\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert s.ado.pat_env == "SOME_PAT_VAR"


def test_invalid_ado_auth_is_rejected(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  auth: bogus\n")
    with pytest.raises(ConfigError, match="az-login"):
        load_settings(start_dir=tmp_path, env={})


def test_malformed_yaml_gives_clear_error(tmp_path):
    _write(tmp_path / "roundtable.yaml", "key: [unclosed\n")
    with pytest.raises(ConfigError, match=r"roundtable\.yaml"):
        load_settings(start_dir=tmp_path, env={})


def test_non_mapping_document_is_rejected(tmp_path):
    _write(tmp_path / "roundtable.yaml", "- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_settings(start_dir=tmp_path, env={})


def test_unknown_top_level_key_warns_and_is_ignored(tmp_path, capsys):
    _write(tmp_path / "roundtable.yaml", "mcp_npm_registry: gpt-x\nmystery: 1\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert s.mcp_npm_registry == "gpt-x"
    assert "mystery" in capsys.readouterr().err


def test_removed_default_model_key_is_now_unknown(tmp_path, capsys):
    # F4 (breaking): default_model was removed as dead config; the yaml key is no
    # longer recognized and must warn+ignore like any other unknown key.
    _write(tmp_path / "roundtable.yaml", "default_model: gpt-x\n")
    s = load_settings(start_dir=tmp_path, env={})
    assert not hasattr(s, "default_model")
    assert "default_model" in capsys.readouterr().err


# ── resolve_ado_auth (pure decision) ────────────────────────────────────────
def test_resolve_auto_prefers_pat_when_present(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})  # auth=auto
    plan = resolve_ado_auth(s, {"ROUNDTABLE_ADO_PAT": "p"})
    assert plan.pat == "p"
    assert plan.allow_bearer is True
    assert plan.error is None


def test_resolve_auto_falls_back_to_bearer_when_no_pat(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    plan = resolve_ado_auth(s, {})
    assert plan.pat is None
    assert plan.allow_bearer is True
    assert plan.error is None


def test_resolve_auto_reads_azure_devops_pat_fallback(tmp_path):
    s = load_settings(start_dir=tmp_path, env={})
    plan = resolve_ado_auth(s, {"AZURE_DEVOPS_PAT": "z"})
    assert plan.pat == "z"


def test_resolve_auto_honours_custom_pat_env(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  pat_env: MY_PAT\n")
    s = load_settings(start_dir=tmp_path, env={})
    plan = resolve_ado_auth(s, {"MY_PAT": "custom"})
    assert plan.pat == "custom"


def test_resolve_az_login_ignores_pat_env(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  auth: az-login\n")
    s = load_settings(start_dir=tmp_path, env={})
    plan = resolve_ado_auth(s, {"ROUNDTABLE_ADO_PAT": "p"})
    assert plan.pat is None
    assert plan.allow_bearer is True


def test_resolve_pat_mode_requires_pat(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  auth: pat\n")
    s = load_settings(start_dir=tmp_path, env={})
    plan = resolve_ado_auth(s, {})
    assert plan.error is not None
    assert plan.pat is None
    assert plan.allow_bearer is False


def test_resolve_pat_mode_uses_pat_when_present(tmp_path):
    _write(tmp_path / "roundtable.yaml", "ado:\n  auth: pat\n")
    s = load_settings(start_dir=tmp_path, env={})
    plan = resolve_ado_auth(s, {"ROUNDTABLE_ADO_PAT": "p"})
    assert plan.pat == "p"
    assert plan.allow_bearer is False
    assert plan.error is None


# ── get_settings cache ──────────────────────────────────────────────────────
def test_get_settings_is_cached_and_resettable(tmp_path, monkeypatch):
    _write(tmp_path / "roundtable.yaml", "mcp_npm_registry: cached-a\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROUNDTABLE_CONFIG", raising=False)
    reset_settings_cache()
    first = get_settings()
    assert first.mcp_npm_registry == "cached-a"
    # a second call returns the identical cached object (no re-read)
    assert get_settings() is first
    _write(tmp_path / "roundtable.yaml", "mcp_npm_registry: cached-b\n")
    assert get_settings().mcp_npm_registry == "cached-a"  # still cached
    reset_settings_cache()
    assert get_settings().mcp_npm_registry == "cached-b"
