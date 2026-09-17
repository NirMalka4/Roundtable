One or more Judge overlay / reference / observation entries violate the per-entry
contract. The Judge references findings by identity and never re-emits their payload.

CORRECTIVE ACTION — per array:
- verdict_overlay[]: each entry needs a non-empty 'finding_id' and 'source_agent'
  (strings), a boolean 'blocking', and a non-empty 'judge_justification'. Optional
  'verdict_severity' must be one of CRITICAL/HIGH/MEDIUM/LOW (any case). Optional
  'merged_with' is an array of {source_agent, finding_id}. Do NOT put payload fields
  (locations, evidence_chain, evidence, exploitability, stepsToReproduce, trace, impact, fix,
  suggestion, description, recommendation, title) on an overlay entry — the specialist
  record owns those; reference the finding by (source_agent, finding_id) instead.
- validated_safe[] / needs_human_judgment[]: each entry needs a non-empty 'finding_id',
  'source_agent', and 'reason'.
- judge_observations[]: each entry needs an 'id' matching /^JO-<number>$/, a 'severity'
  of CRITICAL/HIGH/MEDIUM/LOW (any case), a non-empty 'summary', and a boolean 'blocking'.
