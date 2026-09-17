"""Explicit shipped-bundle fixtures."""

from __future__ import annotations

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.graph import Configuration, get_configuration, register_config_plugins


def _configuration(bundle: str) -> Configuration:
    configuration = get_configuration(resolve_bundle(bundle))
    register_config_plugins(configuration)
    return configuration


@pytest.fixture
def inspectorx_config() -> Configuration:
    return _configuration("inspectorx")


@pytest.fixture
def buddies_config() -> Configuration:
    return _configuration("buddies")
