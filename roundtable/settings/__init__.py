"""User, workspace, and environment settings."""

from .effective import (
    UNSET_FALLBACK,
    ReviewConfig,
    build_review_config,
    render_params,
    settings_params,
)
from .env_compat import with_legacy_fallback
from .workspace import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_ATTEMPTS,
    ConfigError,
    WorkspaceSettings,
    configured_artifacts_root,
    default_artifacts_root,
    get_settings,
    relative_artifact_path,
    resolve_ado_auth,
    resolve_artifact_path,
    validate_update_source,
)

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_MAX_ATTEMPTS",
    "UNSET_FALLBACK",
    "ConfigError",
    "ReviewConfig",
    "WorkspaceSettings",
    "build_review_config",
    "configured_artifacts_root",
    "default_artifacts_root",
    "get_settings",
    "relative_artifact_path",
    "render_params",
    "resolve_ado_auth",
    "resolve_artifact_path",
    "settings_params",
    "validate_update_source",
    "with_legacy_fallback",
]
