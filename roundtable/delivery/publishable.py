"""publishable: the neutral, topology-agnostic publish contract.

The slim result a :class:`~roundtable.delivery.projector.Projector` produces and
the destination sink consumes. It carries ONLY generic types — a session id, a
verdict string, a list of already-resolved findings, a topology-defined
finding-count map, and two optional topology-produced signals (a pre-publish abort
reason and a one-line operational log). No field is shaped by any one graph
topology, so the generic publish path (``run_publish`` / ``run_unpublish`` / the
sink) never reads a topology-specific attribute.

The Roundtable-shaped :class:`~roundtable.ado.publish.PublishPlan` is now an
INTERNAL detail of the Roundtable projector: it builds the plan, runs its own
count-parity gate + operational log line, and projects the result onto this neutral
type. ``all_findings`` is typed against ``PublishableFinding`` only under
``TYPE_CHECKING`` — at runtime it is an opaque list, so this module (in ``output``)
imports nothing from ``ado`` and the ``ado`` ↔ ``output`` cycle stays broken.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commenter import Commenter
    from .finding import PublishableFinding


@dataclass(frozen=True)
class PublishableResult:
    """The neutral projection of a session's terminal output, ready to publish.

    - ``all_findings`` / ``session_id`` / ``verdict`` — the three fields the generic
      publish + unpublish flow renders.
    - ``counts`` — topology-defined finding counts, forwarded verbatim by the
      Verdict node's ``domain_result`` (the projector owns the count vocabulary).
    - ``abort_reason`` — a topology pre-publish gate: non-``None`` means DO NOT
      publish (Roundtable fills it from its count-parity check); ``None`` = safe.
    - ``log_summary`` — an optional one-line operational summary the sink prints.
    - ``commenter`` — the renderer for THIS topology's comment voice, supplied by
      the projector that knows the shape; ``None`` selects the engine default.
    """

    all_findings: list[PublishableFinding]
    session_id: str
    verdict: str
    counts: Mapping[str, int] = field(default_factory=dict)
    abort_reason: str | None = None
    log_summary: str | None = None
    commenter: Commenter | None = None
