"""commenter: the pluggable PR-comment **renderer** seam.

A **commenter** turns already-projected, already-anchored findings into the
markdown bodies a destination sink posts. It is the ONE step that knows how a
given topology wants to *speak* to a human reviewer — InspectorX's
severity-and-fix skeleton vs. Buddies' adjudicated-claim voice (effect badges, a
trust grade on the proposed fix, escalations phrased as questions) — while
everything around it (anchoring, dedup, watermarking, the severity floor, posting)
stays renderer-agnostic and is reused as-is.

Unlike the sink / projector / report registries, a commenter is **not** named by
its own ``agent_graph.yaml`` key. It is supplied by the
:class:`~roundtable.delivery.projector.Projector` on the
:class:`~roundtable.delivery.publishable.PublishableResult` it returns, because the
two are the same decision: whatever knows a topology's output *shape* is what
knows how that shape reads. Leaving it ``None`` selects the engine default, so a
config that never thinks about rendering keeps working untouched.

This module holds the interface only and imports nothing concrete (the ``ado``
types are ``TYPE_CHECKING``-only), so the seam never couples to one destination.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from roundtable.ado import AnchorDecision

    from .finding import PublishableFinding


@runtime_checkable
class Commenter(Protocol):
    """Renders the two comment kinds a publish run posts.

    ``render_thread`` renders one finding's thread body; ``decision`` says whether
    it landed inline (so a one-click ``suggestion`` fence is applicable) or was
    downgraded to a general thread.

    ``render_summary`` renders the single PR-level thread posted before the
    per-finding threads. It receives BOTH the full projected set (``findings``) and
    the post-severity-floor subset that actually became threads (``published``), so
    a renderer can account for what the floor withheld instead of silently dropping
    it. The returned body must embed a watermark; the flow parses its hash back out
    to dedupe re-publishes.
    """

    name: str

    def render_thread(
        self,
        finding: PublishableFinding,
        decision: AnchorDecision,
        *,
        session_id: str,
    ) -> str: ...

    def render_summary(
        self,
        session_id: str,
        verdict: str,
        findings: Sequence[PublishableFinding],
        *,
        published: Sequence[PublishableFinding],
    ) -> str: ...
