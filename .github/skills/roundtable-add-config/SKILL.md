---
name: roundtable-add-config
description: Create a complete Roundtable configuration bundle or compose a new graph configuration for explicit programmatic Engine use, including scaffolding, validation, doctor, and mock execution. Use for requests such as "create a new Roundtable configuration". Do NOT use to add one node to an existing bundle (roundtable-add-agent), change settings, run a review (inspectorx-review), author standalone custom agents (agent-forge), or make MCP-only changes (roundtable-manage-mcp).
---

# Add a Roundtable configuration

Create one document accepted by `Configuration.from_document` and one explicit execution path
through `Engine.run(configuration, inputs)`.

## References

| File | Read when |
| --- | --- |
| `references/configuration-contract.md` | Before designing topology, files, or execution. |
| `references/scaffold-spec.md` | Before invoking the scaffolder. |

## Script

Run [`scripts/scaffold_config.py`](scripts/scaffold_config.py):

```bash
python scripts/scaffold_config.py --spec <spec.json> [--write]
```

Preview is the default. `--write` refuses overwrite, writes atomically, runs doctor, and removes only
files it created when doctor fails. The script never chooses a model, fields, gates, node kind, or
publishing behavior.

## Workflow

1. Establish whether the configuration serves the review CLI or direct `Engine` use. Define
   external input keys, the domain result, and whether report projection/publishing is required.
2. Start with one source, one consumer/sink, `executor: dag`, and explicit `sink_cardinality`. Add
   projector, report, sink, or plugins only when the result contract requires them.
3. Write the explicit scaffold spec. Preview it, inspect every generated path and graph value, then
   use `--write`.
4. Add each LLM node through `roundtable-add-agent`.
5. Load the result with `Configuration.from_file`; run
   `roundtable doctor --config <bundle>`.
6. Execute a deterministic smoke:

   ```python
   configuration = Configuration.from_file(bundle)
   result = Engine("mock").run(configuration, {source_key: source_value})
   ```

7. Run focused tests and `python scripts/gates.py`.

## Hard rules

- Do not create a parallel bundle model or `AgentSpec`; use `Configuration`.
- Do not teach `ROUNDTABLE_CONFIG_ROOT` as the authoring boundary; pass roots and configurations
  explicitly.
- Add plugins only for domain behavior that graph/schema/gates cannot express.
- Do not create `enums.yaml` or impose review terms on a generic graph. Add optional
  `domain_values` only for an invoked run feature that needs ordered values, reusing an existing
  schema vocabulary when available.
- When severity-filtered publishing needs a default floor, declare
  `publishing.default_min_severity` from that configuration's severity values; do not infer one
  from a conventional label or rank position.
- If source inputs or the final domain result are unknown, ask before scaffolding.
