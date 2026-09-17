---
name: roundtable-manage-mcp
description: Maintain Roundtable's provisioned integration registry and least-privilege node
  bindings, including mcp_servers.yaml, graph opt-in, permission, usage policy, and static
  reachability. Use for requests such as "register this existing service for Roundtable" or "scope
  its operations to one reviewer". Do NOT use to build protocol implementations or tool schemas
  (mcp-builder), configure generic third-party Copilot integrations, create a whole bundle
  (roundtable-add-config), author standalone custom agents (agent-forge), or perform unrelated graph
  authoring (roundtable-add-agent).
---

# Manage Roundtable MCP integration

Keep provisioning, node opt-in, and tool permission as three explicit contracts. Static validation
is the default; starting a server is a separate, potentially credentialed operation.

## Reference

Read `references/mcp-contract.md` before changing the registry, runtime parameters, bindings, usage
policy, or graph grants.

## Scripts

Run [`scripts/inspect_mcp.py`](scripts/inspect_mcp.py) and
[`scripts/validate_mcp.py`](scripts/validate_mcp.py):

```bash
python scripts/inspect_mcp.py --config <name-or-path> --json
python scripts/validate_mcp.py --config <name-or-path> --json
```

Inspection reports validated registry specs, placeholders, requirements, advertised bindings, tool
inventories, consuming agents, and grants. Validation reuses the public MCP registry seam and doctor
contracts.

## Workflow

1. Classify the request: bind an existing server; add/change a static definition; add a
   runtime-parameterized definition; change tools/bindings/usage; or investigate reachability.
2. Inspect the registry and explicit target configuration.
3. Edit provisioning in `roundtable/mcp/mcp_servers.yaml`, node opt-in in graph `mcp`, and
   permission in graph `tools` as separate changes.
4. Put only role intent missing from tool metadata in a `usage` file. Never copy raw tool names
   there.
5. Add `bindings` only when a current renderer consumes the advertisement.
6. Run `validate_mcp.py`, `roundtable doctor --config <bundle>`, focused tests, and
   `python scripts/gates.py`.
7. Offer a live prewarm/start probe only when explicitly requested.

## Hard rules

- Every `${field}` is a real `McpBuildContext` field and is listed in `requires`.
- Every advertised binding names a tool in that server's inventory.
- A node that provisions a server must have compatible permission in `tools`; unrestricted tools
  are explicit by omission, not inferred from `mcp`.
- Do not implement server or tool behavior here; delegate that work to `mcp-builder`.
- Never start processes, use credentials, or access external systems as a side effect of static
  authoring.
