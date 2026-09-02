"""Tests for DynamoDB access patterns and summary records."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from src.db.connection import DynamoStore


class _TestableDynamoStore(DynamoStore):
    def __init__(self, table=None):
        self._table = table or MagicMock()

    @property
    def table(self):
        return self._table


def test_latest_prices_uses_stock_metadata_not_stock_data_scan():
    store = _TestableDynamoStore()
    store.list_stocks = MagicMock(
        return_value=[
            {"ticker": "AAPL", "latest_close_price": Decimal("195.12")},
            {"ticker": "MSFT"},
        ]
    )
    store._scan = MagicMock()

    prices = store.latest_prices()

    assert prices == {"AAPL": Decimal("195.12")}
    store.list_stocks.assert_called_once_with(is_active=True)
    store._scan.assert_not_called()


def test_put_market_signal_writes_ticker_date_type_item():
    table = MagicMock()
    store = _TestableDynamoStore(table)

    store.put_market_signal(
        {
            "ticker": "nvda",
            "signal_date": date(2026, 6, 17),
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
            "source": {"provider": "stock_collector"},
            "created_at": "2026-06-17T21:00:00",
        }
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "MARKETSIGNAL#NVDA"
    assert item["SK"] == "DATE#2026-06-17#price_move"
    assert item["GSI1PK"] == "MARKET_SIGNAL"
    assert item["GSI1SK"] == "2026-06-17#NVDA#price_move"
    assert item["price_change_percent"] == Decimal("6.00")


def test_market_signals_for_ticker_queries_date_range():
    store = _TestableDynamoStore()
    store._query = MagicMock(
        return_value=[
            {
                "PK": "MARKETSIGNAL#NVDA",
                "SK": "DATE#2026-06-17#price_move",
                "GSI1PK": "MARKET_SIGNAL",
                "GSI1SK": "2026-06-17#NVDA#price_move",
                "entity": "market_signal",
                "ticker": "NVDA",
                "signal_date": "2026-06-17",
                "signal_type": "price_move",
                "direction": "positive",
                "score": 36,
                "title": "Large daily price move",
                "summary": "NVDA moved 6.00% versus the prior close.",
            }
        ]
    )

    signals = store.market_signals_for_ticker(
        "NVDA", date(2026, 6, 14), date(2026, 6, 17)
    )

    assert signals == [
        {
            "ticker": "NVDA",
            "signal_date": "2026-06-17",
            "signal_type": "price_move",
            "direction": "positive",
            "score": 36,
            "title": "Large daily price move",
            "summary": "NVDA moved 6.00% versus the prior close.",
        }
    ]
    store._query.assert_called_once()


def test_last_collection_methods_use_system_status_records():
    store = _TestableDynamoStore()
    store._get_system_status_timestamp = MagicMock(return_value="2026-06-17T20:00:00")
    store._scan = MagicMock()

    assert store.last_stock_collection() == "2026-06-17T20:00:00"
    assert store.last_news_collection() == "2026-06-17T20:00:00"
    assert store.last_analysis() == "2026-06-17T20:00:00"
    assert store.last_publication() == "2026-06-17T20:00:00"

    store._get_system_status_timestamp.assert_any_call("STOCK_COLLECTION")
    store._get_system_status_timestamp.assert_any_call("NEWS_COLLECTION")
    store._get_system_status_timestamp.assert_any_call("ANALYSIS")
    store._get_system_status_timestamp.assert_any_call("PUBLICATION")
    store._scan.assert_not_called()


def test_seed_collection_manifest_tasks_writes_independent_task_rows():
    table = MagicMock()
    batch = table.batch_writer.return_value.__enter__.return_value
    store = _TestableDynamoStore(table)

    count = store.seed_collection_manifest_tasks(
        date(2026, 7, 31),
        [
            {
                "task_id": "price-0000-AAPL-MSFT",
                "task_type": "price",
                "status": "pending",
                "tickers": ["AAPL", "MSFT"],
                "created_at": "2026-07-31T21:05:00+00:00",
                "updated_at": "2026-07-31T21:05:00+00:00",
            }
        ],
    )

    assert count == 1
    item = batch.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "COLLECTION_MANIFEST#2026-07-31"
    assert item["SK"] == "TASK#price-0000-AAPL-MSFT"
    assert item["version"] == 0


def test_replace_collection_manifest_task_uses_optimistic_version():
    table = MagicMock()
    store = _TestableDynamoStore(table)

    replaced = store.replace_collection_manifest_task(
        date(2026, 7, 31),
        {
            "task_id": "price-0000-AAPL-MSFT",
            "task_type": "price",
            "status": "succeeded",
        },
        expected_version=4,
    )

    assert replaced is True
    kwargs = table.put_item.call_args.kwargs
    assert kwargs["Item"]["version"] == 5
    assert kwargs["Item"]["SK"] == "TASK#price-0000-AAPL-MSFT"
    assert kwargs["ConditionExpression"] is not None


def test_replace_collection_manifest_task_reports_version_conflict():
    table = MagicMock()
    table.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "version changed",
            }
        },
        "PutItem",
    )
    store = _TestableDynamoStore(table)

    replaced = store.replace_collection_manifest_task(
        date(2026, 7, 31),
        {"task_id": "price-0000-AAPL-MSFT", "status": "running"},
        expected_version=1,
    )

    assert replaced is False


def test_suppressed_publication_record_does_not_update_success_status():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store.put_publication_record(
        date(2026, 6, 17),
        {
            "generated_at": "2026-06-17T22:00:00",
            "publication_status": "suppressed",
            "suppression_reason": "collection_coverage_gates_failed",
            "top_picks": [],
            "sell_alerts": [],
            "candidate_count": 0,
            "analyzed_count": 0,
            "data_quality": {"coverage_status": "suppressed"},
        },
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["publication_status"] == "suppressed"
    assert item["suppression_reason"] == "collection_coverage_gates_failed"
    store._put_system_status.assert_not_called()


def test_news_for_ticker_queries_ticker_date_fanout():
    store = _TestableDynamoStore()
    store._query = MagicMock(
        return_value=[
            {
                "PK": "NEWS_TICKER#AAPL",
                "SK": "PUBLISHED#2026-06-17T10:00:00Z#abc",
                "GSI1PK": "NEWS",
                "GSI1SK": "2026-06-17T10:00:00Z",
                "entity": "news_ticker",
                "ticker": "AAPL",
                "title": "Apple news",
                "published_at": "2026-06-17T10:00:00Z",
            }
        ]
    )
    store._scan = MagicMock()

    rows = store.news_for_ticker("AAPL", date(2026, 6, 10), date(2026, 6, 17))

    assert rows == [
        {
            "ticker": "AAPL",
            "title": "Apple news",
            "published_at": "2026-06-17T10:00:00Z",
        }
    ]
    store._query.assert_called_once()
    store._scan.assert_not_called()


def test_candidate_date_reads_query_existing_gsi():
    store = _TestableDynamoStore()
    store._query = MagicMock(return_value=[])
    store._scan = MagicMock()

    assert store.candidate_scores_for_date(date(2026, 6, 17)) == []
    assert store.candidate_analysis_for_date(date(2026, 6, 17)) == []

    assert store._query.call_count == 2
    store._scan.assert_not_called()


def test_put_candidate_score_converts_nested_signal_floats_to_decimal():
    table = MagicMock()
    store = _TestableDynamoStore(table)

    store.put_candidate_score(
        {
            "ticker": "NVDA",
            "score_date": date(2026, 6, 18),
            "opportunity_score": 42,
            "negative_score": 3,
            "signals": [
                {
                    "signal_type": "sector_relative",
                    "score": 12,
                    "source": {
                        "provider": "yfinance",
                        "stock_change_percent": 1.25,
                        "sector_change_percent": -0.5,
                    },
                }
            ],
        }
    )

    item = table.put_item.call_args.kwargs["Item"]
    source = item["signals"][0]["source"]
    assert source["stock_change_percent"] == Decimal("1.25")
    assert source["sector_change_percent"] == Decimal("-0.5")


def test_existing_news_hashes_deduplicates_batch_get_keys():
    table = MagicMock()
    table.meta.client.batch_get_item.return_value = {
        "Responses": {
            "stockara": [
                {"title_source_hash": "hash-a"},
            ]
        }
    }
    store = _TestableDynamoStore(table)

    result = store.existing_news_hashes(["hash-a", "hash-a", "hash-b", ""])

    assert result == {"hash-a"}
    keys = table.meta.client.batch_get_item.call_args.kwargs["RequestItems"]["stockara"]["Keys"]
    assert keys == [
        {"PK": "NEWS#hash-a", "SK": "META"},
        {"PK": "NEWS#hash-b", "SK": "META"},
    ]


def test_put_candidate_analysis_converts_nested_signal_floats_to_decimal():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store.put_candidate_analysis(
        {
            "ticker": "NVDA",
            "analysis_date": date(2026, 6, 18),
            "analysis_method": "fallback_heuristic",
            "recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 40,
            "signals": [
                {
                    "signal_type": "sector_relative",
                    "source": {"relative_performance": 2.75},
                }
            ],
            "created_at": "2026-06-18T07:00:00",
        }
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["signals"][0]["source"]["relative_performance"] == Decimal("2.75")


def test_put_earnings_event_writes_date_indexed_item_and_status():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store.put_earnings_event(
        {
            "ticker": "nvda",
            "company_name": "NVIDIA",
            "event_date": date(2026, 7, 20),
            "eps_estimate": Decimal("2.15"),
            "reported_eps": Decimal("2.40"),
            "surprise_percent": Decimal("5.20"),
            "post_earnings_price_move_percent": Decimal("7.50"),
            "is_upcoming": False,
            "provider": "yfinance",
            "collected_at": "2026-06-17T20:00:00",
        }
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "EARNINGS#NVDA"
    assert item["SK"] == "DATE#2026-07-20"
    assert item["GSI1PK"] == "EARNINGS"
    assert item["GSI1SK"] == "2026-07-20#NVDA"
    assert item["eps_estimate"] == Decimal("2.15")
    store._put_system_status.assert_called_once_with(
        "EARNINGS_COLLECTION", "2026-06-17T20:00:00"
    )


def test_put_earnings_event_persists_reconciliation_provenance():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store.put_earnings_event(
        {
            "ticker": "AAPL",
            "event_date": date(2026, 7, 30),
            "is_upcoming": True,
            "revenue_estimate": Decimal("90000000000"),
            "reported_revenue": Decimal("92000000000"),
            "revenue_surprise_percent": Decimal("2.22"),
            "fiscal_period_end": date(2026, 6, 30),
            "fiscal_quarter": "2026-Q2",
            "canonical_event_id": "AAPL:2026-07-30",
            "provider_observation_id": "finnhub:AAPL:2026-07-30:2026-Q3",
            "reconciliation_status": "confirmed",
            "date_confidence": "high",
            "candidate_dates": [date(2026, 7, 30)],
            "observation_ids": ["finnhub:1", "alpha-vantage:1"],
            "confirmation_status": "candidate_supported",
            "confirmation_providers": ["yfinance"],
            "guidance_evidence": [
                {
                    "metric": "revenue",
                    "direction": "raised",
                    "value_low": Decimal("95000000000"),
                    "source_url": "https://example.com/release",
                }
            ],
            "estimate_revisions": [
                {
                    "metric": "eps",
                    "previous_value": Decimal("1.40"),
                    "current_value": Decimal("1.45"),
                    "source_url": "https://example.com/consensus",
                }
            ],
            "source_urls": ["https://example.com/release"],
            "observed_at": "2026-07-01T12:00:00+00:00",
        }
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["revenue_estimate"] == Decimal("90000000000")
    assert item["reported_revenue"] == Decimal("92000000000")
    assert item["revenue_surprise_percent"] == Decimal("2.22")
    assert item["fiscal_period_end"] == "2026-06-30"
    assert item["candidate_dates"] == ["2026-07-30"]
    assert item["observation_ids"] == ["finnhub:1", "alpha-vantage:1"]
    assert item["confirmation_status"] == "candidate_supported"
    assert item["confirmation_providers"] == ["yfinance"]
    assert item["guidance_evidence"][0]["value_low"] == Decimal("95000000000")
    assert item["estimate_revisions"][0]["current_value"] == Decimal("1.45")
    assert item["source_urls"] == ["https://example.com/release"]
    assert item["observed_at"] == "2026-07-01T12:00:00+00:00"


def test_upcoming_earnings_queries_gsi_and_filters_past_events():
    store = _TestableDynamoStore()
    store._query = MagicMock(
        return_value=[
            {
                "PK": "EARNINGS#NVDA",
                "SK": "DATE#2026-07-20",
                "GSI1PK": "EARNINGS",
                "GSI1SK": "2026-07-20#NVDA",
                "entity": "earnings_event",
                "ticker": "NVDA",
                "event_date": "2026-07-20",
                "is_upcoming": True,
            },
            {
                "PK": "EARNINGS#OLD",
                "SK": "DATE#2026-07-21",
                "GSI1PK": "EARNINGS",
                "GSI1SK": "2026-07-21#OLD",
                "entity": "earnings_event",
                "ticker": "OLD",
                "event_date": "2026-07-21",
                "is_upcoming": False,
            },
        ]
    )

    events = store.upcoming_earnings(date(2026, 6, 17), date(2026, 8, 1))

    assert events == [
        {"ticker": "NVDA", "event_date": "2026-07-20", "is_upcoming": True}
    ]
    store._query.assert_called_once()


def test_earnings_events_queries_gsi_without_filtering_historical_rows():
    store = _TestableDynamoStore()
    store._query = MagicMock(
        return_value=[
            {
                "PK": "EARNINGS#MSFT",
                "SK": "DATE#2025-07-20",
                "GSI1PK": "EARNINGS",
                "GSI1SK": "2025-07-20#MSFT",
                "entity": "earnings_event",
                "ticker": "MSFT",
                "event_date": "2025-07-20",
                "is_upcoming": False,
            },
            {
                "PK": "EARNINGS#AAPL",
                "SK": "DATE#2025-04-20",
                "GSI1PK": "EARNINGS",
                "GSI1SK": "2025-04-20#AAPL",
                "entity": "earnings_event",
                "ticker": "AAPL",
                "event_date": "2025-04-20",
                "is_upcoming": False,
            },
        ]
    )

    events = store.earnings_events(date(2024, 1, 1), date(2026, 9, 1))

    assert [event["ticker"] for event in events] == ["AAPL", "MSFT"]
    store._query.assert_called_once()


def test_upcoming_earnings_is_uncapped_by_default():
    store = _TestableDynamoStore()
    store._query = MagicMock(
        return_value=[
            {
                "PK": f"EARNINGS#T{index:03d}",
                "SK": "DATE#2026-07-20",
                "GSI1PK": "EARNINGS",
                "GSI1SK": f"2026-07-20#T{index:03d}",
                "entity": "earnings_event",
                "ticker": f"T{index:03d}",
                "event_date": "2026-07-20",
                "is_upcoming": True,
            }
            for index in range(40)
        ]
    )

    events = store.upcoming_earnings(date(2026, 6, 17), date(2026, 8, 1))

    assert len(events) == 40


def test_put_dividend_event_writes_date_indexed_item_and_status():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store.put_dividend_event(
        {
            "ticker": "aapl",
            "company_name": "Apple",
            "ex_dividend_date": date(2026, 8, 15),
            "pay_date": "2026-09-01",
            "dividend_amount": Decimal("0.30"),
            "dividend_yield": Decimal("1.50"),
            "post_ex_dividend_price_move_percent": Decimal("-1.00"),
            "is_upcoming": False,
            "provider": "yfinance",
            "collected_at": "2026-06-17T20:15:00",
        }
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "DIVIDEND#AAPL"
    assert item["SK"] == "DATE#2026-08-15"
    assert item["GSI1PK"] == "DIVIDEND"
    assert item["GSI1SK"] == "2026-08-15#AAPL"
    assert item["dividend_amount"] == Decimal("0.30")
    store._put_system_status.assert_called_once_with(
        "DIVIDEND_COLLECTION", "2026-06-17T20:15:00"
    )


def test_upcoming_dividends_queries_gsi_and_filters_past_events():
    store = _TestableDynamoStore()
    store._query = MagicMock(
        return_value=[
            {
                "PK": "DIVIDEND#AAPL",
                "SK": "DATE#2026-08-15",
                "GSI1PK": "DIVIDEND",
                "GSI1SK": "2026-08-15#AAPL",
                "entity": "dividend_event",
                "ticker": "AAPL",
                "ex_dividend_date": "2026-08-15",
                "is_upcoming": True,
            },
            {
                "PK": "DIVIDEND#OLD",
                "SK": "DATE#2026-08-16",
                "GSI1PK": "DIVIDEND",
                "GSI1SK": "2026-08-16#OLD",
                "entity": "dividend_event",
                "ticker": "OLD",
                "ex_dividend_date": "2026-08-16",
                "is_upcoming": False,
            },
        ]
    )

    events = store.upcoming_dividends(date(2026, 6, 17), date(2026, 9, 1))

    assert events == [
        {"ticker": "AAPL", "ex_dividend_date": "2026-08-15", "is_upcoming": True}
    ]
    store._query.assert_called_once()


def test_put_news_summary_writes_ticker_fanout_and_status():
    table = MagicMock()
    batch = MagicMock()
    table.batch_writer.return_value.__enter__.return_value = batch
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    stored = store.put_news_summary(
        {
            "title": "Apple beats estimates",
            "source": "Reuters",
            "published_at": "2026-06-17T10:00:00Z",
        },
        {
            "summary": "Apple beat estimates.",
            "tickers": ["AAPL", "MSFT"],
            "ticker_classifications": [
                {"ticker": "AAPL", "confidence": 0.75, "sources": ["ai"]}
            ],
            "classification_confidence": 0.75,
        },
        "hash123",
    )

    assert stored is True
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["ticker_classifications"] == [
        {"ticker": "AAPL", "confidence": Decimal("0.75"), "sources": ["ai"]}
    ]
    assert item["classification_confidence"] == Decimal("0.75")
    assert batch.put_item.call_count == 2
    store._put_system_status.assert_called_once()


def test_update_news_url_if_missing_updates_canonical_and_ticker_fanout():
    table = MagicMock()
    batch = MagicMock()
    table.batch_writer.return_value.__enter__.return_value = batch
    table.update_item.return_value = {
        "Attributes": {
            "PK": "NEWS#hash123",
            "SK": "META",
            "entity": "news",
            "title": "Apple beats estimates",
            "source": "Reuters",
            "published_at": "2026-06-17T10:00:00Z",
            "tickers": ["AAPL", "MSFT"],
            "summary": "Apple beat estimates.",
            "title_source_hash": "hash123",
            "url": "https://example.com/apple",
        }
    }
    store = _TestableDynamoStore(table)

    updated = store.update_news_url_if_missing(
        "hash123",
        "https://example.com/apple",
    )

    assert updated is True
    table.update_item.assert_called_once()
    assert table.update_item.call_args.kwargs["ExpressionAttributeValues"] == {
        ":url": "https://example.com/apple"
    }
    assert batch.put_item.call_count == 2
    for call in batch.put_item.call_args_list:
        assert call.kwargs["Item"]["url"] == "https://example.com/apple"


def test_update_news_url_if_missing_leaves_existing_url_unchanged():
    table = MagicMock()
    table.update_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "URL already exists",
            }
        },
        "UpdateItem",
    )
    store = _TestableDynamoStore(table)
    store._put_news_ticker_items = MagicMock()

    updated = store.update_news_url_if_missing(
        "hash123",
        "https://example.com/replacement",
    )

    assert updated is False
    store._put_news_ticker_items.assert_not_called()


def test_put_stock_data_persists_provider_provenance_and_updates_summary():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._mark_stock_data_collected = MagicMock()

    stored = store.put_stock_data(
        {
            "ticker": "AAPL",
            "trading_date": date(2026, 6, 17),
            "open_price": Decimal("195.00"),
            "high_price": Decimal("199.00"),
            "low_price": Decimal("194.00"),
            "close_price": Decimal("198.00"),
            "adjusted_close_price": Decimal("197.50"),
            "volume": 123456,
            "data_provider": "yfinance",
            "provider_symbol": "AAPL",
            "provider_endpoint": "yf.download",
            "provider_priority": "primary",
            "price_adjustment": "unadjusted",
            "has_adjusted_close": True,
            "corporate_action_adjusted": False,
            "adjustment_context": "raw_ohlcv_with_adjusted_close",
            "split_dividend_adjustment": "adjusted_close_available",
            "exchange": None,
            "currency": "USD",
            "fetch_period": "5y",
            "fetch_window_start": "2021-06-17",
            "fetch_window_end": "2026-06-17",
            "collected_at": "2026-06-17T21:00:00",
        }
    )

    assert stored is True
    item = table.put_item.call_args.kwargs["Item"]
    assert item["data_provider"] == "yfinance"
    assert item["provider_priority"] == "primary"
    assert item["price_adjustment"] == "unadjusted"
    assert item["adjusted_close_price"] == Decimal("197.50")
    assert item["adjustment_context"] == "raw_ohlcv_with_adjusted_close"
    assert item["split_dividend_adjustment"] == "adjusted_close_available"
    assert item["fetch_window_start"] == "2021-06-17"
    assert item["fetch_window_end"] == "2026-06-17"
    store._mark_stock_data_collected.assert_called_once_with(
        "AAPL",
        "2026-06-17",
        "2026-06-17T21:00:00",
        Decimal("198.00"),
        "yfinance",
        "unadjusted",
    )


def test_put_stock_data_backfill_batch_skips_existing_dates_and_updates_summary_once():
    table = MagicMock()
    batch = MagicMock()
    table.batch_writer.return_value.__enter__.return_value = batch
    store = _TestableDynamoStore(table)
    store.get_stock_data = MagicMock(
        return_value=[{"ticker": "AAPL", "trading_date": "2026-06-17"}]
    )
    store._mark_stock_data_rows_inserted = MagicMock()
    store._mark_stock_data_collected = MagicMock()

    result = store.put_stock_data_backfill_batch(
        [
            {
                "ticker": "AAPL",
                "trading_date": date(2026, 6, 17),
                "open_price": Decimal("195.00"),
                "high_price": Decimal("199.00"),
                "low_price": Decimal("194.00"),
                "close_price": Decimal("198.00"),
                "volume": 123456,
                "data_provider": "operator_backfill",
            },
            {
                "ticker": "AAPL",
                "trading_date": date(2026, 6, 18),
                "open_price": Decimal("198.00"),
                "high_price": Decimal("202.00"),
                "low_price": Decimal("197.00"),
                "close_price": Decimal("201.00"),
                "volume": 234567,
                "data_provider": "operator_backfill",
                "collected_at": "2026-06-19T12:00:00",
            },
        ]
    )

    assert result == {
        "inserted_records": 1,
        "duplicate_records": 1,
        "failed_records": 0,
    }
    batch.put_item.assert_called_once()
    item = batch.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "STOCKDATA#AAPL"
    assert item["SK"] == "DATE#2026-06-18"
    assert item["data_provider"] == "operator_backfill"
    store._mark_stock_data_rows_inserted.assert_called_once_with(
        "AAPL", "2026-06-18", 1
    )
    store._mark_stock_data_collected.assert_called_once_with(
        "AAPL",
        "2026-06-18",
        "2026-06-19T12:00:00",
        Decimal("201.00"),
        "operator_backfill",
        None,
    )


def test_mark_stock_collection_failed_persists_retry_state():
    table = MagicMock()
    store = _TestableDynamoStore(table)

    store.mark_stock_collection_failed(
        "AAPL",
        reason="no_data",
        health="provider_unsupported",
        retry_after_hours=6,
        failed_at="2026-06-17T21:00:00",
    )

    call = table.update_item.call_args.kwargs
    assert call["Key"] == {"PK": "STOCK#AAPL", "SK": "META"}
    assert "latest_stock_collection_failed_at" in call["UpdateExpression"]
    assert call["ExpressionAttributeValues"][":failed_at"] == "2026-06-17T21:00:00"
    assert call["ExpressionAttributeValues"][":reason"] == "no_data"
    assert call["ExpressionAttributeValues"][":health"] == "provider_unsupported"
    assert call["ExpressionAttributeValues"][":retry_after"] == "2026-06-18T03:00:00"


def test_mark_stock_data_collected_clears_failure_state():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store._mark_stock_data_collected(
        "AAPL",
        "2026-06-17",
        "2026-06-17T21:00:00",
        Decimal("198.00"),
        "yfinance",
        "unadjusted",
    )

    update_expression = table.update_item.call_args.kwargs["UpdateExpression"]
    assert "REMOVE latest_stock_collection_failed_at" in update_expression
    assert "latest_stock_collection_failure_reason" in update_expression
    assert "latest_stock_collection_health" in update_expression


def test_put_collection_summary_writes_history_and_latest_status():
    table = MagicMock()
    store = _TestableDynamoStore(table)
    store._put_system_status = MagicMock()

    store.put_collection_summary(
        "STOCK_COLLECTION",
        {
            "status": "partial",
            "selected_ticker_count": 25,
            "records_collected": 72,
            "duplicate_record_count": 3,
            "failed_ticker_count": 2,
            "completeness_ratio": 0.92,
            "nested": {"threshold": 0.9},
        },
    )

    item = table.put_item.call_args.kwargs["Item"]
    assert item["PK"] == "COLLECTIONSUMMARY#STOCK_COLLECTION"
    assert item["SK"].startswith("RUN#")
    assert item["GSI1PK"] == "COLLECTION_SUMMARY"
    assert item["summary"]["duplicate_record_count"] == 3
    assert item["summary"]["completeness_ratio"] == Decimal("0.92")
    assert item["summary"]["nested"]["threshold"] == Decimal("0.9")
    store._put_system_status.assert_called_once()
