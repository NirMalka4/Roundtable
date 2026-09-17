"""Compatibility aliases for the canonical publisher facade."""

from .publisher import (
    Publisher,
    PublishOutcome,
    get_publisher,
)

Sink = Publisher
SinkOutcome = PublishOutcome
get_sink = get_publisher

__all__ = ["Sink", "SinkOutcome", "get_sink"]
