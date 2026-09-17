import pytest

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.report_renderer import (
    _fix_severities,
    _severity_order,
)
from roundtable.configs.inspectorx.plugins.verdict import severity_keys
from roundtable.graph import Configuration, get_configuration
from roundtable.types.severity import severity_rank_map
from roundtable.types.vocabulary import (
    exploitability_ratings,
    severity_blocking,
    severity_levels,
    severity_rank,
    verdict_values,
)

INSPECTORX = get_configuration(resolve_bundle("inspectorx"))


def test_vocabulary_accessors_return_canonical_tuples() -> None:
    assert severity_levels(INSPECTORX) == ("info", "low", "medium", "high", "critical")
    assert severity_blocking(INSPECTORX) == ("low", "medium", "high", "critical")
    assert verdict_values(INSPECTORX) == ("APPROVE", "APPROVE_WITH_SUGGESTIONS", "REJECT")
    assert exploitability_ratings(INSPECTORX) == (
        "trivial",
        "easy",
        "moderate",
        "hard",
        "not_exploitable",
        "unknown",
    )


def test_severity_rank_matches_order() -> None:
    assert severity_rank(INSPECTORX, "info") == 0
    assert severity_rank(INSPECTORX, "low") == 1
    assert severity_rank(INSPECTORX, "medium") == 2
    assert severity_rank(INSPECTORX, "high") == 3
    assert severity_rank(INSPECTORX, "critical") == 4
    assert severity_rank(INSPECTORX, " Critical ") == 4
    assert severity_rank(INSPECTORX, "junk") == -1


def test_derived_values_match_legacy_contracts() -> None:
    assert severity_rank_map(INSPECTORX) == {
        "info": 0,
        "low": 1,
        "medium": 2,
        "high": 3,
        "critical": 4,
    }
    assert {s.upper() for s in severity_blocking(INSPECTORX)} == {
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
    }
    assert severity_keys(INSPECTORX) == ("critical", "high", "medium", "low", "info")
    assert _severity_order() == {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "warning": 3,
        "low": 4,
        "info": 5,
        "style": 6,
    }
    assert frozenset({"critical", "high", "medium"}) == _fix_severities()


def test_sequential_configurations_resolve_distinct_values_without_cache_leakage(tmp_path) -> None:
    def configuration(name: str, severity: list[str]) -> Configuration:
        return Configuration.from_document(
            {
                "name": name,
                "domain_values": {
                    "values": {
                        "severity": severity,
                        "verdict": [f"{name.upper()}_OK"],
                    }
                },
                "agents": [{"key": "Input", "kind": "source", "emoji": "I"}],
            },
            root=tmp_path / name,
        )

    first = configuration("first", ["minor", "major"])
    second = configuration("second", ["notice", "danger", "fatal"])

    assert severity_levels(first) == ("minor", "major")
    assert severity_rank_map(second) == {"notice": 0, "danger": 1, "fatal": 2}
    assert verdict_values(first) == ("FIRST_OK",)
    assert severity_levels(first) == ("minor", "major")


def test_configuration_without_domain_values_fails_only_when_feature_requests_them(
    tmp_path,
) -> None:
    configuration = Configuration.from_document(
        {
            "name": "generic",
            "agents": [{"key": "Input", "kind": "source", "emoji": "I"}],
        },
        root=tmp_path,
    )

    assert configuration.domain_values is None
    with pytest.raises(ValueError, match=r"configuration 'generic'.*domain_values"):
        severity_levels(configuration)
