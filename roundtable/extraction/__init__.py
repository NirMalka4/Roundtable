"""Typed extraction of domain results and findings from agent output."""

from .domain_result import COUNT_KEYS, build_domain_result, parse_domain_result
from .finding_extractor import (
    CodeBlock,
    Exploitability,
    FindingItem,
    NormalizedLocation,
    Remediation,
    RemediationCheck,
    extract_findings,
)

__all__ = [
    "COUNT_KEYS",
    "CodeBlock",
    "Exploitability",
    "FindingItem",
    "NormalizedLocation",
    "Remediation",
    "RemediationCheck",
    "build_domain_result",
    "extract_findings",
    "parse_domain_result",
]
