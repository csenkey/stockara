"""Tests for GitHub Actions Lambda response quality validation."""

from scripts.validate_lambda_response import _validate


def test_stock_validation_fails_on_zero_successful_tickers():
    failures = _validate(
        "stock",
        {
            "collection_summary": {
                "status": "failed",
                "selected_ticker_count": 5,
                "successful_ticker_count": 0,
                "completeness_ratio": 0.0,
                "minimum_completeness_ratio": 0.9,
            }
        },
        None,
    )

    assert any("status=failed" in failure for failure in failures)
    assert any("zero successful tickers" in failure for failure in failures)
    assert any("below required" in failure for failure in failures)


def test_stock_validation_passes_above_completeness_threshold():
    failures = _validate(
        "stock",
        {
            "collection_summary": {
                "status": "partial",
                "selected_ticker_count": 25,
                "successful_ticker_count": 24,
                "completeness_ratio": 0.96,
                "minimum_completeness_ratio": 0.9,
            }
        },
        None,
    )

    assert failures == []


def test_news_validation_fails_on_partial_source_coverage():
    failures = _validate(
        "news",
        {
            "status": "success",
            "collection_summary": {
                "status": "partial",
                "sources_available": 1,
                "sources_total": 2,
                "articles_fetched": 10,
                "completeness_ratio": 0.5,
            },
        },
        None,
    )

    assert any("below required" in failure for failure in failures)


def test_calendar_validation_fails_on_zero_events_for_selected_tickers():
    failures = _validate(
        "earnings",
        {
            "events_collected": 0,
            "selected_ticker_count": 50,
            "failed_tickers": [],
        },
        None,
    )

    assert failures == ["Earnings collector produced zero events."]


def test_calendar_validation_passes_with_events():
    failures = _validate(
        "dividend",
        {
            "events_collected": 3,
            "selected_ticker_count": 50,
            "failed_tickers": ["NOPE"],
        },
        None,
    )

    assert failures == []
