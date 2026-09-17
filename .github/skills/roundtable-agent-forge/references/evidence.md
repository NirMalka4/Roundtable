# Vendored prompt-design evidence

This is the Roundtable-specific subset of the personal `agent-forge` evidence ledger, retained with
its original IDs, grades, and attribution. Tiers: T1 peer-reviewed/official research, T2 preprint,
T3 vendor guidance. Grades: A independently reproduced benchmark evidence, B one benchmarked study
or peer-reviewed survey, C expert/vendor guidance.

## Role and supplied expertise

### `E-RQ1-persona-null` — persona labels do not reliably raise factual accuracy — **[T1, B]**

Across 162 roles, four model families, and 2,410 factual questions, an expert persona did not improve
accuracy over no persona. Role can steer behavior, but knowledge must come from context/retrieval.
Sources: Zheng et al., *When "A Helpful Assistant" Is Not Really Helpful*, Findings of EMNLP 2024,
arXiv:2311.10054; Hu & Collier, *Quantifying the Persona Effect*, ACL 2024, arXiv:2402.10811.

### `E-RQ1-steer` — role steers behavior, tone, format, and process — **[T3, C; T2, B]**

Use a crisp role to shape behavior and process, not to claim knowledge. Sources: Anthropic,
*Effective Context Engineering*; OpenAI, *GPT-4.1 Prompting Guide*; Dong et al.,
arXiv:2601.06403.

### `E-RQ2-jit` — retrieve knowledge instead of stuffing or asserting it — **[T3, C; T2, B]**

Keep identifiers and load relevant context on demand. Sources: Anthropic, *Effective Context
Engineering*; Gao et al., *RAG Survey*, arXiv:2312.10997.

## Design altitude

### `E-DESIGN-altitude` — use the minimal information that fully specifies behavior — **[T3, C]**

Avoid brittle hardcoded logic and vague hand-waving. Source: Anthropic, *Effective Context
Engineering*.

### `E-DESIGN-react` — affordances plus light planning for variable tasks — **[T1, A]**

Interleaving reasoning and tool action reduced hallucination/error propagation and improved
interactive benchmarks. Source: Yao et al., *ReAct*, ICLR 2023, arXiv:2210.03629.

### `E-DESIGN-sop` — codify fragile, well-understood procedures — **[T1, B]**

Structured operating procedures reduced cascading hallucination in software tasks. This is the
counter-case to applying ReAct universally. Source: Hong et al., *MetaGPT*, ICLR 2024,
arXiv:2308.00352.

### `E-DESIGN-output` — consumed output needs a verifiable contract — **[T3, C]**

An explicit shape makes outcomes mechanically verifiable. Source: OpenAI, *GPT-4.1 Prompting
Guide*.

## Placement, tools, and proof

### `E-RQ3-placement` — place critical instructions deliberately — **[T3, C]**

Put non-negotiables near the start and restate them at the end; resolve conflicts with the intended
rule last; pair hard rules with an escape hatch. Source: OpenAI, *GPT-4.1 Prompting Guide*. The
positional basis is Liu et al., *Lost in the Middle*, TACL 2024, arXiv:2307.03172 **[T1, A]**.

### `E-TOOLS` — tool contracts should be precise and least-privilege — **[T3, C]**

Self-contained, non-overlapping tools are easier to select; bloated sets are a common failure.
Sources: Anthropic, *Effective Context Engineering*; OpenAI, *GPT-4.1 Prompting Guide*.

### `E-FAITH` — narrated reasoning is not proof — **[T1, B]**

Models can systematically misreport what drove an answer. Verify outcomes and actions instead.
Sources: Turpin et al., NeurIPS 2023, arXiv:2305.04388; Lanham et al., arXiv:2307.13702; Chen et
al., arXiv:2505.05410.

### `E-EVAL` — inspection does not prove a prompt — **[T3, C]**

Iterate from task-specific outcomes and observed failures, not prose confidence. Sources: OpenAI,
*GPT-4.1 Prompting Guide*; Anthropic, *Effective Context Engineering*.
