"""Tests for Phase 1 scoring and publication ranking."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.analysis import phase1_pipeline
from src.analysis.phase1_pipeline import (
    FALLBACK_CONFIDENCE_CAP,
    _analysis_close_price,
    _analyze_candidate,
    _chat_completion_options,
    _dividend_signals,
    _event_signals,
    _news_signals,
    _price_volume_signals,
    _sector_relative_signals,
    analyze_shortlist,
    build_data_readiness_payload,
    build_publication_payload,
    evaluate_data_freshness,
    publish_data_readiness_report,
    publish_collection_status_payload,
    publish_payload,
    run_phase1_pipeline,
    score_candidates,
    select_shortlist,
    upcoming_dividends_summary,
    upcoming_earnings_summary,
)


def _decision_grade_stock(ticker: str, **overrides):
    stock = {
        "ticker": ticker,
        "company_name": f"{ticker} Corp",
        "sector": "Technology",
        "industry": "Software",
        "company_size": "blue_chip",
        "source": "seed",
        "metadata_source": "nasdaq_company_profile",
        "metadata_source_url": f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}",
        "metadata_as_of": "2026-06-17",
    }
    stock.update(overrides)
    return stock


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
    assert payload["top_picks"][0]["publication_tier"] == "decision_grade"
    assert payload["sell_alerts"][0]["ticker"] == "TSLA"
    assert payload["sell_alerts"][0]["publication_tier"] == "decision_grade"
    assert payload["publication_tiers"]["published_counts"]["decision_grade"] == 2


def test_build_publication_payload_includes_static_price_chart_data():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    scores = [{"ticker": "NVDA", "opportunity_score": 80, "negative_score": 5, "signals": []}]
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
    rows = [
        {
            "ticker": "NVDA",
            "trading_date": date(2026, 5, 18) + timedelta(days=index),
            "open_price": Decimal("100") + Decimal(index),
            "high_price": Decimal("102") + Decimal(index),
            "low_price": Decimal("99") + Decimal(index),
            "close_price": Decimal("101") + Decimal(index),
            "volume": 1000 + index,
            "currency": "USD",
        }
        for index in range(25)
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=rows),
    ):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    chart = payload["top_picks"][0]["price_chart"]
    assert chart["currency"] == "USD"
    assert len(chart["candles"]) == 25
    assert chart["candles"][0]["open"] == 100
    assert len(chart["sma_20"]) == 6
    assert chart["support"] == 104
    assert chart["resistance"] == 126
    assert chart["trend_line"]["slope_per_session"] > 0


def test_static_price_chart_uses_prewindow_history_for_visible_sma():
    rows = [
        {
            "ticker": "NVDA",
            "trading_date": date(2026, 4, 1) + timedelta(days=index),
            "open_price": Decimal("100") + Decimal(index),
            "high_price": Decimal("102") + Decimal(index),
            "low_price": Decimal("99") + Decimal(index),
            "close_price": Decimal("101") + Decimal(index),
            "volume": 1000 + index,
            "currency": "USD",
        }
        for index in range(70)
    ]

    with patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=rows):
        chart = phase1_pipeline._price_chart_for_ticker("NVDA", date(2026, 6, 17))

    assert chart is not None
    assert len(chart["candles"]) == 45
    assert len(chart["sma_20"]) == 45
    assert chart["sma_20"][0]["date"] == chart["candles"][0]["date"]


def test_build_publication_payload_includes_news_events_and_deduped_evidence():
    stocks = [{"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}]
    duplicate_summary = "NVDA analyst recommendation mix: 14 strong buy, 24 buy."
    scores = [
        {
            "ticker": "NVDA",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": [
                {
                    "signal_type": "analyst_action",
                    "score": 40,
                    "summary": duplicate_summary,
                    "source": {"provider": "yfinance"},
                },
                {
                    "signal_type": "analyst_action",
                    "score": 35,
                    "summary": duplicate_summary,
                    "source": {"provider": "finnhub"},
                },
            ],
        }
    ]
    analyses = [
        {
            "ticker": "NVDA",
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Analyst support",
            "expected_timeframe": "1-30 days",
            "reasoning": "Consensus is supportive.",
            "invalidation_criteria": "Consensus weakens.",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": scores[0]["signals"],
        }
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=[]),
        patch(
            "src.analysis.phase1_pipeline.store.news_for_ticker",
            return_value=[
                {
                    "title": "NVIDIA launches platform",
                    "source": "Reuters",
                    "published_at": "2026-06-17T12:00:00Z",
                    "summary": "NVIDIA announced a new platform.",
                    "sentiment": "positive",
                    "url": "https://example.com/nvda",
                }
            ],
        ),
        patch(
            "src.analysis.phase1_pipeline.store.earnings_events_for_ticker",
            return_value=[
                {
                    "event_date": "2026-07-01",
                    "is_upcoming": True,
                    "provider": "finnhub",
                    "source_url": "https://finnhub.io/calendar/earnings",
                }
            ],
        ),
        patch("src.analysis.phase1_pipeline.store.dividend_events_for_ticker", return_value=[]),
    ):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    pick = payload["top_picks"][0]
    assert pick["supporting_evidence"] == [duplicate_summary]
    assert pick["related_news"][0]["url"] == "https://example.com/nvda"
    assert pick["upcoming_events"][0]["event_type"] == "earnings"
    assert pick["upcoming_events"][0]["event_date"] == "2026-07-01"


def test_build_publication_payload_includes_company_info():
    stocks = [
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA",
            "sector": "Technology",
            "industry": "Semiconductors",
            "business_description": "NVIDIA designs accelerated computing platforms.",
            "flagship_products": ["Data center GPUs", "CUDA"],
            "revenue_segments": ["Data Center", "Gaming"],
            "exchange": "NASDAQ",
            "currency": "USD",
            "country": "United States",
            "website": "https://www.nvidia.com",
            "founded_year": 1993,
            "headquarters": "Santa Clara, California",
            "ipo_year": 1999,
            "metadata_source": "nasdaq_company_profile",
            "metadata_source_url": "https://www.nasdaq.com/market-activity/stocks/nvda",
            "metadata_as_of": "2026-06-17",
            "logo_url": "https://cdn.example.com/logos/NVDA/logo.svg",
            "logo_icon_url": "https://cdn.example.com/logos/NVDA/icon.png",
            "logo_source": "polygon_ticker_details",
            "logo_source_url": "https://api.polygon.io/v3/reference/tickers/NVDA",
            "logo_checked_at": "2026-07-06T08:00:00Z",
        }
    ]
    scores = [{"ticker": "NVDA", "opportunity_score": 80, "negative_score": 5, "signals": []}]
    analyses = [
        {
            "ticker": "NVDA",
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 84,
            "catalyst": "Accelerated computing demand",
            "expected_timeframe": "1-30 days",
            "reasoning": "Demand remains supportive.",
            "invalidation_criteria": "Demand weakens.",
            "opportunity_score": 80,
            "negative_score": 5,
            "signals": [],
        }
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.news_for_ticker", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.earnings_events_for_ticker", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.dividend_events_for_ticker", return_value=[]),
    ):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    pick = payload["top_picks"][0]
    info = pick["company_info"]
    assert pick["logo_url"] == "https://cdn.example.com/logos/NVDA/icon.png"
    assert info["description"] == "NVIDIA designs accelerated computing platforms."
    assert info["top_products"] == ["Data center GPUs", "CUDA"]
    assert info["revenue_segments"] == ["Data Center", "Gaming"]
    assert info["brief_history"] == (
        "Founded in 1993; headquartered in Santa Clara, California; IPO in 1999."
    )
    assert info["metadata_source"] == "nasdaq_company_profile"
    assert info["logo_url"] == "https://cdn.example.com/logos/NVDA/logo.svg"
    assert info["logo_icon_url"] == "https://cdn.example.com/logos/NVDA/icon.png"
    assert info["logo_source"] == "polygon_ticker_details"


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
        "exclusion_reason_counts": {"missing_stock_data": 1},
        "warnings": ["1 active ticker(s) were excluded by data freshness gates."],
    }

    with patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]):
        payload = build_publication_payload(
            analyses, scores, stocks, date(2026, 6, 15), data_quality=data_quality
        )

    assert payload["publication_scope"] == "top_opportunities_among_eligible_tickers"
    assert payload["data_quality"]["coverage_status"] == "partial"
    assert payload["data_quality"]["exclusion_reason_counts"]["missing_stock_data"] == 1
    assert "excluded by data freshness gates" in payload["data_warnings"][-1]


def test_build_data_readiness_payload_summarizes_missing_data_and_ai_fallbacks():
    run_date = date(2026, 7, 29)
    freshness = {
        "run_date": run_date.isoformat(),
        "coverage_status": "partial",
        "active_ticker_count": 3,
        "eligible_ticker_count": 1,
        "excluded_ticker_count": 2,
        "excluded_tickers": [
            {
                "ticker": "METALESS",
                "reasons": ["unresolved_watchlist_metadata"],
                "missing_metadata_fields": ["industry", "metadata_source"],
                "latest_stock_data_date": None,
                "history_start_date": None,
                "history_row_count": 0,
            },
            {
                "ticker": "STALE",
                "reasons": ["stale_stock_data", "insufficient_stock_history_rows"],
                "latest_stock_data_date": "2026-07-20",
                "history_start_date": "2026-07-10",
                "history_row_count": 4,
            },
        ],
        "last_news_collection": None,
        "news_stale": True,
        "warnings": ["News freshness is unknown; no collection timestamp is available."],
    }
    analyses = [
        {
            "ticker": "NVDA",
            "analysis_method": "fallback_heuristic",
            "fallback_reason": "openai_error",
            "recommendation": "BUY",
            "publication_allowed": False,
            "confidence_score": 55,
            "created_at": "2026-07-29T22:01:00",
        }
    ]

    payload = build_data_readiness_payload(
        run_date,
        freshness,
        analyses=analyses,
        scores=[{"ticker": "NVDA"}],
        publication_status="published",
    )

    assert payload["artifact_type"] == "data_readiness"
    assert payload["overall_status"] == "blocked"
    assert payload["summary"]["candidate_count"] == 1
    assert payload["summary"]["analyzed_count"] == 1
    assert payload["summary"]["data_type_counts"]["metadata"] == 1
    assert payload["summary"]["data_type_counts"]["price"] == 1
    assert payload["summary"]["data_type_counts"]["history"] == 1
    assert payload["summary"]["data_type_counts"]["news"] == 1
    assert payload["summary"]["data_type_counts"]["ai_analysis"] == 1
    assert payload["summary"]["repair_mode_counts"]["sync_static_metadata"] == 1
    assert payload["summary"]["repair_mode_counts"]["repair_price_gaps"] == 1
    assert payload["summary"]["repair_mode_counts"]["retry_ai_analysis"] == 1
    assert any(
        item["ticker"] == "METALESS"
        and item["reason"] == "unresolved_watchlist_metadata"
        and item["repair_mode"] == "sync_static_metadata"
        for item in payload["items"]
    )


def test_metadata_drift_flags_active_not_in_seed_and_seed_mismatch():
    seed_rows = [
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA Corp",
            "sector": "Technology",
            "industry": "Semiconductors",
            "company_size": "blue_chip",
            "source": "seed",
            "metadata_source": "nasdaq_company_profile",
            "metadata_source_url": "https://www.nasdaq.com/market-activity/stocks/nvda",
            "metadata_as_of": "2026-06-17",
        }
    ]
    stocks = [
        _decision_grade_stock(
            "NVDA",
            company_name="Old NVIDIA Name",
            latest_stock_data_date="2026-07-29",
        ),
        _decision_grade_stock("REMOVED", latest_stock_data_date="2026-07-29"),
    ]

    with patch(
        "src.analysis.phase1_pipeline._load_packaged_watchlist_seed",
        return_value=seed_rows,
    ):
        drift = phase1_pipeline.evaluate_metadata_drift(stocks)

    assert drift["status"] == "drift_detected"
    assert drift["reason_counts"] == {
        "active_not_in_seed": 1,
        "metadata_seed_mismatch": 1,
    }
    assert any(
        row["ticker"] == "REMOVED" and row["reason"] == "active_not_in_seed"
        for row in drift["rows"]
    )
    assert any(
        row["ticker"] == "NVDA"
        and row["reason"] == "metadata_seed_mismatch"
        and "company_name" in row["mismatched_fields"]
        for row in drift["rows"]
    )


def test_data_readiness_payload_includes_metadata_drift_rows():
    run_date = date(2026, 7, 29)
    freshness = {
        "run_date": run_date.isoformat(),
        "coverage_status": "complete",
        "active_ticker_count": 1,
        "eligible_ticker_count": 1,
        "excluded_ticker_count": 0,
        "excluded_tickers": [],
        "metadata_drift": {
            "status": "drift_detected",
            "rows": [
                {
                    "ticker": "REMOVED",
                    "reason": "active_not_in_seed",
                    "missing_required_fields": [],
                    "mismatched_fields": [],
                    "seed_present": False,
                    "production_active": True,
                    "repair_mode": "sync_static_metadata",
                }
            ],
        },
        "last_news_collection": "2026-07-29T21:30:00+00:00",
        "news_stale": False,
        "warnings": [],
    }

    payload = build_data_readiness_payload(run_date, freshness)

    assert payload["overall_status"] == "blocked"
    assert payload["summary"]["data_type_counts"]["metadata"] == 1
    assert payload["summary"]["reason_counts"] == {
        "metadata_drift:active_not_in_seed": 1
    }
    assert payload["items"][0]["repair_mode"] == "sync_static_metadata"


def test_publish_data_readiness_report_writes_latest_and_history_artifacts():
    payload = {
        "artifact_type": "data_readiness",
        "run_date": "2026-07-29",
        "generated_at": "2026-07-29T22:00:00",
        "overall_status": "ready",
        "summary": {},
        "items": [],
    }

    with (
        patch.object(phase1_pipeline, "ARTIFACT_BUCKET", "stockara-artifacts"),
        patch("src.analysis.phase1_pipeline.boto3.client") as boto_client,
    ):
        publish_data_readiness_report(payload)

    s3 = boto_client.return_value
    keys = [call.kwargs["Key"] for call in s3.put_object.call_args_list]
    assert keys == [
        "data-readiness/latest.json",
        "data-readiness/history/2026-07-29.json",
    ]


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


def test_price_volume_signals_include_stored_evidence_signals():
    with patch("src.analysis.phase1_pipeline.store") as mock_store:
        mock_store.market_signals_for_ticker.return_value = [
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "sec_filing",
                "direction": "positive",
                "score": 22,
                "title": "Recent SEC 8-K filing",
                "summary": "NVDA filed an 8-K with the SEC.",
                "source": {
                    "provider": "sec",
                    "raw": {"form": "8-K", "accession_number": "000123-26-000001"},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "analyst_action",
                "direction": "positive",
                "score": 30,
                "title": "Bullish analyst consensus",
                "summary": "NVDA analyst recommendation mix is constructive.",
                "source": {
                    "provider": "finnhub",
                    "raw": {"strong_buy": 4, "buy": 10, "hold": 2, "coverage": 16},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "unsupported",
                "direction": "positive",
                "score": 99,
                "title": "Ignored signal",
                "summary": "Unsupported stored signal type.",
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "analyst_rating",
                "direction": "positive",
                "score": 28,
                "title": "Analyst rating upgraded",
                "summary": "A firm upgraded NVDA from Neutral to Buy.",
                "source": {
                    "provider": "finnhub",
                    "raw": {"action": "upgrade", "from_grade": "Neutral", "to_grade": "Buy"},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "price_target",
                "direction": "positive",
                "score": 25,
                "title": "Analyst price target update",
                "summary": "Mean price target implies upside.",
                "source": {
                    "provider": "finnhub",
                    "raw": {"target_mean": 125, "last_price": 100, "upside_percent": 25},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "earnings_release",
                "direction": "positive",
                "score": 25,
                "title": "Earnings release available",
                "summary": "NVDA published earnings results.",
                "source": {
                    "provider": "finnhub",
                    "raw": {"article_title": "NVDA reports earnings"},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "earnings_transcript",
                "direction": "neutral",
                "score": 16,
                "title": "Earnings call transcript available",
                "summary": "NVDA earnings transcript is available.",
                "source": {
                    "provider": "finnhub",
                    "raw": {"article_title": "NVDA earnings call transcript"},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "sector_context",
                "direction": "positive",
                "score": 10,
                "title": "Sector ETF context",
                "summary": "Technology sector ETF moved higher.",
                "source": {
                    "provider": "yfinance",
                    "raw": {"sector_etf": "XLK", "context_only": True},
                },
            },
            {
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "macro_context",
                "direction": "negative",
                "score": -6,
                "title": "Macro market context",
                "summary": "Macro proxies were a modest headwind.",
                "source": {
                    "provider": "yfinance",
                    "raw": {"equity_risk_move_percent": -1.5, "context_only": True},
                },
            },
        ]

        signals = _price_volume_signals("NVDA", date(2026, 6, 17))

    signal_types = {signal["signal_type"] for signal in signals}
    assert signal_types == {
        "sec_filing",
        "analyst_action",
        "analyst_rating",
        "price_target",
        "earnings_release",
        "earnings_transcript",
        "sector_context",
        "macro_context",
    }
    sec_signal = next(signal for signal in signals if signal["signal_type"] == "sec_filing")
    analyst_signal = next(
        signal for signal in signals if signal["signal_type"] == "analyst_action"
    )
    assert sec_signal["source"]["provider"] == "sec"
    assert sec_signal["source"]["raw"]["form"] == "8-K"
    assert analyst_signal["source"]["provider"] == "finnhub"
    assert analyst_signal["source"]["raw"]["coverage"] == 16
    assert any(signal["signal_type"] == "analyst_rating" for signal in signals)
    assert any(signal["signal_type"] == "price_target" for signal in signals)
    assert any(signal["signal_type"] == "earnings_release" for signal in signals)
    assert any(signal["signal_type"] == "earnings_transcript" for signal in signals)
    assert any(signal["signal_type"] == "sector_context" for signal in signals)
    assert any(signal["signal_type"] == "macro_context" for signal in signals)


def test_score_candidates_skips_live_provider_enrichment_by_default():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    with (
        patch.object(phase1_pipeline, "ENABLE_LIVE_SCORING_PROVIDER_SIGNALS", False),
        patch("src.analysis.phase1_pipeline._price_volume_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline._news_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline._event_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline._options_signals") as options,
        patch("src.analysis.phase1_pipeline._analyst_signals") as analyst,
        patch("src.analysis.phase1_pipeline._insider_signals") as insider,
        patch("src.analysis.phase1_pipeline._institutional_signals") as institutional,
        patch("src.analysis.phase1_pipeline._sector_relative_signals") as sector,
        patch("src.analysis.phase1_pipeline.store.put_candidate_score") as put_score,
    ):
        scores = score_candidates([stock], date(2026, 6, 17))

    assert scores[0]["ticker"] == "NVDA"
    options.assert_not_called()
    analyst.assert_not_called()
    insider.assert_not_called()
    institutional.assert_not_called()
    sector.assert_not_called()
    put_score.assert_called_once()


def test_score_candidates_excludes_neutral_and_context_only_signals_from_totals():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    signals = [
        phase1_pipeline._signal(
            "NVDA",
            "news",
            "neutral",
            40,
            "Elevated news momentum",
            "Ticker appeared in several neutral articles.",
            "news",
            {"article_count": 8},
        ),
        phase1_pipeline._signal(
            "NVDA",
            "sector_context",
            "positive",
            30,
            "Sector context",
            "Sector ETF was supportive background context.",
            "yfinance",
            {"context_only": True},
        ),
        phase1_pipeline._signal(
            "NVDA",
            "technical_trend",
            "positive",
            22,
            "Multi-day technical trend",
            "Multi-day evidence is constructive.",
            "derived_ohlcv",
        ),
    ]

    with (
        patch("src.analysis.phase1_pipeline._price_volume_signals", return_value=signals),
        patch("src.analysis.phase1_pipeline._news_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline._event_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.put_candidate_score") as put_score,
    ):
        scores = score_candidates([stock], date(2026, 6, 17))

    assert scores[0]["opportunity_score"] == 22
    assert scores[0]["negative_score"] == 0
    assert scores[0]["scored_signal_count"] == 1
    assert scores[0]["context_signal_count"] == 2
    put_score.assert_called_once()


def test_score_candidates_excludes_unconfirmed_one_day_market_moves():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    signals = [
        phase1_pipeline._signal(
            "NVDA",
            "price_move",
            "positive",
            42,
            "Large daily price move",
            "NVDA moved sharply in one session.",
            "yfinance",
            {"requires_confirmation": True},
        ),
        phase1_pipeline._signal(
            "NVDA",
            "volume_move",
            "positive",
            25,
            "Unusual volume",
            "NVDA traded at unusual one-session volume.",
            "yfinance",
            {"requires_confirmation": True},
        ),
    ]

    with (
        patch("src.analysis.phase1_pipeline._price_volume_signals", return_value=signals),
        patch("src.analysis.phase1_pipeline._news_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline._event_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.put_candidate_score"),
    ):
        scores = score_candidates([stock], date(2026, 6, 17))

    assert scores[0]["opportunity_score"] == 0
    assert scores[0]["negative_score"] == 0
    assert scores[0]["scored_signal_count"] == 0
    assert scores[0]["context_signal_count"] == 2


def test_score_candidates_counts_one_day_market_moves_when_confirmed():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    signals = [
        phase1_pipeline._signal(
            "NVDA",
            "price_move",
            "positive",
            30,
            "Large daily price move",
            "NVDA moved sharply in one session.",
            "yfinance",
            {"requires_confirmation": True},
        ),
        phase1_pipeline._signal(
            "NVDA",
            "technical_trend",
            "positive",
            24,
            "Multi-day technical trend",
            "Multi-day price action confirms the move.",
            "derived_ohlcv",
        ),
    ]

    with (
        patch("src.analysis.phase1_pipeline._price_volume_signals", return_value=signals),
        patch("src.analysis.phase1_pipeline._news_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline._event_signals", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.put_candidate_score"),
    ):
        scores = score_candidates([stock], date(2026, 6, 17))

    assert scores[0]["opportunity_score"] == 54
    assert scores[0]["negative_score"] == 0
    assert scores[0]["scored_signal_count"] == 2
    assert scores[0]["context_signal_count"] == 0


def test_neutral_news_momentum_is_context_not_scored_evidence():
    with patch("src.analysis.phase1_pipeline.store.news_for_ticker") as news_for_ticker:
        news_for_ticker.return_value = [
            {"title": "Company hosts investor day", "summary": "Executives present strategy."},
            {"title": "Company opens a new office", "summary": "Local expansion continues."},
        ]

        signals = _news_signals("NVDA", date(2026, 6, 17))

    assert signals[0]["direction"] == "neutral"
    assert signals[0]["score"] == 0
    assert signals[0]["source"]["raw"]["context_only"] is True


def test_options_signal_scores_directional_open_interest_skew_only_when_liquid():
    ticker = SimpleNamespace(
        options=["2026-07-17"],
        option_chain=lambda _expiration: SimpleNamespace(
            calls=pd.DataFrame([{"openInterest": 1000}, {"openInterest": 500}]),
            puts=pd.DataFrame([{"openInterest": 400}]),
        ),
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._options_signals("NVDA")

    assert signals[0]["direction"] == "positive"
    assert signals[0]["score"] > 0
    assert signals[0]["source"]["raw"]["put_call_open_interest_ratio"] < 0.7
    assert "context_only" not in signals[0]["source"]["raw"]


def test_options_signal_keeps_thin_options_data_as_context():
    ticker = SimpleNamespace(
        options=["2026-07-17"],
        option_chain=lambda _expiration: SimpleNamespace(
            calls=pd.DataFrame([{"openInterest": 50}]),
            puts=pd.DataFrame([{"openInterest": 25}]),
        ),
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._options_signals("THIN")

    assert signals[0]["direction"] == "neutral"
    assert signals[0]["score"] == 0
    assert signals[0]["source"]["raw"]["context_only"] is True


def test_analyst_signal_scores_clear_consensus_with_coverage():
    ticker = SimpleNamespace(
        recommendations=pd.DataFrame(
            [
                {
                    "period": "0m",
                    "strongBuy": 4,
                    "buy": 8,
                    "hold": 2,
                    "sell": 0,
                    "strongSell": 0,
                }
            ]
        )
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._analyst_signals("NVDA")

    assert signals[0]["direction"] == "positive"
    assert signals[0]["score"] > 0
    assert signals[0]["source"]["raw"]["coverage"] == 14
    assert "context_only" not in signals[0]["source"]["raw"]


def test_analyst_signal_keeps_low_coverage_as_context():
    ticker = SimpleNamespace(
        recommendations=pd.DataFrame(
            [{"period": "0m", "strongBuy": 1, "buy": 1, "hold": 0, "sell": 0, "strongSell": 0}]
        )
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._analyst_signals("SMOL")

    assert signals[0]["direction"] == "neutral"
    assert signals[0]["score"] == 0
    assert signals[0]["source"]["raw"]["context_only"] is True


def test_insider_signal_scores_net_selling_from_directional_transactions():
    ticker = SimpleNamespace(
        insider_transactions=pd.DataFrame(
            [
                {"Transaction": "Sale", "Shares": 700},
                {"Transaction": "Sale", "Shares": 300},
                {"Transaction": "Purchase", "Shares": 100},
            ]
        )
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._insider_signals("ACME")

    assert signals[0]["direction"] == "negative"
    assert signals[0]["score"] < 0
    assert signals[0]["source"]["raw"]["net_purchase_shares"] == -900
    assert "context_only" not in signals[0]["source"]["raw"]


def test_institutional_signal_is_context_without_change_evidence():
    ticker = SimpleNamespace(institutional_holders=pd.DataFrame([{"Holder": "Fund", "Shares": 1000}]))

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._institutional_signals("NVDA")

    assert signals[0]["direction"] == "neutral"
    assert signals[0]["score"] == 0
    assert signals[0]["source"]["raw"]["context_only"] is True


def test_fundamental_signal_scores_quality_at_moderate_valuation():
    ticker = SimpleNamespace(
        get_info=lambda: {
            "revenueGrowth": 0.14,
            "profitMargins": 0.18,
            "debtToEquity": 45,
            "freeCashflow": 2_000_000_000,
            "forwardPE": 16,
            "marketCap": 100_000_000_000,
        }
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._fundamental_signals("ACME")

    assert signals[0]["direction"] == "positive"
    assert signals[0]["score"] == 22
    assert "context_only" not in signals[0]["source"]["raw"]


def test_fundamental_signal_flags_stretched_valuation_without_support():
    ticker = SimpleNamespace(
        get_info=lambda: {
            "revenueGrowth": 0.02,
            "profitMargins": 0.03,
            "debtToEquity": 80,
            "freeCashflow": 100_000,
            "forwardPE": 95,
            "marketCap": 2_000_000_000,
        }
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._fundamental_signals("RICH")

    assert signals[0]["direction"] == "negative"
    assert signals[0]["score"] == -18
    assert "context_only" not in signals[0]["source"]["raw"]


def test_fundamental_signal_keeps_ambiguous_fields_as_context():
    ticker = SimpleNamespace(
        get_info=lambda: {
            "revenueGrowth": 0.03,
            "profitMargins": 0.07,
            "debtToEquity": 90,
            "forwardPE": 28,
            "marketCap": 4_000_000_000,
        }
    )

    with patch("src.analysis.phase1_pipeline.yf.Ticker", return_value=ticker):
        signals = phase1_pipeline._fundamental_signals("MIXD")

    assert signals[0]["direction"] == "neutral"
    assert signals[0]["score"] == 0
    assert signals[0]["source"]["raw"]["context_only"] is True


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


def test_sector_relative_signals_score_multi_window_outperformance():
    run_date = date(2026, 6, 17)
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    stock_rows = _market_context_rows(
        "NVDA",
        run_date - timedelta(days=24),
        closes=[100 + offset for offset in range(25)],
        volumes=[1000] * 25,
    )
    sector_frame = pd.DataFrame({"Close": [100 + offset * 0.25 for offset in range(25)]})

    with (
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=stock_rows),
        patch("src.analysis.phase1_pipeline.yf.download", return_value=sector_frame),
    ):
        signals = _sector_relative_signals(stock, run_date)

    assert len(signals) == 1
    signal = signals[0]
    assert signal["signal_type"] == "sector_relative"
    assert signal["direction"] == "positive"
    assert signal["score"] > 0
    assert signal["source"]["raw"]["sector_etf"] == "XLK"
    assert signal["source"]["raw"]["relative_20d_percent"] > 3


def test_sector_relative_signals_score_multi_window_underperformance():
    run_date = date(2026, 6, 17)
    stock = {"ticker": "AAPL", "company_name": "Apple", "sector": "Technology"}
    stock_rows = _market_context_rows(
        "AAPL",
        run_date - timedelta(days=24),
        closes=[124 - offset for offset in range(25)],
        volumes=[1000] * 25,
    )
    sector_frame = pd.DataFrame({"Close": [100 + offset * 0.25 for offset in range(25)]})

    with (
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=stock_rows),
        patch("src.analysis.phase1_pipeline.yf.download", return_value=sector_frame),
    ):
        signals = _sector_relative_signals(stock, run_date)

    assert len(signals) == 1
    assert signals[0]["direction"] == "negative"
    assert signals[0]["score"] < 0
    assert signals[0]["source"]["raw"]["relative_20d_percent"] < -3


def test_sector_relative_signals_ignore_noise_and_insufficient_history():
    run_date = date(2026, 6, 17)
    stock = {"ticker": "MSFT", "company_name": "Microsoft", "sector": "Technology"}
    stock_rows = _market_context_rows(
        "MSFT",
        run_date - timedelta(days=24),
        closes=[100 + offset * 0.2 for offset in range(25)],
        volumes=[1000] * 25,
    )
    sector_frame = pd.DataFrame({"Close": [100 + offset * 0.19 for offset in range(25)]})

    with (
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=stock_rows),
        patch("src.analysis.phase1_pipeline.yf.download", return_value=sector_frame),
    ):
        assert _sector_relative_signals(stock, run_date) == []

    with patch(
        "src.analysis.phase1_pipeline.store.get_stock_data",
        return_value=stock_rows[:10],
    ):
        assert _sector_relative_signals(stock, run_date) == []


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


def test_earnings_signal_requires_history_or_news_catalyst_to_score():
    with patch("src.analysis.phase1_pipeline.store") as mock_store:
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
        mock_store.news_for_ticker.return_value = []

        signals = _event_signals("NVDA", date(2026, 6, 17))

    earnings = [signal for signal in signals if signal["signal_type"] == "earnings"]
    assert earnings[0]["direction"] == "neutral"
    assert earnings[0]["score"] == 0
    prediction = earnings[0]["source"]["raw"]["prediction"]
    assert prediction["context_only"] is True
    assert prediction["reaction_history_sufficient"] is False


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
    assert signals[0]["direction"] == "neutral"
    assert signals[0]["score"] == 0
    assert signals[0]["source"]["raw"]["prediction"]["context_only"] is True


def test_dividend_signal_scores_only_with_sufficient_reaction_history():
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
                    "post_ex_dividend_price_move_percent": Decimal("1.0"),
                    "is_upcoming": False,
                },
                {
                    "ticker": "AAPL",
                    "ex_dividend_date": "2026-02-15",
                    "post_ex_dividend_price_move_percent": Decimal("1.2"),
                    "is_upcoming": False,
                },
                {
                    "ticker": "AAPL",
                    "ex_dividend_date": "2025-11-15",
                    "post_ex_dividend_price_move_percent": Decimal("0.8"),
                    "is_upcoming": False,
                },
            ],
        ]

        signals = _dividend_signals("AAPL", date(2026, 6, 17))

    assert signals[0]["direction"] == "positive"
    assert signals[0]["score"] > 0
    prediction = signals[0]["source"]["raw"]["prediction"]
    assert prediction["context_only"] is False
    assert prediction["reaction_history_sufficient"] is True


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


def test_ai_analysis_adds_signal_derived_invalidation_when_model_omits_it():
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}
    signal = phase1_pipeline._signal(
        "NVDA",
        "technical_trend",
        "positive",
        35,
        "Multi-day technical trend",
        "NVDA has a confirmed multi-day trend.",
        "derived_ohlcv",
        {
            "return_5d_percent": 6.2,
            "close_vs_sma_20_percent": 4.1,
            "recent_3_session_volume_ratio": 1.8,
        },
    )
    score = {
        "ticker": "NVDA",
        "opportunity_score": 70,
        "negative_score": 5,
        "signals": [signal],
    }

    analysis = phase1_pipeline._normalize_ai_analysis(
        stock,
        score,
        {
            "recommendation": "BUY",
            "risk_level": "MEDIUM",
            "confidence_score": 77,
            "catalyst": "Trend follow-through",
            "expected_timeframe": "1-30 days",
            "reasoning": "Trend evidence is constructive.",
        },
        date(2026, 6, 17),
    )

    assert analysis["invalidation_checks"]
    assert "5-session return" in analysis["invalidation_checks"][0]
    assert "20-session moving-average confirmation" in analysis["invalidation_criteria"]


def test_analysis_prompt_includes_candidate_specific_invalidation_checks():
    score = _candidate_score("NVDA", opportunity_score=90, negative_score=5)
    score["signals"] = [
        phase1_pipeline._signal(
            "NVDA",
            "options",
            "positive",
            15,
            "Options open-interest context",
            "NVDA nearest-expiration options skew is call-heavy.",
            "yfinance",
            {"put_call_open_interest_ratio": 0.4},
        )
    ]
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}

    prompt = phase1_pipeline._build_prompt(stock, score)

    assert "BUY invalidation checks:" in prompt
    assert "Options open-interest skew normalizes" in prompt
    assert "generic wording" in prompt


def test_review_prompt_requires_specific_invalidation_for_actionable_calls():
    analysis = {
        "recommendation": "BUY",
        "risk_level": "MEDIUM",
        "confidence_score": 82,
        "opportunity_score": 90,
        "negative_score": 5,
        "catalyst": "Volume breakout",
        "reasoning": "Evidence supports a near-term catalyst.",
        "invalidation_criteria": "Breakout fails.",
        "invalidation_checks": [
            "volume confirmation fades toward normal levels",
            "Reassess after the stated timeframe.",
        ],
        "signals": [],
    }
    stock = {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"}

    prompt = phase1_pipeline._build_review_prompt(stock, analysis)

    assert "Candidate-specific invalidation checks:" in prompt
    assert "volume confirmation fades" in prompt
    assert "reject if invalidation criteria are generic" in prompt


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
                "rejection_category": "insufficient_evidence",
                "what_would_make_approvable": "More durable catalyst evidence.",
            },
        }
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.news_for_ticker", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.earnings_events_for_ticker", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.dividend_events_for_ticker", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=[]),
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        payload = build_publication_payload(analyses, scores, stocks, date(2026, 6, 17))

    assert payload["top_picks"] == []
    assert payload["review_policy"]["reviewed_count"] == 1
    assert payload["review_policy"]["rejected_count"] == 1
    assert payload["review_policy"]["review_suppressed_count"] == 1
    assert payload["review_policy"]["review_rejection_audit_count"] == 1
    withheld = payload["review_rejections"][0]
    assert withheld["ticker"] == "NVDA"
    assert withheld["company_name"] == "NVIDIA"
    assert withheld["sector"] == "Technology"
    assert withheld["analysis_model"] == "gpt-5.4-mini"
    assert withheld["recommendation"] == "BUY"
    assert withheld["supporting_evidence"] == []
    assert withheld["source_traceability"] == []
    assert withheld["related_news"] == []
    assert withheld["upcoming_events"] == []
    assert withheld["needed_evidence"] == [
        {
            "gap_type": "reviewer_requested_evidence",
            "title": "Reviewer-requested evidence",
            "status": "temporary_suspended",
            "collection_plan": (
                "Turn the reviewer note into a specific collector task or source-backed "
                "manual research item."
            ),
            "source_candidates": ["review model note"],
        }
    ]
    assert withheld["ai_review"] == {
        "status": "rejected",
        "model": "gpt-5.4",
        "approved": False,
        "rationale": "Evidence is too weak.",
        "concerns": ["weak evidence"],
        "rejection_category": "insufficient_evidence",
        "what_would_make_approvable": "More durable catalyst evidence.",
    }
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
    assert payload["publication_tiers"]["analysis_counts"]["fallback_preview"] == 2
    assert payload["publication_tiers"]["published_counts"]["fallback_preview"] == 0
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
    assert payload["top_picks"][0]["publication_tier"] == "decision_grade"


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


def test_publish_collection_status_payload_writes_status_artifacts_only():
    payload = {
        "artifact_type": "collection_gate_status",
        "publication_date": "2026-06-17",
        "generated_at": "2026-06-17T21:00:00",
        "publication_status": "waiting",
        "suppression_reason": "analysis_not_before",
        "candidate_count": 0,
        "analyzed_count": 0,
        "data_quality": {},
        "data_warnings": ["Publication waiting: configured analysis window has not opened."],
    }

    with (
        patch.object(phase1_pipeline, "ARTIFACT_BUCKET", "stockara-artifacts"),
        patch("src.analysis.phase1_pipeline.boto3.client") as boto_client,
    ):
        publish_collection_status_payload(payload, date(2026, 6, 17))

    s3 = boto_client.return_value
    keys = [call.kwargs["Key"] for call in s3.put_object.call_args_list]
    assert keys == [
        "top-picks/status/latest.json",
        "top-picks/status/history/2026-06-17.json",
    ]


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
        _decision_grade_stock("NVDA", latest_stock_data_date="2026-06-17"),
        _decision_grade_stock("STALE", latest_stock_data_date="2026-06-10"),
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
    quality = phase1_pipeline.publication_data_quality(freshness)
    assert quality["exclusion_reason_counts"]["stale_stock_data"] == 1


def test_evaluate_data_freshness_excludes_unresolved_watchlist_metadata():
    run_date = date(2026, 6, 17)
    stocks = [
        _decision_grade_stock(
            "NVDA",
            industry="",
            metadata_source="",
            latest_stock_data_date="2026-06-17",
        )
    ]

    with (
        patch("src.analysis.phase1_pipeline.store.get_stock_data") as get_stock_data,
        patch(
            "src.analysis.phase1_pipeline.store.last_news_collection",
            return_value="2026-06-16T20:30:00+00:00",
        ),
    ):
        freshness = evaluate_data_freshness(stocks, run_date)

    assert freshness["coverage_status"] == "none"
    assert freshness["eligible_stocks"] == []
    excluded = freshness["excluded_tickers"][0]
    assert excluded["ticker"] == "NVDA"
    assert excluded["reasons"] == ["unresolved_watchlist_metadata"]
    assert excluded["missing_metadata_fields"] == ["industry", "metadata_source"]
    get_stock_data.assert_not_called()
    quality = phase1_pipeline.publication_data_quality(freshness)
    assert quality["exclusion_reason_counts"]["unresolved_watchlist_metadata"] == 1


def test_evaluate_data_freshness_excludes_latest_row_without_provenance():
    run_date = date(2026, 6, 17)
    stocks = [_decision_grade_stock("NVDA", latest_stock_data_date="2026-06-17")]

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
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline._collection_gate_response", return_value=None),
        patch(
            "src.analysis.phase1_pipeline.store.active_stock_metadata",
            return_value=[
                _decision_grade_stock("NVDA", latest_stock_data_date="2026-06-01")
            ],
        ),
        patch("src.analysis.phase1_pipeline.store.get_stock_data", return_value=[]),
        patch("src.analysis.phase1_pipeline.store.last_news_collection", return_value=None),
        patch(
            "src.analysis.phase1_pipeline._with_collection_manifest_quality",
            side_effect=lambda quality, run_date: quality,
        ),
        patch("src.analysis.phase1_pipeline.publish_data_readiness_report"),
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline.score_candidates") as score_candidates,
        patch("src.analysis.phase1_pipeline.select_shortlist"),
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline()

    assert result["statusCode"] == 200
    assert "Publication suppressed" in result["body"]
    payload = publish_payload.call_args.args[0]
    assert payload["publication_status"] == "suppressed"
    assert payload["suppression_reason"] == "no_eligible_tickers"
    assert payload["publication_date"] == "2026-06-17"
    assert payload["top_picks"] == []
    assert payload["data_readiness_summary"]["blocked_item_count"] >= 1
    score_candidates.assert_not_called()


def test_publish_from_stored_state_suppresses_when_analysis_is_missing():
    run_date = date(2026, 6, 17)
    context = {
        "eligible_stocks": [
            _decision_grade_stock("NVDA", latest_stock_data_date="2026-06-17")
        ],
        "freshness": {
            "coverage_status": "complete",
            "active_ticker_count": 1,
            "eligible_ticker_count": 1,
            "excluded_ticker_count": 0,
            "excluded_tickers": [],
            "stock_freshness_max_age_days": 3,
            "min_history_calendar_days": 30,
            "min_history_rows": 20,
            "last_news_collection": "2026-06-16T20:30:00+00:00",
            "news_stale": False,
            "warnings": [],
        },
    }
    scores = [_candidate_score("NVDA", opportunity_score=90, negative_score=5)]

    with (
        patch.object(phase1_pipeline, "ARTIFACT_BUCKET", "artifact-bucket"),
        patch(
            "src.analysis.phase1_pipeline._with_collection_manifest_quality",
            side_effect=lambda quality, run_date: quality,
        ),
        patch("src.analysis.phase1_pipeline.store.sell_alert_tickers", return_value=[]),
        patch("src.analysis.phase1_pipeline.publish_data_readiness_report"),
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
    ):
        result = phase1_pipeline._publish_from_stored_state(
            run_date, context, scores, analyses=[]
        )

    assert result == {
        "statusCode": 200,
        "body": "Publication suppressed: no candidate analyses available",
    }
    emit_metric.assert_called_once_with("publication_suppressed", 1)
    payload = publish_payload.call_args.args[0]
    assert payload["publication_status"] == "suppressed"
    assert payload["suppression_reason"] == "no_candidate_analyses"
    assert payload["candidate_count"] == 1
    assert payload["analyzed_count"] == 0
    assert payload["data_readiness_summary"]["candidate_count"] == 1
    assert payload["top_picks"] == []
    assert payload["data_warnings"] == [
        "Publication suppressed: no candidate analyses available."
    ]


def test_collection_manifest_coverage_targets_wait_for_collection_gates():
    run_date = date(2026, 6, 17)
    manifest_payload = {
        "manifest_date": run_date.isoformat(),
        "generated_at": "2026-06-17T07:30:00Z",
        "updated_at": "2026-06-17T08:00:00Z",
        "active_ticker_count": 1,
        "task_types": ["price", "news", "earnings", "dividend"],
        "tasks": [],
        "summary": {
            "total_tasks": 0,
            "coverage_gates": [
                {
                    "name": "price_freshness",
                    "passed": False,
                    "observed_value": "0.5",
                    "required_value": "0.9",
                    "unit": "ratio",
                    "message": "Not enough fresh prices.",
                }
            ],
        },
    }
    body = SimpleNamespace(
        read=lambda: phase1_pipeline.json.dumps(manifest_payload).encode("utf-8")
    )
    with (
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline.boto3.client") as client,
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
        patch(
            "src.analysis.phase1_pipeline.publish_collection_status_payload"
        ) as publish_status,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        result = phase1_pipeline._collection_gate_response(
            run_date,
            publish_status_artifact=True,
        )

    assert result["statusCode"] == 202
    assert result["body"]["stage"] == "waiting_for_collection_gates"
    assert result["body"]["reason"] == "coverage_gates_failed"
    assert result["body"]["failed_gates"][0]["name"] == "price_freshness"
    emit_metric.assert_any_call("collection_coverage_targets_below_threshold", 1)
    emit_metric.assert_any_call("collection_gates_closed", 1)
    payload = publish_status.call_args.args[0]
    assert payload["artifact_type"] == "collection_gate_status"
    assert payload["publication_status"] == "waiting"
    assert payload["suppression_reason"] == "coverage_gates_failed"
    assert payload["data_quality"]["coverage_status"] == "waiting_for_collection_gates"
    assert payload["data_quality"]["collection_manifest"]["manifest_key"] == (
        "collection_manifest/2026-06-17.json"
    )
    assert "collection coverage gates" in payload["data_warnings"][0]
    assert publish_status.call_args.args[1] == run_date


def test_collection_manifest_news_gate_is_advisory_when_required_gates_pass():
    run_date = date(2026, 6, 17)
    manifest_payload = {
        "manifest_date": run_date.isoformat(),
        "generated_at": "2026-06-17T07:30:00Z",
        "updated_at": "2026-06-17T08:00:00Z",
        "active_ticker_count": 1,
        "task_types": ["price", "news", "earnings", "dividend"],
        "tasks": [],
        "summary": {
            "total_tasks": 0,
            "coverage_gates": [
                {
                    "name": "price_freshness",
                    "passed": True,
                    "observed_value": "0.95",
                    "required_value": "0.9",
                    "unit": "ratio",
                },
                {
                    "name": "news_freshness",
                    "passed": False,
                    "observed_value": "0.05",
                    "required_value": "1",
                    "unit": "ratio",
                    "message": "News chunks are incomplete.",
                },
                {
                    "name": "calendar_coverage",
                    "passed": True,
                    "observed_value": "0.95",
                    "required_value": "0.9",
                    "unit": "ratio",
                },
            ],
        },
    }
    body = SimpleNamespace(
        read=lambda: phase1_pipeline.json.dumps(manifest_payload).encode("utf-8")
    )
    with (
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline.boto3.client") as client,
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch(
            "src.analysis.phase1_pipeline.publish_collection_status_payload"
        ) as publish_status,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        result = phase1_pipeline._collection_gate_response(
            run_date,
            publish_status_artifact=True,
        )

    assert result is None
    publish_payload.assert_not_called()
    publish_status.assert_not_called()
    emit_metric.assert_any_call("collection_coverage_targets_below_threshold", 1)
    emit_metric.assert_any_call("collection_gates_open", 1)


def test_collection_manifest_missing_waits_for_manifest():
    run_date = date(2026, 6, 17)
    with (
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline.boto3.client") as client,
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
        patch(
            "src.analysis.phase1_pipeline.publish_collection_status_payload"
        ) as publish_status,
    ):
        client.return_value.get_object.side_effect = RuntimeError("missing")
        result = phase1_pipeline._collection_gate_response(
            run_date,
            publish_status_artifact=True,
        )

    assert result == {
        "statusCode": 202,
        "body": {
            "mode": "collection_gate",
            "stage": "waiting_for_collection_manifest",
            "publication_date": "2026-06-17",
            "reason": "collection_manifest_missing",
            "manifest_key": "collection_manifest/2026-06-17.json",
        },
    }
    emit_metric.assert_called_once_with("collection_gates_closed", 1)
    payload = publish_status.call_args.args[0]
    assert payload["artifact_type"] == "collection_gate_status"
    assert payload["publication_status"] == "waiting"
    assert payload["suppression_reason"] == "collection_manifest_missing"
    assert payload["data_quality"]["collection_gate"]["manifest_key"] == (
        "collection_manifest/2026-06-17.json"
    )


def test_collection_manifest_analysis_window_waits_until_not_before():
    run_date = date(2026, 6, 17)
    manifest_payload = {
        "manifest_date": run_date.isoformat(),
        "generated_at": "2026-06-17T07:30:00Z",
        "updated_at": "2026-06-17T08:00:00Z",
        "analysis_not_before": "2026-06-17T22:00:00Z",
        "active_ticker_count": 1,
        "task_types": ["price", "news", "earnings", "dividend"],
        "tasks": [],
        "summary": {
            "coverage_gates": [
                {
                    "name": "price_freshness",
                    "passed": True,
                    "observed_value": "1",
                    "required_value": "0.9",
                    "unit": "ratio",
                }
            ],
        },
    }
    body = SimpleNamespace(
        read=lambda: phase1_pipeline.json.dumps(manifest_payload).encode("utf-8")
    )
    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2026-06-17T21:00:00+00:00")

        @classmethod
        def utcnow(cls):
            return datetime.fromisoformat("2026-06-17T21:00:00+00:00")

    with (
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline.boto3.client") as client,
        patch("src.analysis.phase1_pipeline.datetime", FixedDateTime),
        patch("src.analysis.phase1_pipeline._emit_metric") as emit_metric,
        patch(
            "src.analysis.phase1_pipeline.publish_collection_status_payload"
        ) as publish_status,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        result = phase1_pipeline._collection_gate_response(
            run_date,
            publish_status_artifact=True,
        )

    assert result["statusCode"] == 202
    assert result["body"]["stage"] == "waiting_for_analysis_window"
    assert result["body"]["analysis_not_before"] == "2026-06-17T22:00:00+00:00"
    emit_metric.assert_called_once_with("collection_gates_closed", 1)
    payload = publish_status.call_args.args[0]
    assert payload["artifact_type"] == "collection_gate_status"
    assert payload["publication_status"] == "waiting"
    assert payload["suppression_reason"] == "analysis_not_before"


def test_collection_manifest_quality_metadata_is_added_when_available():
    run_date = date(2026, 6, 17)
    manifest_payload = {
        "manifest_date": run_date.isoformat(),
        "generated_at": "2026-06-17T07:30:00Z",
        "updated_at": "2026-06-17T08:00:00Z",
        "active_ticker_count": 1000,
        "task_types": ["price", "news", "earnings", "dividend"],
        "tasks": [],
        "summary": {
            "total_tasks": 4,
            "succeeded_tasks": 4,
            "total_tickers": 1000,
            "successful_tickers": 1000,
            "coverage_ratio": "1.0",
            "coverage_gates": [
                {
                    "name": "price_freshness",
                    "passed": True,
                    "observed_value": "1.0",
                    "required_value": "0.9",
                    "unit": "ratio",
                }
            ],
        },
    }
    body = SimpleNamespace(
        read=lambda: phase1_pipeline.json.dumps(manifest_payload).encode("utf-8")
    )
    with (
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline.boto3.client") as client,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        quality = phase1_pipeline._with_collection_manifest_quality(
            {"coverage_status": "complete"},
            run_date,
        )

    assert quality["coverage_status"] == "complete"
    assert quality["collection_manifest"]["manifest_key"] == (
        "collection_manifest/2026-06-17.json"
    )
    assert quality["collection_manifest"]["summary"]["coverage_gates"][0]["passed"] is True


def test_collection_manifest_quality_warns_when_optional_news_gate_is_degraded():
    run_date = date(2026, 6, 17)
    manifest_payload = {
        "manifest_date": run_date.isoformat(),
        "generated_at": "2026-06-17T07:30:00Z",
        "updated_at": "2026-06-17T08:00:00Z",
        "active_ticker_count": 1000,
        "task_types": ["price", "news", "earnings", "dividend"],
        "tasks": [],
        "summary": {
            "total_tasks": 4,
            "succeeded_tasks": 3,
            "total_tickers": 1000,
            "successful_tickers": 1000,
            "coverage_ratio": "1.0",
            "coverage_gates": [
                {
                    "name": "price_freshness",
                    "passed": True,
                    "observed_value": "1.0",
                    "required_value": "0.9",
                    "unit": "ratio",
                },
                {
                    "name": "news_freshness",
                    "passed": False,
                    "observed_value": "0.25",
                    "required_value": "1",
                    "unit": "ratio",
                    "message": "News chunks are incomplete.",
                },
            ],
        },
    }
    body = SimpleNamespace(
        read=lambda: phase1_pipeline.json.dumps(manifest_payload).encode("utf-8")
    )
    with (
        patch("src.analysis.phase1_pipeline.ARTIFACT_BUCKET", "artifact-bucket"),
        patch("src.analysis.phase1_pipeline.boto3.client") as client,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        quality = phase1_pipeline._with_collection_manifest_quality(
            {"coverage_status": "complete", "warnings": []},
            run_date,
        )

    assert quality["warnings"] == [
        "Publication is continuing with degraded optional data: News chunks are incomplete."
    ]


def test_run_phase1_pipeline_scores_only_eligible_tickers_when_coverage_is_partial():
    run_date = date(2026, 6, 17)
    stocks = [
        _decision_grade_stock(
            "NVDA", company_name="NVIDIA", latest_stock_data_date="2026-06-17"
        ),
        _decision_grade_stock(
            "STALE", company_name="Stale Co", latest_stock_data_date="2026-06-01"
        ),
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


def test_run_phase1_score_mode_scores_requested_batch_only():
    stocks = [
        {"ticker": "AAPL", "company_name": "Apple", "sector": "Technology"},
        {"ticker": "MSFT", "company_name": "Microsoft", "sector": "Technology"},
        {"ticker": "NVDA", "company_name": "NVIDIA", "sector": "Technology"},
    ]
    scores = [_candidate_score("NVDA", opportunity_score=60, negative_score=0)]

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline._eligible_context",
            return_value={"eligible_stocks": stocks, "freshness": {}},
        ),
        patch("src.analysis.phase1_pipeline.score_candidates", return_value=scores) as score,
        patch("src.analysis.phase1_pipeline.select_shortlist", return_value=scores),
        patch("src.analysis.phase1_pipeline.analyze_shortlist") as analyze_shortlist,
        patch("src.analysis.phase1_pipeline.publish_payload") as publish_payload,
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline({"mode": "score", "batch_index": 1, "batch_size": 2})

    assert result["statusCode"] == 200
    assert result["body"]["candidate_count"] == 1
    assert result["body"]["eligible_count"] == 3
    assert result["body"]["batch_start"] == 2
    assert result["body"]["batch_end"] == 3
    assert [stock["ticker"] for stock in score.call_args.args[0]] == ["NVDA"]
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


def test_run_phase1_retry_ai_analysis_dry_run_targets_fallback_and_missing():
    scores = [
        _candidate_score("AAPL", opportunity_score=80, negative_score=0),
        _candidate_score("MSFT", opportunity_score=70, negative_score=0),
        _candidate_score("NVDA", opportunity_score=60, negative_score=0),
    ]
    analyses = [
        {"ticker": "AAPL", "analysis_method": "fallback_heuristic"},
        {"ticker": "MSFT", "analysis_method": "ai"},
    ]

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_scores_for_date",
            return_value=scores,
        ),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_analysis_for_date",
            return_value=analyses,
        ),
        patch("src.analysis.phase1_pipeline.select_shortlist", return_value=scores),
        patch("src.analysis.phase1_pipeline.analyze_shortlist") as analyze,
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline(
            {
                "mode": "retry_ai_analysis",
                "run_date": "2026-06-17",
                "max_tickers": 1,
                "dry_run": True,
            }
        )

    assert result["statusCode"] == 200
    assert result["body"]["status"] == "dry_run"
    assert result["body"]["mode"] == "retry_ai_analysis"
    assert result["body"]["targeted_tickers"] == ["AAPL"]
    analyze.assert_not_called()


def test_run_phase1_retry_ai_analysis_reanalyzes_stored_scores():
    stocks = [
        {"ticker": "AAPL", "company_name": "Apple", "sector": "Technology"},
        {"ticker": "MSFT", "company_name": "Microsoft", "sector": "Technology"},
    ]
    scores = [
        _candidate_score("AAPL", opportunity_score=80, negative_score=0),
        _candidate_score("MSFT", opportunity_score=70, negative_score=0),
    ]
    analyses = [{"ticker": "AAPL", "analysis_method": "fallback_heuristic"}]
    retried = [{"ticker": "AAPL", "analysis_method": "ai"}]

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_scores_for_date",
            return_value=scores,
        ),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_analysis_for_date",
            return_value=analyses,
        ),
        patch("src.analysis.phase1_pipeline.select_shortlist", return_value=scores),
        patch("src.analysis.phase1_pipeline.store.active_stock_metadata", return_value=stocks),
        patch(
            "src.analysis.phase1_pipeline.analyze_shortlist",
            return_value=retried,
        ) as analyze,
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline(
            {"mode": "retry_ai_analysis", "run_date": "2026-06-17"}
        )

    assert result["statusCode"] == 200
    assert result["body"]["status"] == "success"
    assert result["body"]["analyzed_tickers"] == ["AAPL"]
    assert analyze.call_args.args[0] == scores


def test_run_phase1_retry_ai_review_reviews_error_results():
    analyses = [
        {
            "ticker": "AAPL",
            "analysis_method": "ai",
            "recommendation": "BUY",
            "confidence_score": 70,
            "publication_allowed": False,
            "signals": [],
            "ai_review": {"status": "error", "approved": False},
        },
        {
            "ticker": "MSFT",
            "analysis_method": "ai",
            "recommendation": "HOLD",
            "confidence_score": 50,
        },
    ]
    stocks = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Technology"}]
    reviewed = {
        **analyses[0],
        "publication_allowed": True,
        "ai_review": {"status": "approved", "approved": True, "model": "review"},
    }

    with (
        patch("src.analysis.phase1_pipeline.DatabasePool"),
        patch(
            "src.analysis.phase1_pipeline.store.candidate_analysis_for_date",
            return_value=analyses,
        ),
        patch("src.analysis.phase1_pipeline.store.active_stock_metadata", return_value=stocks),
        patch("src.analysis.phase1_pipeline._build_openai_client", return_value=object()),
        patch(
            "src.analysis.phase1_pipeline._review_candidate_analysis",
            return_value=reviewed,
        ) as review,
        patch("src.analysis.phase1_pipeline.store.put_candidate_analysis") as put_analysis,
        patch("src.analysis.phase1_pipeline._emit_metric"),
        patch.object(phase1_pipeline, "date", _fixed_date(date(2026, 6, 17))),
    ):
        result = run_phase1_pipeline(
            {"mode": "retry_ai_review", "run_date": "2026-06-17"}
        )

    assert result["statusCode"] == 200
    assert result["body"]["status"] == "success"
    assert result["body"]["reviewed_tickers"] == ["AAPL"]
    review.assert_called_once()
    assert review.call_args.args[2]["publication_allowed"] is True
    put_analysis.assert_called_once_with(reviewed)


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
