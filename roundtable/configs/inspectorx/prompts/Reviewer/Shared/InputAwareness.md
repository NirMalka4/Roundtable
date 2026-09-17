<!--
  Shared input-awareness protocol injected into agent system prompts via
  `additional_agent_input` in agent_graph.yaml (see agent_setup.py). It tells
  every agent to build an input ledger from the already-injected context
  sections before its first tool call. Keep the "What did I receive?" list in
  sync with the section names actually rendered into agent context.
-->
## Input Awareness & Consumption Protocol (ALL AGENTS MUST APPLY)

Before your first substantive tool call, build a short **input ledger** from the context already injected into the session.

### The 3 Required Questions

1. **What did I receive?**
   - `Change Under Review`
   - `Git Context` and `Git History`
   - hard dependency outputs from other agents
   - soft dependency outputs from other agents (when present)
   - orchestrator-injected packs such as `AgentDossier`, `security_intent_pack`, `security_focus_pack`, and changed-test evidence

2. **Why was it injected?**
   Usually each artifact serves one or more of these roles:
   - **scope setter** — tells you which files, paths, or concerns matter
   - **semantics guide** — explains what a symbol, value, or flow actually represents
   - **prioritizer** — tells you where to spend depth first
   - **evidence accelerator** — gives you already-derived facts you should build on
   - **de-duplication guard** — prevents rediscovering, re-testing, or re-deriving work that already exists

3. **How should I use it?**
   - Start from injected summaries and dependency outputs **before** broad discovery
   - Reuse them to narrow file reads, reduce search space, and focus hypotheses
   - Only re-derive a fact if the injected artifact is missing, contradictory, stale, or not detailed enough for the specific claim you need to prove
   - When deeper evidence is required, drill down from the injected file/path/symbol references instead of rediscovering the whole repo

### Mandatory Behavior

- Treat dependency outputs as **inputs with semantics**, not as decorative context.
- If an upstream agent already mapped scope, flow, or intent, use that as your **starting boundary**.
- If changed tests already prove a scenario, do not rediscover it unless a real gap remains (e.g., attacker reachability, missing assertion, contradictory evidence).
- If the injected context or a dependency output already answers a factual question, reuse it first and spend tool calls on the remaining uncertainty only.
- When you do need additional reads, be able to state which question the existing injected input could not answer.

### Anti-Patterns

- Rebuilding the Profiler's flow map from scratch when `data_flow_map` is already injected
- Re-querying branch / PR / build facts that already exist in the injected `Git Context` / `Git History`
- Ignoring dossier semantics and treating all upstream results as undifferentiated raw text
- Launching broad repository discovery before reading the dependency outputs already selected for you

