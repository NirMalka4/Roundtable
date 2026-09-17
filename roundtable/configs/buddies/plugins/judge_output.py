"""judge_output: locate THIS config's Judge ruling inside a session's results.

The Judge is found by the SHAPE of its output — a ``verdict`` object beside a
``claims`` list — never by node name, so renaming the node in
``agent_graph.yaml`` cannot silently empty a report or an empty publish plan.

Shared by the two readers of that ruling: the ``claims`` report renderer and the
``claims`` publish projector. It lives here rather than in either of them because
"which output is the Judge's" is one question with one answer, and a second copy
of it is a second thing to get wrong.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from roundtable.result_access import response_of

from .configuration import buddies_configuration


def parsed_outputs(session_results: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every agent output that parsed as a JSON object, in graph-declaration order.

    Declaration order (not completion order) so two runs of the same graph produce
    the same document.
    """
    parsed: list[tuple[str, dict[str, Any]]] = []
    for entry in buddies_configuration().entries:
        result = session_results.get(entry.key)
        if result is None:
            continue
        raw = response_of(result)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            parsed.append((entry.key, obj))
    return parsed


def judge_payload(outputs: Sequence[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """The output shaped like a Judge ruling: a ``verdict`` object beside ``claims``."""
    for _key, obj in outputs:
        if isinstance(obj.get("verdict"), Mapping) and isinstance(obj.get("claims"), list):
            return obj
    return None
