"""Declarative validation for schema-backed output submissions.

The runtime validates parsed values submitted through ``roundtable_submit_output``.
Raw parsing helpers remain for diagnostics and compatibility tests; schema-less LLM
responses do not enter this package.
"""

from __future__ import annotations

from .coherence import (
    validate_conditional_predicates,
    validate_cross_agent_refs,
    validate_dossier_completeness,
    validate_extractor_vocab_coherence,
    validate_fan_out,
    validate_finding_adapter_targets,
    validate_ovg_coherence,
)
from .gate_kit import (
    MISSING,
    Diagnostic,
    GateRequest,
    absent,
    is_js_integer,
    is_js_number,
    js_str,
    js_typeof,
    nonempty_str,
    nullish,
)
from .gates import (
    GateSpec,
    SchemaLoadError,
    compile_validator,
    format_schema_errors,
    load_gate_registry,
    load_hint,
    load_schema_document,
    register_gate_function,
)
from .json_format import (
    OvgResult,
    hypothetical_closure,
    json_parse_error_message,
    parse_json_value,
    validate_json_format,
)
from .pipeline import (
    LeveledDiagnostic,
    PipelineResult,
    evaluate_agent_output,
    evaluate_agent_value,
    run_parsed_pipeline,
)
from .submission_schema import build_submission_schema

__all__ = [
    "MISSING",
    "Diagnostic",
    "GateRequest",
    "GateSpec",
    "LeveledDiagnostic",
    "OvgResult",
    "PipelineResult",
    "SchemaLoadError",
    "absent",
    "build_submission_schema",
    "compile_validator",
    "evaluate_agent_output",
    "evaluate_agent_value",
    "format_schema_errors",
    "hypothetical_closure",
    "is_js_integer",
    "is_js_number",
    "js_str",
    "js_typeof",
    "json_parse_error_message",
    "load_gate_registry",
    "load_hint",
    "load_schema_document",
    "nonempty_str",
    "nullish",
    "parse_json_value",
    "register_gate_function",
    "run_parsed_pipeline",
    "validate_conditional_predicates",
    "validate_cross_agent_refs",
    "validate_dossier_completeness",
    "validate_extractor_vocab_coherence",
    "validate_fan_out",
    "validate_finding_adapter_targets",
    "validate_json_format",
    "validate_ovg_coherence",
]
