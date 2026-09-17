# Current Roundtable authoring architecture

Validated against the current source on 2026-08-10. Re-check with
`python .github/skills/roundtable-add-agent/scripts/inspect_bundle.py --config buddies --json`,
`roundtable doctor --config buddies`, and the cited source.

- `roundtable.graph.Configuration` is the sole model. File and memory documents enter through
  `Configuration.from_file` and `Configuration.from_document`.
- `roundtable/graph/config_meta.schema.yaml` is the accepted `agent_graph.yaml` grammar. Do not
  maintain a prose key roster.
- Valid `kind` values are the live keys of `roundtable.engine.NODE_HANDLERS`.
- Execution crosses `Engine.run(configuration, inputs)` with an explicit configuration.
- Ordered domain values are optional `Configuration.domain_values`, never a required
  `enums.yaml` sidecar. Reuse a schema vocabulary when it already owns the values.
- Optional `Configuration.publishing.default_min_severity` supplies the bundle's CLI publishing
  floor; it must name that configuration's declared severity value.
- Bundle Python behavior registers through `roundtable.plugins`; graph `plugins` lists modules.
- An LLM node with `output_schema` uses the reserved terminal submission transport and ordered
  `ovg_gates`. A schema-less LLM node returns backend text unchanged.
- MCP registry provisioning lives in `roundtable/mcp/mcp_servers.yaml`; graph `mcp` opts a node into
  provisioning; graph `tools` grants permission as `<server>/<bare-tool>`.
- Optional graph `tool_policy` owns exceptional execution limits for concrete SDK tool names.
  Backend enforcement consumes that declaration and must not branch on an agent key.

`roundtable.runtime.validate_agents`, surfaced by doctor, already checks graph integrity, prompts,
models/tools/MCP, schema/gate coherence, cross-agent field references, finding adapters,
extractor/vocabulary coherence, zero-drop dossier fan-in, conditional edges, fan-out, sink
cardinality, and sink resolution. Fix the declared source when it fails. Do not add aliases,
coercion, a second kind list, or a separate fan-in checker.
