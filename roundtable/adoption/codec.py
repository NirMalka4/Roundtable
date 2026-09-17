"""Strict Azure DevOps v1 adoption wire codec."""

from __future__ import annotations

import json

from .model import AdoptionRecord, canonical_record


class AdoV1Codec:
    provider = "azure_devops"
    version = 1

    def encode(self, record: AdoptionRecord) -> bytes:
        if record.provider != self.provider or record.schema_version != self.version:
            raise ValueError("ADO v1 codec requires an azure_devops schema-version 1 record")
        return canonical_record(record)

    def decode(self, payload: bytes | str) -> AdoptionRecord:
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        return AdoptionRecord.from_dict(json.loads(raw))
