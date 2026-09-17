"""Engine-generic grounding transforms applied to a validated agent response.

Grounding replaces model-narrated evidence with facts read directly from the
review checkout, so a downstream consumer (the judge, the report, the ADO
publish) sees byte-truth rather than the model's paraphrase. Every transform is
**opt-in via schema metadata** and a **no-op** for any agent whose schema does
not declare it — the engine hardcodes no field names.
"""

from .source_excerpt import ground_source_excerpts

__all__ = ["ground_source_excerpts"]
