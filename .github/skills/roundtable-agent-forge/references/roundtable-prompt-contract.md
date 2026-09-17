# Roundtable prompt composition contract

Validated against the current source on 2026-08-10. Re-check by reading
`roundtable/engine/agent_runner/output_hint.py`, `roundtable/runtime/agent_setup.py`, and
`roundtable/graph/config_meta.schema.yaml`, then run the prompt-composition tests.

## What the body owns

The body owns role behavior, concern boundaries, evidence method, design altitude, tool-use intent
not already conveyed by metadata, and how to handle missing evidence. Its frontmatter contains only
`description:`.

## What the graph owns

`agent_graph.yaml` owns identity, `kind`, edges, prompt composition paths, model, tools, MCP,
timeouts, schema path, and gates. The accepted shape is the meta-schema; do not duplicate its field
set in prompt prose.

## What the schema and runtime append

`build_output_contract` reads the node's schema and appends `## Output contract` to the system
prompt. It includes:

- the schema `preface`;
- required root keys and whether additional root keys are accepted;
- inline property descriptions;
- descriptions of referenced shared-vocabulary definitions;
- `examples[0]`, or the declared conforming `output_example`.

The body therefore must not repeat output keys, types, enums, field meanings, JSON-only syntax, or
the canonical example. Put semantics in schema descriptions so runtime validation and model
guidance share one source.

Configuration-scoped `domain_values` serve run features that need ordered values; they are not
prompt content. A generic agent schema need not introduce review severity, verdict, or
exploitability terms.

Gate failure feedback is attempt-specific context. Do not pre-copy gate diagnostics or retry hints
into the stable body.
