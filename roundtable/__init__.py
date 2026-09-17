"""Roundtable: a config-driven multi-agent orchestration engine.

Runs an agent graph (default bundle: the Buddies code-review roster) over your
git changes via the `github-copilot-sdk` and reconciles the agents' outputs into
one verdict. Package layout spans
config/context/runtime/orchestration/validation/ado/persistence.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from ._version import __version__ as _authored_version
from .updater import current_install_is_editable


def _resolve_version() -> str:
    """Report the running version, kept provenance-honest for unreleased trees.

    A wheel installed from the feed reports its clean ``X.Y.Z`` metadata version —
    a real release. An editable/source run reuses the *authored* ``_version.py``
    value but suffixes ``+dev`` so provenance remains honest. PR adoption labels
    record this version together with the separately detected feed/local package
    acquisition source.
    """
    try:
        base = _pkg_version("roundtable")
    except PackageNotFoundError:  # source checkout without an installed distribution
        base = _authored_version
    if current_install_is_editable() and "+" not in base:
        return f"{base}+dev"
    return base


__version__ = _resolve_version()
