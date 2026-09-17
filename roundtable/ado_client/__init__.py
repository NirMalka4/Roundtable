"""Shared Azure DevOps transport and authentication primitives."""

from .client import (
    ADO_RESOURCE_ID,
    ado_auth_header,
    ado_bearer_token,
    build_ado_base_url,
    encode_segment,
    ensure_allowed_ado_host,
    is_allowed_ado_host,
)

__all__ = [
    "ADO_RESOURCE_ID",
    "ado_auth_header",
    "ado_bearer_token",
    "build_ado_base_url",
    "encode_segment",
    "ensure_allowed_ado_host",
    "is_allowed_ado_host",
]
