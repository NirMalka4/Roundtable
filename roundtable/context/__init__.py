"""Typed context rendering, injection, and deterministic plugin seams."""

from .ado_context import render_ado_context_for_servers
from .builder import diff_cap_stats, render_git_context_section
from .enrichers import (
    EnricherFn,
    enricher_names,
    get_enricher,
    register_enricher,
)
from .extractors import (
    ExtractorFn,
    extractor_names,
    get_extractor,
    register_extractor,
)
from .hint_staging import HintTooLarge, stage_hint
from .injection import Corpus, build_injection_sections, resolve_corpus
from .session_header import SessionHeaderInputs, build_session_header, parse_diff_stats

__all__ = [
    "Corpus",
    "EnricherFn",
    "ExtractorFn",
    "HintTooLarge",
    "SessionHeaderInputs",
    "build_injection_sections",
    "build_session_header",
    "diff_cap_stats",
    "enricher_names",
    "extractor_names",
    "get_enricher",
    "get_extractor",
    "parse_diff_stats",
    "register_enricher",
    "register_extractor",
    "render_ado_context_for_servers",
    "render_git_context_section",
    "resolve_corpus",
    "stage_hint",
]
