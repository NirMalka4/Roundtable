"""Import guard + version surface for the SDK backend.

The ``github-copilot-sdk`` (imported as ``copilot``) is the LLM backend's core
dependency. Two concerns live here so no other module hard-imports ``copilot``:

* :func:`require_sdk` — lazy-import with a single, actionable error if the package
  is missing (a broken/incomplete install), keeping ``copilot`` out of the
  import path of offline commands such as ``--help`` and ``view``.
* :func:`sdk_versions` — surface the installed package + wire-protocol versions
  for diagnostics and the ``doctor`` check.

The SDK speaks a *version-checked* JSON-RPC protocol to the local ``copilot`` CLI;
the real handshake happens inside ``CopilotClient.start()`` (which raises on a
mismatch — classified as :attr:`errors.BackendOutcome.PROTOCOL_MISMATCH`). This
module only proves the Python package is importable and pinned as expected; it
does not itself talk to the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

# Pinned EXACT in pyproject (core dependency) — the wire protocol is version-locked
# to the CLI, so a drifting install is a real hazard, not a nuisance. Kept here as
# the single source for the runtime belt-and-suspenders check below.
PINNED_SDK_VERSION = "1.0.10rc1"

_INSTALL_HINT = (
    "The 'github-copilot-sdk' package (the LLM backend) is not importable — this "
    "indicates a broken or incomplete install. Reinstall with:\n"
    "    pip install --force-reinstall roundtable"
)


class SdkUnavailableError(ImportError):
    """``github-copilot-sdk`` is not importable (broken/incomplete install)."""


@dataclass(frozen=True)
class SdkVersions:
    """Installed SDK package version and its declared wire-protocol version."""

    package: str
    protocol: str

    @property
    def matches_pin(self) -> bool:
        return self.package == PINNED_SDK_VERSION


def require_sdk() -> ModuleType:
    """Import and return the ``copilot`` module, or raise a clear install error.

    Kept as the *only* place that imports ``copilot``, so offline commands
    such as ``--help`` and ``view`` never pay the import cost.
    """
    try:
        import copilot
    except ImportError as exc:
        raise SdkUnavailableError(_INSTALL_HINT) from exc
    return copilot


def sdk_versions() -> SdkVersions:
    """Return the installed SDK package + wire-protocol versions (raises if absent)."""
    copilot = require_sdk()
    package = str(getattr(copilot, "__version__", "unknown"))
    protocol_value = getattr(copilot, "_sdk_protocol_version", "unknown")
    if isinstance(protocol_value, ModuleType):
        getter = getattr(protocol_value, "get_sdk_protocol_version", None)
        protocol_value = (
            getter()
            if callable(getter)
            else getattr(protocol_value, "SDK_PROTOCOL_VERSION", "unknown")
        )
    protocol = str(protocol_value)
    return SdkVersions(package=package, protocol=protocol)


def require_terminal_tool_support() -> ModuleType:
    """Fail startup unless the pinned SDK exposes the terminal-tool contract."""

    copilot = require_sdk()
    tool = getattr(copilot, "Tool", None)
    annotations = getattr(tool, "__annotations__", {})
    if tool is None or "parameters" not in annotations or "is_terminal" not in annotations:
        raise SdkUnavailableError(
            "The installed github-copilot-sdk lacks terminal custom-tool support "
            f"required by Roundtable; install version {PINNED_SDK_VERSION}."
        )
    return copilot
