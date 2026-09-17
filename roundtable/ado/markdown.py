"""ado.markdown: the one safety policy for agent-authored Markdown blocks.

Both renderers — the PR comment (``comment_format``) and the offline report
(``report``) — interleave agent-written Markdown into a larger document, so the
policy that makes that safe lives here once rather than in each:

* the watermark terminator ``-->`` is always neutralized, so agent text can
  neither break the layout nor forge the watermark; and
* triple-backtick runs are defanged ONLY when unbalanced (odd count) — an
  unclosed fence would bleed into the rest of the document, while a *balanced*
  fence is legitimate agent code and must survive to render as a real block.

Block-valued agent text (a remediation ``prose``, a prose ``fix``) is emitted
verbatim through :func:`clean_markdown`, never flattened: the contract types it
as Markdown, and collapsing its newlines destroys the lists, paragraphs and
fences that make it Markdown. Agent-agnostic — no field, agent or config
knowledge.
"""

from __future__ import annotations


def neutralize_fences(text: str) -> str:
    """Neutralize the watermark terminator always; defang triple-backtick runs
    ONLY when unbalanced (odd count), since an unclosed fence would bleed into the
    rest of the document. Balanced fenced blocks are legitimate agent code
    (```suggestion / ```lang) and are preserved so they render as real,
    one-click-appliable blocks."""
    safe = text.replace("-->", "--\u200b>")
    if safe.count("```") % 2 != 0:
        safe = safe.replace("```", "`\u200b``")
    return safe


def clean_markdown(text: str | None) -> str:
    """Agent-authored Markdown, made safe to embed but structurally verbatim."""
    return neutralize_fences(text.strip()) if text and text.strip() else ""
