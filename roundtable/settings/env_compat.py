"""Backward-compatibility shim for the ``INSPECTORX_*`` → ``ROUNDTABLE_*`` rename.

For one deprecation release the engine reads its new ``ROUNDTABLE_*`` environment
variables but transparently falls back to the legacy ``INSPECTORX_*`` name when the
new one is unset, emitting a one-time :class:`DeprecationWarning` per key. Wrapping
``os.environ`` once at each entry point (via :class:`LegacyEnvFallback`) covers the
whole settings surface without touching the individual ``env.get(NAME)`` call sites.

# rebrand-compat: delete this module when the deprecation window closes (see Q5).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator, Mapping

CURRENT_PREFIX = "ROUNDTABLE_"
LEGACY_PREFIX = "INSPECTORX_"

# The pre-rebrand config filename, still discovered as a fallback for one release.
LEGACY_CONFIG_FILENAME = "inspectorx.yaml"

_warned: set[str] = set()


def _warn_once(current: str, legacy: str) -> None:
    if legacy in _warned:
        return
    _warned.add(legacy)
    warnings.warn(
        f"${legacy} is deprecated; use ${current} instead "
        f"(the legacy name is honored for one release).",
        DeprecationWarning,
        stacklevel=3,
    )


def _legacy_name(key: str) -> str | None:
    if key.startswith(CURRENT_PREFIX):
        return LEGACY_PREFIX + key[len(CURRENT_PREFIX) :]
    return None


class LegacyEnvFallback(Mapping[str, str]):
    """A read-only view over ``base`` that resolves ``ROUNDTABLE_X`` to a legacy
    ``INSPECTORX_X`` value when the new name is absent (warning once per key)."""

    def __init__(self, base: Mapping[str, str]) -> None:
        self._base = base

    def __getitem__(self, key: str) -> str:
        if key in self._base:
            return self._base[key]
        legacy = _legacy_name(key)
        if legacy is not None and legacy in self._base:
            _warn_once(key, legacy)
            return self._base[legacy]
        raise KeyError(key)

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        if key in self._base:
            return True
        legacy = _legacy_name(key) if isinstance(key, str) else None
        return legacy is not None and legacy in self._base

    def __iter__(self) -> Iterator[str]:
        return iter(self._base)

    def __len__(self) -> int:
        return len(self._base)


def with_legacy_fallback(env: Mapping[str, str]) -> Mapping[str, str]:
    """Wrap ``env`` so absent ``ROUNDTABLE_*`` keys fall back to ``INSPECTORX_*``.

    Idempotent: an already-wrapped mapping is returned unchanged.
    """
    if isinstance(env, LegacyEnvFallback):
        return env
    return LegacyEnvFallback(env)
