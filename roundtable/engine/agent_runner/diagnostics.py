"""Structured per-attempt submission diagnostics with bounded extraction.

Schema-backed validation records why a submission was rejected; for offline debugging it also
wants *what* the agent emitted that a gate rejected — but persisting the whole
response per failed attempt bloats the trace. Instead each diagnostic carries a
``snippet``: only the offending fragment.

* A structured gate (``json_schema``/``locations_floor``/…) reports a JSON
  ``path`` (``findings[2].severity``); the snippet is the *value at that path*
  in the parsed output — the exact bad field, nothing else.
* The ``format`` gate fires when the output did not parse at all (``parsed`` is
  ``None``); there is no path to navigate, so the snippet is a bounded head+tail
  window of the raw text — enough to see whether it was prose, a code fence, or
  truncated JSON, without carrying kilobytes of essay.

Pure functions, no run-loop state, so the extraction is unit-testable on its own.
"""

from __future__ import annotations

import json
import re
from typing import Any

from roundtable.validation import LeveledDiagnostic

# Value at a diagnostic path is a single field — keep it tight.
SNIPPET_CAP = 300
# An unparseable output has no path; show a small head+tail window of the raw text.
FORMAT_WINDOW = 400

_MISSING: Any = object()
# Tokens of a ``a.b[0].c`` JSON path: a bare key OR a ``[<int>]`` index.
_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _bounded(text: str, limit: int) -> str:
    """Head+tail window of ``text`` capped at ``limit`` chars (middle elided)."""
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}…[+{dropped} chars]…{text[-half:]}"


def _navigate(parsed: Any, path: str) -> Any:
    """Return the value at JSON ``path`` in ``parsed``, or ``_MISSING`` if absent."""
    node = parsed
    for key, idx in _PATH_TOKEN_RE.findall(path):
        try:
            node = node[int(idx)] if idx else node[key]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return node


def extract_snippet(parsed: Any | None, path: str, raw: str) -> str:
    """The *problematic part* of the deviating output for one diagnostic.

    ``parsed is None`` ⇒ the output did not parse (``format`` gate): a bounded
    head+tail window of ``raw``. Otherwise the value at ``path`` (whole document
    when ``path`` is empty), compact-serialised and capped. Returns ``""`` when
    the path cannot be located (nothing to show).
    """
    if parsed is None:
        return _bounded(raw.strip(), FORMAT_WINDOW)
    node = _navigate(parsed, path) if path else parsed
    if node is _MISSING:
        return ""
    try:
        text = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(node)
    return _bounded(text, SNIPPET_CAP)


def structured_diags(
    diags: list[LeveledDiagnostic], parsed: Any | None, raw: str
) -> list[dict[str, str]]:
    """Serialise leveled diagnostics to ``{gate, path?, message, snippet?}`` dicts,
    attaching the extracted problematic-part snippet to each."""
    out: list[dict[str, str]] = []
    for d in diags:
        item: dict[str, str] = {"gate": d.gate}
        if d.path:
            item["path"] = d.path
        item["message"] = d.message
        for key in ("keyword", "expected", "actual", "description"):
            value = getattr(d, key, "")
            if value:
                item[key] = value
        snippet = extract_snippet(parsed, d.path, raw)
        if snippet:
            item["snippet"] = snippet
        out.append(item)
    return out


def runtime_error(kind: str, message: str, snippet: str = "") -> dict[str, str]:
    """A non-gate terminal failure as an ``errors[]`` entry.

    ``kind`` is the specific SDK/API failure cause, mirroring the attempt's
    ``outcome``. It doubles as the discriminator that sets a
    runtime entry apart from a gate diagnostic (which carries ``gate`` instead): an
    entry carries either ``gate`` or ``kind``, never both.
    """
    item: dict[str, str] = {"kind": kind, "message": message}
    if snippet:
        item["snippet"] = snippet
    return item


def run_notice(kind: str, message: str) -> dict[str, str]:
    """A run-level (not per-attempt) advisory notice as a ``warnings[]`` entry.

    Unlike a gate/runtime diagnostic (which belongs to one attempt and lives in
    ``AttemptDetail.errors``), these describe a decision or terminal status of the
    whole run: ``session_adopt`` (adopted the CLI-reported session id),
    ``backend_unclean_exit`` (output was accepted before the backend turn faulted),
    or ``abort`` (no valid output after all attempts). ``kind`` is the same
    discriminator key used by ``runtime_error`` — an entry carries either ``gate``
    or ``kind``, never both — so notices stay machine-filterable, not free prose.
    """
    return {"kind": kind, "message": message}
