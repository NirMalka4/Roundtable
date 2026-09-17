"""Unit tests for json_utils.extract_json strategy ladder."""

from __future__ import annotations

from roundtable.utils.json_utils import extract_json, extract_json_detailed


def test_fast_path_raw_json():
    r = extract_json_detailed('{"a": 1}')
    assert r.source == "raw"
    assert r.json == '{"a": 1}'


def test_empty_input():
    r = extract_json_detailed("   ")
    assert r.source == "original"
    assert r.extracted is False


def test_fenced_block():
    text = 'Here you go:\n```json\n{"x": 2}\n```\nthanks'
    r = extract_json_detailed(text)
    assert r.source == "fenced"
    assert r.json == '{"x": 2}'


def test_balanced_scan_in_prose():
    text = 'analysis... {"findings": [1,2,3]} done'
    r = extract_json_detailed(text)
    assert r.source == "balanced"
    assert r.json == '{"findings": [1,2,3]}'


def test_tail_anchored_small_json_in_large_prose():
    prose = "x" * 400
    text = f'{prose}\n{{"k": 1}}'
    r = extract_json_detailed(text)
    assert r.source == "tail"
    assert r.json == '{"k": 1}'


def test_no_json_returns_original():
    r = extract_json_detailed("just prose, no json here")
    assert r.source == "original"
    assert r.extracted is False


def test_garbage_braces_not_valid_json():
    r = extract_json_detailed("{ not json }")
    assert r.source == "original"


def test_extract_json_returns_string():
    assert extract_json('{"a":1}') == '{"a":1}'
