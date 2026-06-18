"""Tests for Phase 1 scoring and publication ranking."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from src.analysis import phase1_pipeline
from src.analysis.phase1_pipeline import (
    FALLBACK_CONFIDENCE_CAP,
    _analysis_close_price,
    _analyze_candidate,
    _chat_completion_options,
    _dividend_signals,
    _event_signals,
    _price_volume_signals,
    analyze_shortlist,
    build_publication_payload,
    evaluate_data_freshness,
    publish_payload,
    run_phase1_pipeline,
    select_shortlist,
    upcoming_dividends_summary,
    upcoming_earnings_summary,
)


def test_select_shortlist_prioritizes_high_opportunity_and_sell_watch():
    scores = [
        {"ticker": "AAPL", "opportunity_score": 30, "negative_score": 10, "signals": []},
        {"ticker": "TSLA", "opportunity_score": 10, "negative_score": 45, "signals": []},
        {"ticker": "NVDA", "opportunity_score": 80, "negative_score": 0, "signals": []},
    ]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=["TSLA"]):
        shortlist = select_shortlist(scores)

    assert [row["ticker"] for row in shortlist[:2]] == ["TSLA", "NVDA"]


def test_build_publication_payload_splits_top_picks_and_sell_alerts():
    stocks = [
        {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"},
        {"ticker": "TSLA", "company_name": "Tesla", "sector": "Consumer Discretionary"},
    ]
    scores = [
        {"ticker": "NVDA", "opportunity_score": 80, "negative_score": 5, "signals": []},
        {"ticker": "TSLA", "opportunity_score": 5, "negative_score": 90, "signals": []},
    ]
    analyses = [
        {
            "ticker": "NVDA",
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Unusual volume",
            "expected_timeframe": "1-30 days",
            "reasoning": "Momentum cluster is positive.",
            "invalidation_criteria": "Momentum fades.",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": [],
        },
        {
            "ticker": "TSLA",
            "recommendation": "SELL",
            "risk_level": "HIGH",
            "confidence_score": 78,
            "catalyst": "Negative news",
            "expected_timeframe": "1-7 days",
            "reasoning": "Negative catalyst cluster is severe.",
            "invalidation_criteria": "News reverses.",
            "opportunity_score": 5,
            "negative_score": 90,
            "signals": [],
        },
    ]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=["TSLA"]):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 15))

    assert payload["top_picks"][0]["ticker"] == "NVDA"
    assert payload["sell_alerts"][0]["ticker"] == "TSLA"


def test_build_publication_payload_includes_partial_coverage_quality():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    scores = [
        {"ticker": "NVDA", "opportunity_score": 80, "negative_score": 5, "signals": []}
    ]
    analyses = [
        {
            "ticker": "NVDA",
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Unusual volume",
            "expected_timeframe": "1-30 days",
            "reasoning": "Momentum cluster is positive.",
            "invalidation_criteria": "Momentum fades.",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": [],
        }
    ]
    data_quality = {
        "coverage_status": "partial",
        "active_ticker_count": 2,
        "eligible_ticker_count": 1,
        "excluded_ticker_count": 1,
        "warnings": ["1 active ticker(s) were excluded by data freshness gates."],
    }

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]):
        payload = build_publication_payload(
            analyses, scores, stocks, date(2026, 6, 15), data_quality=data_quality
        )

    assert payload["publication_scope"] == "top_opportunities_among_eligible_tickers"
    assert payload["data_quality"]["coverage_status"] == "partial"
    assert "excluded by data freshness gates" in payload["data_warnings"][-1]


def test_price_volume_signals_prefer_stored_market_signals():
    with patch("src.analysis.phase1_pipeline.store") as mock_store:
        mock_store.market_signals_for_ticker.return_value = [
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "price_move",
                "direction": "positive",
                "score": 36,
                "title": "Large daily price move",
                "summary": "NVDA moved 6.00% versus the prior close.",
                "price_change_percent": Decimal("6.00"),
                "close_price": Decimal("106"),
                "previous_close_price": Decimal("100"),
                "volume": 220,
                "average_volume": Decimal("100"),
            }
        ]

        signals = _price_volume_signals("NVDA", date(2026, 6, 17))

    assert len(signals) == 1
    assert signals[0]["signal_type"] == "price_move"
    assert signals[0]["source"]["provider"] == "stock_collector"
    assert signals[0]["source"]["raw"]["price_change_percent"] == 6


def test_price_volume_signals_add_multi_day_market_context():
    run_date = date(2026, 6, 17)
    rows = _market_context_rows(
        "NVDA",
        run_date - timedelta(days=29),
        closes=[100 + offset for offset in range(30)],
        volumes=[1000] * 27 + [2200, 2300, 2400],
    )

    with patch("src.analysis.phase1_pipeline.store") as mock_store:
        mock_store.market_signals_for_ticker.return_value = []
        mock_store.get_stock_data.return_value = rows

        signals = _price_volume_signals("NVDA", run_date)

    signal_types = {signal["signal_type"] for signal in signals}
    assert "technical_trend" in signal_types
    assert "volume_persistence" in signal_types
    trend = next(signal for signal in signals if signal["signal_type"] == "technical_trend")
    assert trend["direction"] == "positive"
    assert trend["score"] > 0
    assert trend["source"]["provider"] == "derived_ohlcv"
    assert trend["source"]["raw"]["return_20d_percent"] > 0


def test_price_volume_signals_do_not_call_one_day_jump_a_trend():
    run_date = date(2026, 6, 17)
    rows = _market_context_rows(
        "AAPL",
        run_date - timedelta(days=24),
        closes=[100] * 24 + [104],
        volumes=[1000] * 25,
    )

    with patch("src.analysis.phase1_pipeline.store") as mock_store:
        mock_store.market_signals_for_ticker.return_value = []
        mock_store.get_stock_data.return_value = rows

        signals = _price_volume_signals("AAPL", run_date)

    assert any(signal["signal_type"] == "price_move" for signal in signals)
    assert not any(signal["signal_type"] == "technical_trend" for signal in signals)


def test_build_publication_payload_includes_upcoming_earnings():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    scores = [{"ticker": "NVDA", "opportunity_score": 0, "negative_score": 0, "signals": []}]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]):
        payload = build_publication_payload(
            [],
            scores,
            stocks,
            date(2026, 6, 17),
            upcoming_earnings=[
                {
                    "ticker": "NVDA",
                    "company_name": "NVIDIA",
                    "event_date": "2026-07-20",
                    "eps_estimate": 2.15,
                }
            ],
        )

    assert payload["upcoming_earnings"] == [
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA",
            "event_date": "2026-07-20",
            "eps_estimate": 2.15,
        }
    ]


def test_build_publication_payload_includes_upcoming_dividends():
    stocks = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Technology"}]
    scores = [{"ticker": "AAPL", "opportunity_score": 0, "negative_score": 0, "signals": []}]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]):
        payload = build_publication_payload(
            [],
            scores,
            stocks,
            date(2026, 6, 17),
            upcoming_dividends=[
                {
                    "ticker": "AAPL",
                    "company_name": "Apple",
                    "ex_dividend_date": "2026-08-15",
                    "dividend_amount": 0.3,
                    "dividend_yield": 1.5,
                }
            ],
        )

    assert payload["upcoming_dividends"] == [
        {
            "ticker": "AAPL",
            "company_name": "Apple",
            "ex_dividend_date": "2026-08-15",
            "dividend_amount": 0.3,
            "dividend_yield": 1.5,
        }
    ]


def test_earnings_signal_uses_upcoming_event_history_and_news():
    with (
        patch("src.analysis.phase1_pipeline.store") as mock_store,
        patch("src.analysis.phase1_pipeline.yf.Ticker", side_effect=Exception("offline")),
    ):
        mock_store.earnings_events_for_ticker.side_effect = [
            [
                {
                    "ticker": "NVDA",
                    "event_date": "2026-07-20",
                    "eps_estimate": Decimal("2.15"),
                    "is_upcoming": True,
                }
            ],
            [
                {
                    "ticker": "NVDA",
                    "event_date": "2026-04-20",
                    "surprise_percent": Decimal("5.0"),
                    "post_earnings_price_move_percent": Decimal("8.0"),
                    "is_upcoming": False,
                }
            ],
        ]
        mock_store.news_for_ticker.return_value = [
            {
                "title": "NVIDIA raises guidance",
                "summary": "Analysts expect record revenue.",
            }
        ]

        signals = _event_signals("NVDA", date(2026, 6, 17))

    earnings = [signal for signal in signals if signal["signal_type"] == "earnings"]
    assert earnings
    assert earnings[0]["direction"] == "positive"
    assert earnings[0]["score"] > 0
    assert "reports earnings" in earnings[0]["summary"]
    assert earnings[0]["source"]["raw"]["historical_event_count"] == 1


def test_dividend_signal_uses_upcoming_event_and_history():
    with patch("src.analysis.phase1_pipeline.store") as mock_store:
        mock_store.dividend_events_for_ticker.side_effect = [
            [
                {
                    "ticker": "AAPL",
                    "ex_dividend_date": "2026-08-15",
                    "dividend_amount": Decimal("0.30"),
                    "dividend_yield": Decimal("1.50"),
                    "is_upcoming": True,
                }
            ],
            [
                {
                    "ticker": "AAPL",
                    "ex_dividend_date": "2026-05-15",
                    "post_ex_dividend_price_move_percent": Decimal("-1.0"),
                    "is_upcoming": False,
                }
            ],
        ]

        signals = _dividend_signals("AAPL", date(2026, 6, 17))

    assert signals
    assert signals[0]["signal_type"] == "dividend"
    assert "goes ex-dividend" in signals[0]["summary"]
    assert signals[0]["source"]["raw"]["historical_event_count"] == 1


def test_upcoming_earnings_summary_returns_jsonable_events():
    with patch("src.analysis.phase1_pipeline.store.upcoming_earnings") as upcoming:
        upcoming.return_value = [
            {
                "ticker": "NVDA",
                "company_name": "NVIDIA",
                "event_date": date(2026, 7, 20),
                "eps_estimate": Decimal("2.15"),
                "is_upcoming": True,
            }
        ]

        events = upcoming_earnings_summary(date(2026, 6, 17))

    assert events == [
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA",
            "event_date": "2026-07-20",
            "eps_estimate": 2.15,
            "is_upcoming": True,
        }
    ]


def test_upcoming_dividends_summary_returns_jsonable_events():
    with patch("src.analysis.phase1_pipeline.store.upcoming_dividends") as upcoming:
        upcoming.return_value = [
            {
                "ticker": "AAPL",
                "company_name": "Apple",
                "ex_dividend_date": date(2026, 8, 15),
                "dividend_amount": Decimal("0.30"),
                "dividend_yield": Decimal("1.50"),
                "is_upcoming": True,
            }
        ]

        events = upcoming_dividends_summary(date(2026, 6, 17))

    assert events == [
        {
            "ticker": "AAPL",
            "company_name": "Apple",
            "ex_dividend_date": "2026-08-15",
            "dividend_amount": 0.3,
            "dividend_yield": 1.5,
            "is_upcoming": True,
        }
    ]


def test_fallback_analysis_labels_method_and_caps_confidence_when_openai_missing():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    score = _candidate_score("NVDA", opportunity_score=200, negative_score=0)

    analysis = _analyze_candidate(None, stock, score, date(2026, 6, 17))

    assert analysis["analysis_method"] == "fallback_heuristic"
    assert analysis["fallback_reason"] == "openai_client_unavailable"
    assert analysis["confidence_score"] <= FALLBACK_CONFIDENCE_CAP
    assert analysis["recommendation"] == "BUY"
    assert analysis["publication_allowed"] is False


def test_fallback_analysis_labels_openai_errors_and_caps_confidence():
    stock = {"ticker": "TSLA", "company_name": "Tesla", "sector": "Consumer Discretionary"}
    score = _candidate_score("TSLA", opportunity_score=0, negative_score=200)

    analysis = _analyze_candidate(_FailingOpenAIClient(), stock, score, date(2026, 6, 17))

    assert analysis["analysis_method"] == "fallback_heuristic"
    assert analysis["fallback_reason"] == "openai_error"
    assert analysis["confidence_score"] <= FALLBACK_CONFIDENCE_CAP
    assert analysis["recommendation"] == "SELL"
    assert analysis["publication_allowed"] is False


def test_analyze_shortlist_falls_back_when_openai_client_init_fails():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    score = _candidate_score("NVDA", opportunity_score=200, negative_score=0)

    with (
        patch("src.analysis.phase1_pipeline.get_openai_api_key", return_value="test-key"),
        patch("src.analysis.phase1_pipeline.OpenAI", side_effect=TypeError("bad httpx")),
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch("src.analysis.phase1_pipeline.store.put_candidate_analysis") as put_analysis,
    ):
        analyses = analyze_shortlist([score], [stock], date(2026, 6, 17))

    assert analyses[0]["analysis_method"] == "fallback_heuristic"
    assert analyses[0]["fallback_reason"] == "openai_client_unavailable"
    put_analysis.assert_called_once()


def test_chat_completion_options_use_gpt5_token_parameter():
    assert _chat_completion_options(
        "gpt-5.4-mini", max_tokens=500, temperature=0.25
    ) == {"max_completion_tokens": 500}
    assert _chat_completion_options(
        "gpt-4o-mini", max_tokens=500, temperature=0.25
    ) == {"max_tokens": 500, "temperature": 0.25}


def test_analyze_shortlist_reviews_actionable_ai_recommendations():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    score = _candidate_score("NVDA", opportunity_score=90, negative_score=5)
    client = _SequencedOpenAIClient(
        [
            {
                "recommendation": "BUY",
                "risk_level": "MEDIUM",
                "confidence_score": 82,
                "catalyst": "Volume breakout",
                "expected_timeframe": "1-30 days",
                "reasoning": "Evidence supports a near-term catalyst.",
                "invalidation_criteria": "Breakout fails.",
            },
            {
                "approved": True,
                "rationale": "The thesis is specific and supported.",
                "concerns": [],
                "confidence_adjustment": 3,
            },
        ]
    )

    with (
        patch("src.analysis.phase1_pipeline._build_openai_client", return_value=client),
        patch("src.analysis.phase1_pipeline.store.put_candidate_analysis") as put_analysis,
        patch("src.analysis.phase1_pipeline._emit_metric"),
    ):
        analyses = analyze_shortlist([score], [stock], date(2026, 6, 17))

    assert client.models == ["gpt-5.4-mini", "gpt-5.4"]
    assert analyses[0]["analysis_method"] == "ai"
    assert analyses[0]["analysis_model"] == "gpt-5.4-mini"
    assert analyses[0]["publication_allowed"] is True
    assert analyses[0]["confidence_score"] == 85
    assert analyses[0]["ai_review"]["status"] == "approved"
    assert analyses[0]["ai_review"]["model"] == "gpt-5.4"
    put_analysis.assert_called_once()


def test_review_rejection_suppresses_public_publication():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    scores = [_candidate_score("NVDA", opportunity_score=90, negative_score=5)]
    analyses = [
        {
            "ticker": "NVDA",
            "analysis_method": "ai",
            "analysis_model": "gpt-5.4-mini",
            "publication_allowed": False,
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Unusual volume",
            "expected_timeframe": "1-30 days",
            "reasoning": "The setup looks interesting.",
            "invalidation_criteria": "Momentum fades.",
            "opportunity_score": 90,
            "negative_score": 5,
            "signals": [],
            "ai_review": {
                "status": "rejected",
                "model": "gpt-5.4",
                "approved": False,
                "rationale": "Evidence is too weak.",
                "concerns": ["weak evidence"],
            },
        }
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    assert payload["top_picks"] == []
    assert payload["review_policy"]["reviewed_count"] == 1
    assert payload["review_policy"]["rejected_count"] == 1
    assert payload["review_policy"]["review_suppressed_count"] == 1
    assert "withheld by the review model" in payload["data_warnings"][-1]
    emit_metric.assert_called_once_with("review_publication_suppressed", 1)


def test_review_failure_suppresses_actionable_ai_recommendation():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    score = _candidate_score("NVDA", opportunity_score=90, negative_score=5)
    client = _SequencedOpenAIClient(
        [
            {
                "recommendation": "BUY",
                "risk_level": "MEDIUM",
                "confidence_score": 82,
                "catalyst": "Volume breakout",
                "expected_timeframe": "1-30 days",
                "reasoning": "Evidence supports a near-term catalyst.",
                "invalidation_criteria": "Breakout fails.",
            },
        ],
        fail_after=1,
    )

    with (
        patch("src.analysis.phase1_pipeline._build_openai_client", return_value=client),
        patch("src.analysis.phase1_pipeline.store.put_candidate_analysis"),
        patch("src.analysis.phase1_pipeline._emit_metric"),
    ):
        analyses = analyze_shortlist([score], [stock], date(2026, 6, 17))

    assert analyses[0]["recommendation"] == "BUY"
    assert analyses[0]["publication_allowed"] is False
    assert analyses[0]["ai_review"]["status"] == "error"
    assert analyses[0]["ai_review"]["concerns"] == ["review_model_error"]


def test_build_publication_payload_suppresses_fallback_buy_and_sell_by_default():
    stocks = [
        {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"},
        {"ticker": "TSLA", "company_name": "Tesla", "sector": "Consumer Discretionary"},
    ]
    scores = [
        _candidate_score("NVDA", opportunity_score=90, negative_score=5),
        _candidate_score("TSLA", opportunity_score=5, negative_score=90),
    ]
    analyses = [
        {
            "ticker": "NVDA",
            "analysis_method": "fallback_heuristic",
            "fallback_reason": "openai_client_unavailable",
            "publication_allowed": False,
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 55,
            "catalyst": "Unusual volume",
            "expected_timeframe": "1-30 days",
            "reasoning": "Fallback BUY should not publish.",
            "invalidation_criteria": "Momentum fades.",
            "opportunity_score": 90,
            "negative_score": 5,
            "signals": [],
        },
        {
            "ticker": "TSLA",
            "analysis_method": "fallback_heuristic",
            "fallback_reason": "openai_error",
            "publication_allowed": False,
            "recommendation": "SELL",
            "risk_level": "HIGH",
            "confidence_score": 55,
            "catalyst": "Negative news",
            "expected_timeframe": "1-7 days",
            "reasoning": "Fallback SELL should not publish.",
            "invalidation_criteria": "News reverses.",
            "opportunity_score": 5,
            "negative_score": 90,
            "signals": [],
        },
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=["TSLA"]),
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    assert payload["top_picks"] == []
    assert payload["sell_alerts"] == []
    assert payload["fallback_policy"]["fallback_analysis_count"] == 2
    assert payload["fallback_policy"]["suppressed_fallback_count"] == 2
    assert payload["fallback_policy"]["fallback_reason_counts"] == {
        "openai_client_unavailable": 1,
        "openai_error": 1,
    }
    assert "heuristic fallback" in payload["data_warnings"][-2]
    assert "withheld from public publication" in payload["data_warnings"][-1]
    emit_metric.assert_called_once_with("fallback_publication_suppressed", 2)


def test_build_publication_payload_includes_ai_method_on_public_pick():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    scores = [_candidate_score("NVDA", opportunity_score=80, negative_score=5)]
    analyses = [
        {
            "ticker": "NVDA",
            "analysis_method": "ai",
            "publication_allowed": True,
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Unusual volume",
            "expected_timeframe": "1-30 days",
            "reasoning": "AI-backed momentum cluster is positive.",
            "invalidation_criteria": "Momentum fades.",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": [],
        }
    ]

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    assert payload["top_picks"][0]["analysis_method"] == "ai"


def test_publish_payload_writes_latest_and_history_artifacts():
    payload = {
        "publication_date": "2026-06-17",
        "generated_at": "2026-06-17T22:00:00",
        "top_picks": [{"ticker": "NVDA"}],
        "sell_alerts": [{"ticker": "TSLA"}],
        "data_quality": {"coverage_status": "complete"},
        "data_warnings": [],
    }

    with (
        patch.object(phase1_pipeline, "ARTIFACT_BUCKET", "stockara-artifacts"),
        patch("src.analysis.phase1_pipeline.boto3.client") as boto_client,
        patch("src.analysis.phase1_pipeline.store.put_publication_record") as put_record,
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        publish_payload(payload, date(2026, 6, 17))

    s3 = boto_client.return_value
    keys = [call.kwargs["Key"] for call in s3.put_object.call_args_list]
    assert keys == [
        "top-picks/latest.json",
        "top-picks/history/2026-06-17.json",
        "sell-alerts/latest.json",
        "sell-alerts/history/2026-06-17.json",
    ]
    put_record.assert_called_once_with(date(2026, 6, 17), payload)
    emit_metric.assert_called_once_with("artifact_publish_failures", 0)


def test_publish_payload_emits_failure_metric_when_s3_write_fails():
    payload = {
        "publication_date": "2026-06-17",
        "generated_at": "2026-06-17T22:00:00",
        "top_picks": [],
        "sell_alerts": [],
        "data_quality": {},
        "data_warnings": [],
    }

    with (
        patch.object(phase1_pipeline, "ARTIFACT_BUCKET", "stockara-artifacts"),
        patch("src.analysis.phase1_pipeline.boto3.client") as boto_client,
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        boto_client.return_value.put_object.side_effect = RuntimeError("S3 unavailable")
        try:
            publish_payload(payload, date(2026, 6, 17))
        except RuntimeError as exc:
            assert "S3 unavailable" in str(exc)
        else:
            raise AssertionError("publish_payload should re-raise S3 write failures")

    emit_metric.assert_called_once_with("artifact_publish_failures", 1)


def test_publish_payload_fails_when_artifact_bucket_is_not_configured():
    payload = {
        "publication_date": "2026-06-17",
        "generated_at": "2026-06-17T22:00:00",
        "top_picks": [],
        "sell_alerts": [],
        "data_quality": {},
        "data_warnings": [],
    }

    with (
        patch.object(phase1_pipeline, "ARTIFACT_BUCKET", ""),
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        try:
            publish_payload(payload, date(2026, 6, 17))
        except RuntimeError as exc:
            assert "Artifact bucket is not configured" in str(exc)
        else:
            raise AssertionError("publish_payload should fail without an artifact bucket")

    emit_metric.assert_called_once_with("artifact_publish_failures", 1)


def test_evaluate_data_freshness_excludes_stale_ticker_but_allows_partial_coverage():
    run_date = date(2026, 6, 17)
    stocks = [
        {"ticker": "NVDA", "latest_stock_data_date": "2026-06-17"},
        {"ticker": "STALE", "latest_stock_data_date": "2026-06-10"},
    ]

    def stock_rows(ticker, _start, _end):
        if ticker == "NVDA":
            return _stock_rows("NVDA", run_date - timedelta(days=34), 31)
        return _stock_rows("STALE", run_date - timedelta(days=50), 20)

    with (
        patch("src.analysis.phase1_pipeline.store.get_stock_data", side_effect=stock_rows),
        patch(
            "src.analysis.phase1_pipeline.store.last_news_collection",
            return_value="2026-06-16T20:30:00+00:00",
        ),
    ):
        freshness = evaluate_data_freshness(stocks, run_date)

    assert freshness["coverage_status"] == "partial"
    assert [stock["ticker"] for stock in freshness["eligible_stocks"]] == ["NVDA"]
    assert freshness["excluded_tickers"][0]["ticker"] == "STALE"
    assert "stale_stock_data" in freshness["excluded_tickers"][0]["reasons"]


def test_evaluate_data_freshness_excludes_latest_row_without_provenance():
    run_date = date(2026, 6, 17)
    stocks = [{"ticker": "NVDA", "latest_stock_data_date": "2026-06-17"}]

    with (
        patch(
            "src.analysis.phase1_pipeline.store.get_stock_data",
            return_value=_stock_rows(
                "NVDA",
                run_date - timedelta(days=34),
                31,
                include_provenance=False,
            ),
        ),
        patch(
            "src.analysis.phase1_pipeline.store.last_news_collection",
            return_value="2026-06-16T20:30:00+00:00",
        ),
    ):
        freshness = evaluate_data_freshness(stocks, run_date)

    assert freshness["coverage_status"] == "none"
    assert freshness["excluded_tickers"][0]["ticker"] == "NVDA"
    assert (
        "missing_market_data_provenance"
        in freshness["excluded_tickers"][0]["reasons"]
    )


def test_analysis_close_price_prefers_adjusted_close_when_available():
    assert _analysis_close_price(
        {"close_price": "110.00", "adjusted_close_price": "100.00"}
    ) == Decimal("100.00")
    assert _analysis_close_price({"close_price": "110.00"}) == Decimal("110.00")


def test_run_phase1_pipeline_suppresses_publication_when_no_ticker_is_eligible():
    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline.store.active_stock_metadata",
            return_value=[{"ticker": "NVDA", "latest_stock_data_date": "2026-06-01"}],
        ),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.last_news_collection", return_value=None),
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline.score_candidates") as score_candidates,
        patch("src.analysis.phase1_pipeline.select_shortlist"),
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline()

    assert result["statusCode"] == 200
    assert "Publication suppressed" in result["body"]
    publish_payload.assert_not_called()
    score_candidates.assert_not_called()


def test_run_phase1_pipeline_scores_only_eligible_tickers_when_coverage_is_partial():
    run_date = date(2026, 6, 17)
    stocks = [
        {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology", "latest_stock_data_date": "2026-06-17"},
        {"ticker": "STALE", "company_name": "Stale Co", "sector": "Technology", "latest_stock_data_date": "2026-06-01"},
    ]

    def stock_rows(ticker, _start, _end):
        if ticker == "NVDA":
            return _stock_rows("NVDA", run_date - timedelta(days=34), 31)
        return []

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch("src.analysis.phase1_pipeline.store.active_stock_metadata", return_value=stocks),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", side_effect=stock_rows),
        patch(
            "src.analysis.phase1_pipeline.store.last_news_collection",
            return_value="2026-06-16T20:30:00+00:00",
        ),
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.score_candidates", return_value=[]) as score_candidates,
        patch("src.analysis.phase1_pipeline.select_shortlist", return_value=[]),
        patch("src.analysis.phase1_pipeline.analyze_shortlist", return_value=[]),
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline.upcoming_earnings_summary", return_value=[]),
        patch("src.analysis.phase1_pipeline.upcoming_dividends_summary", return_value=[]),
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(run_date)),
    ):
        result = run_phase1_pipeline()

    assert result["statusCode"] == 200
    scored_stocks = score_candidates.call_args.args[0]
    assert [stock["ticker"] for stock in scored_stocks] == ["NVDA"]
    payload = publish_payload.call_args.args[0]
    assert payload["data_quality"]["coverage_status"] == "partial"


def test_run_phase1_score_mode_scores_without_analyzing_or_publishing():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    scores = [_candidate_score("NVDA", opportunity_score=80, negative_score=5)]

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline._eligible_context",
            return_value={"eligible_stocks": [stock], "freshness": {}},
        ),
        patch("src.analysis.phase1_pipeline.score_candidates", return_value=scores),
        patch("src.analysis.phase1_pipeline.select_shortlist", return_value=scores),
        patch("src.analysis.phase1_pipeline.analyze_shortlist") as analyze_shortlist,
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline({"mode": "score"})

    assert result["statusCode"] == 200
    assert result["body"]["candidate_count"] == 1
    assert result["body"]["shortlisted_tickers"] == ["NVDA"]
    analyze_shortlist.assert_not_called()
    publish_payload.assert_not_called()


def test_run_phase1_analyze_batch_mode_slices_shortlist():
    stocks = [
        {"ticker": "AAPL", "company_name": "Apple", "sector": "Technology"},
        {"ticker": "MSFT", "company_name": "Microsoft", "sector": "Technology"},
        {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"},
    ]
    scores = [
        _candidate_score("AAPL", opportunity_score=80, negative_score=0),
        _candidate_score("MSFT", opportunity_score=70, negative_score=0),
        _candidate_score("NVDA", opportunity_score=60, negative_score=0),
    ]
    analyses = [{"ticker": "NVDA"}]

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_scores_for_date",
            return_value=scores,
        ),
        patch("src.analysis.phase1_pipeline.select_shortlist", return_value=scores),
        patch("src.analysis.phase1_pipeline.store.active_stock_metadata", return_value=stocks),
        patch("src.analysis.phase1_pipeline.analyze_shortlist", return_value=analyses) as analyze,
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline(
            {"mode": "analyze_batch", "batch_index": 1, "batch_size": 2}
        )

    assert result["statusCode"] == 200
    assert result["body"]["analyzed_count"] == 1
    assert result["body"]["analyzed_tickers"] == ["NVDA"]
    assert analyze.call_args.args[0] == [scores[2]]


def test_run_phase1_publish_mode_uses_stored_scores_and_analyses():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    scores = [_candidate_score("NVDA", opportunity_score=80, negative_score=5)]
    analyses = [
        {"ticker": "NVDA", "recommendation": "BUY"},
        {"ticker": "AAPL", "recommendation": "BUY"},
    ]
    payload = {"top_picks": [{"ticker": "NVDA"}], "sell_alerts": []}

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline._eligible_context",
            return_value={
                "eligible_stocks": stocks,
                "freshness": {"coverage_status": "complete"},
            },
        ),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_scores_for_date",
            return_value=scores,
        ),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_analysis_for_date",
            return_value=analyses,
        ),
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.publication_data_quality", return_value={}),
        patch("src.analysis.phase1_pipeline.upcoming_earnings_summary", return_value=[]),
        patch("src.analysis.phase1_pipeline.upcoming_dividends_summary", return_value=[]),
        patch(
            "src.analysis.phase1_pipeline.build_publication_payload",
            return_value=payload,
        ) as build_payload,
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline({"mode": "publish"})

    assert result["statusCode"] == 200
    build_payload.assert_called_once()
    assert build_payload.call_args.args[0] == [{"ticker": "NVDA", "recommendation": "BUY"}]
    publish_payload.assert_called_once_with(payload, date(2026, 6, 17))


def _stock_rows(
    ticker: str, start_date: date, count: int, include_provenance: bool = True
) -> list[dict]:
    rows = []
    for offset in range(count):
        row = {
            "ticker": ticker,
            "trading_date": (start_date + timedelta(days=offset)).isoformat(),
            "close_price": "100",
            "volume": 1000,
        }
        if include_provenance:
            row.update(
                {
                    "data_provider": "yfinance",
                    "price_adjustment": "unadjusted",
                    "provider_priority": "primary",
                }
            )
        rows.append(row)
    return rows


def _market_context_rows(
    ticker: str, start_date: date, closes: list[int], volumes: list[int]
) -> list[dict]:
    return [
        {
            "ticker": ticker,
            "trading_date": (start_date + timedelta(days=offset)).isoformat(),
            "close_price": str(close),
            "volume": volumes[offset],
            "data_provider": "yfinance",
            "price_adjustment": "unadjusted",
            "provider_priority": "primary",
        }
        for offset, close in enumerate(closes)
    ]


def _candidate_score(
    ticker: str, opportunity_score: int, negative_score: int
) -> dict:
    return {
        "ticker": ticker,
        "opportunity_score": opportunity_score,
        "negative_score": negative_score,
        "signals": [
            {
                "ticker": ticker,
                "signal_type": "volume_move",
                "direction": "positive" if opportunity_score >= negative_score else "negative",
                "score": opportunity_score or -negative_score,
                "title": "Test signal",
                "summary": "Synthetic test signal.",
                "source": {
                    "provider": "test",
                    "observed_at": "2026-06-17T00:00:00Z",
                    "raw": {},
                },
            }
        ],
    }


class _FailingOpenAIClient:
    class chat:
        class completions:
            @staticmethod
            def create(**_kwargs):
                raise RuntimeError("OpenAI unavailable")


class _SequencedOpenAIClient:
    def __init__(self, responses: list[dict], fail_after: int | None = None):
        self.responses = responses
        self.fail_after = fail_after
        self.calls = 0
        self.models: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs):
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("review unavailable")
        self.models.append(kwargs["model"])
        payload = self.responses[self.calls]
        self.calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=phase1_pipeline.json.dumps(payload),
                    )
                )
            ]
        )


def _fixed_date(today: date):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return today

    return FixedDate
