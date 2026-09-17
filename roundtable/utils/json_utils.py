"""json_utils: lenient JSON extraction from LLM output.

The extraction strategies, applied in source order:

  1. fast path  — the whole (trimmed) response is already valid JSON
  2. fenced     — first parseable ```json ... ``` / ``` ... ``` block
  3. balanced   — scan for a balanced object/array; keep the LONGEST valid block
                  and the TAIL-anchored block; accept by the same size heuristics
  4. salvage    — reconstruct a truncated tail  (**deferred**, see below)

Strategy 4 (``salvageTruncatedJson``) is intentionally
NOT ported. The judge_overlay oracle corpus (7 sessions) never exercises it —
every findings-bearing specialist response parses via the fast path, and the
lone non-JSON input (DeterministicPreScan prose) yields no findings either way.
``extract_json_detailed`` exposes ``source`` so a guard test can assert the
corpus stays within the ported strategies; if a future input needs salvage,
``salvage_truncated_json`` is the single seam to fill (it currently returns
``None``). This keeps parse errors scoped to complete JSON objects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCED_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


@dataclass
class JsonExtractionResult:
    """Result of :func:`extract_json_detailed`."""

    json: str
    extracted: bool
    source: str  # 'original' | 'raw' | 'fenced' | 'balanced' | 'tail' | 'salvaged'
    salvaged: bool = False
    reason: str | None = None


def _try_parse(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (ValueError, TypeError):
        return False


def salvage_truncated_json(text: str) -> str | None:
    """Deferred salvage seam — see module docstring. Always ``None`` for now."""
    return None


def extract_json_detailed(text: str) -> JsonExtractionResult:
    """Extract JSON from ``text`` with metadata about the winning strategy."""
    trimmed = text.strip()
    if not trimmed:
        return JsonExtractionResult(
            json=text, extracted=False, source="original", reason="Input is empty"
        )

    # 1) Fast path: full response is already valid JSON.
    if _try_parse(trimmed):
        return JsonExtractionResult(json=trimmed, extracted=False, source="raw")

    # 2) Prefer fenced blocks.
    for match in _FENCED_RE.finditer(trimmed):
        candidate = (match.group(1) or "").strip()
        if not candidate:
            continue
        if _try_parse(candidate):
            return JsonExtractionResult(json=candidate, extracted=True, source="fenced")

    # 3) Scan for a balanced JSON object/array; track longest + tail-anchored.
    longest_candidate = ""
    tail_candidate = ""
    starts = [i for i, ch in enumerate(trimmed) if ch in "{["]

    for start in starts:
        stack: list[str] = []
        in_string = False
        escape = False
        i = start
        n = len(trimmed)
        while i < n:
            ch = trimmed[i]
            if escape:
                escape = False
                i += 1
                continue
            if in_string:
                if ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch in "{[":
                stack.append(ch)
                i += 1
                continue
            if ch in "}]":
                if not stack:
                    break
                open_ch = stack.pop()
                if (open_ch == "{" and ch != "}") or (open_ch == "[" and ch != "]"):
                    break
                if not stack:
                    candidate = trimmed[start : i + 1]
                    if _try_parse(candidate):
                        if len(candidate) > len(longest_candidate):
                            longest_candidate = candidate
                        after = trimmed[i + 1 :].strip()
                        if len(after) == 0 and len(candidate) > len(tail_candidate):
                            tail_candidate = candidate
                    break  # move to next start index
            i += 1

    # 4) Truncation salvage — DEFERRED (always None). Kept as the single seam.
    salvaged = salvage_truncated_json(trimmed)
    if salvaged and _try_parse(salvaged):
        prefer_salvaged = not longest_candidate or (
            len(salvaged) >= len(longest_candidate) * 2 and len(salvaged) >= 500
        )
        if prefer_salvaged:
            return JsonExtractionResult(
                json=salvaged,
                extracted=True,
                source="salvaged",
                salvaged=True,
                reason=(
                    f"Recovered {len(salvaged)} chars from truncated payload "
                    f"(original {len(trimmed)} chars)"
                ),
            )

    if longest_candidate:
        if len(longest_candidate) >= len(trimmed) * 0.1 or len(trimmed) < 200:
            return JsonExtractionResult(json=longest_candidate, extracted=True, source="balanced")
        if tail_candidate:
            return JsonExtractionResult(json=tail_candidate, extracted=True, source="tail")
        return JsonExtractionResult(
            json=text,
            extracted=False,
            source="original",
            reason=(
                f"Found only a partial JSON fragment "
                f"({len(longest_candidate)}/{len(trimmed)} chars)"
            ),
        )

    return JsonExtractionResult(
        json=text,
        extracted=False,
        source="original",
        reason="No valid JSON object or array found in payload",
    )


def extract_json(text: str) -> str:
    """Return the extracted JSON string (or the original text on failure)."""
    return extract_json_detailed(text).json
