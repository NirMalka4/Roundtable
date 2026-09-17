"""effective: the single place that materialises a run's *effective* config.

:mod:`roundtable.settings.workspace` resolves the ``env > roundtable.yaml >
built-in default`` half of the precedence and records the winning layer per key in
``Settings.sources``. This module stamps the **CLI-flag** layer on top — so the
full ``flag > env > file > default`` decision for every run-affecting parameter is
computed in *one* function instead of scattered ``args.x or settings.y`` fallbacks
across the CLI.

The output is a list of :class:`EffectiveParam` (name, resolved value, and the
layer that won). Callers use it three ways: to *drive* the run (the resolved
values), to *print* a compact table to the terminal, and to *record* the config
into ``trace.json`` (``as_trace``), so a verdict is tied to exactly the parameters
that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .workspace import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT,
    Settings,
    default_artifacts_root,
)

# Source label for a value that can only come from the command line (a pure CLI
# toggle with an argparse default — no env/file/default chain behind it).
CLI = "cli"

# What a review-scoped parameter falls back to when it is left unset. Stated once:
# rendered after ``(unset)`` in the effective-config table AND used verbatim in the
# flag's ``--help`` text (see :func:`cli.build_parser`), so the two cannot disagree.
UNSET_FALLBACK: dict[str, str] = {
    "base_branch": "auto-detect origin/HEAD, else origin/main",
    "pr": "review the working tree, not a PR",
}


@dataclass(frozen=True)
class EffectiveParam:
    """One parameter's resolved value and the layer that won it.

    ``source`` is one of ``flag:<--name>`` / ``env:<NAME>`` / ``roundtable.yaml`` /
    ``default`` / ``cli`` — a human-readable, artifact-friendly provenance tag.

    ``fallback`` describes what happens when ``value`` is unset, so a bare
    ``(unset)`` never leaves the reader guessing how the run was actually
    configured. It is display prose, not a value: it is never recorded in the trace.
    """

    name: str
    value: Any
    source: str
    fallback: str = ""


@dataclass(frozen=True)
class ReviewConfig:
    """A review run's effective parameters — resolved values + their provenance."""

    max_attempts: int
    concurrency: int
    artifacts_root: Path
    artifacts_from_default: bool
    params: tuple[EffectiveParam, ...]

    def render(self) -> str:
        """A compact, aligned ``name = value  [source]`` block for the terminal."""
        return render_params(self.params, title="effective config")

    def as_trace(self) -> list[dict[str, Any]]:
        """The ``trace.json`` shape: ``[{name, value, source}, …]`` (JSON-safe)."""
        return [
            {"name": p.name, "value": _jsonable(p.value), "source": p.source} for p in self.params
        ]


def settings_params(settings: Settings, args: Any | None = None) -> tuple[EffectiveParam, ...]:
    """The shared spine: which bundle runs, plus every ``env/file/default`` setting
    with its **resolved** value and the layer that won it.

    Every command that reports config renders exactly these rows, so a key can
    never read ``(unset)`` in one place and its resolved value in another. Pass
    ``args`` to stamp the CLI-flag layer on top; omit it for a command that has no
    review invocation to report (``doctor``), which then shows the ``env > file >
    default`` state a review would start from.
    """
    return (
        *_config_params(),
        *(_setting(spec, settings, args) for spec in _SETTINGS),
    )


def review_toggles(args: Any) -> tuple[EffectiveParam, ...]:
    """The rows that only a review invocation has: its diff base, target and modes."""
    return tuple(
        EffectiveParam(name, getattr(args, name, None), CLI, UNSET_FALLBACK.get(name, ""))
        for name in (
            "base_branch",
            "pr",
            "input_from",
            "session_reuse",
            "dump_prompts",
            "dry_run",
            "simulate",
        )
    )


@dataclass(frozen=True)
class _Setting:
    """One settings key: where its raw value lives, its built-in default, and — when
    it has no default — what happens instead."""

    name: str
    attr: str
    flag: str = ""
    default: Any = None
    fallback: str = ""


