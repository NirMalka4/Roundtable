# Roundtable prompt-body rubric

Use the complete composed prompt, graph, and schema as evidence. A prose smell is a question until
the target or an outcome demonstrates a defect.

## Role and scope

- Does the role constrain behavior, concern, and format without claiming unsupported expertise?
  (`E-RQ1-persona-null`, `E-RQ1-steer`)
- Is necessary knowledge actually delivered by an edge, shared context, or tool? (`E-RQ2-jit`)
- Are exclusions concrete enough to prevent concern overlap with sibling agents?

## Design altitude

- Variable investigation: does the body provide evidence standards, affordances, and a goal rather
  than a fixed sequence? (`E-DESIGN-react`)
- Fragile repeatable procedure: are ordering and error branches explicit? (`E-DESIGN-sop`)
- Is every step necessary to fully specify behavior, rather than ceremony? (`E-DESIGN-altitude`)

## Placement and failure handling

- Are non-negotiables visible near the beginning and restated at the end?
- Are conflicts removed or ordered with the intended rule last?
- Does every unconditional instruction say what to do when required information is unavailable?
  (`E-RQ3-placement`)

## Tool prose

- Does prose state role intent rather than copy tool names or schemas?
- Does every described capability exist in graph `tools`/`mcp`?
- Is the tool surface narrow enough that each grant has a distinct purpose? (`E-TOOLS`)

## Output and evaluation

- Does the body avoid restating keys, types, enums, descriptions, and canonical examples supplied by
  the schema?
- Does it verify evidence/artifacts rather than trust narrated reasoning? (`E-FAITH`)
- Are evaluation prompts and expected answers absent from, and unlinked by, the prompt?
- Were revisions driven by external outcome cases rather than inspection alone? (`E-EVAL`)
