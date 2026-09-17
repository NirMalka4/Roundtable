"""Ordered domain values read from one explicit Configuration."""

from __future__ import annotations

from roundtable.graph import Configuration


def enum_values(configuration: Configuration, term: str, *, feature: str) -> tuple[str, ...]:
    """Ordered values for ``term``, resolved only from ``configuration``."""
    if configuration.domain_values is None:
        raise ValueError(
            f"{feature} requires ordered domain values for {term!r}; "
            f"configuration {configuration.name!r} declares no `domain_values`"
        )
    return configuration.domain_values.require(term, feature=feature)


def severity_levels(configuration: Configuration) -> tuple[str, ...]:
    """Severity enum, ascending (info..critical)."""
    return enum_values(configuration, "severity", feature="severity handling")


def severity_rank(configuration: Configuration, value: str) -> int:
    """Index of a severity in the ordered scale; -1 if unknown (case-insensitive)."""
    levels = severity_levels(configuration)
    v = value.strip().lower()
    return levels.index(v) if v in levels else -1


def severity_blocking(configuration: Configuration) -> tuple[str, ...]:
    """Blocking subset = severity minus 'info' (mirrors the vocab severity_blocking $def)."""
    return tuple(v for v in severity_levels(configuration) if v != "info")


def verdict_values(configuration: Configuration) -> tuple[str, ...]:
    """Emitted verdict enum (excludes the internal-only UNKNOWN fallback)."""
    return enum_values(configuration, "verdict", feature="review verdict handling")


def exploitability_ratings(configuration: Configuration) -> tuple[str, ...]:
    """Exploitability rating enum, ascending (trivial..unknown)."""
    return enum_values(
        configuration,
        "exploitability_rating",
        feature="exploitability validation",
    )
