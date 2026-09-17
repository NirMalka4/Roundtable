"""Compatibility facade for destination-neutral publication finding types."""

from roundtable.delivery import (
    GroundingUnit,
    PublishableFinding,
    compute_stable_hash,
    locations_of,
    watermark_hashed,
    watermark_plain,
)

__all__ = [
    "GroundingUnit",
    "PublishableFinding",
    "compute_stable_hash",
    "locations_of",
    "watermark_hashed",
    "watermark_plain",
]
