"""feedback: the engine-core Deviation/Feedback contract.

The typed unit a validation tier emits and a re-activation step consumes. It is
the SHARED seam behind two re-activation loops at different scales:

- **micro (exists):** an OVG gate rejects a node's output -> the runner re-invokes
  the SAME node with the deviations, under a per-node attempt budget
  (``run_agent_with_ovg`` / ``max_attempts``).
- **macro (future):** a downstream node emits a *revise* signal naming
  deviations -> the executor re-activates an UPSTREAM node with them, under a
  graph-level budget over the activation log.

Both consume the same :class:`Feedback` shape. This module defines that shape
ONLY — no loop, no budget guard, no re-activation step. The existing OVG pipeline
is the first producer (see ``PipelineResult.to_feedback``); a future macro
executor is the second consumer. Keeping the contract here — a dependency-free
leaf module — lets both the validation tier and the orchestration tier import it
without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

Level = str  # 'error' | 'warn'


@dataclass(frozen=True)
class Deviation:
    """One typed departure from a node's output contract.

    ``source`` names what raised it (a gate name at the micro scale; a node key at
    the macro scale). ``level`` decides whether it blocks (``error``) or merely
    advises (``warn``). ``path`` locates it within the output; ``message`` is the
    human/agent-readable explanation used as revision feedback.
    """

    source: str
    level: Level
    path: str
    message: str

    @property
    def blocking(self) -> bool:
        """True iff this deviation must be resolved (drives a re-activation)."""
        return self.level == "error"

    def render(self) -> str:
        """One agent-readable line: ``[source] path: message`` (path optional)."""
        loc = f"{self.path}: " if self.path else ""
        return f"[{self.source}] {loc}{self.message}"


@dataclass(frozen=True)
class Feedback:
    """An ordered set of deviations addressed to the node that must revise.

    ``target`` is the node key a re-activation step would re-invoke with this
    feedback. The same object serves the micro-loop (``target`` = the node whose
    own output failed) and a future macro-loop (``target`` = an upstream node a
    downstream challenger asks to revise).
    """

    target: str
    deviations: tuple[Deviation, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.deviations

    @property
    def blocking(self) -> bool:
        """True iff any deviation blocks — the signal a budgeted loop acts on."""
        return any(d.blocking for d in self.deviations)

    @property
    def blocking_deviations(self) -> tuple[Deviation, ...]:
        return tuple(d for d in self.deviations if d.blocking)

    def render(self) -> str:
        """Multi-line, ordered feedback block for the target node's next attempt."""
        return "\n".join(d.render() for d in self.deviations)
