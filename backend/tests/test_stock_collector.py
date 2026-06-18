"""Unit tests for stock data collector.

Tests batch fetching logic, retry behavior, fallback to Alpha Vantage,
duplicate detection, and malformed data handling.

Requirements: 1.3, 1.6, 1.7
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.src.collectors.stock_collector import (
    handler,
    _fetch_yfinance_with_retry,
    _process_batch,
    _select_due_stocks,
    _group_stocks_by_period,
    _build_collection_summary,
    _emit_collection_summary_metrics,
    _record_failed_ticker_state,
    _movement_signals_from_rows,
    _extract_ticker_data,
    _should_stop_for_time,
    BatchResult,
    ExtractResult,
    StoreResult,
    _validate_record,
    _fetch_window,
    _store_records,
    _alpha_vantage_fallback,
    _fetch_alpha_vantage_with_retry,
    _fetch_nasdaq_with_retry,
    _fetch_stooq_with_retry,
    _nasdaq_fallback_with_details,
    _parse_nasdaq_response,
    _parse_stooq_csv,
    _stooq_fallback_with_details,
    _stooq_symbol,
)


class _RemainingTimeContext:
    def __init__(self, remaining_values: list[int]):
        self.remaining_values = remaining_values
        self.index = 0

    def get_remaining_time_in_millis(self) -> int:
        if self.index >= len(self.remaining_values):
            return self.remaining_values[-1]
        value = self.remaining_values[self.index]
        self.index += 1
        return value


# --- Fixtures ---

@pytest.fixture
def mock_db_pool():
    """Mock DatabasePool and its connection."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("backend.src.collectors.stock_collector.DatabasePool") as mock_pool:
        mock_pool._pool = MagicMock()
        mock_pool._pool.getconn.return_value = mock_conn
        yield mock_pool, mock_conn, mock_cursor


@pytest.fixture
def sample_yfinance_dataframe():
    """Create a sample multi-ticker yfinance DataFrame."""
    idx = pd.DatetimeIndex([datetime(2025, 1, 15)])
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Open"), ("AAPL", "High"), ("AAPL", "Low"),
        ("AAPL", "Close"), ("AAPL", "Adj Close"), ("AAPL", "Volume"),
        ("MSFT", "Open"), ("MSFT", "High"), ("MSFT", "Low"),
        ("MSFT", "Close"), ("MSFT", "Adj Close"), ("MSFT", "Volume"),
    ])
    data = [[150.0, 155.0, 149.0, 153.0, 152.5, 1000000,
             400.0, 410.0, 395.0, 405.0, 404.5, 500000]]
    return pd.DataFrame(data, index=idx, columns=columns)


@pytest.fixture
def sample_single_ticker_dataframe():
    """Create a sample single-ticker yfinance DataFrame."""
    idx = pd.DatetimeIndex([datetime(2025, 1, 15)])
    data = {"Open": [150.0], "High": [155.0], "Low": [149.0],
            "Close": [153.0], "Adj Close": [152.5], "Volume": [1000000]}
    return pd.DataFrame(data, index=idx)


# --- Tests for _validate_record ---

