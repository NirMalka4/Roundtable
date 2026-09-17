---
name: roundtable-add-agent
description: >-
  Add or modify one node in an existing Roundtable configuration bundle end to end: graph entry,
  edges, prompt, output schema, OVG gates, tools/MCP bindings, optional plugin behavior, and doctor
  validation. Use for requests such as "add one reviewer to Buddies". Do NOT use for whole-bundle
  creation (roundtable-add-config), prompt-body-only work (roundtable-agent-forge), standalone
  custom agents (agent-forge), MCP server implementation (mcp-builder), registry-only MCP changes
  (roundtable-manage-mcp), or running reviews (inspectorx-review).
---

# Add or change a Roundtable agent

Change one node through the run-scoped `Configuration` architecture. Derive graph, schema, and gate
choices from the explicit target and its consumers; do not maintain a second roster or dialect.

## References

| File | Read when |
| --- | --- |
| `references/output-contracts-and-gates.md` | Required before creating or changing any LLM node. |
| `references/current-architecture.md` | Before changing graph, plugin, fan-in, or execution wiring. |

## Script

Run [`scripts/inspect_bundle.py`](scripts/inspect_bundle.py):

```bash
python scripts/inspect_bundle.py --config <name-or-path> [--agent <key>] --json
```

The script emits configuration identity, topology, registered kinds, capability inventory, gates,
schemas, finding markers, and MCP bindings. It makes no edits and no recommendations.

## Workflow

1. Resolve the explicit target and load it through `Configuration.from_file`. Inspect it with the
   script before editing.
2. Trace the task, downstream consumer, required evidence, and domain result. Ask only when those
   cannot be established from code/config.
3. Select `kind` from `roundtable.engine.NODE_HANDLERS`. Use an LLM for variable judgment; use a
   registered code/reducer handler for deterministic transformation.
4. For an LLM body, invoke `roundtable-agent-forge`.
5. Define the schema from fields the downstream reader consumes. Then select deterministic gates
   whose declared `requires` paths and semantic invariant apply.
6. Add `edges` from actual data requirements. Let doctor derive finding producers, zero-drop
   consolidation coverage, cross-agent references, fan-out, and sink topology.
7. Grant the smallest tool and MCP surface supported by observed need. Delegate registry changes to
   `roundtable-manage-mcp`.
8. Register bundle Python behavior only through `roundtable.plugins`.
9. Run focused tests, `roundtable doctor --config <bundle>`, and
   `python scripts/gates.py`.

## Hard rules

- Use only `kind`, `edges`, and `system_prompt` in the current graph grammar; the meta-schema is the
  key roster.
- Give an LLM node `output_schema` and ordered `ovg_gates` only when downstream consumers require
  structured output. The schema implies the reserved submission transport; a schema-less node
  returns unvalidated text and declares no structured-output gates.
- Do not invent a downstream contract. Stop and ask when the actual consumer cannot establish it.
- Do not add a fan-in checker or prose kind roster; doctor and `NODE_HANDLERS` own those facts.
- Pass the configuration explicitly to runtime APIs; never teach ambient bundle selection.