# The single list of run-affecting settings. Adding one here surfaces it in every
# command that reports config, and in trace.json, with no second edit.
_SETTINGS: tuple[_Setting, ...] = (
    _Setting("prompt_dir", "prompt_dir", fallback="the active bundle's prompts/Reviewer"),
    _Setting("mcp_npm_registry", "mcp_npm_registry", fallback="npm's own configured registry"),
    _Setting("max_attempts", "max_attempts", "--max-attempts", DEFAULT_MAX_ATTEMPTS),
    _Setting("concurrency", "concurrency", "--concurrency", DEFAULT_CONCURRENCY),
    _Setting("artifacts_dir", "artifacts_dir", "--artifacts-dir", None),
    _Setting("ado.auth", "ado.auth"),
    _Setting("ado.pat_env", "ado.pat_env"),
    _Setting("update.source", "update.source"),
    _Setting(
        "workspace.checkout_timeout_seconds",
        "workspace.checkout_timeout_seconds",
        default=DEFAULT_WORKSPACE_CHECKOUT_TIMEOUT,
    ),
)


def _setting(spec: _Setting, settings: Settings, args: Any | None) -> EffectiveParam:
    """Resolve one settings key: ``flag > env/file > built-in default``."""
    default = default_artifacts_root() if spec.name == "artifacts_dir" else spec.default
    flag_value = _flag_value(spec, args)
    if flag_value is not None:
        return EffectiveParam(spec.name, flag_value, f"flag:{spec.flag}")
    raw = _attr(settings, spec.attr)
    if raw is not None:
        return EffectiveParam(spec.name, raw, settings.sources.get(spec.name, "default"))
    if default is not None:
        return EffectiveParam(spec.name, default, "default")
    return EffectiveParam(spec.name, None, "default", spec.fallback)


def _flag_value(spec: _Setting, args: Any | None) -> Any:
    """The flag's value, or ``None`` when unset or when this key has no flag.

    A flag-backed key must use ``None`` as its argparse default; that is what makes
    "not passed" distinguishable from "passed the same value as the default".
    """
    return getattr(args, spec.attr, None) if (spec.flag and args is not None) else None


def _attr(settings: Settings, dotted: str) -> Any:
    value: Any = settings
    for part in dotted.split("."):
        value = getattr(value, part)
    return value


def build_review_config(settings: Settings, args: Any) -> ReviewConfig:
    """Compute a review's effective config from ``settings`` + parsed ``args``.

    The whole ``flag > env > file > default`` decision happens here, once, for every
    parameter — the resolved values drive the run, print to the terminal, and land in
    ``trace.json``, so what ran is never a mystery.
    """
    params = (*settings_params(settings, args), *review_toggles(args))
    by_name = {p.name: p for p in params}
    artifacts = by_name["artifacts_dir"]
    return ReviewConfig(
        max_attempts=int(by_name["max_attempts"].value),
        concurrency=int(by_name["concurrency"].value),
        artifacts_root=Path(str(artifacts.value)).expanduser(),
        artifacts_from_default=(artifacts.source == "default"),
        params=params,
    )


def _config_params() -> tuple[EffectiveParam, EffectiveParam]:
    """Which configuration bundle this review runs, and the layer that chose it.

    Listed first because the rest of the table is meaningless without it: every
    agent, schema, gate and severity level comes from this bundle.
    """
    from roundtable.bundle import resolve_config_root
    from roundtable.graph import describe_active_configuration

    root, layer = resolve_config_root()
    return (
        EffectiveParam("config", describe_active_configuration()["name"], layer),
        EffectiveParam("config_root", str(root), layer),
    )


def render_params(params: tuple[EffectiveParam, ...] | list[EffectiveParam], *, title: str) -> str:
    """Render an ``EffectiveParam`` sequence as an aligned ``name = value  [source]`` block."""
    items = list(params)
    if not items:
        return f"{title}: (none)"
    width = max(len(p.name) for p in items)
    lines = [f"{title}:"]
    for p in items:
        lines.append(f"  {p.name.ljust(width)} = {_display(p)}  [{p.source}]")
    return "\n".join(lines)


def _display(param: EffectiveParam) -> str:
    value = param.value
    if value is None:
        return f"(unset -> {param.fallback})" if param.fallback else "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
