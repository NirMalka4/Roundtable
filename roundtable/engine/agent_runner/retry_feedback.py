"""Small output-text utilities used around schema submission diagnostics."""

from __future__ import annotations

import re

# Strip a short LLM preamble ("I'll...", a markdown header, etc.) — up to 6 lines.
PREAMBLE_RE = re.compile(
    r"^(?:\s*(?:#+\s+.*|I understand|I'll|As the|Let me|Sure,|Understood)[^\n]{0,200}\n){1,6}",
    re.IGNORECASE,
)

TRUNCATED_ECHO_LIMIT = 4000


def strip_preamble(text: str) -> str:
    return PREAMBLE_RE.sub("", text)


def truncate_for_echo(text: str) -> str:
    if len(text) <= TRUNCATED_ECHO_LIMIT:
        return text
    half = TRUNCATED_ECHO_LIMIT // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]