class TestValidateRecord:
    """Tests for malformed data detection (Requirement 1.6)."""

    def test_valid_record(self):
        """Valid OHLCV record returns dict."""
        row = pd.Series({"Open": 150.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", datetime(2025, 1, 15), row)
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["open_price"] == Decimal("150.0")
        assert result["volume"] == 1000000
        assert result["data_provider"] == "yfinance"
        assert result["provider_priority"] == "primary"
        assert result["price_adjustment"] == "unadjusted"
        assert result["corporate_action_adjusted"] is False
        assert result["fetch_period"] == "1d"

    def test_valid_record_includes_adjusted_close_when_available(self):
        """Yfinance records preserve adjusted close for analysis."""
        row = pd.Series({"Open": 150.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Adj Close": 152.5, "Volume": 1000000})
        result = _validate_record(
            "AAPL",
            datetime(2025, 1, 15),
            row,
            period="5y",
            stock_metadata={"exchange": "NASDAQ", "currency": "USD"},
        )
        assert result is not None
        assert result["adjusted_close_price"] == Decimal("152.5")
        assert result["has_adjusted_close"] is True
        assert result["fetch_period"] == "5y"
        assert result["exchange"] == "NASDAQ"
        assert result["currency"] == "USD"
        assert result["adjustment_context"] == "raw_ohlcv_with_adjusted_close"
        assert result["split_dividend_adjustment"] == "adjusted_close_available"
        assert result["fetch_window_start"] is not None
        assert result["fetch_window_end"] is not None

    def test_missing_open_price(self):
        """Record with None open price is discarded."""
        row = pd.Series({"Open": None, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", datetime(2025, 1, 15), row)
        assert result is None

    def test_negative_price(self):
        """Record with negative price is discarded."""
        row = pd.Series({"Open": -10.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", datetime(2025, 1, 15), row)
        assert result is None

    def test_zero_price(self):
        """Record with zero price is discarded."""
        row = pd.Series({"Open": 0.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", datetime(2025, 1, 15), row)
        assert result is None

    def test_nan_price(self):
        """Record with NaN price is discarded."""
        row = pd.Series({"Open": float("nan"), "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", datetime(2025, 1, 15), row)
        assert result is None

    def test_negative_volume(self):
        """Record with negative volume is discarded."""
        row = pd.Series({"Open": 150.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": -100})
        result = _validate_record("AAPL", datetime(2025, 1, 15), row)
        assert result is None

    def test_date_object_as_trading_date(self):
        """Handles date object directly."""
        row = pd.Series({"Open": 150.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", date(2025, 1, 15), row)
        assert result is not None
        assert result["trading_date"] == date(2025, 1, 15)

    def test_string_date(self):
        """Handles string date parsing."""
        row = pd.Series({"Open": 150.0, "High": 155.0, "Low": 149.0,
                         "Close": 153.0, "Volume": 1000000})
        result = _validate_record("AAPL", "2025-01-15", row)
        assert result is not None
        assert result["trading_date"] == date(2025, 1, 15)


# --- Tests for _fetch_yfinance_with_retry ---

class TestFetchYfinanceWithRetry:
    """Tests for retry behavior with exponential backoff (Requirement 1.3)."""

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.yf.download")
    def test_success_on_first_attempt(self, mock_download, mock_sleep):
        """Returns data on successful first attempt."""
        mock_df = pd.DataFrame({"Open": [150.0]}, index=[datetime(2025, 1, 15)])
        mock_download.return_value = mock_df

        result = _fetch_yfinance_with_retry(["AAPL"])
        assert result is not None
        mock_sleep.assert_not_called()

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.yf.download")
    def test_success_on_second_attempt(self, mock_download, mock_sleep):
        """Retries and succeeds on second attempt."""
        mock_df = pd.DataFrame({"Open": [150.0]}, index=[datetime(2025, 1, 15)])
        mock_download.side_effect = [Exception("timeout"), mock_df]

        result = _fetch_yfinance_with_retry(["AAPL"])
        assert result is not None
        mock_sleep.assert_called_once_with(2)  # Initial backoff

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.yf.download")
    def test_all_retries_exhausted(self, mock_download, mock_sleep):
        """Returns None after all 3 retries fail."""
        mock_download.side_effect = Exception("network error")

        result = _fetch_yfinance_with_retry(["AAPL"])
        assert result is None
        # Should sleep twice (between attempts 1-2, 2-3)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2)   # First backoff
        mock_sleep.assert_any_call(4)   # Second backoff (doubled)

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.yf.download")
    def test_empty_dataframe_triggers_retry(self, mock_download, mock_sleep):
        """Empty DataFrame triggers retry."""
        empty_df = pd.DataFrame()
        valid_df = pd.DataFrame({"Open": [150.0]}, index=[datetime(2025, 1, 15)])
        mock_download.side_effect = [empty_df, valid_df]

        result = _fetch_yfinance_with_retry(["AAPL"])
        assert result is not None
        mock_sleep.assert_called_once_with(2)


class TestProviderProvenance:
    """Tests market data provenance helpers."""

    def test_fetch_window_for_year_period(self):
        window = _fetch_window("5y", today=date(2026, 6, 17))

        assert window == {
            "fetch_window_start": "2021-06-17",
            "fetch_window_end": "2026-06-17",
        }

    def test_fetch_window_for_alpha_vantage_compact_output(self):
        window = _fetch_window("compact", today=date(2026, 6, 17))

        assert window == {
            "fetch_window_start": "2026-03-09",
            "fetch_window_end": "2026-06-17",
        }


# --- Tests for _extract_ticker_data ---

class TestExtractTickerData:
    """Tests for data extraction from yfinance DataFrame."""

    def test_extract_multi_ticker(self, sample_yfinance_dataframe):
        """Extracts data for a specific ticker from multi-ticker response."""
        records = _extract_ticker_data(sample_yfinance_dataframe, "AAPL")
        assert records is not None
        assert len(records) == 1
        assert records[0]["ticker"] == "AAPL"
        assert records[0]["close_price"] == Decimal("153.0")
        assert records[0]["adjusted_close_price"] == Decimal("152.5")
        assert records[0]["data_provider"] == "yfinance"

    def test_extract_single_ticker(self, sample_single_ticker_dataframe):
        """Extracts data from single-ticker response."""
        records = _extract_ticker_data(sample_single_ticker_dataframe, "AAPL")
        assert records is not None
        assert len(records) == 1

    def test_ticker_not_in_response(self, sample_yfinance_dataframe):
        """Returns None when ticker is not in the DataFrame."""
        result = _extract_ticker_data(sample_yfinance_dataframe, "GOOG")
        assert result is None

    def test_all_records_invalid(self):
        """Returns None when all extracted records are invalid."""
        idx = pd.DatetimeIndex([datetime(2025, 1, 15)])
        columns = pd.MultiIndex.from_tuples([
            ("BAD", "Open"), ("BAD", "High"), ("BAD", "Low"),
            ("BAD", "Close"), ("BAD", "Volume"),
        ])
        data = [[-1.0, -1.0, -1.0, -1.0, -100]]
        df = pd.DataFrame(data, index=idx, columns=columns)
        result = _extract_ticker_data(df, "BAD")
        assert result is None


# --- Tests for _process_batch ---

class TestProcessBatch:
    """Tests for batch processing logic."""

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._extract_ticker_data_result")
    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_successful_batch(self, mock_fetch, mock_extract, mock_store):
        """All tickers in batch processed successfully."""
        mock_fetch.return_value = MagicMock()
        mock_extract.return_value = ExtractResult(records=[{"ticker": "AAPL"}])
        mock_store.return_value = StoreResult(inserted_records=1)

        result = _process_batch(["AAPL", "MSFT"])
        assert result.records_inserted == 2
        assert result.failed_tickers == []
        assert result.collected_tickers == {"AAPL", "MSFT"}

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._extract_ticker_data_result")
    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_partial_failure(self, mock_fetch, mock_extract, mock_store):
        """Some tickers fail extraction, returned in failed list."""
        mock_fetch.return_value = MagicMock()
        mock_extract.side_effect = [
            ExtractResult(records=[{"ticker": "AAPL"}]),
            ExtractResult(failure_reason="malformed"),
        ]
        mock_store.return_value = StoreResult(inserted_records=1)

        result = _process_batch(["AAPL", "MSFT"])
        assert result.records_inserted == 1
        assert result.failed_tickers == ["MSFT"]
        assert result.malformed_tickers == ["MSFT"]
        assert result.collected_tickers == {"AAPL"}

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._extract_ticker_data_result")
    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_duplicate_records_count_as_ticker_success(
        self, mock_fetch, mock_extract, mock_store
    ):
        """Duplicate rows still prove the ticker had provider coverage."""
        mock_fetch.return_value = MagicMock()
        mock_extract.return_value = ExtractResult(records=[{"ticker": "AAPL"}])
        mock_store.return_value = StoreResult(duplicate_records=1)

        result = _process_batch(["AAPL"])

        assert result.records_inserted == 0
        assert result.duplicate_records == 1
        assert result.failed_tickers == []
        assert result.collected_tickers == {"AAPL"}

    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_entire_batch_fails(self, mock_fetch):
        """When yfinance returns None, all tickers are failed."""
        mock_fetch.return_value = None

        result = _process_batch(["AAPL", "MSFT", "GOOG"])
        assert result.records_inserted == 0
        assert result.failed_tickers == ["AAPL", "MSFT", "GOOG"]
        assert result.no_data_tickers == ["AAPL", "MSFT", "GOOG"]
        assert result.collected_tickers == set()


class TestMovementSignals:
    """Tests price/volume movement signal generation."""

    def test_generates_price_and_volume_signals_from_latest_rows(self):
        rows = [
            {
                "ticker": "NVDA",
                "trading_date": date(2026, 6, 16),
                "close_price": Decimal("100"),
                "volume": 100,
            },
            {
                "ticker": "NVDA",
                "trading_date": date(2026, 6, 17),
                "close_price": Decimal("106"),
                "volume": 220,
            },
        ]

        signals = _movement_signals_from_rows("NVDA", rows)

        assert {signal["signal_type"] for signal in signals} == {
            "price_move",
            "volume_move",
        }
        assert signals[0]["signal_date"] == date(2026, 6, 17)
        assert signals[0]["price_change_percent"] == Decimal("6.00")
        assert signals[1]["volume_ratio"] == Decimal("2.2")


class TestDueStockSelection:
    """Tests bounded, stale-first collector selection."""

    def test_select_due_stocks_prioritizes_never_collected_then_oldest(self):
        stocks = [
            {"ticker": "MSFT", "latest_stock_data_date": "2026-06-15"},
            {"ticker": "AAPL"},
            {"ticker": "NVDA", "latest_stock_data_date": "2026-06-14"},
        ]

        selected = _select_due_stocks(stocks, {"max_tickers": 2})

        assert [stock["ticker"] for stock in selected] == ["AAPL", "NVDA"]

    def test_select_due_stocks_honors_explicit_tickers(self):
        stocks = [
            {"ticker": "MSFT", "latest_stock_data_date": "2026-06-15"},
            {"ticker": "AAPL"},
        ]

        selected = _select_due_stocks(stocks, {"tickers": ["msft"], "max_tickers": 25})

        assert [stock["ticker"] for stock in selected] == ["MSFT"]

    def test_group_stocks_by_period_uses_backfill_for_new_tickers(self):
        grouped = _group_stocks_by_period([
            {"ticker": "AAPL"},
            {"ticker": "MSFT", "latest_stock_data_date": "2026-06-15"},
        ])

        assert [stock["ticker"] for stock in grouped["5y"]] == ["AAPL"]
        assert [stock["ticker"] for stock in grouped["10d"]] == ["MSFT"]

    def test_should_stop_for_time_when_context_is_near_timeout(self):
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 90_000

        assert _should_stop_for_time(context) is True

    def test_handler_defers_unattempted_batches_near_soft_deadline(self):
        stocks = [{"ticker": f"T{index}"} for index in range(10)]
        context = _RemainingTimeContext([300_000, 60_000])

        with (
            patch("backend.src.collectors.stock_collector.DatabasePool"),
            patch(
                "backend.src.collectors.stock_collector.store.active_stock_metadata",
                return_value=stocks,
            ),
            patch("backend.src.collectors.stock_collector._process_batch") as process_batch,
            patch("backend.src.collectors.stock_collector._record_collection_summary") as record_summary,
            patch("backend.src.collectors.stock_collector._record_failed_ticker_state"),
            patch("backend.src.collectors.stock_collector._emit_metric"),
            patch("backend.src.collectors.stock_collector._emit_collection_summary_metrics"),
            patch(
                "backend.src.collectors.stock_collector._compute_and_store_movement_signals",
                return_value=0,
            ),
            patch("backend.src.collectors.stock_collector.time.sleep"),
        ):
            process_batch.return_value = BatchResult(
                records_inserted=5,
                collected_tickers={f"T{index}" for index in range(5)},
            )

            result = handler({"max_tickers": 10}, context)

        assert result["statusCode"] == 200
        assert "5 deferred" in result["body"]
        process_batch.assert_called_once()
        summary = record_summary.call_args.args[0]
        assert summary["selected_ticker_count"] == 5
        assert summary["successful_ticker_count"] == 5


class TestCollectionSummary:
    """Tests collection completeness/failure summaries."""

    def test_complete_summary(self):
        summary = _build_collection_summary(
            active_ticker_count=100,
            selected_ticker_count=25,
            records_collected=75,
            failed_tickers=[],
        )

        assert summary["status"] == "complete"
        assert summary["successful_ticker_count"] == 25
        assert summary["failed_ticker_count"] == 0
        assert summary["completeness_ratio"] == 1.0

    def test_partial_summary_includes_failures(self):
        summary = _build_collection_summary(
            active_ticker_count=100,
            selected_ticker_count=25,
            records_collected=72,
            duplicate_records=3,
            malformed_tickers=["MSFT"],
            no_data_tickers=["NVDA"],
            failed_tickers=["MSFT", "NVDA"],
        )

        assert summary["status"] == "partial"
        assert summary["successful_ticker_count"] == 23
        assert summary["failed_ticker_count"] == 2
        assert summary["duplicate_record_count"] == 3
        assert summary["malformed_ticker_count"] == 1
        assert summary["no_data_ticker_count"] == 1
        assert summary["completeness_ratio"] == 0.92
        assert summary["minimum_completeness_ratio"] == 0.9
        assert summary["completeness_threshold_met"] is True
        assert summary["failed_tickers"] == ["MSFT", "NVDA"]

    def test_degraded_summary_when_completeness_below_threshold(self):
        summary = _build_collection_summary(
            active_ticker_count=100,
            selected_ticker_count=25,
            records_collected=40,
            failed_tickers=["T1", "T2", "T3"],
        )

        assert summary["status"] == "degraded"
        assert summary["completeness_ratio"] == 0.88
        assert summary["completeness_threshold_met"] is False

    def test_no_active_tickers_summary(self):
        summary = _build_collection_summary(
            active_ticker_count=0,
            selected_ticker_count=0,
            records_collected=0,
            failed_tickers=[],
        )

        assert summary["status"] == "no_active_tickers"
        assert summary["completeness_ratio"] == 1.0

    @patch("backend.src.collectors.stock_collector.boto3.client")
    def test_summary_metrics_are_emitted(self, mock_client):
        cloudwatch = MagicMock()
        mock_client.return_value = cloudwatch
        summary = _build_collection_summary(
            active_ticker_count=100,
            selected_ticker_count=25,
            records_collected=72,
            failed_tickers=["MSFT", "NVDA"],
        )

        _emit_collection_summary_metrics(summary)

        metric_data = cloudwatch.put_metric_data.call_args.kwargs["MetricData"]
        metric_names = {metric["MetricName"] for metric in metric_data}
        assert "stock_collection_completeness_percent" in metric_names
        assert "stock_collection_failed_tickers" in metric_names
        assert "stock_collection_duplicate_records" in metric_names
        assert "stock_collection_malformed_tickers" in metric_names
        assert "stock_collection_no_data_tickers" in metric_names
        assert "stock_collection_threshold_breaches" in metric_names
        assert "stock_collection_partial_runs" in metric_names
        assert any(
            metric["MetricName"] == "stock_collection_partial_runs"
            and metric["Value"] == 1
            for metric in metric_data
        )

    @patch("backend.src.collectors.stock_collector.store")
    def test_failed_ticker_state_is_persisted_with_reason(self, mock_store):
        summary = _build_collection_summary(
            active_ticker_count=100,
            selected_ticker_count=2,
            records_collected=0,
            malformed_tickers=["MSFT"],
            no_data_tickers=["NVDA"],
            failed_tickers=["MSFT", "NVDA"],
        )

        _record_failed_ticker_state(summary)

        mock_store.mark_stock_collection_failed.assert_any_call(
            "MSFT",
            reason="malformed",
            retry_after_hours=6,
        )
        mock_store.mark_stock_collection_failed.assert_any_call(
            "NVDA",
            reason="no_data",
            retry_after_hours=6,
        )


# --- Tests for _store_records (Duplicate Detection - Requirement 1.7) ---

class TestStoreRecords:
    """Tests for duplicate detection and record storage."""

    @patch("backend.src.collectors.stock_collector.store")
    def test_insert_new_records(self, mock_store, mock_db_pool):
        """New records are inserted successfully."""
        mock_store.put_stock_data.return_value = True
        records = [{
            "ticker": "AAPL",
            "trading_date": date(2025, 1, 15),
            "open_price": Decimal("150.0"),
            "high_price": Decimal("155.0"),
            "low_price": Decimal("149.0"),
            "close_price": Decimal("153.0"),
            "volume": 1000000,
        }]

        result = _store_records(records)
        assert result.inserted_records == 1
        assert result.duplicate_records == 0
        mock_store.put_stock_data.assert_called_once()

    @patch("backend.src.collectors.stock_collector.store")
    def test_duplicate_record_skipped(self, mock_store, mock_db_pool):
        """Duplicate records (rowcount=0) are skipped."""
        mock_store.put_stock_data.return_value = False
        records = [{
            "ticker": "AAPL",
            "trading_date": date(2025, 1, 15),
            "open_price": Decimal("150.0"),
            "high_price": Decimal("155.0"),
            "low_price": Decimal("149.0"),
            "close_price": Decimal("153.0"),
            "volume": 1000000,
        }]

        result = _store_records(records)
        assert result.inserted_records == 0
        assert result.duplicate_records == 1

    def test_empty_records_list(self, mock_db_pool):
        """Empty records list returns 0 without DB call."""
        result = _store_records([])
        assert result.inserted_records == 0
        assert result.duplicate_records == 0

    @patch("backend.src.collectors.stock_collector.store")
    def test_mixed_new_and_duplicate(self, mock_store, mock_db_pool):
        """Mix of new and duplicate records counts correctly."""
        mock_store.put_stock_data.side_effect = [True, False]
        records = [
            {
                "ticker": "AAPL", "trading_date": date(2025, 1, 15),
                "open_price": Decimal("150.0"), "high_price": Decimal("155.0"),
                "low_price": Decimal("149.0"), "close_price": Decimal("153.0"),
                "volume": 1000000,
            },
            {
                "ticker": "AAPL", "trading_date": date(2025, 1, 14),
                "open_price": Decimal("148.0"), "high_price": Decimal("152.0"),
                "low_price": Decimal("147.0"), "close_price": Decimal("151.0"),
                "volume": 900000,
            },
        ]

        result = _store_records(records)
        assert result.inserted_records == 1
        assert result.duplicate_records == 1


# --- Tests for _alpha_vantage_fallback ---

class TestAlphaVantageFallback:
    """Tests for Alpha Vantage fallback behavior (Requirement 1.3)."""

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._fetch_alpha_vantage_with_retry")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_fallback_success(self, mock_fetch_av, mock_store):
        """Fallback collects data for failed tickers."""
        mock_fetch_av.return_value = [{"ticker": "AAPL"}]
        mock_store.return_value = StoreResult(inserted_records=1)

        result = _alpha_vantage_fallback(["AAPL", "MSFT"])
        assert result == 2
        assert mock_fetch_av.call_count == 2

    @patch("backend.src.collectors.stock_collector._fetch_alpha_vantage_with_retry")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "")
    def test_fallback_no_api_key(self, mock_fetch_av):
        """Returns 0 when no API key is configured."""
        result = _alpha_vantage_fallback(["AAPL"])
        assert result == 0
        mock_fetch_av.assert_not_called()

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._fetch_alpha_vantage_with_retry")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_fallback_partial_success(self, mock_fetch_av, mock_store):
        """Some tickers succeed via fallback, some fail."""
        mock_fetch_av.side_effect = [
            [{"ticker": "AAPL"}],  # AAPL succeeds
            None,                   # MSFT fails
        ]
        mock_store.return_value = StoreResult(inserted_records=1)

        result = _alpha_vantage_fallback(["AAPL", "MSFT"])
        assert result == 1


# --- Tests for _fetch_alpha_vantage_with_retry ---

class TestFetchAlphaVantageWithRetry:
    """Tests for Alpha Vantage retry logic."""

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_success_first_attempt(self, mock_get, mock_sleep):
        """Returns records on first successful attempt."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Time Series (Daily)": {
                "2025-01-15": {
                    "1. open": "150.0", "2. high": "155.0",
                    "3. low": "149.0", "4. close": "153.0",
                    "5. volume": "1000000",
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_alpha_vantage_with_retry("AAPL")
        assert result is not None
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["close_price"] == Decimal("153.0")
        assert result[0]["data_provider"] == "alpha_vantage"
        assert result[0]["provider_priority"] == "fallback"
        assert result[0]["price_adjustment"] == "unadjusted"
        assert result[0]["fetch_period"] == "compact"
        assert result[0]["adjustment_context"] == "raw_ohlcv_only"
        assert result[0]["split_dividend_adjustment"] == "not_available"
        assert result[0]["fetch_window_start"] is not None
        assert result[0]["fetch_window_end"] is not None
        mock_sleep.assert_not_called()

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_alpha_vantage_records_include_metadata_exchange_currency(
        self, mock_get, mock_sleep
    ):
        """Fallback records carry static exchange/currency provenance when available."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Time Series (Daily)": {
                "2025-01-15": {
                    "1. open": "150.0", "2. high": "155.0",
                    "3. low": "149.0", "4. close": "153.0",
                    "5. volume": "1000000",
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_alpha_vantage_with_retry(
            "AAPL",
            stock_metadata={"exchange": "NASDAQ", "currency": "USD"},
        )

        assert result is not None
        assert result[0]["exchange"] == "NASDAQ"
        assert result[0]["currency"] == "USD"
        mock_sleep.assert_not_called()

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_retry_on_request_exception(self, mock_get, mock_sleep):
        """Retries on request exception with backoff."""
        import requests as req
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Time Series (Daily)": {
                "2025-01-15": {
                    "1. open": "150.0", "2. high": "155.0",
                    "3. low": "149.0", "4. close": "153.0",
                    "5. volume": "1000000",
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.side_effect = [
            req.exceptions.Timeout("timeout"),
            mock_response,
        ]

        result = _fetch_alpha_vantage_with_retry("AAPL")
        assert result is not None
        mock_sleep.assert_called_once_with(2)

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_all_retries_exhausted(self, mock_get, mock_sleep):
        """Returns None after all retries fail."""
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("failed")

        result = _fetch_alpha_vantage_with_retry("AAPL")
        assert result is None
        assert mock_sleep.call_count == 2

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_api_error_message_triggers_retry(self, mock_get, mock_sleep):
        """API error response triggers retry."""
        error_response = MagicMock()
        error_response.json.return_value = {"Error Message": "Invalid ticker"}
        error_response.raise_for_status = MagicMock()

        success_response = MagicMock()
        success_response.json.return_value = {
            "Time Series (Daily)": {
                "2025-01-15": {
                    "1. open": "150.0", "2. high": "155.0",
                    "3. low": "149.0", "4. close": "153.0",
                    "5. volume": "1000000",
                }
            }
        }
        success_response.raise_for_status = MagicMock()
        mock_get.side_effect = [error_response, success_response]

        result = _fetch_alpha_vantage_with_retry("AAPL")
        assert result is not None
        mock_sleep.assert_called_once_with(2)


# --- Tests for no-key market data fallbacks ---

class TestNoKeyMarketDataFallbacks:
    """Tests for no-key market data fallback providers."""

    def test_parse_nasdaq_response_returns_recent_rows_with_provenance(self):
        payload = {
            "data": {
                "tradesTable": {
                    "rows": [
                        {
                            "date": "06/16/2026",
                            "close": "$299.24",
                            "volume": "39,874,400",
                            "open": "$295.245",
                            "high": "$300.48",
                            "low": "$293.97",
                        },
                        {
                            "date": "06/15/2026",
                            "close": "$296.42",
                            "volume": "45,732,570",
                            "open": "$294.12",
                            "high": "$297.78",
                            "low": "$291.70",
                        },
                    ]
                }
            }
        }

        result = _parse_nasdaq_response(
            "AAPL",
            payload,
            stock_metadata={"exchange": "NASDAQ", "currency": "USD"},
        )

        assert result is not None
        assert len(result) == 2
        assert result[0]["trading_date"] == date(2026, 6, 16)
        assert result[0]["close_price"] == Decimal("299.24")
        assert result[0]["volume"] == 39874400
        assert result[0]["data_provider"] == "nasdaq"
        assert result[0]["provider_symbol"] == "AAPL"
        assert result[0]["provider_priority"] == "fallback"
        assert result[0]["exchange"] == "NASDAQ"
        assert result[0]["currency"] == "USD"
        assert result[0]["fetch_period"] == "nasdaq_recent"

    def test_parse_nasdaq_response_handles_null_data_payload(self):
        assert _parse_nasdaq_response("ACLX", {"data": None}) is None

    def test_parse_nasdaq_response_handles_null_trades_table(self):
        assert _parse_nasdaq_response("BF.B", {"data": {"tradesTable": None}}) is None

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    def test_fetch_nasdaq_with_retry_success(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "tradesTable": {
                    "rows": [
                        {
                            "date": "06/16/2026",
                            "close": "$299.24",
                            "volume": "39,874,400",
                            "open": "$295.245",
                            "high": "$300.48",
                            "low": "$293.97",
                        }
                    ]
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_nasdaq_with_retry("AAPL", {"exchange": "NASDAQ"})

        assert result is not None
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["data_provider"] == "nasdaq"
        mock_get.assert_called_once()
        assert mock_get.call_args.kwargs["params"]["assetclass"] == "stocks"
        assert mock_get.call_args.kwargs["headers"]["Accept"] == "application/json"
        mock_sleep.assert_not_called()

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._fetch_nasdaq_with_retry")
    def test_nasdaq_fallback_returns_successful_tickers(self, mock_fetch, mock_store):
        mock_fetch.side_effect = [[{"ticker": "AAPL"}], None]
        mock_store.return_value = StoreResult(inserted_records=2)

        result, succeeded = _nasdaq_fallback_with_details(["AAPL", "MSFT"])

        assert result.inserted_records == 2
        assert succeeded == {"AAPL"}

    def test_stooq_symbol_uses_us_suffix_for_us_exchanges(self):
        assert _stooq_symbol("AAPL", {"exchange": "NASDAQ"}) == "aapl.us"
        assert _stooq_symbol("BRK.B", {"exchange": "NYSE"}) == "brk-b.us"

    def test_parse_stooq_csv_rejects_challenge_page(self):
        result = _parse_stooq_csv(
            "AAPL",
            "aapl.us",
            "<html><body>This site requires JavaScript to verify your browser.</body></html>",
        )

        assert result is None

    def test_parse_stooq_csv_returns_recent_window_with_provenance(self):
        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2025-01-13,148.0,152.0,147.0,151.0,900000\n"
            "2025-01-14,151.0,154.0,150.0,152.0,950000\n"
            "2025-01-15,150.0,155.0,149.0,153.0,1000000\n"
        )

        result = _parse_stooq_csv(
            "AAPL",
            "aapl.us",
            csv_text,
            stock_metadata={"exchange": "NASDAQ", "currency": "USD"},
        )

        assert result is not None
        assert len(result) == 3
        assert result[0]["trading_date"] == date(2025, 1, 15)
        assert result[0]["close_price"] == Decimal("153.0")
        assert result[0]["data_provider"] == "stooq"
        assert result[0]["provider_symbol"] == "aapl.us"
        assert result[0]["provider_priority"] == "fallback"
        assert result[0]["exchange"] == "NASDAQ"
        assert result[0]["currency"] == "USD"
        assert result[0]["fetch_period"] == "stooq_daily"

    @patch("backend.src.collectors.stock_collector.time.sleep")
    @patch("backend.src.collectors.stock_collector.requests.get")
    def test_fetch_stooq_with_retry_success(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2025-01-15,150.0,155.0,149.0,153.0,1000000\n"
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_stooq_with_retry("AAPL", {"exchange": "NASDAQ"})

        assert result is not None
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["data_provider"] == "stooq"
        mock_get.assert_called_once()
        assert mock_get.call_args.kwargs["params"] == {"s": "aapl.us", "i": "d"}
        mock_sleep.assert_not_called()

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._fetch_stooq_with_retry")
    def test_stooq_fallback_returns_successful_tickers(self, mock_fetch, mock_store):
        mock_fetch.side_effect = [[{"ticker": "AAPL"}], None]
        mock_store.return_value = StoreResult(inserted_records=2)

        result, succeeded = _stooq_fallback_with_details(["AAPL", "MSFT"])

        assert result.inserted_records == 2
        assert succeeded == {"AAPL"}


# --- Tests for handler ---

class TestHandler:
    """Tests for the main Lambda handler."""

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._compute_and_store_movement_signals")
    @patch("backend.src.collectors.stock_collector._record_failed_ticker_state")
    @patch("backend.src.collectors.stock_collector._record_collection_summary")
    @patch("backend.src.collectors.stock_collector._alpha_vantage_fallback_with_details")
    @patch("backend.src.collectors.stock_collector._process_batch")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_success(self, mock_pool, mock_watchlist,
                             mock_batch, mock_fallback, mock_summary,
                             mock_failure_state, mock_movement, mock_metric):
        """Handler processes all batches and returns success."""
        mock_watchlist.return_value = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
        mock_batch.return_value = BatchResult(
            records_inserted=2,
            collected_tickers={"AAPL", "MSFT"},
        )
        mock_movement.return_value = 2

        result = handler({}, None)
        assert result["statusCode"] == 200
        assert "2" in result["body"]
        mock_metric.assert_any_call("stocks_collected", 2)
        mock_metric.assert_any_call("market_movement_signals_collected", 2)
        mock_movement.assert_called_once_with({"AAPL", "MSFT"})
        mock_summary.assert_called_once()
        mock_failure_state.assert_called_once()

    @patch("backend.src.collectors.stock_collector._record_collection_summary")
    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_empty_watchlist(self, mock_pool, mock_watchlist, mock_metric, mock_summary):
        """Handler returns early when no active tickers found."""
        mock_watchlist.return_value = []

        result = handler({}, None)
        assert result["statusCode"] == 200
        assert "No active tickers" in result["body"]
        mock_metric.assert_not_called()
        mock_summary.assert_called_once()

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._compute_and_store_movement_signals")
    @patch("backend.src.collectors.stock_collector._record_failed_ticker_state")
    @patch("backend.src.collectors.stock_collector._record_collection_summary")
    @patch("backend.src.collectors.stock_collector._alpha_vantage_fallback_with_details")
    @patch("backend.src.collectors.stock_collector._process_batch")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_with_fallback(self, mock_pool, mock_watchlist,
                                   mock_batch, mock_fallback, mock_summary,
                                   mock_failure_state, mock_movement, mock_metric):
        """Handler triggers Alpha Vantage fallback for failed tickers."""
        mock_watchlist.return_value = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
        mock_batch.return_value = BatchResult(
            records_inserted=1,
            failed_tickers=["MSFT"],
            no_data_tickers=["MSFT"],
            collected_tickers={"AAPL"},
        )
        mock_fallback.return_value = (StoreResult(inserted_records=1), {"MSFT"})
        mock_movement.return_value = 2

        result = handler({}, None)
        assert result["statusCode"] == 200
        mock_fallback.assert_called_once_with(
            ["MSFT"],
            stock_metadata_by_ticker={
                "AAPL": {"ticker": "AAPL"},
                "MSFT": {"ticker": "MSFT"},
            },
        )
        mock_movement.assert_called_once_with({"AAPL", "MSFT"})

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_exception_emits_zero_metric(self, mock_pool,
                                                  mock_watchlist, mock_metric):
        """Handler emits 0 metric and re-raises on exception."""
        mock_watchlist.side_effect = Exception("DB connection failed")

        with pytest.raises(Exception, match="DB connection failed"):
            handler({}, None)
        mock_metric.assert_called_with("stocks_collected", 0)

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._compute_and_store_movement_signals")
    @patch("backend.src.collectors.stock_collector._record_failed_ticker_state")
    @patch("backend.src.collectors.stock_collector._record_collection_summary")
    @patch("backend.src.collectors.stock_collector._alpha_vantage_fallback_with_details")
    @patch("backend.src.collectors.stock_collector._process_batch")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_batches_large_watchlist(self, mock_pool, mock_watchlist,
                                             mock_batch, mock_fallback,
                                             mock_summary, mock_failure_state,
                                             mock_movement, mock_metric):
        """Handler processes the bounded due slice in small batches."""
        mock_watchlist.return_value = [{"ticker": f"T{i}"} for i in range(250)]
        mock_batch.return_value = BatchResult(
            records_inserted=5,
            collected_tickers={"T0", "T1", "T2", "T3", "T4"},
        )
        mock_movement.return_value = 5

        handler({}, None)
        assert mock_batch.call_count == 5
        # Verify batch sizes
        calls = mock_batch.call_args_list
        assert len(calls[0][0][0]) == 5
        assert len(calls[1][0][0]) == 5
        assert len(calls[2][0][0]) == 5
