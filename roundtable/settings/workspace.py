"""settings: the single, predictable resolver for Roundtable's non-secret config.

Env-var-only configuration is undiscoverable and awkward in CI. This module adds
an optional, committed, repo-local ``roundtable.yaml`` as a discoverable home for
NON-SECRET settings, loaded dynamically only if present. Secrets (the ADO PAT)
NEVER live in the file — they stay in the environment; the file only *names*
which env var to read (indirection via ``ado.pat_env``).

Two invariants keep this safe to adopt:

* **Absent file ⇒ zero behaviour change.** With no file and no env, every setting
  is ``None``/default, so the call sites fall back to exactly today's logic.
* **Precedence is centralised here:** env var > ``roundtable.yaml`` > persisted
  user setting > built-in default (a CLI flag, where one exists, still overrides
  the resolved default).

Guardrails reject inline secrets, an invalid ``ado.auth`` mode, malformed YAML,
and a non-mapping document; unknown keys warn (forward-compatible) and are
ignored. ``resolve_ado_auth`` is a pure decision (no network/``az``) so the auth
routing is unit-testable without credentials.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .env_compat import LEGACY_CONFIG_FILENAME, with_legacy_fallback

CONFIG_FILENAME = "roundtable.yaml"
CONFIG_PATH_ENV = "ROUNDTABLE_CONFIG"

PROMPT_DIR_ENV = "ROUNDTABLE_PY_PROMPT_DIR"
MCP_NPM_REGISTRY_ENV = "ROUNDTABLE_MCP_NPM_REGISTRY"
MAX_ATTEMPTS_ENV = "ROUNDTABLE_MAX_ATTEMPTS"
CONCURRENCY_ENV = "ROUNDTABLE_CONCURRENCY"
ARTIFACTS_DIR_ENV = "ROUNDTABLE_ARTIFACTS_DIR"
ADO_AUTH_ENV = "ROUNDTABLE_ADO_AUTH"
UPDATE_SOURCE_ENV = "ROUNDTABLE_UPDATE_SOURCE"
REPO_SEARCH_PATHS_ENV = "ROUNDTABLE_REPO_SEARCH_PATHS"
CLONE_CACHE_DIR_ENV = "ROUNDTABLE_CLONE_CACHE_DIR"
CLONE_CACHE_MAX_GB_ENV = "ROUNDTABLE_CLONE_CACHE_MAX_GB"
CLONE_CACHE_TTL_DAYS_ENV = "ROUNDTABLE_CLONE_CACHE_TTL_DAYS"
WORKSPACE_CHECKOUT_TIMEOUT_ENV = "ROUNDTABLE_WORKSPACE_CHECKOUT_TIMEOUT"

# Built-in caps for the review-workspace clone cache (a bounded, GC'd store of
# blobless clones used when the reviewed repo isn't already on disk). Overridable
# via the env vars above or the `workspace:` block in roundtable.yaml.
DEFAULT_CLONE_CACHE_MAX_GB = 20
DEFAULT_CLONE_CACHE_TTL_DAYS = 14
DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT = 600
MAX_WORKSPACE_CHECKOUT_TIMEOUT = 3600

# Built-in values for the settings that have one. Declared here, beside the env
# names and the file keys, so the layer that resolves `env > file > default` also
# owns what "default" *is* — no consumer re-states them, and `doctor` and `review`
# cannot disagree about a value neither of them set.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_CONCURRENCY = 6

_SESSION_NAME_RE = re.compile(r"^(session_\d{14})(?:_(pr-\d+)(?:-|$))?")
_SESSION_ALIAS_RE = re.compile(r"^session_\d{14}(?:_pr-\d+)?$")


def default_artifacts_root() -> Path:
    """Built-in artifacts root. A function because the home dir is redirectable."""
    from roundtable.bundle import home_root

    return home_root() / "artifacts"


def configured_artifacts_root() -> Path:
    """The artifacts root selected by settings, independent of the current directory."""
    configured = get_settings().artifacts_dir
    return Path(configured).expanduser() if configured else default_artifacts_root()


def resolve_artifact_path(reference: str | Path) -> Path:
    """Resolve an absolute path or an artifacts-root-relative reference."""
    path = Path(reference).expanduser()
    if path.is_absolute():
        return path
    candidate = configured_artifacts_root() / path
    if candidate.exists():
        return candidate
    return _resolve_session_alias(candidate, path.parts)


def _resolve_session_alias(candidate: Path, parts: tuple[str, ...]) -> Path:
    for index, part in enumerate(parts):
        if _SESSION_ALIAS_RE.fullmatch(part):
            parent = configured_artifacts_root().joinpath(*parts[:index])
            matches = _session_alias_matches(parent, part)
            if len(matches) == 1:
                return matches[0].joinpath(*parts[index + 1 :])
            problem = "not found" if not matches else f"ambiguous ({len(matches)} matches)"
            raise ValueError(f"Artifact session alias {part!r} is {problem} under {parent}.")
    return candidate


def _session_alias_matches(parent: Path, alias: str) -> list[Path]:
    delimiter = "-" if "_pr-" in alias else "_"
    try:
        children = parent.iterdir()
    except OSError:
        return []
    return sorted(
        child for child in children if child.is_dir() and child.name.startswith(alias + delimiter)
    )


def relative_artifact_path(path: str | Path, root: str | Path | None = None) -> str:
    """Return a privacy-safe artifacts-root-relative session alias."""
    resolved_root = (
        Path(root).expanduser().resolve() if root else configured_artifacts_root().resolve()
    )
    candidate = Path(path).expanduser().resolve()
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError as err:
        raise ValueError(
            f"Publishing requires the session directory to be under the configured "
            f"artifacts root ({resolved_root})."
        ) from err
    match = _SESSION_NAME_RE.match(relative.name)
    if match is None:
        if relative.name.startswith("session_"):
            raise ValueError(
                "Publishing requires a session directory named "
                "'session_<14-digit UTC timestamp>_<label>'."
            )
        return relative.as_posix()
    alias = "_".join(part for part in match.groups() if part)
    return (relative.parent / alias).as_posix()


DEFAULT_PAT_ENV = "ROUNDTABLE_ADO_PAT"
FALLBACK_PAT_ENV = "AZURE_DEVOPS_PAT"

VALID_ADO_AUTH = ("auto", "az-login", "pat")

# Value-bearing keys that would leak a secret if committed. ``pat_env``/``pat_file``
# are references (they name where the secret lives) and stay allowed.
_FORBIDDEN_SECRET_KEYS = frozenset(
    {"pat", "token", "secret", "password", "api_key", "apikey", "access_token"}
)
_KNOWN_TOP_KEYS = frozenset(
    {
        "prompt_dir",
        "mcp_npm_registry",
        "max_attempts",
        "concurrency",
        "artifacts_dir",
        "ado",
        "update",
        "workspace",
    }
)
_KNOWN_ADO_KEYS = frozenset({"auth", "pat_env"})
_KNOWN_UPDATE_KEYS = frozenset({"source"})
_KNOWN_WORKSPACE_KEYS = frozenset(
    {
        "repo_search_paths",
        "clone_cache_dir",
        "clone_cache_max_gb",
        "clone_cache_ttl_days",
        "checkout_timeout_seconds",
    }
)


class ConfigError(ValueError):
    """Raised when ``roundtable.yaml`` is present but invalid.

    Subclasses ``ValueError`` so callers already catching ``ValueError`` (e.g.
    ``doctor``) treat a bad config the same as any other validation failure.
    """


@dataclass(frozen=True)
class AdoSettings:
    auth: str = "auto"
    pat_env: str = DEFAULT_PAT_ENV


@dataclass(frozen=True)
class UpdateSettings:
    """Where `roundtable update` pulls new versions from, when configured."""

    source: str | None = None


@dataclass(frozen=True)
class WorkspaceSettings:
    """Knobs for review-workspace resolution (repo discovery + clone cache).

    ``repo_search_paths`` empty and ``clone_cache_dir`` ``None`` both mean "use the
    built-in default", resolved by the workspace module (``[~/repos]`` and the
    artifacts-sibling ``clones/`` directory respectively). Home-dir resolution is
    deliberately kept out of this pure loader — mirroring how ``artifacts_dir``
    defers its default to the orchestration layer.
    """

    repo_search_paths: tuple[str, ...] = ()
    clone_cache_dir: str | None = None
    clone_cache_max_gb: int = DEFAULT_CLONE_CACHE_MAX_GB
    clone_cache_ttl_days: int = DEFAULT_CLONE_CACHE_TTL_DAYS
    checkout_timeout_seconds: int = DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT


@dataclass(frozen=True)
class Settings:
    prompt_dir: str | None = None
    mcp_npm_registry: str | None = None
    max_attempts: int | None = None
    concurrency: int | None = None
    artifacts_dir: str | None = None
    ado: AdoSettings = field(default_factory=AdoSettings)
    update: UpdateSettings = field(default_factory=UpdateSettings)
    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    source_path: Path | None = None
    # Winning env>file>default layer per setting; the flag layer is added by
    # :mod:`roundtable.settings.effective`. Empty for hand-built Settings.
    sources: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AdoAuthPlan:
    """Pure routing decision for ADO REST auth (no network / ``az`` here).

    ``pat`` set ⇒ build a Basic header from it. Else, if ``allow_bearer``, mint an
    AAD bearer via ``az``. ``error`` set ⇒ the mode's precondition failed.
    """

    pat: str | None
    allow_bearer: bool
    error: str | None = None


def discover_config(start_dir: Path) -> Path | None:
    """Return the nearest ``roundtable.yaml`` walking up from ``start_dir``.

    For one deprecation release a legacy ``inspectorx.yaml`` in the same directory
    is honored as a fallback when no ``roundtable.yaml`` is present.
    """
    start = Path(start_dir).resolve()
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        legacy = directory / LEGACY_CONFIG_FILENAME  # rebrand-compat (Q5)
        if legacy.is_file():
            _warn(
                f"{LEGACY_CONFIG_FILENAME} is deprecated; rename it to "
                f"{CONFIG_FILENAME} (the legacy name is honored for one release)."
            )
            return legacy
    return None


def _warn(message: str) -> None:
    print(f"roundtable.yaml: warning: {message}", file=sys.stderr)


def _reject_inline_secrets(node: object, path: str = "") -> None:
    if not isinstance(node, Mapping):
        return
    for key, value in node.items():
        where = f"{path}.{key}" if path else str(key)
        if str(key).lower() in _FORBIDDEN_SECRET_KEYS:
            raise ConfigError(
                f"{CONFIG_FILENAME}: key '{where}' looks like an inline secret and "
                f"must never be committed. Keep the value in an environment variable "
                f"and reference it via 'ado.pat_env' instead."
            )
        _reject_inline_secrets(value, where)


def _resolve_config_path(start_dir: Path, env: Mapping[str, str]) -> Path | None:
    override = env.get(CONFIG_PATH_ENV)
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise ConfigError(f"{CONFIG_PATH_ENV}={override!r} is not a file")
        return path
    return discover_config(start_dir)


def _parse_config_file(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ConfigError(f"{CONFIG_FILENAME} at {path} is not valid YAML: {err}") from err
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(
            f"{CONFIG_FILENAME} at {path} must be a mapping of settings, got {type(raw).__name__}"
        )
    _reject_inline_secrets(raw)
    return dict(raw)


def _build_ado(file_ado: object, env: Mapping[str, str]) -> AdoSettings:
    data: Mapping[str, object] = file_ado if isinstance(file_ado, Mapping) else {}
    for key in data:
        if str(key) not in _KNOWN_ADO_KEYS:
            _warn(f"unknown key 'ado.{key}' ignored")
    auth = env.get(ADO_AUTH_ENV) or data.get("auth") or "auto"
    if auth not in VALID_ADO_AUTH:
        raise ConfigError(
            f"{CONFIG_FILENAME}: 'ado.auth' must be one of {', '.join(VALID_ADO_AUTH)}, got {auth!r}"
        )
    pat_env = data.get("pat_env") or DEFAULT_PAT_ENV
    return AdoSettings(auth=str(auth), pat_env=str(pat_env))


def validate_update_source(source: str, *, source_name: str = "update source") -> str:
    """Validate and normalize a PyPI simple-index HTTP(S) URL."""
    value = source.strip()
    parsed = urlsplit(value)
    if not value:
        raise ConfigError(f"{source_name} must not be blank")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{source_name} must be an HTTP(S) PyPI simple-index URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{source_name} must not contain credentials")
    if parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/simple"):
        raise ConfigError(
            f"{source_name} must end in /simple or /simple/ with no query or fragment"
        )
    return value


def _build_update(
    file_update: object,
    user_source: str | None,
    env: Mapping[str, str],
) -> UpdateSettings:
    data: Mapping[str, object] = file_update if isinstance(file_update, Mapping) else {}
    for key in data:
        if str(key) not in _KNOWN_UPDATE_KEYS:
            _warn(f"unknown key 'update.{key}' ignored")
    if UPDATE_SOURCE_ENV in env:
        source = env[UPDATE_SOURCE_ENV]
    elif "source" in data:
        source = str(data["source"]) if data["source"] is not None else ""
    else:
        source = user_source
    return UpdateSettings(
        source=(
            validate_update_source(source, source_name="update.source")
            if source is not None
            else None
        )
    )


def _resolve_search_paths(env: Mapping[str, str], file_value: object) -> tuple[str, ...]:
    """Env (``os.pathsep``-separated) > file (YAML list or single string) > empty.

    Empty means "use the built-in default", applied by the workspace module. Blank
    entries are dropped so a stray separator can't inject the current directory.
    """
    raw = env.get(REPO_SEARCH_PATHS_ENV)
    if raw is not None:
        parts = [p.strip() for p in raw.split(os.pathsep)]
    elif isinstance(file_value, str):
        parts = [file_value.strip()]
    elif isinstance(file_value, (list, tuple)):
        parts = [str(p).strip() for p in file_value]
    elif file_value is None:
        parts = []
    else:
        raise ConfigError(
            f"{CONFIG_FILENAME}: 'workspace.repo_search_paths' must be a list of paths or a "
            f"string, got {type(file_value).__name__}"
        )
    return tuple(p for p in parts if p)


def _build_workspace(file_workspace: object, env: Mapping[str, str]) -> WorkspaceSettings:
    data: Mapping[str, object] = file_workspace if isinstance(file_workspace, Mapping) else {}
    for key in data:
        if str(key) not in _KNOWN_WORKSPACE_KEYS:
            _warn(f"unknown key 'workspace.{key}' ignored")
    max_gb = _pick_int(
        env, CLONE_CACHE_MAX_GB_ENV, data.get("clone_cache_max_gb"), "workspace.clone_cache_max_gb"
    )
    ttl_days = _pick_int(
        env,
        CLONE_CACHE_TTL_DAYS_ENV,
        data.get("clone_cache_ttl_days"),
        "workspace.clone_cache_ttl_days",
    )
    checkout_timeout = _pick_int(
        env,
        WORKSPACE_CHECKOUT_TIMEOUT_ENV,
        data.get("checkout_timeout_seconds"),
        "workspace.checkout_timeout_seconds",
        maximum=MAX_WORKSPACE_CHECKOUT_TIMEOUT,
    )
    return WorkspaceSettings(
        repo_search_paths=_resolve_search_paths(env, data.get("repo_search_paths")),
        clone_cache_dir=_pick(env, CLONE_CACHE_DIR_ENV, data.get("clone_cache_dir")),
        clone_cache_max_gb=max_gb if max_gb is not None else DEFAULT_CLONE_CACHE_MAX_GB,
        clone_cache_ttl_days=ttl_days if ttl_days is not None else DEFAULT_CLONE_CACHE_TTL_DAYS,
        checkout_timeout_seconds=(
            checkout_timeout if checkout_timeout is not None else DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT
        ),
    )


def _pick(env: Mapping[str, str], env_key: str, file_value: object) -> str | None:
    """Precedence: env var wins over the file value, else ``None``.

    Truthiness-based to match :func:`_source_of`: an empty/falsy value (``""``,
    ``0``, ``False``) is treated as unset so it falls through to the built-in
    default, and ``sources`` never records a layer the value didn't come from.
    """
    resolved = env.get(env_key) or file_value
    return str(resolved) if resolved else None


def _source_of(env: Mapping[str, str], env_key: str, file_value: object) -> str:
    """Winning layer for a truthiness-resolved setting (``env or file or default``).

    Mirrors :func:`_pick` / ``env.get(k) or data.get(k) or DEFAULT``: an empty env
    string is falsy and falls through to the file, then the built-in default.
    """
    if env.get(env_key):
        return f"env:{env_key}"
    if file_value:
        return CONFIG_FILENAME
    return "default"


def _int_source_of(env: Mapping[str, str], env_key: str, file_value: object) -> str:
    """Winning layer for an integer setting (mirrors :func:`_pick_int`).

    ``_pick_int`` treats the env var as present when ``env.get(env_key) is not
    None`` (even an empty string), so the source logic matches that, not truthiness.
    """
    if env.get(env_key) is not None:
        return f"env:{env_key}"
    if file_value is not None:
        return CONFIG_FILENAME
    return "default"


def _pick_int(
    env: Mapping[str, str],
    env_key: str,
    file_value: object,
    key_name: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int | None:
    """Resolve an integer setting with the standard env > file > ``None`` precedence.

    The raw value (an env string or a YAML scalar) must be a whole integer ``>=
    minimum``; anything else (non-integer, float like ``3.5``, boolean, below the
    floor) is a :class:`ConfigError` naming the offending source, so a typo fails
    loudly at load time instead of silently truncating or being ignored.
    """
    raw = env.get(env_key)
    source = env_key if raw is not None else CONFIG_FILENAME
    value = raw if raw is not None else file_value
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        raise ConfigError(f"{source}: '{key_name}' must be an integer, got {value!r}")
    try:
        parsed = int(str(value).strip())
    except ValueError:
        raise ConfigError(f"{source}: '{key_name}' must be an integer, got {value!r}") from None
    if parsed < minimum:
        raise ConfigError(f"{source}: '{key_name}' must be >= {minimum}, got {parsed}")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{source}: '{key_name}' must be <= {maximum}, got {parsed}")
    return parsed


def load_settings(
    start_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    user_update_path: Path | None = None,
) -> Settings:
    """Resolve :class:`Settings` from ``roundtable.yaml`` (if any) and ``env``.

    Pure with respect to its inputs: pass an explicit ``start_dir``/``env`` in
    tests. ``start_dir`` defaults to the current working directory and ``env`` to
    ``os.environ``.
    """
    start_dir = Path(start_dir) if start_dir is not None else Path.cwd()
    env = with_legacy_fallback(env if env is not None else os.environ)

    path = _resolve_config_path(start_dir, env)
    file_data = _parse_config_file(path) if path is not None else {}
    from .user import load_user_update_source, user_update_settings_path

    resolved_user_update_path = user_update_path or user_update_settings_path(env=env)
    user_update_source = load_user_update_source(resolved_user_update_path)

    for key in file_data:
        if str(key) not in _KNOWN_TOP_KEYS:
            _warn(f"unknown key {str(key)!r} ignored")

    _ado = file_data.get("ado")
    ado_file = _ado if isinstance(_ado, Mapping) else {}
    _update = file_data.get("update")
    update_file = _update if isinstance(_update, Mapping) else {}
    _workspace = file_data.get("workspace")
    workspace_file = _workspace if isinstance(_workspace, Mapping) else {}
    sources = {
        "prompt_dir": _source_of(env, PROMPT_DIR_ENV, file_data.get("prompt_dir")),
        "mcp_npm_registry": _source_of(
            env, MCP_NPM_REGISTRY_ENV, file_data.get("mcp_npm_registry")
        ),
        "max_attempts": _int_source_of(env, MAX_ATTEMPTS_ENV, file_data.get("max_attempts")),
        "concurrency": _int_source_of(env, CONCURRENCY_ENV, file_data.get("concurrency")),
        "artifacts_dir": _source_of(env, ARTIFACTS_DIR_ENV, file_data.get("artifacts_dir")),
        "ado.auth": _source_of(env, ADO_AUTH_ENV, ado_file.get("auth")),
        # ado.pat_env has no env layer — file > built-in default only.
        "ado.pat_env": CONFIG_FILENAME if ado_file.get("pat_env") else "default",
        "update.source": (
            _source_of(env, UPDATE_SOURCE_ENV, update_file.get("source"))
            if env.get(UPDATE_SOURCE_ENV) or update_file.get("source")
            else (f"user:{resolved_user_update_path}" if user_update_source else "default")
        ),
        "workspace.checkout_timeout_seconds": _int_source_of(
            env,
            WORKSPACE_CHECKOUT_TIMEOUT_ENV,
            workspace_file.get("checkout_timeout_seconds"),
        ),
    }

    return Settings(
        prompt_dir=_pick(env, PROMPT_DIR_ENV, file_data.get("prompt_dir")),
        mcp_npm_registry=_pick(env, MCP_NPM_REGISTRY_ENV, file_data.get("mcp_npm_registry")),
        max_attempts=_pick_int(
            env, MAX_ATTEMPTS_ENV, file_data.get("max_attempts"), "max_attempts"
        ),
        concurrency=_pick_int(env, CONCURRENCY_ENV, file_data.get("concurrency"), "concurrency"),
        artifacts_dir=_pick(env, ARTIFACTS_DIR_ENV, file_data.get("artifacts_dir")),
        ado=_build_ado(file_data.get("ado"), env),
        update=_build_update(file_data.get("update"), user_update_source, env),
        workspace=_build_workspace(file_data.get("workspace"), env),
        source_path=path,
        sources=sources,
    )


def resolve_ado_auth(settings: Settings, env: Mapping[str, str]) -> AdoAuthPlan:
    """Decide how to authenticate the ADO REST call — a pure routing function.

    * ``auto`` (default, back-compat): use the PAT if the named env var (or
      ``AZURE_DEVOPS_PAT``) is set, else mint an ``az`` bearer.
    * ``az-login``: ignore any PAT env var, go straight to the ``az`` bearer.
    * ``pat``: require the PAT env var, error out if it is missing.
    """
    pat = env.get(settings.ado.pat_env) or env.get(FALLBACK_PAT_ENV)
    mode = settings.ado.auth
    if mode == "az-login":
        return AdoAuthPlan(pat=None, allow_bearer=True)
    if mode == "pat":
        if not pat:
            return AdoAuthPlan(
                pat=None,
                allow_bearer=False,
                error=(
                    f"ado.auth is 'pat' but no PAT was found in "
                    f"${settings.ado.pat_env} (or ${FALLBACK_PAT_ENV}). Set it, or "
                    f"switch ado.auth to 'auto'/'az-login' to use your 'az login'."
                ),
            )
        return AdoAuthPlan(pat=pat, allow_bearer=False)
    return AdoAuthPlan(pat=pat, allow_bearer=True)  # auto


_CACHE: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` (loaded on first use)."""
    global _CACHE
    if _CACHE is None:
        _CACHE = load_settings()
    return _CACHE


def reset_settings_cache() -> None:
    """Drop the cached settings so the next :func:`get_settings` reloads."""
    global _CACHE
    _CACHE = None
