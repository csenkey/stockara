"""Unit tests for stock data collector.

Tests batch fetching logic, retry behavior, fallback to Alpha Vantage,
duplicate detection, and malformed data handling.

Requirements: 1.3, 1.6, 1.7
"""

import math
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

from backend.src.collectors.stock_collector import (
    handler,
    _fetch_yfinance_with_retry,
    _process_batch,
    _extract_ticker_data,
    _validate_record,
    _store_records,
    _alpha_vantage_fallback,
    _fetch_alpha_vantage_with_retry,
)


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
        ("AAPL", "Close"), ("AAPL", "Volume"),
        ("MSFT", "Open"), ("MSFT", "High"), ("MSFT", "Low"),
        ("MSFT", "Close"), ("MSFT", "Volume"),
    ])
    data = [[150.0, 155.0, 149.0, 153.0, 1000000,
             400.0, 410.0, 395.0, 405.0, 500000]]
    return pd.DataFrame(data, index=idx, columns=columns)


@pytest.fixture
def sample_single_ticker_dataframe():
    """Create a sample single-ticker yfinance DataFrame."""
    idx = pd.DatetimeIndex([datetime(2025, 1, 15)])
    data = {"Open": [150.0], "High": [155.0], "Low": [149.0],
            "Close": [153.0], "Volume": [1000000]}
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
    @patch("backend.src.collectors.stock_collector._extract_ticker_data")
    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_successful_batch(self, mock_fetch, mock_extract, mock_store):
        """All tickers in batch processed successfully."""
        mock_fetch.return_value = MagicMock()
        mock_extract.return_value = [{"ticker": "AAPL"}]
        mock_store.return_value = 1

        collected, failed = _process_batch(["AAPL", "MSFT"])
        assert collected == 2
        assert failed == []

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._extract_ticker_data")
    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_partial_failure(self, mock_fetch, mock_extract, mock_store):
        """Some tickers fail extraction, returned in failed list."""
        mock_fetch.return_value = MagicMock()
        mock_extract.side_effect = [
            [{"ticker": "AAPL"}],  # AAPL succeeds
            None,                   # MSFT fails
        ]
        mock_store.return_value = 1

        collected, failed = _process_batch(["AAPL", "MSFT"])
        assert collected == 1
        assert failed == ["MSFT"]

    @patch("backend.src.collectors.stock_collector._fetch_yfinance_with_retry")
    def test_entire_batch_fails(self, mock_fetch):
        """When yfinance returns None, all tickers are failed."""
        mock_fetch.return_value = None

        collected, failed = _process_batch(["AAPL", "MSFT", "GOOG"])
        assert collected == 0
        assert failed == ["AAPL", "MSFT", "GOOG"]


# --- Tests for _store_records (Duplicate Detection - Requirement 1.7) ---

class TestStoreRecords:
    """Tests for duplicate detection and record storage."""

    def test_insert_new_records(self, mock_db_pool):
        """New records are inserted successfully."""
        _, mock_conn, mock_cursor = mock_db_pool
        mock_cursor.rowcount = 1

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
        assert result == 1
        mock_cursor.execute.assert_called_once()
        # Verify ON CONFLICT DO NOTHING is in the SQL
        sql = mock_cursor.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    def test_duplicate_record_skipped(self, mock_db_pool):
        """Duplicate records (rowcount=0) are skipped."""
        _, mock_conn, mock_cursor = mock_db_pool
        mock_cursor.rowcount = 0  # ON CONFLICT DO NOTHING triggers

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
        assert result == 0  # No new inserts

    def test_empty_records_list(self, mock_db_pool):
        """Empty records list returns 0 without DB call."""
        _, mock_conn, mock_cursor = mock_db_pool
        result = _store_records([])
        assert result == 0
        mock_cursor.execute.assert_not_called()

    def test_mixed_new_and_duplicate(self, mock_db_pool):
        """Mix of new and duplicate records counts correctly."""
        _, mock_conn, mock_cursor = mock_db_pool
        # First call: new record, second: duplicate
        type(mock_cursor).rowcount = pytest.importorskip(
            "unittest.mock"
        ).PropertyMock(side_effect=[1, 0])

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
        assert result == 1  # Only one new record


# --- Tests for _alpha_vantage_fallback ---

class TestAlphaVantageFallback:
    """Tests for Alpha Vantage fallback behavior (Requirement 1.3)."""

    @patch("backend.src.collectors.stock_collector._store_records")
    @patch("backend.src.collectors.stock_collector._fetch_alpha_vantage_with_retry")
    @patch("backend.src.collectors.stock_collector.ALPHA_VANTAGE_API_KEY", "test-key")
    def test_fallback_success(self, mock_fetch_av, mock_store):
        """Fallback collects data for failed tickers."""
        mock_fetch_av.return_value = [{"ticker": "AAPL"}]
        mock_store.return_value = 1

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
        mock_store.return_value = 1

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


# --- Tests for handler ---

class TestHandler:
    """Tests for the main Lambda handler."""

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._alpha_vantage_fallback")
    @patch("backend.src.collectors.stock_collector._process_batch")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_success(self, mock_pool, mock_watchlist,
                             mock_batch, mock_fallback, mock_metric):
        """Handler processes all batches and returns success."""
        mock_watchlist.return_value = ["AAPL", "MSFT"]
        mock_batch.return_value = (2, [])

        result = handler({}, None)
        assert result["statusCode"] == 200
        assert "2" in result["body"]
        mock_metric.assert_called_with("stocks_collected", 2)

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_empty_watchlist(self, mock_pool, mock_watchlist, mock_metric):
        """Handler returns early when no active tickers found."""
        mock_watchlist.return_value = []

        result = handler({}, None)
        assert result["statusCode"] == 200
        assert "No active tickers" in result["body"]
        mock_metric.assert_not_called()

    @patch("backend.src.collectors.stock_collector._emit_metric")
    @patch("backend.src.collectors.stock_collector._alpha_vantage_fallback")
    @patch("backend.src.collectors.stock_collector._process_batch")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_with_fallback(self, mock_pool, mock_watchlist,
                                   mock_batch, mock_fallback, mock_metric):
        """Handler triggers Alpha Vantage fallback for failed tickers."""
        mock_watchlist.return_value = ["AAPL", "MSFT"]
        mock_batch.return_value = (1, ["MSFT"])
        mock_fallback.return_value = 1

        result = handler({}, None)
        assert result["statusCode"] == 200
        mock_fallback.assert_called_once_with(["MSFT"])

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
    @patch("backend.src.collectors.stock_collector._alpha_vantage_fallback")
    @patch("backend.src.collectors.stock_collector._process_batch")
    @patch("backend.src.collectors.stock_collector._fetch_watchlist")
    @patch("backend.src.collectors.stock_collector.DatabasePool")
    def test_handler_batches_large_watchlist(self, mock_pool, mock_watchlist,
                                             mock_batch, mock_fallback,
                                             mock_metric):
        """Handler splits 250 tickers into 3 batches of 100."""
        mock_watchlist.return_value = [f"T{i}" for i in range(250)]
        mock_batch.return_value = (100, [])

        handler({}, None)
        assert mock_batch.call_count == 3
        # Verify batch sizes
        calls = mock_batch.call_args_list
        assert len(calls[0][0][0]) == 100
        assert len(calls[1][0][0]) == 100
        assert len(calls[2][0][0]) == 50
