# Scaffolder spec

The JSON object supplies every design choice. Required top-level values are `root`, `name`,
`executor`, `sink`, `sink_cardinality`, `source`, and `node`. Optional graph declarations are
`branding`, `projector`, `report`, `plugins`, `max_steps`, `domain_values`, and `publishing`. Use
`domain_values` only when a run feature needs ordered values; select either an existing schema
vocabulary or inline values as described in `configuration-contract.md`. A non-null
`publishing.default_min_severity` must name one of the declared severity values.

`source` requires `key` and `emoji`. `node` requires `key`, `kind`, and `emoji`. An LLM node also
requires `agent_id`, `display_name`, `description`, `prompt_body`, `model`, the complete
`output_schema` object, and non-empty `ovg_gates`; optional capability fields are copied without
interpretation. A code/reducer node
requires `code_fn`.

Example shape:

```json
{
  "root": "C:/tmp/example-bundle",
  "name": "example",
  "executor": "dag",
  "sink": null,
  "sink_cardinality": "one",
  "source": {"key": "Input", "emoji": "I"},
  "node": {
    "key": "Result",
    "kind": "llm",
    "emoji": "R",
    "agent_id": "result",
    "display_name": "Result",
    "description": "Produces the requested deterministic demo result.",
    "prompt_body": "# Result\n\nUse the supplied input.",
    "model": "gpt-5.6-sol",
    "output_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "additionalProperties": false,
      "required": ["value"],
      "properties": {"value": {"type": "string", "description": "The domain result."}},
      "examples": [{"value": "example"}]
    },
    "ovg_gates": [{"gate": "json_schema"}],
    "tools": []
  }
}
```

Preview first. A write refuses any generated target that already exists. Plugin modules must already
exist under the target root so the scaffolder never invents plugin behavior.
