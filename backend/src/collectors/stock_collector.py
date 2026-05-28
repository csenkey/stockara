"""Stock Data Collector Lambda handler.

Fetches daily OHLCV data for all monitored stocks from yfinance (primary)
with Alpha Vantage as fallback. Triggered by EventBridge daily at 21:00 UTC.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

import os
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
import requests
import structlog
import yfinance as yf
from psycopg2.extras import RealDictCursor

from backend.src.db.connection import DatabasePool

logger = structlog.get_logger(__name__)

# Configuration
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
BATCH_SIZE = 100
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2
CLOUDWATCH_NAMESPACE = "StockMonitoring"


def handler(event: dict, context: Any) -> dict:
    """Lambda handler for stock data collection.

    Triggered by EventBridge daily after market close.
    Fetches OHLCV data for all active stocks in the watchlist.
    """
    log = logger.bind(event=event)
    log.info("stock_collector_started")

    try:
        DatabasePool.initialize()
        tickers = _fetch_watchlist()

        if not tickers:
            log.warning("no_active_tickers_found")
            return {"statusCode": 200, "body": "No active tickers to collect"}

        log.info("watchlist_loaded", ticker_count=len(tickers))

        # Batch tickers in groups of 100 for yfinance
        batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

        collected_count = 0
        failed_tickers: list[str] = []

        for batch_idx, batch in enumerate(batches):
            log.info("processing_batch", batch_index=batch_idx, batch_size=len(batch))
            batch_collected, batch_failed = _process_batch(batch)
            collected_count += batch_collected
            failed_tickers.extend(batch_failed)

        # Attempt Alpha Vantage fallback for failed tickers
        if failed_tickers:
            log.info("alpha_vantage_fallback_starting", failed_count=len(failed_tickers))
            fallback_collected = _alpha_vantage_fallback(failed_tickers)
            collected_count += fallback_collected
            remaining_failures = len(failed_tickers) - fallback_collected

            if remaining_failures > 0:
                log.error(
                    "tickers_failed_all_providers",
                    remaining_failures=remaining_failures,
                )

        # Emit CloudWatch metric
        _emit_metric("stocks_collected", collected_count)

        log.info(
            "stock_collector_completed",
            collected=collected_count,
            failed=len(failed_tickers),
        )

        return {
            "statusCode": 200,
            "body": f"Collected data for {collected_count} stocks",
        }

    except Exception as e:
        log.error("stock_collector_failed", error=str(e), exc_info=True)
        _emit_metric("stocks_collected", 0)
        raise
    finally:
        DatabasePool.close()


def _fetch_watchlist() -> list[str]:
    """Fetch active tickers from the stocks watchlist table."""
    DatabasePool.initialize()
    conn = DatabasePool._pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker"
            )
            rows = cur.fetchall()
        conn.commit()
        return [row["ticker"] for row in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        DatabasePool._pool.putconn(conn)


def _process_batch(tickers: list[str]) -> tuple[int, list[str]]:
    """Process a batch of tickers via yfinance with retry logic.

    Returns:
        Tuple of (successfully collected count, list of failed tickers)
    """
    collected = 0
    failed: list[str] = []

    data = _fetch_yfinance_with_retry(tickers)

    if data is None:
        # Entire batch failed
        return 0, tickers

    for ticker in tickers:
        records = _extract_ticker_data(data, ticker)
        if records:
            stored = _store_records(records)
            collected += stored
        else:
            failed.append(ticker)

    return collected, failed


def _fetch_yfinance_with_retry(tickers: list[str]) -> Any | None:
    """Fetch data from yfinance with exponential backoff retry.

    Retries up to MAX_RETRIES times with exponential backoff starting
    at INITIAL_BACKOFF_SECONDS.
    """
    ticker_str = " ".join(tickers)
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(attempt=attempt, ticker_count=len(tickers))
            log.info("yfinance_fetch_attempt")

            data = yf.download(
                ticker_str,
                period="1d",
                group_by="ticker",
                progress=False,
                timeout=30,
            )

            if data is not None and not data.empty:
                log.info("yfinance_fetch_success")
                return data

            log.warning("yfinance_returned_empty")

        except Exception as e:
            logger.warning(
                "yfinance_fetch_failed",
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("yfinance_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("yfinance_all_retries_exhausted", tickers=tickers[:5])
    return None


def _extract_ticker_data(data: Any, ticker: str) -> list[dict] | None:
    """Extract OHLCV records for a single ticker from yfinance DataFrame.

    Returns None if data is missing or malformed.
    """
    try:
        # For single ticker downloads, columns are flat
        # For multi-ticker downloads, columns are multi-level (ticker, field)
        if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
            if ticker not in data.columns.get_level_values(0):
                logger.warning("ticker_not_in_response", ticker=ticker)
                return None
            ticker_data = data[ticker]
        else:
            # Single ticker case
            ticker_data = data

        records = []
        for idx, row in ticker_data.iterrows():
            record = _validate_record(ticker, idx, row)
            if record:
                records.append(record)

        return records if records else None

    except Exception as e:
        logger.warning(
            "ticker_data_extraction_failed",
            ticker=ticker,
            error=str(e),
        )
        return None


def _validate_record(ticker: str, trading_date: Any, row: Any) -> dict | None:
    """Validate and normalize a single OHLCV record.

    Returns None and logs a warning if the record is malformed.
    """
    try:
        # Extract and validate date
        if hasattr(trading_date, "date"):
            record_date = trading_date.date()
        elif isinstance(trading_date, date):
            record_date = trading_date
        else:
            record_date = date.fromisoformat(str(trading_date)[:10])

        # Extract price fields - yfinance uses 'Open', 'High', 'Low', 'Close', 'Volume'
        open_price = _to_decimal(row.get("Open"))
        high_price = _to_decimal(row.get("High"))
        low_price = _to_decimal(row.get("Low"))
        close_price = _to_decimal(row.get("Close"))
        volume = int(row.get("Volume", 0))

        # Validate all required fields are present and valid
        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "malformed_record_missing_prices",
                ticker=ticker,
                trading_date=str(record_date),
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "malformed_record_invalid_prices",
                ticker=ticker,
                trading_date=str(record_date),
            )
            return None

        if volume < 0:
            logger.warning(
                "malformed_record_invalid_volume",
                ticker=ticker,
                trading_date=str(record_date),
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "volume": volume,
        }

    except (ValueError, TypeError, InvalidOperation) as e:
        logger.warning(
            "malformed_record_discarded",
            ticker=ticker,
            error=str(e),
        )
        return None


def _to_decimal(value: Any) -> Decimal | None:
    """Convert a value to Decimal, returning None if conversion fails."""
    if value is None:
        return None
    try:
        import math

        float_val = float(value)
        if math.isnan(float_val) or math.isinf(float_val):
            return None
        return Decimal(str(round(float_val, 4)))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _store_records(records: list[dict]) -> int:
    """Store OHLCV records in the database, skipping duplicates.

    Uses ON CONFLICT DO NOTHING to skip records where (ticker, trading_date)
    already exists (UNIQUE constraint).

    Returns the number of successfully inserted records.
    """
    if not records:
        return 0

    inserted = 0
    conn = DatabasePool._pool.getconn()
    try:
        with conn.cursor() as cur:
            for record in records:
                try:
                    cur.execute(
                        """
                        INSERT INTO stock_data
                            (ticker, trading_date, open_price, high_price,
                             low_price, close_price, volume, collected_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticker, trading_date) DO NOTHING
                        """,
                        (
                            record["ticker"],
                            record["trading_date"],
                            record["open_price"],
                            record["high_price"],
                            record["low_price"],
                            record["close_price"],
                            record["volume"],
                            datetime.utcnow(),
                        ),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                    else:
                        logger.debug(
                            "duplicate_record_skipped",
                            ticker=record["ticker"],
                            trading_date=str(record["trading_date"]),
                        )
                except Exception as e:
                    logger.warning(
                        "record_insert_failed",
                        ticker=record["ticker"],
                        error=str(e),
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        DatabasePool._pool.putconn(conn)

    return inserted


def _alpha_vantage_fallback(tickers: list[str]) -> int:
    """Attempt to fetch data from Alpha Vantage for failed tickers.

    Alpha Vantage free tier: 500 calls/day, one ticker at a time.
    Uses retry logic with exponential backoff.

    Returns the number of successfully collected records.
    """
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("alpha_vantage_api_key_not_configured")
        return 0

    collected = 0

    for ticker in tickers:
        records = _fetch_alpha_vantage_with_retry(ticker)
        if records:
            stored = _store_records(records)
            collected += stored

    return collected


def _fetch_alpha_vantage_with_retry(ticker: str) -> list[dict] | None:
    """Fetch daily data for a single ticker from Alpha Vantage with retry."""
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(ticker=ticker, attempt=attempt, provider="alpha_vantage")
            log.info("alpha_vantage_fetch_attempt")

            response = requests.get(
                ALPHA_VANTAGE_BASE_URL,
                params={
                    "function": "TIME_SERIES_DAILY",
                    "symbol": ticker,
                    "outputsize": "compact",
                    "apikey": ALPHA_VANTAGE_API_KEY,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            # Check for API errors
            if "Error Message" in data or "Note" in data:
                error_msg = data.get("Error Message", data.get("Note", "Unknown error"))
                log.warning("alpha_vantage_api_error", error=error_msg)
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= 2
                continue

            time_series = data.get("Time Series (Daily)", {})
            if not time_series:
                log.warning("alpha_vantage_no_data")
                return None

            # Get only the most recent trading day
            records = []
            for date_str, values in sorted(time_series.items(), reverse=True)[:1]:
                record = _parse_alpha_vantage_record(ticker, date_str, values)
                if record:
                    records.append(record)

            if records:
                log.info("alpha_vantage_fetch_success")
                return records

        except requests.exceptions.RequestException as e:
            logger.warning(
                "alpha_vantage_request_failed",
                ticker=ticker,
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("alpha_vantage_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("alpha_vantage_all_retries_exhausted", ticker=ticker)
    return None


def _parse_alpha_vantage_record(
    ticker: str, date_str: str, values: dict
) -> dict | None:
    """Parse a single Alpha Vantage daily record."""
    try:
        record_date = date.fromisoformat(date_str)
        open_price = _to_decimal(values.get("1. open"))
        high_price = _to_decimal(values.get("2. high"))
        low_price = _to_decimal(values.get("3. low"))
        close_price = _to_decimal(values.get("4. close"))
        volume = int(values.get("5. volume", 0))

        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "alpha_vantage_malformed_record",
                ticker=ticker,
                trading_date=date_str,
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "alpha_vantage_invalid_prices",
                ticker=ticker,
                trading_date=date_str,
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "volume": volume,
        }

    except (ValueError, TypeError) as e:
        logger.warning(
            "alpha_vantage_parse_failed",
            ticker=ticker,
            date_str=date_str,
            error=str(e),
        )
        return None


def _emit_metric(metric_name: str, value: float) -> None:
    """Emit a custom CloudWatch metric."""
    try:
        client = boto3.client("cloudwatch")
        client.put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Timestamp": datetime.utcnow(),
                }
            ],
        )
        logger.info("cloudwatch_metric_emitted", metric=metric_name, value=value)
    except Exception as e:
        logger.warning("cloudwatch_metric_failed", metric=metric_name, error=str(e))
