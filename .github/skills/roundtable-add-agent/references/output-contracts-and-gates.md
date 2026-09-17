# Output contracts and validation gates

Read this before creating or changing any LLM node. Validated against
`roundtable/engine/agent_runner/output_hint.py`, `roundtable/validation/coherence.py`,
`roundtable/validation/pipeline.py`, and the shipped schemas/gates on 2026-08-10. Re-check with
focused validation tests and `roundtable doctor --config <bundle>`.

## Decide whether the LLM needs a schema

Use a schema when a downstream consumer requires structured data. Free-form prose cannot be
projected or checked reliably, but an LLM whose output is consumed as text should remain
schema-less rather than acquire a fictional JSON contract.

For a schema-backed LLM, the declared schema is:

1. the parameter contract of the engine-owned terminal `roundtable_submit_output` tool;
2. the structural contract enforced by the `json_schema` output-validation gate; and
3. the source of the system-prompt `## Output contract`.

The runtime renders required root keys, field descriptions, referenced shared-vocabulary
definitions, and the canonical example from the schema. `examples[0]` is also the mock/simulation
happy path, so it must validate structurally and pass every context-free error gate. A
schema-less LLM receives no submission tool, parsing, output validation, or validation retry; its
first successful backend response is forwarded unchanged.

## Introduce or change a schema

1. Trace the actual downstream projector, reducer, reader, or prompt field reference. List only
   fields it consumes. If no consumer establishes the contract, stop and ask.
2. Reuse a schema only when semantics and consumers are the same, not merely because the shape is
   similar.
3. Use JSON Schema Draft 2020-12, explicit `required`, and `additionalProperties: false` unless the
   consumer intentionally accepts extensions.
4. Put field semantics in `description`; those descriptions reach the model.
5. Reuse the bundle's shared vocabulary when one exists. Do not invent a new vocabulary or copy an
   enum from another bundle.
6. Mark a top-level array `x-finding-array: true` only when it is a finding corpus consumed by
   extraction/consolidation. Add `x-finding-adapter` only for real aliases; doctor validates adapter
   targets.
7. Add a realistic, conforming `examples[0]`. It is not an evaluation answer key. Use
   `output_example` only when a richer displayed example is necessary and still schema-conforming.
8. Run doctor to prove schema compilation, example validity, cross-agent references, finding
   metadata, and downstream coherence.

## Choose gates from invariants

- Wire `json_schema` for every schema-backed LLM. The schema already selects the submission
  transport; omitting structural enforcement would make the declared contract incoherent.
- Wire an existing domain gate only when every declared `requires` path exists and its invariant
  applies semantically. Doctor rejects dead gates and warns when fields suggest an applicable gate
  is unwired.
- Add a gate only for a deterministic invariant computable from `GateRequest.output`,
  `GateRequest.context`, exact `requires`, and explicit `params`. Subjective quality belongs in an
  agent/Judge.
- Use `error` when a violation makes output unusable or unsafe for its downstream consumer. Use
  `warn` when the output remains usable and retry cost is unjustified.

## Introduce a domain gate

1. Put the callable in the owning bundle's plugin module and register it with
   `roundtable.plugins.register_gate_function`.
2. Return neutral `Diagnostic` values. Python does not choose error/warn level.
3. Declare the gate in the bundle `gates.yaml` with `fn`, `default_level`, optional
   `default_hint`, and exact `requires` paths.
4. Add a concise hint under the bundle `hints/` only when an error can be corrected on retry.
5. Wire it in the node's ordered `ovg_gates`. Override level, hint, or params only when this node's
   contract genuinely differs.
6. Add positive, negative, and boundary unit cases for the callable. Add an example that passes all
   context-free error gates.
7. Run doctor. Fix schema, gate, plugin, or example source rather than adding aliases/coercion.

## Proof checklist

- The complete example validates against Draft 2020-12.
- Each gate has a distinct deterministic defect and reachable `requires` paths.
- Positive, negative, and boundary behavior has executed.
- Prompt body does not duplicate schema fields or examples.
- `roundtable doctor --config <bundle>` and `python scripts/gates.py` pass.
