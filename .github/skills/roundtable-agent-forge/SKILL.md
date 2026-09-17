---
name: roundtable-agent-forge
description: Write or review the prompt body of a Roundtable bundle agent, including role, design altitude, instruction placement, tool-use prose, output-contract separation, and evaluation boundaries. Use for requests such as "rewrite this Roundtable reviewer prompt" or "audit this bundle agent body". Do NOT use for standalone Copilot custom-agent profiles or .agent.md frontmatter (agent-forge), graph/schema/gate/config wiring (roundtable-add-agent), skill authoring (skill-forge), or review execution (inspectorx-review).
---

# Roundtable agent forge

Author or review only the human-written body of a Roundtable bundle agent. A role steers behavior
and process; it does not supply expertise. The graph, schema, and runtime supply contracts that the
body must not restate.

## References

| File | Read when |
| --- | --- |
| `references/evidence.md` | Before making a prompt-design claim; cite its graded evidence ID. |
| `references/prompt-body-rubric.md` | Before authoring or reviewing a body. |
| `references/roundtable-prompt-contract.md` | Before deciding whether content belongs in the body, graph, or schema. |

## Authoring workflow

1. Read the target node, its consumers, schema, graph-supplied tools/context, and observed failure
   evidence. Ask only for behavior that those sources do not establish.
2. Choose the design altitude: affordances and light planning for variable judgment; codified steps
   for a fragile, repeatable procedure.
3. Write a crisp role and scope. Supply expertise through delivered context and tools, never an
   unsupported persona claim.
4. Put non-negotiables near the start and restate them at the end. Pair a hard rule with what to do
   when its precondition cannot be met.
5. Describe tool-use intent only where metadata does not convey it. Do not name tools the graph has
   not granted.
6. Keep the generated output shape, field definitions, and examples out of the body.
7. Validate the complete configuration with `roundtable doctor --config <bundle>`, then evaluate
   representative outcomes outside the prompt.

## Review workflow

1. Compare the body with the rubric and the composed contract.
2. Report defects by evidence ID and observed text: persona-as-expertise, wrong altitude, misplaced
   rules, unsupported tools, duplicated schema, leaked evaluation cases, or narrated self-checks.
3. Revise only defects supported by the target's requirements or measured outcomes.
4. Run doctor and the external evaluation again. Never call inspection of the prose proof that it
   works.

## Hard rules

- Bundle prompt frontmatter contains `description:` only; graph identity and capabilities remain in
  `agent_graph.yaml`.
- Never duplicate schema fields, meanings, or canonical examples in the body; if the schema cannot
  express the required contract, return to `roundtable-add-agent`.
- Keep evaluation prompts and expected answers outside the agent prompt tree and do not point the
  body at them.
- Verify artifacts and outcomes, not the agent's account of its reasoning.
