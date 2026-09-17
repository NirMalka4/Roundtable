"""Agent identity derived from the active configuration graph.

Canonical id, report display name, and name resolution are ALL derived from the
loaded graph (``agent_graph.yaml``) — there is NO hardcoded roster. The bundle is
the single source of truth: each non-source entry carries ``agent_id`` (the
canonical snake_case id used by OVG schemas + the finding index) and
``display_name`` (the report label). Point the engine at another bundle and
identity re-derives from it — nothing here knows the Roundtable roster.

Resolution accepts any name form an entry exposes — the graph ``key``, its
``agent_id``, or its ``display_name`` (each also in lowercase / underscored
variants) — and maps it to the canonical ``agent_id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roundtable.graph import Configuration

_WS = re.compile(r"[\s]+")


@dataclass(frozen=True)
class AgentIdentityEntry:
    key: str
    canonical_id: str
    display_name: str


def _derive(cfg: Configuration) -> tuple[AgentIdentityEntry, ...]:
    """The identity roster for a configuration: one entry per non-source node
    that declares an ``agent_id`` (doctor Layer 4 guarantees presence)."""
    out: list[AgentIdentityEntry] = []
    for e in cfg.entries:
        if e.kind == "source" or not e.agent_id:
            continue
        out.append(AgentIdentityEntry(e.key, e.agent_id, e.display_name or e.key.replace("_", " ")))
    return tuple(out)


def _build_index(identities: tuple[AgentIdentityEntry, ...]) -> dict[str, str]:
    index: dict[str, str] = {}
    for it in identities:
        index[it.key.lower()] = it.canonical_id
        index[it.canonical_id] = it.canonical_id
        display = it.display_name.lower()
        index.setdefault(display, it.canonical_id)
        index.setdefault(_WS.sub("_", display), it.canonical_id)
    return index


# Derived views memoized against the active Configuration object. get_configuration()
# is process-cached; when it is cleared/swapped (tests overriding the config root) it
# returns a NEW object, so an identity mismatch rebuilds — no stale roster leaks.
_MEMO: dict[str, object] = {"cfg": None, "identities": None, "index": None}


def _active() -> Configuration:
    from roundtable.graph import get_configuration

    return get_configuration()


def _ensure(
    cfg: Configuration | None = None,
) -> tuple[tuple[AgentIdentityEntry, ...], dict[str, str]]:
    cfg = cfg if cfg is not None else _active()
    if _MEMO["cfg"] is not cfg:
        identities = _derive(cfg)
        _MEMO.update(cfg=cfg, identities=identities, index=_build_index(identities))
    return _MEMO["identities"], _MEMO["index"]  # type: ignore[return-value]


def try_resolve_canonical_id(name: str, config: Configuration | None = None) -> str | None:
    """Resolve any agent name form to its canonical id, or None if unknown."""
    if not name:
        return None
    _, index = _ensure(config)
    lower = name.lower()
    return index.get(lower) or index.get(_WS.sub("_", lower))


def resolve_canonical_id(name: str, config: Configuration | None = None) -> str:
    result = try_resolve_canonical_id(name, config)
    if result is None:
        raise ValueError(f'Unknown agent: "{name[:80]}". Not found in the graph roster.')
    return result


def get_identity_registry(config: Configuration | None = None) -> tuple[AgentIdentityEntry, ...]:
    identities, _ = _ensure(config)
    return identities


def is_judge_like_agent(name: str, config: Configuration | None = None) -> bool:
    if not name:
        return False
    canonical = try_resolve_canonical_id(name, config) or name.lower()
    return canonical.lower().startswith("judge")


def get_agent_display_name(key: str, config: Configuration | None = None) -> str:
    """Friendly report label for an agent key: the graph ``display_name``, else
    ``key`` with ``_`` → space."""
    identities, _ = _ensure(config)
    for it in identities:
        if it.key == key:
            return it.display_name
    return key.replace("_", " ")


def get_agent_label(name: str, config: Configuration | None = None) -> str:
    """``⚡ Big-O`` — how an agent introduces itself to a human, glyph and name as one.

    The single home for that composition, so no surface can credit an agent by name
    alone while its neighbour shows the glyph too. Any alias is resolved first: a raw
    session key is not always the graph key, and a near-miss would silently drop the
    glyph rather than fail.
    """
    from roundtable.graph import get_agent_emoji

    key = try_resolve_canonical_id(name, config) or name
    return f"{get_agent_emoji(key)} {get_agent_display_name(key, config)}".strip()
