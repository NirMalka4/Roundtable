"""Stable adoption analytics contracts."""

from pathlib import Path

from .codec import AdoV1Codec
from .collector import AdoptionCollector, CollectionResult, collect_to, write_collection
from .model import (
    MAX_LABEL_LENGTH,
    AdoptionLabel,
    AdoptionRecord,
    FindingRecord,
    ReviewRecord,
    compact_summary,
    encode_label,
    encode_metadata_marker,
    finding_records,
    parse_label,
    parse_metadata_markers,
)
from .provider import AdoptionProvider, UnsupportedAdoptionOperation
from .query import query_jsonl, query_records, read_jsonl, render_table
from .report import render_html
from .report import write_report as _write_report
from .source import classify_installation_source, installation_source


def write_report(input_path: str | Path, output_path: str | Path) -> Path:
    from roundtable.ado import adoption_record_url

    return _write_report(input_path, output_path, link_resolver=adoption_record_url)


__all__ = [
    "MAX_LABEL_LENGTH",
    "AdoV1Codec",
    "AdoptionCollector",
    "AdoptionLabel",
    "AdoptionProvider",
    "AdoptionRecord",
    "CollectionResult",
    "FindingRecord",
    "ReviewRecord",
    "UnsupportedAdoptionOperation",
    "classify_installation_source",
    "collect_to",
    "compact_summary",
    "encode_label",
    "encode_metadata_marker",
    "finding_records",
    "installation_source",
    "parse_label",
    "parse_metadata_markers",
    "query_jsonl",
    "query_records",
    "read_jsonl",
    "render_html",
    "render_table",
    "write_collection",
    "write_report",
]
