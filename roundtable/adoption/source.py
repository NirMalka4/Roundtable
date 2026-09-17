"""Classify the running package's PEP 610 acquisition source."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import Distribution, PackageNotFoundError
from pathlib import Path
from typing import Protocol

from roundtable.updater import PACKAGE_NAME, is_under_any


class DistributionLike(Protocol):
    def read_text(self, filename: str) -> str | None: ...


def classify_installation_source(
    distribution: DistributionLike | None,
    package_path: Path,
    site_roots: Sequence[str],
) -> str:
    if distribution is None or not is_under_any(package_path, site_roots):
        return "local"
    direct_url = distribution.read_text("direct_url.json")
    if not direct_url:
        return "feed"
    return "local"


def installation_source() -> str:
    import site

    try:
        distribution: DistributionLike | None = Distribution.from_name(PACKAGE_NAME)
    except PackageNotFoundError:
        distribution = None
    roots = list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    user_root = site.getusersitepackages()
    if user_root:
        roots.append(user_root)
    return classify_installation_source(
        distribution,
        Path(__file__).resolve().parents[1],
        roots,
    )
