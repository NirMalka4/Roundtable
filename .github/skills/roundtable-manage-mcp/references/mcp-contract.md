# Roundtable MCP contracts

Validated against `roundtable/mcp/registry.py`, `roundtable/runtime/agent_setup.py`, and
`roundtable/graph/config_meta.schema.yaml` on 2026-08-10. Re-check with `validate_mcp.py`, doctor,
and MCP registry tests.

## Keep three contracts separate

1. **Provisioning registry:** `roundtable/mcp/mcp_servers.yaml` defines command, arguments, runtime
   placeholders/requirements, tool inventory, timeout, and optional advertisement bindings.
2. **Node opt-in:** graph `mcp` declares which registered servers Roundtable may provision for a
   node.
3. **Permission:** graph `tools` grants `<server>/<bare-tool>`. An omitted `tools` value is
   unrestricted; an explicit list must grant the intended MCP tools.

Changing one does not imply either of the others.

## Static and runtime-parameterized specs

Static specs contain no placeholders. Runtime placeholders use `${field}` where `field` is a real
`McpBuildContext` dataclass field, and every placeholder must also appear in `requires`. Missing
required context drops that server for the run rather than emitting malformed launch data.

MCP launch parameters remain in `McpBuildContext`; do not route them through a configuration's
optional ordered `domain_values`.

The public `roundtable.mcp.validate_mcp_specs` seam validates types, known context fields,
placeholder/requirement agreement, and advertised binding tools. Do not reproduce these rules in a
skill-local schema table.

## Bindings and usage

`bindings` is advertisement metadata only. Add it when a current renderer consumes the capability
label/tool pair, and ensure the bare tool is in the server inventory.

An optional graph `mcp[].usage` file carries role intent that tool metadata does not communicate.
It must not copy raw tool names; doctor checks the reference and rejects that drift.

## Verification

Static authoring runs inspection, validation, doctor, focused tests, and repository gates. A live
prewarm/start can launch processes, access credentials, and contact external services, so offer it
only on explicit request. Implementing server or tool behavior belongs to `mcp-builder`.
