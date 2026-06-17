"""Tests for Phase 1 scoring and publication ranking."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from src.analysis import phase1_pipeline
from src.analysis.phase1_pipeline import (
    FALLBACK_CONFIDENCE_CAP,
    _analysis_close_price,
    _analyze_candidate,
    _dividend_signals,
    _event_signals,
    _price_volume_signals,
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


def _fixed_date(today: date):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return today

    return FixedDate
