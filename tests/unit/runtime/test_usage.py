"""Unit tests for SDK-derived token/cost usage (``runtime.backend.usage``)."""

from __future__ import annotations

from roundtable.backend.usage import (
    EMPTY_USAGE,
    NOT_APPLICABLE_BILLING,
    AgentUsage,
    BillingStatus,
    BillingValue,
    anomalous_finish_reasons,
    format_ai_credits,
    merge_usage,
)

# ── merge / dataclass ────────────────────────────────────────────────────────


def test_merge_usage_sums_fields() -> None:
    a = AgentUsage(
        output_tokens=10,
        rounds=1,
        duration_ms=100,
        session_duration_ms=500,
        billing=BillingValue.complete(500_000_000.0, 0.5),
    )
    b = AgentUsage(
        output_tokens=20,
        rounds=2,
        duration_ms=200,
        session_duration_ms=700,
        billing=BillingValue.complete(250_000_000.0, 0.25),
    )
    merged = merge_usage([a, b])
    assert merged.output_tokens == 30
    assert merged.rounds == 3
    assert merged.billing == BillingValue.complete(750_000_000.0, 0.75)
    assert merged.duration_ms == 300
    assert merged.session_duration_ms == 1200


def test_merge_usage_sums_input_and_cache_tokens() -> None:
    a = AgentUsage(output_tokens=10, input_tokens=100, cache_read_tokens=40, reasoning_tokens=2)
    b = AgentUsage(output_tokens=20, input_tokens=50, cache_read_tokens=10, cache_write_tokens=8)
    merged = merge_usage([a, b])
    assert merged.input_tokens == 150
    assert merged.output_tokens == 30
    assert merged.cache_read_tokens == 50
    assert merged.cache_write_tokens == 8
    assert merged.reasoning_tokens == 2
    assert merged.total_tokens == 180  # input + output


def test_to_dict_camelcase_keys() -> None:
    d = AgentUsage(
        output_tokens=3,
        rounds=1,
        duration_ms=12.3,
        session_duration_ms=99.0,
        billing=BillingValue.complete(123_456_789.0, 0.123456789),
    ).to_dict()
    assert d == {
        "outputTokens": 3,
        "rounds": 1,
        "durationMs": 12.3,
        "billing": {
            "source": "copilot-sdk/session.usage.getMetrics",
            "status": "complete",
            "totalNanoAiu": 123_456_789.0,
            "totalPremiumRequestCost": 0.123456789,
        },
        "sessionDurationMs": 99.0,
    }


def test_to_dict_emits_token_breakdown_when_present() -> None:
    d = AgentUsage(
        output_tokens=30,
        input_tokens=200,
        cache_read_tokens=80,
        cache_write_tokens=10,
        reasoning_tokens=5,
        rounds=1,
    ).to_dict()
    assert d["inputTokens"] == 200
    assert d["totalTokens"] == 230
    assert d["cacheReadTokens"] == 80
    assert d["cacheWriteTokens"] == 10
    assert d["reasoningTokens"] == 5


def test_to_dict_omits_token_breakdown_when_zero() -> None:
    # A run that never surfaces input/cache detail stays byte-stable (output-only).
    d = AgentUsage(output_tokens=3, rounds=1).to_dict()
    for key in (
        "inputTokens",
        "totalTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "reasoningTokens",
    ):
        assert key not in d


def test_to_dict_omits_session_duration_when_zero() -> None:
    d = AgentUsage(output_tokens=3, rounds=1).to_dict()
    assert "sessionDurationMs" not in d


def test_total_tokens_is_input_plus_output() -> None:
    assert AgentUsage(input_tokens=100, output_tokens=25).total_tokens == 125
    assert AgentUsage(output_tokens=25).total_tokens == 25  # input unmeasured


def test_empty_usage_is_empty() -> None:
    assert EMPTY_USAGE.is_empty
    assert not AgentUsage(rounds=1).is_empty
    assert not AgentUsage(output_tokens=5).is_empty
    assert not AgentUsage(billing=BillingValue.complete(0.0, 0.0)).is_empty


def test_billing_merge_marks_partial_totals_unavailable() -> None:
    complete = BillingValue.complete(1_000_000_000.0, 1.0)
    unavailable = BillingValue.unavailable()
    assert NOT_APPLICABLE_BILLING.merge(complete) == complete
    merged = complete.merge(unavailable)
    assert merged.status is BillingStatus.UNAVAILABLE
    assert merged.total_nano_aiu is None
    assert merged.warning_code == "sdk_usage_metrics_unavailable"


def test_ai_credit_format_uses_the_central_nano_conversion() -> None:
    assert format_ai_credits(1_250_000_000.0) == "1.25"
    assert format_ai_credits(1.0) == "0.000000001"
    assert format_ai_credits(10_000_000_000.0) == "10"


# ── finish_reason enrichment ─────────────────────────────────────────────────


def test_anomalous_finish_reasons_filters_benign() -> None:
    assert anomalous_finish_reasons(["stop", "tool_calls", "STOP"]) == ()
    assert anomalous_finish_reasons(["length"]) == ("length",)
    assert anomalous_finish_reasons(["stop", "content_filter", "length"]) == (
        "content_filter",
        "length",
    )


def test_anomalous_finish_reasons_dedups_preserving_order() -> None:
    assert anomalous_finish_reasons(["length", "length", "content_filter"]) == (
        "length",
        "content_filter",
    )


def test_merge_unions_finish_reasons() -> None:
    a = AgentUsage(rounds=1, finish_reasons=("stop", "length"))
    b = AgentUsage(rounds=1, finish_reasons=("length", "content_filter"))
    assert merge_usage([a, b]).finish_reasons == ("stop", "length", "content_filter")


def test_to_dict_emits_only_anomalous_finish_reasons() -> None:
    d = AgentUsage(output_tokens=3, rounds=1, finish_reasons=("stop", "length")).to_dict()
    assert d["anomalousFinishReasons"] == ["length"]


def test_to_dict_omits_finish_reasons_when_benign() -> None:
    d = AgentUsage(output_tokens=3, rounds=1, finish_reasons=("stop", "tool_calls")).to_dict()
    assert "anomalousFinishReasons" not in d
