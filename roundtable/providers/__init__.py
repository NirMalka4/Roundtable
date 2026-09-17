"""Repository and change-request provider contracts."""

from .model import (
    ChangeRequestIdentity,
    ProviderId,
    RepositoryIdentity,
    RevisionProvenance,
)
from .registry import (
    RepositoryProvider,
    canonicalize_remote,
    get_provider,
    register_provider,
)

__all__ = [
    "ChangeRequestIdentity",
    "ProviderId",
    "RepositoryIdentity",
    "RepositoryProvider",
    "RevisionProvenance",
    "canonicalize_remote",
    "get_provider",
    "register_provider",
]
