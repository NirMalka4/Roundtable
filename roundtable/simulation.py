"""Deterministic output transforms used only by the mock review backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SimulationRequest:
    output: dict[str, Any]
    context: Mapping[str, Any]
    requires: tuple[str, ...]


SimulationTransform = Callable[[SimulationRequest], dict[str, Any]]
_TRANSFORMS: dict[str, SimulationTransform] = {}


def register_simulation_transform(name: str, transform: SimulationTransform) -> None:
    if name in _TRANSFORMS:
        raise ValueError(f"simulation transform already registered: {name!r}")
    _TRANSFORMS[name] = transform


def apply_simulation_transform(
    name: str,
    output: dict[str, Any],
    context: Mapping[str, Any],
    requires: tuple[str, ...],
) -> dict[str, Any]:
    transform = _TRANSFORMS.get(name)
    if transform is None:
        return output
    result = transform(SimulationRequest(deepcopy(output), context, requires))
    if not isinstance(result, dict):
        raise TypeError(f"simulation transform {name!r} must return an object")
    return result
