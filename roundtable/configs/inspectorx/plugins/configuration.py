from pathlib import Path

from roundtable.graph import Configuration, get_configuration

_ROOT = Path(__file__).resolve().parents[1]


def inspectorx_configuration() -> Configuration:
    return get_configuration(_ROOT)
