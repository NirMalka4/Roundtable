"""Static CI metadata and authenticated runtime capabilities."""

from .inventory import (
    RuntimeCapabilities,
    always_on_tool_names,
    builtin_tool_names,
    copilot_cli_version,
    inventory_cli_version,
    inventory_provenance,
    resolve_runtime_capabilities,
    write_inventory,
)

__all__ = [
    "RuntimeCapabilities",
    "always_on_tool_names",
    "builtin_tool_names",
    "copilot_cli_version",
    "inventory_cli_version",
    "inventory_provenance",
    "resolve_runtime_capabilities",
    "write_inventory",
]
