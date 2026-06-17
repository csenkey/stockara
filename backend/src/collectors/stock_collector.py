"""Stock Data Collector Lambda handler.

Fetches daily OHLCV data for all monitored stocks from yfinance (primary)
with Alpha Vantage as fallback. Triggered by EventBridge daily at 21:00 UTC.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
import requests
import structlog
import yfinance as yf

from src.db.connection import DatabasePool, store

logger = structlog.get_logger(__name__)

# Configuration
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
STOOQ_BASE_URL = os.environ.get("STOOQ_BASE_URL", "https://stooq.com/q/d/l/")
STOOQ_MAX_RECORDS_PER_TICKER = int(os.environ.get("STOOQ_MAX_RECORDS_PER_TICKER", "90"))
DEFAULT_MARKET_DATA_CURRENCY = os.environ.get("STOCK_DATA_DEFAULT_CURRENCY", "USD")
BATCH_SIZE = int(os.environ.get("STOCK_COLLECTOR_BATCH_SIZE", "5"))
MAX_TICKERS_PER_RUN = int(os.environ.get("STOCK_COLLECTOR_MAX_TICKERS", "25"))
INITIAL_HISTORY_PERIOD = os.environ.get("STOCK_INITIAL_HISTORY_PERIOD", "5y")
INCREMENTAL_PERIOD = os.environ.get("STOCK_INCREMENTAL_PERIOD", "10d")
YFINANCE_BATCH_PAUSE_SECONDS = float(os.environ.get("YFINANCE_BATCH_PAUSE_SECONDS", "1"))
STOCK_COLLECTION_MIN_COMPLETENESS = float(
    os.environ.get("STOCK_COLLECTION_MIN_COMPLETENESS", "0.9")
)
STOCK_FAILED_RETRY_AFTER_HOURS = int(
    os.environ.get("STOCK_FAILED_RETRY_AFTER_HOURS", "6")
)
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2
CLOUDWATCH_NAMESPACE = "StockMonitoring"


@dataclass
class ExtractResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None


@dataclass
class StoreResult:
    inserted_records: int = 0
    duplicate_records: int = 0
    failed_records: int = 0


@dataclass
class BatchResult:
    records_inserted: int = 0
    duplicate_records: int = 0
    failed_records: int = 0
    failed_tickers: list[str] = field(default_factory=list)
    malformed_tickers: list[str] = field(default_factory=list)
    no_data_tickers: list[str] = field(default_factory=list)
    collected_tickers: set[str] = field(default_factory=set)


def handler(event: dict, context: Any) -> dict:
    """Lambda handler for stock data collection.

    Triggered by EventBridge daily after market close.
    Fetches OHLCV data for all active stocks in the watchlist.
    """
    log = logger.bind(lambda_event=event)
    log.info("stock_collector_started")

    try:
        DatabasePool.initialize()
        stocks = _fetch_watchlist()

        if not stocks:
            log.warning("no_active_tickers_found")
            _record_collection_summary(
                _build_collection_summary(
                    active_ticker_count=0,
                    selected_ticker_count=0,
                    records_collected=0,
                    failed_tickers=[],
                )
            )
            return {"statusCode": 200, "body": "No active tickers to collect"}

        selected_stocks = _select_due_stocks(stocks, event or {})
        if not selected_stocks:
            log.info("no_due_tickers_found", active_ticker_count=len(stocks))
            _record_collection_summary(
                _build_collection_summary(
                    active_ticker_count=len(stocks),
                    selected_ticker_count=0,
                    records_collected=0,
                    failed_tickers=[],
                )
            )
            return {"statusCode": 200, "body": "No due tickers to collect"}

        log.info(
            "watchlist_loaded",
            active_ticker_count=len(stocks),
            selected_ticker_count=len(selected_stocks),
            batch_size=BATCH_SIZE,
        )

        collected_count = 0
        duplicate_count = 0
        malformed_tickers: list[str] = []
        no_data_tickers: list[str] = []
        failed_tickers: list[str] = []
        fallback_succeeded: set[str] = set()
        collected_tickers: set[str] = set()
        stock_metadata_by_ticker = {
            stock["ticker"]: stock for stock in selected_stocks
        }

        for period, period_stocks in _group_stocks_by_period(selected_stocks).items():
            tickers = [stock["ticker"] for stock in period_stocks]
            batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
            for batch_idx, batch in enumerate(batches):
                log.info(
                    "processing_batch",
                    batch_index=batch_idx,
                    batch_size=len(batch),
                    period=period,
                )
                batch_result = _process_batch(
                    batch,
                    period=period,
                    stock_metadata_by_ticker=stock_metadata_by_ticker,
                )
                collected_count += batch_result.records_inserted
                duplicate_count += batch_result.duplicate_records
                failed_tickers.extend(batch_result.failed_tickers)
                malformed_tickers.extend(batch_result.malformed_tickers)
                no_data_tickers.extend(batch_result.no_data_tickers)
                collected_tickers.update(batch_result.collected_tickers)
                if YFINANCE_BATCH_PAUSE_SECONDS > 0:
                    time.sleep(YFINANCE_BATCH_PAUSE_SECONDS)

        # Attempt fallback providers for failed tickers
        if failed_tickers:
            log.info("market_data_fallback_starting", failed_count=len(failed_tickers))
            fallback_result, fallback_succeeded = _alpha_vantage_fallback_with_details(
                failed_tickers,
                stock_metadata_by_ticker=stock_metadata_by_ticker,
            )
            collected_count += fallback_result.inserted_records
            duplicate_count += fallback_result.duplicate_records
            collected_tickers.update(fallback_succeeded)
            remaining_tickers = sorted(set(failed_tickers) - fallback_succeeded)

            if remaining_tickers:
                stooq_result, stooq_succeeded = _stooq_fallback_with_details(
                    remaining_tickers,
                    stock_metadata_by_ticker=stock_metadata_by_ticker,
                )
                collected_count += stooq_result.inserted_records
                duplicate_count += stooq_result.duplicate_records
                fallback_succeeded.update(stooq_succeeded)
                collected_tickers.update(stooq_succeeded)

            remaining_failures = len(set(failed_tickers) - fallback_succeeded)

            if remaining_failures > 0:
                log.error(
                    "tickers_failed_all_providers",
                    remaining_failures=remaining_failures,
                )

        remaining_failed_tickers = sorted(set(failed_tickers) - fallback_succeeded)
        summary = _build_collection_summary(
            active_ticker_count=len(stocks),
            selected_ticker_count=len(selected_stocks),
            records_collected=collected_count,
            duplicate_records=duplicate_count,
            malformed_tickers=malformed_tickers,
            no_data_tickers=no_data_tickers,
            recovered_tickers=sorted(collected_tickers),
            failed_tickers=remaining_failed_tickers,
        )
        _record_collection_summary(summary)
        _record_failed_ticker_state(summary)

        # Emit CloudWatch metric
        _emit_metric("stocks_collected", collected_count)
        _emit_collection_summary_metrics(summary)
        movement_signal_count = _compute_and_store_movement_signals(collected_tickers)
        _emit_metric("market_movement_signals_collected", movement_signal_count)

        log.info(
            "stock_collector_completed",
            collected=collected_count,
            failed=len(remaining_failed_tickers),
            selected=len(selected_stocks),
            completeness_ratio=summary["completeness_ratio"],
        )

        return {
            "statusCode": 200,
            "body": (
                f"Collected {collected_count} new records for "
                f"{len(selected_stocks)} selected tickers"
            ),
        }

    except Exception as e:
        log.error("stock_collector_failed", error=str(e), exc_info=True)
        _emit_metric("stocks_collected", 0)
        raise
    finally:
        DatabasePool.close()


def _fetch_watchlist() -> list[dict[str, Any]]:
    """Fetch active stock metadata from the stocks watchlist table."""
    return store.active_stock_metadata()


def _select_due_stocks(stocks: list[dict[str, Any]], event: dict[str, Any]) -> list[dict[str, Any]]:
    requested = {ticker.upper() for ticker in event.get("tickers", [])}
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]

    max_tickers = int(event.get("max_tickers", MAX_TICKERS_PER_RUN))
    ordered = sorted(
        stocks,
        key=lambda stock: (
            not _failed_retry_due(stock),
            stock.get("latest_stock_data_date") is not None,
            stock.get("latest_stock_data_date") or "",
            stock["ticker"],
        ),
    )
    return ordered[:max_tickers]


def _group_stocks_by_period(stocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        INITIAL_HISTORY_PERIOD: [],
        INCREMENTAL_PERIOD: [],
    }
    for stock in stocks:
        period = INCREMENTAL_PERIOD if stock.get("latest_stock_data_date") else INITIAL_HISTORY_PERIOD
        grouped.setdefault(period, []).append(stock)
    return {period: values for period, values in grouped.items() if values}


def _failed_retry_due(stock: dict[str, Any], now: datetime | None = None) -> bool:
    failed_at = stock.get("latest_stock_collection_failed_at")
    if not failed_at:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        failed_time = datetime.fromisoformat(str(failed_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if failed_time.tzinfo is None:
        failed_time = failed_time.replace(tzinfo=timezone.utc)
    return now - failed_time >= timedelta(hours=STOCK_FAILED_RETRY_AFTER_HOURS)


def _build_collection_summary(
    active_ticker_count: int,
    selected_ticker_count: int,
    records_collected: int,
    failed_tickers: list[str],
    duplicate_records: int = 0,
    malformed_tickers: list[str] | None = None,
    no_data_tickers: list[str] | None = None,
    recovered_tickers: list[str] | None = None,
) -> dict[str, Any]:
    malformed_tickers = malformed_tickers or []
    no_data_tickers = no_data_tickers or []
    recovered_tickers = recovered_tickers or []
    failed_ticker_count = len(set(failed_tickers))
    successful_ticker_count = max(selected_ticker_count - failed_ticker_count, 0)
    completeness_ratio = (
        successful_ticker_count / selected_ticker_count if selected_ticker_count else 1.0
    )
    if active_ticker_count == 0:
        status = "no_active_tickers"
    elif selected_ticker_count == 0:
        status = "no_due_tickers"
    elif failed_ticker_count == 0:
        status = "complete"
    elif successful_ticker_count == 0:
        status = "failed"
    elif completeness_ratio < STOCK_COLLECTION_MIN_COMPLETENESS:
        status = "degraded"
    else:
        status = "partial"

    threshold_met = completeness_ratio >= STOCK_COLLECTION_MIN_COMPLETENESS
    return {
        "status": status,
        "active_ticker_count": active_ticker_count,
        "selected_ticker_count": selected_ticker_count,
        "successful_ticker_count": successful_ticker_count,
        "failed_ticker_count": failed_ticker_count,
        "records_collected": records_collected,
        "duplicate_record_count": duplicate_records,
        "malformed_ticker_count": len(set(malformed_tickers)),
        "no_data_ticker_count": len(set(no_data_tickers)),
        "completeness_ratio": round(completeness_ratio, 4),
        "minimum_completeness_ratio": STOCK_COLLECTION_MIN_COMPLETENESS,
        "completeness_threshold_met": threshold_met,
        "failed_tickers": failed_tickers[:50],
        "failed_tickers_truncated": len(failed_tickers) > 50,
        "malformed_tickers": sorted(set(malformed_tickers))[:50],
        "malformed_tickers_truncated": len(set(malformed_tickers)) > 50,
        "no_data_tickers": sorted(set(no_data_tickers))[:50],
        "no_data_tickers_truncated": len(set(no_data_tickers)) > 50,
        "recovered_tickers": sorted(set(recovered_tickers))[:50],
        "recovered_tickers_truncated": len(set(recovered_tickers)) > 50,
        "retry_after_hours": STOCK_FAILED_RETRY_AFTER_HOURS,
    }


def _record_collection_summary(summary: dict[str, Any]) -> None:
    try:
        store.put_collection_summary("STOCK_COLLECTION", summary)
    except Exception as e:
        logger.warning("stock_collection_summary_write_failed", error=str(e))


def _record_failed_ticker_state(summary: dict[str, Any]) -> None:
    failed_tickers = summary.get("failed_tickers", [])
    failed_set = set(failed_tickers)
    malformed_set = set(summary.get("malformed_tickers", []))
    no_data_set = set(summary.get("no_data_tickers", []))
    for ticker in failed_tickers:
        if ticker in malformed_set:
            reason = "malformed"
        elif ticker in no_data_set:
            reason = "no_data"
        else:
            reason = "all_providers_failed"
        try:
            store.mark_stock_collection_failed(
                ticker,
                reason=reason,
                retry_after_hours=STOCK_FAILED_RETRY_AFTER_HOURS,
            )
        except Exception as exc:
            logger.warning(
                "stock_collection_failure_state_write_failed",
                ticker=ticker,
                error=str(exc),
            )

    # Clear stale failure markers for tickers that recovered in this run.
    for ticker in set(summary.get("recovered_tickers", [])) - failed_set:
        try:
            store.clear_stock_collection_failure(ticker)
        except Exception as exc:
            logger.warning(
                "stock_collection_failure_state_clear_failed",
                ticker=ticker,
                error=str(exc),
            )


def _emit_collection_summary_metrics(summary: dict[str, Any]) -> None:
    status = str(summary.get("status", "unknown"))
    metric_data = [
        {
            "MetricName": "stock_collection_completeness_percent",
            "Value": float(summary.get("completeness_ratio", 0)) * 100,
            "Unit": "Percent",
        },
        {
            "MetricName": "stock_collection_failed_tickers",
            "Value": int(summary.get("failed_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_duplicate_records",
            "Value": int(summary.get("duplicate_record_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_malformed_tickers",
            "Value": int(summary.get("malformed_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_no_data_tickers",
            "Value": int(summary.get("no_data_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_successful_tickers",
            "Value": int(summary.get("successful_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_partial_runs",
            "Value": 1 if status == "partial" else 0,
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_failed_runs",
            "Value": 1 if status in {"failed", "degraded"} else 0,
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_threshold_breaches",
            "Value": 0 if summary.get("completeness_threshold_met", True) else 1,
            "Unit": "Count",
        },
    ]
    _emit_metric_data(metric_data)


def _process_batch(
    tickers: list[str],
    period: str = "1d",
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> BatchResult:
    """Process a batch of tickers via yfinance with retry logic.

    Returns granular accounting for inserted records, duplicates, and failures.
    """
    result = BatchResult()

    data = _fetch_yfinance_with_retry(tickers, period=period)

    if data is None:
        # Entire batch failed
        result.failed_tickers.extend(tickers)
        result.no_data_tickers.extend(tickers)
        return result

    for ticker in tickers:
        extract = _extract_ticker_data_result(
            data,
            ticker,
            period=period,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if extract.records:
            stored = _store_records(extract.records)
            result.records_inserted += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                result.collected_tickers.add(ticker)
            else:
                result.failed_tickers.append(ticker)
        else:
            result.failed_tickers.append(ticker)
            if extract.failure_reason == "malformed":
                result.malformed_tickers.append(ticker)
            else:
                result.no_data_tickers.append(ticker)

    return result


def _compute_and_store_movement_signals(tickers: set[str]) -> int:
    signal_count = 0
    for ticker in sorted(tickers):
        try:
            rows = store.get_stock_data(
                ticker,
                date.today() - timedelta(days=60),
                date.today(),
            )
            for signal in _movement_signals_from_rows(ticker, rows):
                store.put_market_signal(signal)
                signal_count += 1
        except Exception as exc:
            logger.warning("movement_signal_generation_failed", ticker=ticker, error=str(exc))
    return signal_count


def _movement_signals_from_rows(ticker: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return []
    ordered = sorted(rows, key=lambda row: str(row["trading_date"]))
    latest = ordered[-1]
    previous = ordered[-2]
    close = _analysis_close_price(latest)
    previous_close = _analysis_close_price(previous)
    if previous_close <= 0:
        return []
    price_change = (close - previous_close) / previous_close * Decimal("100")
    prior_rows = ordered[:-1]
    avg_volume = sum(Decimal(str(row.get("volume", 0))) for row in prior_rows) / max(
        Decimal(len(prior_rows)), Decimal("1")
    )
    latest_volume = Decimal(str(latest.get("volume", 0)))
    volume_ratio = latest_volume / avg_volume if avg_volume > 0 else Decimal("1")
    signal_date = _record_date(latest["trading_date"])
    signals = []
    if abs(price_change) >= Decimal("3"):
        score = int(max(-45, min(45, float(price_change) * 6)))
        signals.append(
            _market_signal(
                ticker,
                signal_date,
                "price_move",
                "positive" if price_change > 0 else "negative",
                score,
                "Large daily price move",
                f"{ticker} moved {price_change:.2f}% versus the prior close.",
                {
                    "price_change_percent": price_change,
                    "close_price": close,
                    "previous_close_price": previous_close,
                    "volume": int(latest_volume),
                    "average_volume": avg_volume,
                },
            )
        )
    if volume_ratio >= Decimal("1.8"):
        signals.append(
            _market_signal(
                ticker,
                signal_date,
                "volume_move",
                "positive" if price_change >= 0 else "negative",
                25 if price_change >= 0 else -25,
                "Unusual volume",
                f"{ticker} traded at {volume_ratio:.1f}x its recent average volume.",
                {
                    "price_change_percent": price_change,
                    "volume_ratio": volume_ratio,
                    "close_price": close,
                    "previous_close_price": previous_close,
                    "volume": int(latest_volume),
                    "average_volume": avg_volume,
                },
            )
        )
    return signals


def _market_signal(
    ticker: str,
    signal_date: date,
    signal_type: str,
    direction: str,
    score: int,
    title: str,
    summary: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "direction": direction,
        "score": score,
        "title": title,
        "summary": summary,
        "source": {
            "provider": "stock_collector",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "raw": {key: str(value) for key, value in metrics.items()},
        },
        **metrics,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _analysis_close_price(row: dict[str, Any]) -> Decimal:
    adjusted = row.get("adjusted_close_price")
    if adjusted is not None:
        return Decimal(str(adjusted))
    return Decimal(str(row["close_price"]))


def _record_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _fetch_yfinance_with_retry(tickers: list[str], period: str = "1d") -> Any | None:
    """Fetch data from yfinance with exponential backoff retry.

    Retries up to MAX_RETRIES times with exponential backoff starting
    at INITIAL_BACKOFF_SECONDS.
    """
    ticker_str = " ".join(tickers)
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(attempt=attempt, ticker_count=len(tickers), period=period)
            log.info("yfinance_fetch_attempt")

            data = yf.download(
                ticker_str,
                period=period,
                group_by="ticker",
                auto_adjust=False,
                threads=False,
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


def _extract_ticker_data(
    data: Any,
    ticker: str,
    period: str = "1d",
    stock_metadata: dict[str, Any] | None = None,
) -> list[dict] | None:
    """Extract OHLCV records for a single ticker from yfinance DataFrame.

    Returns None if data is missing or malformed.
    """
    result = _extract_ticker_data_result(data, ticker, period, stock_metadata)
    return result.records if result.records else None


def _extract_ticker_data_result(
    data: Any,
    ticker: str,
    period: str = "1d",
    stock_metadata: dict[str, Any] | None = None,
) -> ExtractResult:
    """Extract OHLCV records and classify failures for collection summaries."""
    try:
        # For single ticker downloads, columns are flat
        # For multi-ticker downloads, columns are multi-level (ticker, field)
        if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
            if ticker not in data.columns.get_level_values(0):
                logger.warning("ticker_not_in_response", ticker=ticker)
                return ExtractResult(failure_reason="no_data")
            ticker_data = data[ticker]
        else:
            # Single ticker case
            ticker_data = data

        if ticker_data.empty:
            return ExtractResult(failure_reason="no_data")

        records = []
        for idx, row in ticker_data.iterrows():
            record = _validate_record(
                ticker,
                idx,
                row,
                period=period,
                stock_metadata=stock_metadata,
            )
            if record:
                records.append(record)

        if records:
            return ExtractResult(records=records)
        return ExtractResult(failure_reason="malformed")

    except Exception as e:
        logger.warning(
            "ticker_data_extraction_failed",
            ticker=ticker,
            error=str(e),
        )
        return ExtractResult(failure_reason="malformed")


def _validate_record(
    ticker: str,
    trading_date: Any,
    row: Any,
    period: str = "1d",
    stock_metadata: dict[str, Any] | None = None,
) -> dict | None:
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
        adjusted_close_price = _to_decimal(row.get("Adj Close"))
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
            "adjusted_close_price": adjusted_close_price,
            "volume": volume,
            "data_provider": "yfinance",
            "provider_symbol": ticker,
            "provider_endpoint": "yf.download",
            "provider_priority": "primary",
            "price_adjustment": "unadjusted",
            "has_adjusted_close": adjusted_close_price is not None,
            "corporate_action_adjusted": False,
            "adjustment_context": (
                "raw_ohlcv_with_adjusted_close"
                if adjusted_close_price is not None
                else "raw_ohlcv_only"
            ),
            "split_dividend_adjustment": (
                "adjusted_close_available"
                if adjusted_close_price is not None
                else "not_available"
            ),
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": period,
            **_fetch_window(period),
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


def _metadata_value(
    stock_metadata: dict[str, Any] | None,
    field: str,
    default: str | None = None,
) -> str | None:
    if not stock_metadata:
        return default
    value = stock_metadata.get(field)
    if value is None:
        return default
    value_str = str(value).strip()
    return value_str or default


def _fetch_window(period: str, today: date | None = None) -> dict[str, str | None]:
    today = today or date.today()
    if period.endswith("d") and period[:-1].isdigit():
        start = today - timedelta(days=int(period[:-1]))
    elif period.endswith("y") and period[:-1].isdigit():
        years = int(period[:-1])
        try:
            start = today.replace(year=today.year - years)
        except ValueError:
            start = today.replace(month=2, day=28, year=today.year - years)
    elif period == "compact":
        start = today - timedelta(days=100)
    else:
        start = None

    return {
        "fetch_window_start": start.isoformat() if start else None,
        "fetch_window_end": today.isoformat(),
    }


def _store_records(records: list[dict]) -> StoreResult:
    """Store OHLCV records in DynamoDB, skipping duplicate ticker/date items."""
    result = StoreResult()
    if not records:
        return result

    for record in records:
        try:
            record["collected_at"] = datetime.utcnow().isoformat()
            if store.put_stock_data(record):
                result.inserted_records += 1
            else:
                result.duplicate_records += 1
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
            result.failed_records += 1

    return result


def _alpha_vantage_fallback(tickers: list[str]) -> int:
    """Attempt to fetch data from Alpha Vantage for failed tickers.

    Alpha Vantage free tier: 500 calls/day, one ticker at a time.
    Uses retry logic with exponential backoff.

    Returns the number of successfully collected records.
    """
    result, _ = _alpha_vantage_fallback_with_details(tickers)
    return result.inserted_records


def _alpha_vantage_fallback_with_details(
    tickers: list[str],
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> tuple[StoreResult, set[str]]:
    """Attempt Alpha Vantage fallback and return collected records plus successful tickers."""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("alpha_vantage_api_key_not_configured")
        return StoreResult(), set()

    result = StoreResult()
    successful_tickers: set[str] = set()

    for ticker in tickers:
        records = _fetch_alpha_vantage_with_retry(
            ticker,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if records:
            stored = _store_records(records)
            result.inserted_records += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                successful_tickers.add(ticker)

    return result, successful_tickers


def _stooq_fallback_with_details(
    tickers: list[str],
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> tuple[StoreResult, set[str]]:
    """Attempt no-key Stooq fallback and return collected records plus successful tickers."""
    result = StoreResult()
    successful_tickers: set[str] = set()

    for ticker in tickers:
        records = _fetch_stooq_with_retry(
            ticker,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if records:
            stored = _store_records(records)
            result.inserted_records += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                successful_tickers.add(ticker)

    return result, successful_tickers


def _fetch_alpha_vantage_with_retry(
    ticker: str,
    stock_metadata: dict[str, Any] | None = None,
) -> list[dict] | None:
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
                record = _parse_alpha_vantage_record(
                    ticker,
                    date_str,
                    values,
                    stock_metadata=stock_metadata,
                )
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


def _fetch_stooq_with_retry(
    ticker: str,
    stock_metadata: dict[str, Any] | None = None,
) -> list[dict] | None:
    """Fetch the most recent daily OHLCV record from Stooq without an API key."""
    backoff = INITIAL_BACKOFF_SECONDS
    provider_symbol = _stooq_symbol(ticker, stock_metadata)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(
                ticker=ticker,
                provider_symbol=provider_symbol,
                attempt=attempt,
                provider="stooq",
            )
            log.info("stooq_fetch_attempt")

            response = requests.get(
                STOOQ_BASE_URL,
                params={"s": provider_symbol, "i": "d"},
                timeout=30,
            )
            response.raise_for_status()
            records = _parse_stooq_csv(
                ticker,
                provider_symbol,
                response.text,
                stock_metadata=stock_metadata,
            )
            if records:
                log.info("stooq_fetch_success")
                return records
            log.warning("stooq_no_data")

        except requests.exceptions.RequestException as e:
            logger.warning(
                "stooq_request_failed",
                ticker=ticker,
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("stooq_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("stooq_all_retries_exhausted", ticker=ticker)
    return None


def _stooq_symbol(ticker: str, stock_metadata: dict[str, Any] | None = None) -> str:
    exchange = _metadata_value(stock_metadata, "exchange", "").upper()
    normalized = ticker.lower().replace(".", "-")
    if exchange in {"NYSE", "NASDAQ", "NYSEARCA", "NYSEAMERICAN", "AMEX", ""}:
        return f"{normalized}.us"
    return normalized


def _parse_stooq_csv(
    ticker: str,
    provider_symbol: str,
    csv_text: str,
    stock_metadata: dict[str, Any] | None = None,
) -> list[dict] | None:
    try:
        import csv
        from io import StringIO

        rows = list(csv.DictReader(StringIO(csv_text.strip())))
        if not rows:
            return None

        records: list[dict] = []
        for row in reversed(rows):
            record = _parse_stooq_record(
                ticker,
                provider_symbol,
                row,
                stock_metadata=stock_metadata,
            )
            if record:
                records.append(record)
            if len(records) >= STOOQ_MAX_RECORDS_PER_TICKER:
                break
        return records or None
    except csv.Error as e:
        logger.warning("stooq_csv_parse_failed", ticker=ticker, error=str(e))
        return None


def _parse_stooq_record(
    ticker: str,
    provider_symbol: str,
    values: dict[str, Any],
    stock_metadata: dict[str, Any] | None = None,
) -> dict | None:
    try:
        record_date = date.fromisoformat(str(values.get("Date", ""))[:10])
        open_price = _to_decimal(values.get("Open"))
        high_price = _to_decimal(values.get("High"))
        low_price = _to_decimal(values.get("Low"))
        close_price = _to_decimal(values.get("Close"))
        volume = int(values.get("Volume") or 0)

        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "stooq_malformed_record",
                ticker=ticker,
                trading_date=str(values.get("Date")),
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "stooq_invalid_prices",
                ticker=ticker,
                trading_date=str(values.get("Date")),
            )
            return None

        if volume < 0:
            logger.warning(
                "stooq_invalid_volume",
                ticker=ticker,
                trading_date=str(values.get("Date")),
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
            "data_provider": "stooq",
            "provider_symbol": provider_symbol,
            "provider_endpoint": "q/d/l",
            "provider_priority": "fallback",
            "price_adjustment": "unadjusted",
            "adjusted_close_price": None,
            "has_adjusted_close": False,
            "corporate_action_adjusted": False,
            "adjustment_context": "raw_ohlcv_only",
            "split_dividend_adjustment": "not_available",
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": "stooq_daily",
            **_fetch_window("compact"),
        }

    except (ValueError, TypeError) as e:
        logger.warning(
            "stooq_parse_failed",
            ticker=ticker,
            values=values,
            error=str(e),
        )
        return None


def _parse_alpha_vantage_record(
    ticker: str,
    date_str: str,
    values: dict,
    stock_metadata: dict[str, Any] | None = None,
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
            "data_provider": "alpha_vantage",
            "provider_symbol": ticker,
            "provider_endpoint": "TIME_SERIES_DAILY",
            "provider_priority": "fallback",
            "price_adjustment": "unadjusted",
            "adjusted_close_price": None,
            "has_adjusted_close": False,
            "corporate_action_adjusted": False,
            "adjustment_context": "raw_ohlcv_only",
            "split_dividend_adjustment": "not_available",
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": "compact",
            **_fetch_window("compact"),
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
    _emit_metric_data(
        [
            {
                "MetricName": metric_name,
                "Value": value,
                "Unit": "Count",
            }
        ]
    )


def _emit_metric_data(metric_data: list[dict[str, Any]]) -> None:
    """Emit custom CloudWatch metrics."""
    try:
        client = boto3.client("cloudwatch")
        timestamp = datetime.now(timezone.utc)
        client.put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    **metric,
                    "Timestamp": timestamp,
                }
                for metric in metric_data
            ],
        )
        logger.info(
            "cloudwatch_metrics_emitted",
            metrics=[metric["MetricName"] for metric in metric_data],
        )
    except Exception as e:
        logger.warning(
            "cloudwatch_metric_failed",
            metrics=[metric.get("MetricName") for metric in metric_data],
            error=str(e),
        )
