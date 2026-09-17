"""Persist and resolve the configuration bundle that produced a session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from roundtable.bundle import CONFIGS_DIR

from .loader import graph_config_sha

if TYPE_CHECKING:
    from .model import Configuration

FILENAME = "configuration.json"
_VERSION = 1


class ConfigurationIdentityError(ValueError):
    """A session's configuration identity is absent, invalid, or no longer resolvable."""


@dataclass(frozen=True)
class ConfigurationIdentity:
    root: Path
    name: str
    graph_config_sha: str | None
    domain_values: tuple[tuple[str, tuple[str, ...]], ...] | None = None


def fingerprint_drift(identity: ConfigurationIdentity) -> tuple[str, str] | None:
    """Return ``(recorded, current)`` when a new-format session's bundle changed."""
    if identity.graph_config_sha is None:
        return None
    current = graph_config_sha(identity.root)
    if current == identity.graph_config_sha:
        return None
    return identity.graph_config_sha, current


def require_compatible_fingerprint(
    identity: ConfigurationIdentity, *, allow_drift: bool = False
) -> tuple[str, str] | None:
    """Enforce the publish-time bundle compatibility policy."""
    drift = fingerprint_drift(identity)
    if drift is not None and not allow_drift:
        recorded, current = drift
        raise ConfigurationIdentityError(
            "session configuration fingerprint differs from the resolved bundle "
            f"(recorded={recorded}, current={current}); refusing to publish. "
            "Use --allow-config-drift only after auditing the bundle change."
        )
    return drift


def _graph_name(root: Path) -> str:
    graph = root / "agent_graph.yaml"
    try:
        payload = yaml.safe_load(graph.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as err:
        raise ConfigurationIdentityError(f"cannot read configuration graph {graph}: {err}") from err
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationIdentityError(f"configuration graph {graph} has no valid `name`")
    return name.strip()


def describe_active_configuration(
    config: Configuration | str | Path | None = None,
) -> dict[str, Any]:
    """Return the stable identity contract persisted with a new session."""
    from .model import Configuration, get_configuration

    configuration = config if isinstance(config, Configuration) else get_configuration(config)
    return dict(configuration.identity)


def _resolve_contract(payload: dict[str, Any]) -> ConfigurationIdentity:
    if payload.get("version") != _VERSION:
        raise ConfigurationIdentityError(
            f"unsupported configuration identity version {payload.get('version')!r}"
        )
    kind = payload.get("kind")
    if kind == "shipped":
        bundle = payload.get("bundle")
        if not isinstance(bundle, str) or not bundle:
            raise ConfigurationIdentityError("shipped configuration identity has no bundle")
        root = (CONFIGS_DIR / bundle).resolve()
        try:
            root.relative_to(CONFIGS_DIR.resolve())
        except ValueError as err:
            raise ConfigurationIdentityError(
                "shipped bundle identifier escapes package configs"
            ) from err
    elif kind == "external":
        raw_path = payload.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigurationIdentityError("external configuration identity has no path")
        root = Path(raw_path).expanduser().resolve()
    else:
        raise ConfigurationIdentityError(f"unknown configuration identity kind {kind!r}")

    recorded_name = payload.get("name")
    actual_name = _graph_name(root)
    if not isinstance(recorded_name, str) or recorded_name != actual_name:
        raise ConfigurationIdentityError(
            f"configuration identity names {recorded_name!r}, but {root} contains {actual_name!r}"
        )
    digest = payload.get("graphConfigSha")
    if not isinstance(digest, str) or not digest:
        raise ConfigurationIdentityError("new-format configuration identity has no graphConfigSha")
    recorded_domain_values = payload.get("domainValues")
    normalized_domain_values: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    if recorded_domain_values is not None:
        if not isinstance(recorded_domain_values, dict) or any(
            not isinstance(term, str)
            or not isinstance(values, list)
            or any(not isinstance(value, str) for value in values)
            for term, values in recorded_domain_values.items()
        ):
            raise ConfigurationIdentityError("configuration identity has invalid domainValues")
        normalized_domain_values = tuple(
            (term, tuple(values)) for term, values in recorded_domain_values.items()
        )
        from .model import Configuration

        current = Configuration.from_file(root)
        current_values = (
            current.domain_values.to_dict() if current.domain_values is not None else {}
        )
        if recorded_domain_values != current_values:
            raise ConfigurationIdentityError(
                "session configuration domain values differ from the resolved bundle"
            )
    return ConfigurationIdentity(
        root=root,
        name=actual_name,
        graph_config_sha=digest,
        domain_values=normalized_domain_values,
    )


def _resolve_legacy_graph(session_dir: Path) -> ConfigurationIdentity:
    graph_path = session_dir / "graph.json"
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ConfigurationIdentityError(
            f"session has no resolvable {FILENAME} and graph.json cannot identify its bundle"
        ) from err
    display_name = graph.get("displayName") if isinstance(graph, dict) else None
    if not isinstance(display_name, str) or not display_name:
        raise ConfigurationIdentityError(
            f"legacy session {graph_path} has no configuration displayName"
        )

    matches: list[Path] = []
    for graph_config in CONFIGS_DIR.glob("*/agent_graph.yaml"):
        try:
            if _graph_name(graph_config.parent) == display_name:
                matches.append(graph_config.parent.resolve())
        except ConfigurationIdentityError:
            continue
    if len(matches) != 1:
        raise ConfigurationIdentityError(
            f"legacy configuration {display_name!r} does not identify exactly one shipped bundle"
        )
    return ConfigurationIdentity(root=matches[0], name=display_name, graph_config_sha=None)


def resolve_session_configuration(session_dir: str | Path) -> ConfigurationIdentity:
    """Resolve new identity metadata, or carefully infer a shipped legacy bundle."""
    session = Path(session_dir)
    identity_path = session / FILENAME
    if not identity_path.exists():
        return _resolve_legacy_graph(session)
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ConfigurationIdentityError(f"cannot read {identity_path}: {err}") from err
    if not isinstance(payload, dict):
        raise ConfigurationIdentityError(f"{identity_path} must contain a JSON object")
    return _resolve_contract(payload)
