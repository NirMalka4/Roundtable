"""Per-agent backend execution and schema-submission handling.

Public surface (unchanged from the former flat module — importers keep
``from roundtable.engine import X``):

  * run loop      — ``run_agent_with_ovg``, ``Backend``
  * result model  — ``AgentRunOutcome``, ``AttemptDetail``
  * output hint   — ``build_output_contract`` (system-prompt contract), ``build_schema_hint``
                     (+ ``_resolve_output_example`` for tests/coverage)
  * text utilities — ``strip_preamble``, ``truncate_for_echo``

Internal layout: ``model`` (dataclasses), ``output_hint`` (schema → prompt anchor),
``retry_feedback`` (text utilities), ``run_loop`` (the ``_RunState`` retry loop).
"""

from __future__ import annotations

from .model import AgentRunOutcome, AttemptDetail
from .output_hint import (
    _field_meanings_block,
    _referenced_vocab_terms,
    _resolve_output_example,
    build_output_contract,
    build_schema_hint,
)
from .retry_feedback import strip_preamble, truncate_for_echo
from .run_loop import DEFAULT_AGENT_TIMEOUT_S, Backend, run_agent_with_ovg

__all__ = [
    "DEFAULT_AGENT_TIMEOUT_S",
    "AgentRunOutcome",
    "AttemptDetail",
    "Backend",
    "_field_meanings_block",
    "_referenced_vocab_terms",
    "_resolve_output_example",
    "build_output_contract",
    "build_schema_hint",
    "run_agent_with_ovg",
    "strip_preamble",
    "truncate_for_echo",
]
